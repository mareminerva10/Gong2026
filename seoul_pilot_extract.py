"""
seoul_pilot_extract.py
======================

Resumable AlphaEarth extractor for the 마포구 + 강남구 legal-dong pilot.

Input:
    data/pilot_legal_dong_manifest.parquet

Output:
    data/seoul_pilot_alphaearth_cache/bjd_<emd_cd>_<year>.parquet
    data/seoul_pilot_alphaearth.parquet

This script intentionally does not learn a model, repair labels, or call any
dashboard code. It only turns the official D001 EMD pilot polygons into cached
AlphaEarth annual mean embeddings so the pilot acceptance checks can run.

Examples:
    python seoul_pilot_extract.py --dry-run
    python seoul_pilot_extract.py --gcp-project gong2026
    python seoul_pilot_extract.py --offline
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_MANIFEST = DATA / "pilot_legal_dong_manifest.parquet"
DEFAULT_CACHE_DIR = DATA / "seoul_pilot_alphaearth_cache"
DEFAULT_OUTPUT = DATA / "seoul_pilot_alphaearth.parquet"

YEARS = list(range(2017, 2025))
EMBED_COLS = [f"A{i:02d}" for i in range(64)]
TARGET_CRS = "EPSG:4326"
PILOT_LAWD_CDS = {"11440", "11680"}  # 마포구 + 강남구
REQUIRED_OVERLAPS = {
    ("11440124", "연남동"),
    ("11440123", "망원동"),
    ("11680110", "압구정동"),
    ("11680106", "대치동"),
}


def crs_label(gdf: gpd.GeoDataFrame) -> str:
    epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    return f"EPSG:{epsg}" if epsg else str(gdf.crs)


def initialize_ee(gcp_project: str, service_account_key: str | None) -> None:
    import ee
    if service_account_key:
        info = json.loads(Path(service_account_key).read_text(encoding="utf-8"))
        creds = ee.ServiceAccountCredentials(info["client_email"], service_account_key)
        ee.Initialize(creds, project=gcp_project)
    else:
        ee.Initialize(project=gcp_project)


def cache_path(cache_dir: Path, emd_cd: str, year: int) -> Path:
    return cache_dir / f"bjd_{emd_cd}_{year}.parquet"


def parse_years(raw: str) -> list[int]:
    years: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            years.extend(range(int(start), int(end) + 1))
        else:
            years.append(int(part))
    out = sorted(set(years))
    if not out:
        raise ValueError("no years parsed")
    return out


def load_manifest(path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"missing pilot manifest: {path}. Run legal_dong_polygons.py first.")
    gdf = gpd.read_parquet(path)
    required = {
        "emd_cd", "dong_name_kr", "lawd_cd", "gu_name",
        "centroid_lat", "centroid_lon", "geometry",
    }
    missing = required - set(gdf.columns)
    if missing:
        raise ValueError(f"manifest missing required columns: {sorted(missing)}")
    if gdf.crs is None:
        raise ValueError("manifest has no CRS")
    if gdf.crs.to_string() != TARGET_CRS:
        raise ValueError(f"manifest CRS must be {TARGET_CRS}; got {gdf.crs}")

    gdf = gdf.copy()
    gdf["emd_cd"] = gdf["emd_cd"].astype("string")
    gdf["lawd_cd"] = gdf["lawd_cd"].astype("string")
    gdf["dong_name_kr"] = gdf["dong_name_kr"].astype("string")
    gdf["gu_name"] = gdf["gu_name"].astype("string")

    bad_lawd = sorted(set(gdf["lawd_cd"]) - PILOT_LAWD_CDS)
    if bad_lawd:
        raise ValueError(f"manifest includes non-pilot lawd_cd values: {bad_lawd}")
    dupes = gdf[gdf["emd_cd"].duplicated(keep=False)]
    if not dupes.empty:
        raise ValueError(
            "manifest has duplicate emd_cd values: "
            f"{dupes[['emd_cd', 'dong_name_kr']].to_dict('records')}")
    if not gdf.geometry.is_valid.all():
        bad = gdf.loc[~gdf.geometry.is_valid, ["emd_cd", "dong_name_kr"]]
        raise ValueError(f"manifest has invalid geometries: {bad.to_dict('records')}")

    keys = set(zip(gdf["emd_cd"], gdf["dong_name_kr"]))
    missing_overlaps = REQUIRED_OVERLAPS - keys
    if missing_overlaps:
        raise ValueError(f"manifest missing required labeled overlaps: {missing_overlaps}")
    return gdf.sort_values(["lawd_cd", "emd_cd"]).reset_index(drop=True)


def fetch_one(poly_geom: dict, year: int, max_pixels: float, tile_scale: int) -> np.ndarray | None:
    import ee
    geom = ee.Geometry(poly_geom)
    coll = (ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
            .select(EMBED_COLS)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .filterBounds(geom))
    img = coll.mosaic()
    stats = img.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom,
        scale=10,
        maxPixels=max_pixels,
        tileScale=tile_scale,
    ).getInfo()
    if not stats or stats.get("A00") is None:
        return None
    return np.array([float(stats.get(b, 0.0)) for b in EMBED_COLS], dtype="float32")


def record_from(row: pd.Series, year: int, vec: np.ndarray) -> dict:
    rec = {
        "emd_cd": str(row.emd_cd),
        "dong_name_kr": str(row.dong_name_kr),
        "lawd_cd": str(row.lawd_cd),
        "gu_name": str(row.gu_name),
        "year": int(year),
        "centroid_lat": float(row.centroid_lat),
        "centroid_lon": float(row.centroid_lon),
        "physical_source": "alphaearth_ee",
        "physical_grain": "legal-dong-year",
        "physical_artifact_policy": "flag_2022",
    }
    if "effective_date" in row.index:
        rec["polygon_effective_date"] = str(row.effective_date)
    rec.update(dict(zip(EMBED_COLS, vec.astype("float32").tolist())))
    return rec


def fetch_embeddings(
    manifest: gpd.GeoDataFrame,
    years: list[int],
    cache_dir: Path,
    *,
    offline: bool,
    dry_run: bool,
    max_pixels: float,
    tile_scale: int,
    sleep_s: float,
    retries: int,
    limit: int | None,
) -> pd.DataFrame:
    if not offline and not dry_run:
        cache_dir.mkdir(parents=True, exist_ok=True)

    tasks = [(row, year) for _, row in manifest.iterrows() for year in years]
    if limit is not None:
        tasks = tasks[:limit]

    rows: list[dict] = []
    missing: list[tuple[str, int]] = []
    cached = 0
    pulled = 0
    planned = 0
    total = len(tasks)

    for row, year in tasks:
        emd_cd = str(row.emd_cd)
        cache = cache_path(cache_dir, emd_cd, year)
        if cache.exists():
            rows.append(pd.read_parquet(cache).iloc[0].to_dict())
            cached += 1
            continue
        if offline:
            missing.append((emd_cd, year))
            continue
        if dry_run:
            planned += 1
            continue

        vec = None
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                vec = fetch_one(mapping(row.geometry), year, max_pixels, tile_scale)
                break
            except Exception as exc:  # Earth Engine transient errors vary by backend.
                last_error = exc
                if attempt >= retries:
                    raise
                wait_s = min(2.0 * (attempt + 1), 10.0)
                print(f"  ! retry {attempt + 1}/{retries} for "
                      f"{emd_cd} {year}: {exc}", file=sys.stderr)
                time.sleep(wait_s)
        if vec is None:
            missing.append((emd_cd, year))
            if last_error is not None:
                print(f"  ! no embedding {emd_cd} {row.dong_name_kr} {year} "
                      f"after retry: {last_error}", file=sys.stderr)
            print(f"  ! no embedding {emd_cd} {row.dong_name_kr} {year}", file=sys.stderr)
            continue
        rec = record_from(row, year, vec)
        # Atomic write: parquet → .tmp → rename. Path.replace is atomic on
        # POSIX and Windows, so a SIGKILL mid-flush can leave a .tmp file
        # behind but cannot leave a truncated .parquet that the next run's
        # `cache.exists()` check would accept as cached.
        # See docs/full_seoul_expansion_scope.md §8 caveat #3.
        tmp = cache.with_suffix(".tmp")
        pd.DataFrame([rec]).to_parquet(tmp, index=False)
        tmp.replace(cache)
        rows.append(rec)
        pulled += 1
        if pulled % 10 == 0:
            print(f"  ... pulled {pulled}/{total} fresh records")
        if sleep_s:
            time.sleep(sleep_s)

    if dry_run:
        print(f"  dry-run plan: {planned} uncached, {cached} cached, {total} target rows")
    else:
        print(f"  embeddings: {pulled} fresh, {cached} cached, "
              f"{len(missing)} missing  (target {total})")
    if missing:
        print("  missing cache/result pairs:")
        for emd_cd, year in missing[:20]:
            print(f"    {emd_cd} {year}")
        if len(missing) > 20:
            print(f"    ... {len(missing) - 20} more")
    return pd.DataFrame(rows)


def validate_panel(df: pd.DataFrame, expected_rows: int) -> None:
    if df.empty:
        print("  panel validation: no cached/fetched rows available")
        return
    required = {"emd_cd", "year", *EMBED_COLS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"panel missing columns after fetch: {sorted(missing)}")
    dupes = df[df.duplicated(["emd_cd", "year"], keep=False)]
    if not dupes.empty:
        raise ValueError(
            "duplicate cache records for emd_cd/year: "
            f"{dupes[['emd_cd', 'year']].to_dict('records')[:10]}")
    if len(df) == expected_rows:
        print(f"  panel validation: complete ({len(df)}/{expected_rows})")
    else:
        print(f"  panel validation: partial ({len(df)}/{expected_rows})")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Extract AlphaEarth embeddings for the 마포구+강남구 "
                    "legal-dong pilot. Per-call cache; resumable.")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help=f"pilot GeoParquet manifest (default: {DEFAULT_MANIFEST})")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                    help=f"per-call cache directory (default: {DEFAULT_CACHE_DIR})")
    ap.add_argument("--output",
                    help=f"combined output parquet (default: {DEFAULT_OUTPUT}; "
                         "skipped for --limit runs unless explicitly set)")
    ap.add_argument("--years", default="2017-2024",
                    help="comma/range years, e.g. 2017-2024 or 2021,2022")
    ap.add_argument("--gcp-project",
                    help="Google Cloud project for Earth Engine billing/quota")
    ap.add_argument("--service-account-key",
                    help="optional service-account JSON key path")
    ap.add_argument("--offline", action="store_true",
                    help="read cache only; no Earth Engine calls")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate manifest and print cache plan; no Earth Engine calls")
    ap.add_argument("--limit", type=int,
                    help="limit number of (polygon, year) tasks for smoke tests")
    ap.add_argument("--max-pixels", type=float, default=1e8)
    ap.add_argument("--tile-scale", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=0.05,
                    help="seconds to sleep after fresh EE pulls")
    ap.add_argument("--retries", type=int, default=2,
                    help="retry count for transient EE reduceRegion errors")
    args = ap.parse_args(argv)

    years = parse_years(args.years)
    manifest = load_manifest(Path(args.manifest))
    full_expected = len(manifest) * len(years)
    expected = full_expected
    if args.limit is not None:
        expected = min(expected, args.limit)
    print(f"Manifest: {len(manifest)} legal dongs "
          f"({(manifest.lawd_cd == '11440').sum()} 마포구 + "
          f"{(manifest.lawd_cd == '11680').sum()} 강남구), CRS={crs_label(manifest)}")
    print(f"Years: {years[0]}..{years[-1]} ({len(years)} years); target rows={expected}")

    if not args.offline and not args.dry_run:
        if not args.gcp_project:
            print("--gcp-project is required unless --offline or --dry-run is set",
                  file=sys.stderr)
            return 2
        initialize_ee(args.gcp_project, args.service_account_key)

    started = time.perf_counter()
    df = fetch_embeddings(
        manifest,
        years,
        Path(args.cache_dir),
        offline=args.offline,
        dry_run=args.dry_run,
        max_pixels=args.max_pixels,
        tile_scale=args.tile_scale,
        sleep_s=args.sleep,
        retries=args.retries,
        limit=args.limit,
    )
    elapsed_s = time.perf_counter() - started
    validate_panel(df, expected)
    print(f"  elapsed: {elapsed_s:.1f}s  "
          f"({elapsed_s / max(expected, 1):.2f}s per target row)")
    output_arg_was_set = args.output is not None
    output_path = Path(args.output) if output_arg_was_set else DEFAULT_OUTPUT
    should_write_output = (
        not args.dry_run
        and not df.empty
        and (output_arg_was_set or args.limit is None or len(df) == full_expected)
    )
    if should_write_output:
        output = output_path
        output.parent.mkdir(parents=True, exist_ok=True)
        df = df.sort_values(["lawd_cd", "emd_cd", "year"]).reset_index(drop=True)
        df.to_parquet(output, index=False)
        print(f"Panel written: {output} rows={len(df)} cols={len(df.columns)}")
    elif not args.dry_run and not df.empty:
        print("Panel output skipped for bounded --limit run; per-call cache was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
