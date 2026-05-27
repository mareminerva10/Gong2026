"""
seoul_pilot_qa.py
=================

QA report for the 마포구 + 강남구 AlphaEarth legal-dong pilot.

This script reads the generated pilot panel and checks the non-negotiables
from docs/full_seoul_expansion_scope.md:

- 40 legal dongs × 8 years are present.
- No duplicate or missing embedding rows.
- Within-gu embedding variance is non-trivial.
- 2021→2022 AlphaEarth artifact diagnostics are surfaced.
- Optional overlap comparison against the legacy 12-dong EE panel if that
  artifact exists locally.

It does not call Earth Engine and does not write model/dashboard outputs.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from legal_dong_polygons import (
    PILOT_LAWD_CDS,
    SEOUL_GU_NAME,
    _resolve_shp_path,
    load_emd,
)


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_PANEL = DATA / "seoul_pilot_alphaearth.parquet"
DEFAULT_MANIFEST = DATA / "pilot_legal_dong_manifest.parquet"
DEFAULT_LEGACY = DATA / "alphaearth_ee.parquet"
DEFAULT_REPORT = DATA / "seoul_pilot_alphaearth_qa.json"

YEARS = list(range(2017, 2025))
EMBED_COLS = [f"A{i:02d}" for i in range(64)]
SUSPECT_PAIR = "2021-2022"
REQUIRED_OVERLAPS = {
    "11440124": "Yeonnam",
    "11440123": "Mangwon",
    "11680110": "Apgujeong",
    "11680106": "Daechi",
}
LEGACY_OVERLAP_CODES = {
    # legacy 12-dong EE panel used 1km proxy boxes keyed by pre-repair codes
    "11440124": "11440710",  # Yeonnam
    "11440123": "11440730",  # Mangwon
    "11680110": "11680105",  # Apgujeong
    "11680106": "11680117",  # Daechi
}


def unit(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-9 else v


def load_panel(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing pilot AlphaEarth panel: {path}")
    df = pd.read_parquet(path)
    required = {"emd_cd", "dong_name_kr", "lawd_cd", "gu_name", "year", *EMBED_COLS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"panel missing required columns: {sorted(missing)}")
    df = df.copy()
    df["emd_cd"] = df["emd_cd"].astype(str)
    df["lawd_cd"] = df["lawd_cd"].astype(str)
    df["year"] = df["year"].astype(int)
    return df


def completeness(panel: pd.DataFrame, manifest_path: Path, years: list[int]) -> dict:
    manifest_rows = None
    manifest_dongs = None
    if manifest_path.exists():
        manifest = pd.read_parquet(manifest_path)
        manifest["emd_cd"] = manifest["emd_cd"].astype(str)
        manifest_rows = int(len(manifest))
        manifest_dongs = sorted(manifest["emd_cd"].unique().tolist())
    else:
        manifest_dongs = sorted(panel["emd_cd"].unique().tolist())
        manifest_rows = len(manifest_dongs)

    expected_pairs = {(emd, year) for emd in manifest_dongs for year in years}
    actual_pairs = {(r.emd_cd, int(r.year)) for r in panel.itertuples()}
    missing_pairs = sorted(expected_pairs - actual_pairs)
    extra_pairs = sorted(actual_pairs - expected_pairs)
    duplicate_count = int(panel.duplicated(["emd_cd", "year"]).sum())
    missing_embed_cells = int(panel[EMBED_COLS].isna().sum().sum())
    by_gu = (panel.groupby(["lawd_cd", "gu_name"])["emd_cd"]
             .nunique()
             .reset_index(name="n_dongs")
             .to_dict("records"))

    return {
        "manifest_rows": manifest_rows,
        "expected_rows": len(expected_pairs),
        "actual_rows": int(len(panel)),
        "n_dongs": int(panel["emd_cd"].nunique()),
        "years": sorted(panel["year"].unique().astype(int).tolist()),
        "by_gu": by_gu,
        "duplicate_count": duplicate_count,
        "missing_embed_cells": missing_embed_cells,
        "missing_pairs": missing_pairs[:20],
        "missing_pairs_count": len(missing_pairs),
        "extra_pairs": extra_pairs[:20],
        "extra_pairs_count": len(extra_pairs),
        "pass": (
            len(panel) == len(expected_pairs)
            and duplicate_count == 0
            and missing_embed_cells == 0
            and not missing_pairs
            and not extra_pairs
        ),
    }


def yoy_distances(panel: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for emd_cd, sub in panel.groupby("emd_cd"):
        sub = sub.sort_values("year")
        vecs = sub[EMBED_COLS].to_numpy("float32")
        years = sub["year"].to_numpy()
        first = sub.iloc[0]
        for i in range(len(years) - 1):
            a, b = vecs[i], vecs[i + 1]
            a_u, b_u = unit(a), unit(b)
            cos = float(np.clip(np.dot(a_u, b_u), -1.0, 1.0))
            rows.append({
                "emd_cd": str(emd_cd),
                "dong_name_kr": first["dong_name_kr"],
                "lawd_cd": str(first["lawd_cd"]),
                "gu_name": first["gu_name"],
                "year_from": int(years[i]),
                "year_to": int(years[i + 1]),
                "year_pair": f"{years[i]}-{years[i + 1]}",
                "angular": float(np.arccos(cos)),
                "cosine_dist": float(1.0 - cos),
                "euclid": float(np.linalg.norm(a - b)),
            })
    return pd.DataFrame(rows)


def artifact_summary(yoy: pd.DataFrame) -> dict:
    summaries: list[dict] = []
    for (lawd_cd, gu_name), sub in yoy.groupby(["lawd_cd", "gu_name"]):
        idx = sub.groupby("emd_cd")["angular"].idxmax()
        max_pair = sub.loc[idx, ["emd_cd", "year_pair"]]
        suspect = sub["year_pair"] == SUSPECT_PAIR
        suspect_med = float(sub.loc[suspect, "angular"].median())
        other_med = float(sub.loc[~suspect, "angular"].median())
        summaries.append({
            "lawd_cd": str(lawd_cd),
            "gu_name": gu_name,
            "n_dongs": int(sub["emd_cd"].nunique()),
            "share_max_is_2021_2022": float((max_pair["year_pair"] == SUSPECT_PAIR).mean()),
            "angular_2021_2022_median": suspect_med,
            "angular_other_median": other_med,
            "angular_ratio": suspect_med / other_med if other_med > 1e-12 else None,
        })

    idx = yoy.groupby("emd_cd")["angular"].idxmax()
    max_pair = yoy.loc[idx, ["emd_cd", "year_pair"]]
    suspect = yoy["year_pair"] == SUSPECT_PAIR
    suspect_med = float(yoy.loc[suspect, "angular"].median())
    other_med = float(yoy.loc[~suspect, "angular"].median())
    return {
        "overall": {
            "n_dongs": int(yoy["emd_cd"].nunique()),
            "share_max_is_2021_2022": float((max_pair["year_pair"] == SUSPECT_PAIR).mean()),
            "angular_2021_2022_median": suspect_med,
            "angular_other_median": other_med,
            "angular_ratio": suspect_med / other_med if other_med > 1e-12 else None,
        },
        "by_gu": summaries,
    }


def variance_summary(panel: pd.DataFrame) -> dict:
    rows: list[dict] = []
    for (lawd_cd, gu_name, year), sub in panel.groupby(["lawd_cd", "gu_name", "year"]):
        mat = sub[EMBED_COLS].to_numpy("float32")
        std_norm = float(np.linalg.norm(mat.std(axis=0)))
        centroid = mat.mean(axis=0)
        spread = float(np.median(np.linalg.norm(mat - centroid, axis=1)))
        rows.append({
            "lawd_cd": str(lawd_cd),
            "gu_name": gu_name,
            "year": int(year),
            "n_dongs": int(len(sub)),
            "std_vector_norm": std_norm,
            "median_distance_to_gu_centroid": spread,
        })
    df = pd.DataFrame(rows)
    return {
        "min_std_vector_norm": float(df["std_vector_norm"].min()),
        "median_std_vector_norm": float(df["std_vector_norm"].median()),
        "min_median_distance_to_gu_centroid": float(df["median_distance_to_gu_centroid"].min()),
        "rows": rows,
        "pass": bool((df["std_vector_norm"] > 1e-9).all()),
    }


def source_completeness(panel: pd.DataFrame,
                         source_shp_path: Path | None) -> dict:
    """Verify that the pilot manifest reflects every 마포구+강남구 dong
    present in the source D001 EMD shapefile. Catches manifest-filtering
    bugs and any rows manually dropped between source and panel.

    If `source_shp_path` is None or missing on disk, returns
    `{"checked": False, ...}` and contributes nothing to hard-fail.

    Honest caveat: the "authoritative dong list" here is the source SHP
    itself, not an independent MOIS 법정동 코드 표. This closes the
    manifest-regression gap but does not catch source-vs-MOIS divergence.
    Maps to §8 acceptance criterion #1.
    """
    if source_shp_path is None:
        return {"checked": False, "reason": "no --source-shp provided"}
    if not source_shp_path.exists():
        return {"checked": False,
                "reason": f"source SHP path not found: {source_shp_path}"}

    with tempfile.TemporaryDirectory() as td:
        shp = _resolve_shp_path(source_shp_path, Path(td))
        gdf_src = load_emd(shp)

    pilot_src = gdf_src[gdf_src["lawd_cd"].astype(str).isin(PILOT_LAWD_CDS)].copy()
    src_dongs = set(pilot_src["emd_cd"].astype(str))
    panel_dongs = set(panel["emd_cd"].astype(str))
    missing = sorted(src_dongs - panel_dongs)
    extra = sorted(panel_dongs - src_dongs)
    by_gu_src = (pilot_src.groupby("lawd_cd")["emd_cd"]
                          .nunique()
                          .to_dict())

    return {
        "checked": True,
        "source_shp": str(source_shp_path),
        "n_source_dongs": len(src_dongs),
        "n_panel_dongs": len(panel_dongs),
        "n_missing_from_panel": len(missing),
        "n_extra_in_panel": len(extra),
        "missing_examples": missing[:10],
        "extra_examples": extra[:10],
        "by_gu_source": {str(k): int(v) for k, v in by_gu_src.items()},
        "pass": (len(missing) == 0 and len(extra) == 0),
    }


def lawd_gu_consistency(panel: pd.DataFrame) -> dict:
    """Verify that every panel row's `gu_name` agrees with the canonical
    `SEOUL_GU_NAME[lawd_cd]` mapping from `legal_dong_polygons`. The
    consistency is enforced by construction in
    `legal_dong_polygons.build_pilot_manifest` (both fields derive from
    the same shapefile field `A4`), but asserting it here catches any
    regression — e.g. a manual panel mutation or a schema change that
    decouples the two columns.

    Maps to §8 acceptance criterion #4a in
    `docs/full_seoul_expansion_scope.md`.
    """
    lawd = panel["lawd_cd"].astype(str)
    expected_gu = lawd.map(SEOUL_GU_NAME)
    actual_gu = panel["gu_name"].astype(str)

    unknown_mask = expected_gu.isna()
    mismatch_mask = (~unknown_mask) & (expected_gu != actual_gu)

    return {
        "n_rows": int(len(panel)),
        "n_unknown_lawd": int(unknown_mask.sum()),
        "n_mismatch": int(mismatch_mask.sum()),
        "unknown_lawd_examples": (
            panel.loc[unknown_mask, ["emd_cd", "dong_name_kr", "lawd_cd"]]
            .head(10).to_dict("records")),
        "mismatch_examples": (
            panel.loc[mismatch_mask,
                       ["emd_cd", "dong_name_kr", "lawd_cd", "gu_name"]]
            .head(10).to_dict("records")),
        "pass": (not unknown_mask.any()) and (not mismatch_mask.any()),
    }


def overlap_summary(panel: pd.DataFrame, legacy_path: Path) -> dict:
    present = sorted(set(panel["emd_cd"]) & set(REQUIRED_OVERLAPS))
    out = {
        "required": REQUIRED_OVERLAPS,
        "present_in_pilot": present,
        "legacy_path": str(legacy_path),
        "status": "legacy_artifact_missing",
    }
    if not legacy_path.exists():
        return out
    legacy = pd.read_parquet(legacy_path)
    if "dong_code" not in legacy.columns:
        out["status"] = "legacy_missing_dong_code_column"
        return out
    legacy = legacy.copy()
    legacy["dong_code"] = legacy["dong_code"].astype(str)
    legacy["year"] = legacy["year"].astype(int)
    diffs = []
    for emd_cd in REQUIRED_OVERLAPS:
        psub = panel[panel["emd_cd"] == emd_cd].sort_values("year")
        legacy_code = emd_cd if emd_cd in set(legacy["dong_code"]) else LEGACY_OVERLAP_CODES.get(emd_cd)
        lsub = legacy[legacy["dong_code"] == legacy_code].sort_values("year")
        if psub.empty or lsub.empty:
            diffs.append({
                "emd_cd": emd_cd,
                "legacy_dong_code": legacy_code,
                "status": "missing_in_one_panel",
            })
            continue
        merged = psub[["year", *EMBED_COLS]].merge(
            lsub[["year", *EMBED_COLS]], on="year", suffixes=("_pilot", "_legacy"))
        if merged.empty:
            diffs.append({"emd_cd": emd_cd, "status": "no_year_overlap"})
            continue
        delta = merged[[f"{c}_pilot" for c in EMBED_COLS]].to_numpy("float32") - merged[
            [f"{c}_legacy" for c in EMBED_COLS]].to_numpy("float32")
        norms = np.linalg.norm(delta, axis=1)
        diffs.append({
            "emd_cd": emd_cd,
            "legacy_dong_code": legacy_code,
            "name_roman": REQUIRED_OVERLAPS[emd_cd],
            "status": "compared",
            "n_years": int(len(merged)),
            "max_abs_delta": float(np.max(np.abs(delta))),
            "median_l2_delta": float(np.median(norms)),
            "max_l2_delta": float(np.max(norms)),
        })
    out["status"] = "compared_to_legacy_proxy_boxes"
    out["interpretation"] = (
        "Diagnostic only: the legacy EE panel used 1km proxy boxes and old "
        "pre-repair dong codes, while the pilot uses official legal-dong polygons. "
        "Non-zero deltas are expected and should not block the polygon pilot.")
    out["diffs"] = diffs
    return out


def print_report(report: dict) -> None:
    c = report["completeness"]
    print("Completeness:")
    print(f"  rows: {c['actual_rows']}/{c['expected_rows']}  "
          f"dongs={c['n_dongs']}  duplicate_pairs={c['duplicate_count']}  "
          f"missing_embed_cells={c['missing_embed_cells']}")
    for row in c["by_gu"]:
        print(f"  {row['gu_name']} ({row['lawd_cd']}): {row['n_dongs']} dongs")

    v = report["within_gu_variance"]
    print("\nWithin-gu variance:")
    print(f"  min std-vector norm: {v['min_std_vector_norm']:.6f}")
    print(f"  median std-vector norm: {v['median_std_vector_norm']:.6f}")
    print(f"  pass: {v['pass']}")

    sc = report["source_completeness"]
    print("\nSource SHP completeness (§8 #1):")
    if not sc.get("checked"):
        print(f"  not checked: {sc.get('reason', 'unknown')}")
    else:
        print(f"  source dongs (마포구+강남구): {sc['n_source_dongs']}  "
              f"panel dongs: {sc['n_panel_dongs']}  "
              f"missing: {sc['n_missing_from_panel']}  "
              f"extra: {sc['n_extra_in_panel']}")
        print(f"  pass: {sc['pass']}")

    lgc = report["lawd_gu_consistency"]
    print("\nlawd_cd ↔ gu_name consistency (§8 #4a):")
    print(f"  rows: {lgc['n_rows']}  mismatches: {lgc['n_mismatch']}  "
          f"unknown lawd_cd: {lgc['n_unknown_lawd']}")
    print(f"  pass: {lgc['pass']}")
    for ex in lgc["mismatch_examples"][:3]:
        print(f"    mismatch: {ex}")
    for ex in lgc["unknown_lawd_examples"][:3]:
        print(f"    unknown lawd_cd: {ex}")

    a = report["artifact_2022"]["overall"]
    print("\n2021-2022 artifact diagnostic:")
    print(f"  share max YoY pair is 2021-2022: {a['share_max_is_2021_2022']:.3f}")
    print(f"  angular median 2021-2022 / other: "
          f"{a['angular_2021_2022_median']:.6f} / {a['angular_other_median']:.6f} "
          f"(ratio={a['angular_ratio']:.3f})")
    for row in report["artifact_2022"]["by_gu"]:
        print(f"  {row['gu_name']}: share={row['share_max_is_2021_2022']:.3f}, "
              f"ratio={row['angular_ratio']:.3f}")

    o = report["overlap"]
    print("\nOverlap comparison:")
    print(f"  required present in pilot: {len(o['present_in_pilot'])}/4")
    print(f"  legacy comparison status: {o['status']}")
    for row in o.get("diffs", []):
        if row.get("status") == "compared":
            print(f"  {row['name_roman']}: legacy_code={row['legacy_dong_code']} "
                  f"max_abs_delta={row['max_abs_delta']:.6f} "
                  f"median_l2_delta={row['median_l2_delta']:.6f}")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="QA the Seoul AlphaEarth pilot panel.")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--legacy-panel", default=str(DEFAULT_LEGACY))
    ap.add_argument("--source-shp", default=None,
                    help="optional path to the source D001 EMD ZIP/SHP/dir "
                         "for the §8 #1 source-completeness check; skipped if unset")
    ap.add_argument("--output", default=str(DEFAULT_REPORT))
    args = ap.parse_args(argv)

    panel = load_panel(Path(args.panel))
    yoy = yoy_distances(panel)
    source_shp = Path(args.source_shp) if args.source_shp else None
    report = {
        "panel": str(Path(args.panel)),
        "completeness": completeness(panel, Path(args.manifest), YEARS),
        "source_completeness": source_completeness(panel, source_shp),
        "lawd_gu_consistency": lawd_gu_consistency(panel),
        "within_gu_variance": variance_summary(panel),
        "artifact_2022": artifact_summary(yoy),
        "overlap": overlap_summary(panel, Path(args.legacy_panel)),
    }
    print_report(report)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nQA report written: {output}")

    sc = report["source_completeness"]
    source_fail = sc.get("checked", False) and not sc.get("pass", False)
    hard_fail = (
        not report["completeness"]["pass"]
        or source_fail
        or not report["lawd_gu_consistency"]["pass"]
        or not report["within_gu_variance"]["pass"]
        or len(report["overlap"]["present_in_pilot"]) != 4
    )
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
