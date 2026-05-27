"""
legal_dong_polygons.py
======================

Pilot polygon manifest builder for 마포구 (Mapo-gu) + 강남구 (Gangnam-gu).

Reads the NSDI D001 monthly AL EMD snapshot (distributed via
`data.go.kr 15045881` / VWorld `dsId=21`), reprojects to EPSG:4326,
filters to the pilot gus, and writes a GeoParquet manifest under
`data/`. Crosswalks every labeled case in `data/labeled_cases.csv`
against the canonical `A1` 법정동 code by `(dong_name_kr, lawd_cd)`
and surfaces any `dong_code` mismatches as non-fatal legacy-code
drift. After the canonical-code repair, the expected mismatch count is 0.

This module does **no** Earth Engine calls, no model-panel changes,
and no `labeled_cases.csv` repair. The mismatch report it prints is
the input to a future, separate CSV-repair commit.

Source schema (verified 2026-05-27 against `AL_D001_00_20260509(EMD)`,
see `docs/full_seoul_expansion_scope.md` §4):

    A0  int32   internal feature id (dropped)
    A1  str(8)  canonical 법정동 code  →  emd_cd
    A2  str     Korean dong name       →  dong_name_kr
    A3  date    effective date         →  effective_date
    A4  str(5)  시군구 code            →  lawd_cd

CRS: source EPSG:5186 (Korea 2000 Central Belt 2010) → reproject to
EPSG:4326 for downstream Earth Engine handoff. Attribute encoding:
CP949 → decoded to UTF-8 at load time.

Usage
-----
    python legal_dong_polygons.py \\
        --input "C:/Users/marem/Documents/g/20251119-20260521/AL_D001_00_20260509.zip"

The `--input` arg accepts the outer AL ZIP, the nested EMD ZIP, an
extracted directory, or a `.shp` file directly.

Acceptance behavior
-------------------
Fatal (exit 1):
- no `(dong_name_kr, lawd_cd)` match against the canonical EMD lookup
- ambiguous match not resolved by lawd_cd
- the four required pilot dongs (Yeonnam, Mangwon, Apgujeong, Daechi)
  are absent from `labeled_cases.csv`

Non-fatal (printed as `[data-QA]` warnings, manifest still writes):
- `labeled_cases.csv.dong_code != canonical A1`. This should remain 0
  after the canonical-code repair, but is kept as a guardrail for future
  labeled-case additions or source refreshes.
- `labeled_cases.csv` lat/lon is not contained in the matched 법정동
  polygon. These coordinates were used operationally as 1km
  proxy-box centers (see `prototype.write_polygons_if_absent`), not
  as guaranteed in-polygon centroids; non-containment for small
  central-Seoul 법정동 (성수동1가, 익선동, 압구정동) is therefore a
  design artifact, not a data error.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CASES_CSV = DATA / "labeled_cases.csv"
DEFAULT_OUTPUT = DATA / "pilot_legal_dong_manifest.parquet"

# Pilot scope
PILOT_LAWD_CDS: set[str] = {"11440", "11680"}  # 마포구 + 강남구
REQUIRED_PILOT_CASES = {
    ("연남동",   "11440"): "Yeonnam",
    ("망원동",   "11440"): "Mangwon",
    ("압구정동", "11680"): "Apgujeong",
    ("대치동",   "11680"): "Daechi",
}

# Field mapping from the D001 AL EMD schema
F_ROW_ID = "A0"
F_EMD_CD = "A1"
F_DONG_NAME = "A2"
F_EFFECTIVE_DATE = "A3"
F_LAWD_CD = "A4"

SHP_ENCODING = "cp949"
TARGET_CRS = "EPSG:4326"

# Reverse Seoul gu map (lawd_cd → Korean gu name) for downstream provenance.
# Source: standard 행정안전부 5-digit 시군구 codes; matches the existing
# molit_unsold_client SEOUL_GU_LAWD_CD inversed.
SEOUL_GU_NAME: dict[str, str] = {
    "11110": "종로구", "11140": "중구",     "11170": "용산구",
    "11200": "성동구", "11215": "광진구",   "11230": "동대문구",
    "11260": "중랑구", "11290": "성북구",   "11305": "강북구",
    "11320": "도봉구", "11350": "노원구",   "11380": "은평구",
    "11410": "서대문구", "11440": "마포구", "11470": "양천구",
    "11500": "강서구", "11530": "구로구",   "11545": "금천구",
    "11560": "영등포구", "11590": "동작구", "11620": "관악구",
    "11650": "서초구", "11680": "강남구",   "11710": "송파구",
    "11740": "강동구",
}


# --- ZIP / SHP resolution ------------------------------------------------

def _resolve_shp_path(input_path: Path, temp_dir: Path) -> Path:
    """Return a usable .shp path. Accepts:
      - outer AL ZIP containing AL_*(EMD).zip
      - inner EMD ZIP
      - a directory containing *EMD*.shp
      - a .shp file directly
    Nested ZIPs are extracted into temp_dir as needed.
    """
    if input_path.is_dir():
        candidates = list(input_path.rglob("*EMD*.shp"))
        if not candidates:
            raise FileNotFoundError(f"no *EMD*.shp under {input_path}")
        return candidates[0]
    if input_path.suffix.lower() == ".shp":
        return input_path
    if input_path.suffix.lower() != ".zip":
        raise ValueError(
            f"unsupported input type: {input_path} "
            "(expected .zip, .shp, or directory)")

    def extract_flat(zf: zipfile.ZipFile, member: str, dest: Path) -> Path:
        target = dest / Path(member).name
        target.write_bytes(zf.read(member))
        return target

    with zipfile.ZipFile(input_path) as zf:
        names = zf.namelist()
        # Case 1: inner ZIP holds the SHP family directly
        shp_names = [n for n in names
                     if n.lower().endswith(".shp") and "emd" in n.lower()]
        if shp_names:
            for name in names:
                if not name.endswith("/"):
                    extract_flat(zf, name, temp_dir)
            return temp_dir / Path(shp_names[0]).name
        # Case 2: outer ZIP contains nested EMD ZIP
        nested = [n for n in names
                  if n.lower().endswith(".zip") and "emd" in n.lower()]
        if not nested:
            raise FileNotFoundError(
                f"no EMD .shp or nested EMD .zip inside {input_path}; "
                f"got first entries: {names[:5]}")
        inner_zip = extract_flat(zf, nested[0], temp_dir)
        with zipfile.ZipFile(inner_zip) as zf2:
            for name in zf2.namelist():
                if not name.endswith("/"):
                    extract_flat(zf2, name, temp_dir)

    shp_candidates = list(temp_dir.glob("*.shp"))
    if not shp_candidates:
        raise FileNotFoundError(f"no .shp emerged after extracting {input_path}")
    return shp_candidates[0]


# --- Load + manifest -----------------------------------------------------

def load_emd(shp_path: Path) -> gpd.GeoDataFrame:
    """Load the D001 AL EMD shapefile. Returns a GeoDataFrame in the
    source CRS (EPSG:5186) with normalized column names. Reprojection
    to EPSG:4326 happens later, after centroid math is done in the
    projected source CRS (avoids the lat/lon-centroid bias warning and
    keeps centroids accurate to the meter).
    """
    gdf = gpd.read_file(str(shp_path), encoding=SHP_ENCODING, engine="pyogrio")
    required = {F_EMD_CD, F_DONG_NAME, F_LAWD_CD}
    missing = required - set(gdf.columns)
    if missing:
        raise ValueError(
            f"D001 EMD schema mismatch — expected fields {sorted(required)}, "
            f"missing {sorted(missing)}. Got: {list(gdf.columns)}")
    rename = {F_EMD_CD: "emd_cd",
              F_DONG_NAME: "dong_name_kr",
              F_LAWD_CD: "lawd_cd"}
    if F_EFFECTIVE_DATE in gdf.columns:
        rename[F_EFFECTIVE_DATE] = "effective_date"
    gdf = gdf.rename(columns=rename)
    if F_ROW_ID in gdf.columns:
        gdf = gdf.drop(columns=[F_ROW_ID])

    gdf["emd_cd"] = gdf["emd_cd"].astype("string")
    gdf["lawd_cd"] = gdf["lawd_cd"].astype("string")
    gdf["dong_name_kr"] = gdf["dong_name_kr"].astype("string")
    if "effective_date" in gdf.columns:
        gdf["effective_date"] = pd.to_datetime(gdf["effective_date"]).dt.strftime("%Y-%m-%d")
    if gdf.crs is None:
        raise ValueError(
            "EMD shapefile has no CRS set; .prj is missing or malformed")
    return gdf


def build_pilot_manifest(gdf_src: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter to 마포구 + 강남구, compute centroids in the source CRS
    for accuracy, then reproject to EPSG:4326. Adds `gu_name`,
    `centroid_lat`, `centroid_lon` columns alongside the schema-normalized
    attributes.
    """
    pilot_src = gdf_src[gdf_src["lawd_cd"].isin(PILOT_LAWD_CDS)].copy()
    if pilot_src.empty:
        raise RuntimeError(
            f"no pilot rows after filter to lawd_cd in {sorted(PILOT_LAWD_CDS)}. "
            "Source file or A4 schema may be unexpected.")

    # Centroids in source CRS (EPSG:5186), then reproject the points.
    centroids_src = pilot_src.geometry.centroid
    pilot = pilot_src.to_crs(TARGET_CRS)
    centroids_4326 = gpd.GeoSeries(
        centroids_src, crs=pilot_src.crs).to_crs(TARGET_CRS)
    pilot["centroid_lat"] = centroids_4326.y.astype("float64")
    pilot["centroid_lon"] = centroids_4326.x.astype("float64")
    pilot["gu_name"] = pilot["lawd_cd"].map(SEOUL_GU_NAME).astype("string")

    # Final column order: identifying first, then metadata, then geometry.
    cols = ["emd_cd", "dong_name_kr", "lawd_cd", "gu_name"]
    if "effective_date" in pilot.columns:
        cols.append("effective_date")
    cols += ["centroid_lat", "centroid_lon", "geometry"]
    pilot = pilot[cols].sort_values(["lawd_cd", "emd_cd"]).reset_index(drop=True)
    return pilot


# --- Labeled-case crosswalk ---------------------------------------------

def crosswalk_labeled_cases(cases: pd.DataFrame,
                            full_lookup: gpd.GeoDataFrame
                            ) -> dict:
    """Crosswalk every row in `labeled_cases.csv` against the canonical
    EMD lookup. Key on `(dong_name_kr, lawd_cd)` against `(A2, A4)`.

    Returns a structured report with:
      rows: per-case match record (status ∈ {ok, dong_code_mismatch,
            missing, ambiguous})
      fatal: list of fatal errors that should abort manifest writing
      warnings: list of non-fatal data-QA notes (legacy code drift,
                lat/lon not contained in matched polygon)

    `dong_code_mismatch` and lat/lon-non-containment are both
    non-fatal — they reflect legacy CSV state that should be repaired
    in a separate dedicated commit, not blocked here.
    """
    expected = {"dong_code", "dong_name_kr", "name_roman", "lawd_cd"}
    missing_cols = expected - set(cases.columns)
    if missing_cols:
        raise ValueError(
            f"labeled_cases.csv missing required columns: {sorted(missing_cols)}")
    cases = cases.copy()
    cases["dong_code"] = cases["dong_code"].astype(str)
    cases["lawd_cd"] = cases["lawd_cd"].astype(str)

    rows: list[dict] = []
    fatal: list[str] = []
    has_latlon = {"lat", "lon"}.issubset(cases.columns)

    for r in cases.itertuples():
        match = full_lookup[
            (full_lookup["dong_name_kr"] == r.dong_name_kr)
            & (full_lookup["lawd_cd"] == r.lawd_cd)
        ]
        if match.empty:
            fatal.append(
                f"{r.name_roman}: no EMD match for "
                f"(name={r.dong_name_kr}, lawd_cd={r.lawd_cd})")
            rows.append({"name_roman": r.name_roman,
                         "match_status": "missing"})
            continue
        if len(match) > 1:
            fatal.append(
                f"{r.name_roman}: ambiguous EMD match — {len(match)} rows for "
                f"(name={r.dong_name_kr}, lawd_cd={r.lawd_cd})")
            rows.append({"name_roman": r.name_roman,
                         "match_status": "ambiguous"})
            continue

        m = match.iloc[0]
        canonical = m["emd_cd"]
        dong_ok = (r.dong_code == canonical)
        contained = None
        if has_latlon and pd.notna(r.lat) and pd.notna(r.lon):
            pt = Point(float(r.lon), float(r.lat))
            contained = bool(m.geometry.contains(pt))
        rows.append({
            "name_roman": r.name_roman,
            "csv_dong_code": r.dong_code,
            "canonical_emd_cd": canonical,
            "csv_lawd_cd": r.lawd_cd,
            "match_status": "ok" if dong_ok else "dong_code_mismatch",
            "contained": contained,
        })

    # All four required pilot dongs must appear in cases
    case_keys = {(r.dong_name_kr, r.lawd_cd) for r in cases.itertuples()}
    for (name, lawd), roman in REQUIRED_PILOT_CASES.items():
        if (name, lawd) not in case_keys:
            fatal.append(
                f"required pilot dong missing from labeled_cases.csv: "
                f"{roman} ({name}, lawd_cd={lawd})")

    # Build non-fatal warning summaries from the row records.
    warnings: list[str] = []
    n_total = len([r for r in rows if r.get("match_status") in
                   ("ok", "dong_code_mismatch")])
    mismatches = [r for r in rows if r.get("match_status") == "dong_code_mismatch"]
    if mismatches:
        warnings.append(
            f"Legacy dong_code mismatches: {len(mismatches)}/{n_total} labeled cases. "
            "Expected under current transition; EMD.A1 is canonical.")
    not_contained = [r for r in rows
                     if r.get("contained") is False]
    if not_contained:
        names = ", ".join(r["name_roman"] for r in not_contained)
        warnings.append(
            f"Lat/lon outside matched 법정동 polygon: {len(not_contained)}/{n_total} labeled cases. "
            "These coordinates were used as legacy 1km proxy-box centers, "
            "not guaranteed polygon centroids. "
            f"Cases: {names}.")

    return {"rows": rows, "fatal": fatal, "warnings": warnings}


def print_crosswalk(report: dict) -> None:
    rows = report["rows"]
    for w in report.get("warnings", []):
        print(f"\n[data-QA] {w}")

    print(f"\nPer-case crosswalk (canonical lookup via dong_name_kr + lawd_cd):")
    print(f"  {'name_roman':<12} {'csv_dong_code':<14} {'canonical_emd':<14} "
          f"{'lawd_cd':<8} {'status':<22} {'contained':<10}")
    for r in rows:
        if r.get("match_status") in ("missing", "ambiguous"):
            print(f"  {r['name_roman']:<12} {'':<14} {'':<14} {'':<8} "
                  f"{r['match_status']:<22} {'':<10}")
            continue
        contained = "n/a" if r["contained"] is None else (
            "yes" if r["contained"] else "NO")
        print(f"  {r['name_roman']:<12} {r['csv_dong_code']:<14} "
              f"{r['canonical_emd_cd']:<14} {r['csv_lawd_cd']:<8} "
              f"{r['match_status']:<22} {contained:<10}")


# --- CLI -----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Load D001 AL EMD legal-dong polygons; build "
                    "마포구+강남구 pilot manifest. No Earth Engine calls.")
    ap.add_argument("--input", required=True,
                    help="path to AL EMD ZIP (outer or nested), .shp, or "
                         "extracted directory")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT),
                    help=f"output GeoParquet (default: {DEFAULT_OUTPUT})")
    args = ap.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"input not found: {input_path}", file=sys.stderr)
        return 1

    DATA.mkdir(parents=True, exist_ok=True)
    td_path = DATA / f"_legal_dong_{uuid.uuid4().hex[:8]}"
    td_path.mkdir()
    try:
        shp_path = _resolve_shp_path(input_path, td_path)
        print(f"Loading EMD shapefile: {shp_path.name}")
        gdf_src = load_emd(shp_path)
        print(f"  total features: {len(gdf_src)}  source CRS: {gdf_src.crs}")
        if "effective_date" in gdf_src.columns:
            print(f"  effective_date (first row): "
                  f"{gdf_src['effective_date'].iloc[0]}")

        pilot = build_pilot_manifest(gdf_src)
        n_mapo = int((pilot["lawd_cd"] == "11440").sum())
        n_gangnam = int((pilot["lawd_cd"] == "11680").sum())
        print(f"  pilot dongs: {len(pilot)}  "
              f"(마포구 {n_mapo} + 강남구 {n_gangnam})  target CRS: {pilot.crs}")

        # Use the full nationwide GDF (reprojected to 4326) for the
        # crosswalk so labeled cases outside the pilot scope still resolve.
        full_4326 = gdf_src.to_crs(TARGET_CRS)
        cases = pd.read_csv(CASES_CSV)
        report = crosswalk_labeled_cases(cases, full_4326)
        print_crosswalk(report)

        if report["fatal"]:
            print("\nFATAL data-QA errors — manifest NOT written:",
                  file=sys.stderr)
            for f in report["fatal"]:
                print(f"  - {f}", file=sys.stderr)
            return 1

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        pilot.to_parquet(output, index=False)
        print(f"\nManifest written: {output}  "
              f"rows={len(pilot)}  cols={len(pilot.columns)}")
    finally:
        shutil.rmtree(td_path, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
