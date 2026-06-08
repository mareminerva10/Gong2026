"""
dashboard_pilot_contract.py
===========================

Build a dashboard-ready pilot contract from the completed Seoul AlphaEarth
legal-dong pilot. This is a descriptive handoff table, not a forecast table.

The contract intentionally keeps evidence blocks separate and explicit:

- Block 2 physical change is live for the Mapo-gu + Gangnam-gu pilot.
- Block 1 tenure pressure is parked.
- Block 3 vulnerability is not scoped.
- Block 4 controls are included only if their local parquet artifacts exist;
  otherwise the output records missing-local-artifact status fields.

No Earth Engine calls are made here. No composite score, prediction, calibrated
probability, or gentrification label is computed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

DEFAULT_ALPHAEARTH = DATA / "seoul_pilot_alphaearth.parquet"
DEFAULT_QA = DATA / "seoul_pilot_alphaearth_qa.json"
DEFAULT_UNSOLD = DATA / "statnuri_unsold_panel.parquet"
DEFAULT_COMPLETED_UNSOLD = DATA / "statnuri_completed_unsold_panel.parquet"
DEFAULT_REDEV = DATA / "national_redevelopment_intensity.parquet"
DEFAULT_LANDUSE = DATA / "statnuri_landuse_panel.parquet"
DEFAULT_RESIDUALIZED = DATA / "seoul_pilot_physical_residualized.parquet"
DEFAULT_OUTPUT = DATA / "dashboard_pilot_contract.parquet"

YEARS = list(range(2017, 2025))
EMBED_COLS = [f"A{i:02d}" for i in range(64)]
SUSPECT_TRANSITION_TO_YEAR = 2022
PROHIBITED_SUBSTRINGS = (
    "forecast",
    "prediction",
    "probability",
    "risk_score",
    "composite_score",
    "gentrification_score",
)


def _unit(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-12 else v


def load_alphaearth(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing AlphaEarth pilot panel: {path}")
    df = pd.read_parquet(path)
    required = {
        "emd_cd",
        "dong_name_kr",
        "lawd_cd",
        "gu_name",
        "year",
        "centroid_lat",
        "centroid_lon",
        *EMBED_COLS,
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"AlphaEarth panel missing required columns: {sorted(missing)}")
    df = df.copy()
    df["emd_cd"] = df["emd_cd"].astype(str)
    df["lawd_cd"] = df["lawd_cd"].astype(str)
    df["year"] = df["year"].astype(int)
    df = df.sort_values(["emd_cd", "year"]).reset_index(drop=True)
    return df


def physical_metrics(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel.copy()
    mat = out[EMBED_COLS].to_numpy("float64")
    out["physical_embedding_norm"] = np.linalg.norm(mat, axis=1)

    yoy_rows: list[dict] = []
    for emd_cd, sub in out.groupby("emd_cd", sort=False):
        sub = sub.sort_values("year")
        vecs = sub[EMBED_COLS].to_numpy("float64")
        years = sub["year"].to_numpy("int64")
        for i in range(1, len(sub)):
            a = vecs[i - 1]
            b = vecs[i]
            a_u = _unit(a)
            b_u = _unit(b)
            cos = float(np.clip(np.dot(a_u, b_u), -1.0, 1.0))
            yoy_rows.append({
                "emd_cd": str(emd_cd),
                "year": int(years[i]),
                "physical_yoy_year_pair": f"{years[i - 1]}-{years[i]}",
                "physical_yoy_angular": float(np.arccos(cos)),
                "physical_yoy_cosine_dist": float(1.0 - cos),
                "physical_yoy_euclid": float(np.linalg.norm(b - a)),
            })

    yoy = pd.DataFrame(yoy_rows)
    out = out.merge(yoy, on=["emd_cd", "year"], how="left")
    out["physical_2022_artifact_flag"] = out["year"].eq(SUSPECT_TRANSITION_TO_YEAR)
    out.loc[out["physical_yoy_angular"].isna(), "physical_2022_artifact_flag"] = False

    grouped = out.groupby(["lawd_cd", "year"], dropna=False)["physical_yoy_angular"]
    mean = grouped.transform("mean")
    std = grouped.transform("std")
    out["physical_yoy_angular_gu_z"] = (out["physical_yoy_angular"] - mean) / std
    out.loc[std.fillna(0).eq(0), "physical_yoy_angular_gu_z"] = np.nan
    out["physical_yoy_angular_gu_rank_desc"] = grouped.rank(method="min", ascending=False)
    count = grouped.transform("count")
    out["physical_yoy_angular_gu_percentile_desc"] = (
        1.0 - ((out["physical_yoy_angular_gu_rank_desc"] - 1.0) / (count - 1.0))
    )
    out.loc[count.le(1) | out["physical_yoy_angular"].isna(),
            "physical_yoy_angular_gu_percentile_desc"] = np.nan
    return out


def add_status_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["physical_source"] = "alphaearth_ee"
    out["physical_grain"] = "legal-dong-year"
    out["physical_status"] = "live"
    # Analytical policy per docs/dashboard_mvp_spec.md §7. The strict
    # drop_2022 rule and the UI flag_2022 rule are consumer/renderer
    # conventions layered on top; they are not stored values of this
    # field. The `physical_2022_artifact_flag` boolean still carries
    # the UI flag_2022 signal.
    out["physical_artifact_policy"] = "metric_year_fe"

    out["tenure_source"] = "parked"
    out["tenure_grain"] = "parked"
    out["tenure_status"] = "parked"

    out["vulnerability_source"] = "not_scoped"
    out["vulnerability_grain"] = "not_scoped"
    out["vulnerability_status"] = "not_scoped"

    out["housing_stress_source"] = "statnuri_2082_128"
    out["housing_stress_grain"] = "gu-year"
    out["housing_stress_status"] = "missing_local_artifact"

    out["development_pressure_source"] = "statnuri_6189_1"
    out["development_pressure_grain"] = "national-year"
    out["development_pressure_status"] = "missing_local_artifact"
    out["development_pressure_spatial_variation"] = "none"

    # Block 4c (gu-year land-use context). Defaults to missing-artifact.
    # When the StatNuri 2300/2 panel is present, merge_optional_landuse
    # flips landuse_status to "live" and development_pressure_spatial_variation
    # from "none" to "gu". This is gu-level broadcast, NOT a dong-grain
    # designation overlay; see docs/dashboard_mvp_spec.md §4 / §5.
    out["landuse_source"] = "statnuri_2300_2"
    out["landuse_grain"] = "gu-year"
    out["landuse_status"] = "missing_local_artifact"

    # Block 4b second sub-row: post-completion unsold (StatNuri 5328/1).
    # Pre-completion unsold (2082/128) is captured under housing_stress_*;
    # post-completion is structurally a different signal (canonical
    # 'overhang' indicator) and tracked under its own status field. See
    # docs/molit_probe_2026-06-07.md §7 Target A.
    out["completed_unsold_source"] = "statnuri_5328_1"
    out["completed_unsold_grain"] = "gu-year"
    out["completed_unsold_status"] = "missing_local_artifact"

    out["dashboard_claim_scope"] = "descriptive_physical_change_only"
    out["composite_score_status"] = "not_computed"
    return out


def merge_optional_unsold(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    if not path.exists():
        return df
    unsold = pd.read_parquet(path).copy()
    required = {"lawd_cd", "year"}
    missing = required - set(unsold.columns)
    if missing:
        raise ValueError(f"unsold panel missing required columns: {sorted(missing)}")
    unsold["lawd_cd"] = unsold["lawd_cd"].astype(str)
    unsold["year"] = unsold["year"].astype(int)
    value_cols = [c for c in unsold.columns
                  if c.startswith("statnuri_unsold_")]
    out = df.merge(
        unsold[["lawd_cd", "year", *value_cols]],
        on=["lawd_cd", "year"],
        how="left",
    )
    out["housing_stress_status"] = np.where(
        out[value_cols].notna().any(axis=1), "live", "missing_join_row")
    return out


def merge_optional_residualized(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Merge the three-policy physical-change feature layer from
    `seoul_physical_residualized.py` if its parquet is present. Adds
    side-by-side `physical_{metric}_{policy}` columns for the three
    artifact-handling policies (raw / tokyo_taipei_offset / metric_year_fe)
    and the `metric_year_fe_scope` provenance. Does not overwrite
    existing contract columns (dong_name_kr, lawd_cd, the legacy
    non-policy-suffixed metrics, artifact_transition_flag, etc.)."""
    if not path.exists():
        return df
    res = pd.read_parquet(path).copy()
    res["emd_cd"] = res["emd_cd"].astype(str)
    res["year"] = res["year"].astype(int)
    policy_cols = [c for c in res.columns
                   if c.startswith("physical_yoy_") and
                   any(c.endswith(f"_{p}")
                       for p in ("raw", "tokyo_taipei_offset", "metric_year_fe"))]
    policy_cols += [c for c in res.columns
                    if c.startswith("physical_embedding_norm_")]
    keep = ["emd_cd", "year", *policy_cols, "metric_year_fe_scope"]
    keep = [c for c in keep if c in res.columns]
    return df.merge(res[keep], on=["emd_cd", "year"], how="left")


def merge_optional_completed_unsold(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Merge the StatNuri 5328/1 post-completion unsold panel (Block 4b
    sub-row) if its parquet is present.

    Joined by `lawd_cd × year`. The panel is at gu × year grain; on the
    dong-year contract this is gu-level broadcast, like the pre-completion
    unsold (`statnuri_unsold_*`) and the land-use shares. Pre-completion
    unsold and post-completion unsold are intentionally kept under
    separate status fields (`housing_stress_status` vs
    `completed_unsold_status`) so a future consumer cannot conflate the
    'inventory waiting to sell' and 'inventory built but unsold' signals,
    which carry different downstream meaning.

    Differs from form 2082/128 in zero handling: 5328/1 rows are present
    with explicit `호=0`, so missing values after the merge are true
    join-failure NAs, not legitimate zeros."""
    if not path.exists():
        return df
    cu = pd.read_parquet(path).copy()
    required = {"lawd_cd", "year"}
    missing = required - set(cu.columns)
    if missing:
        raise ValueError(
            "completed-unsold panel missing required columns: "
            f"{sorted(missing)}")
    cu["lawd_cd"] = cu["lawd_cd"].astype(str)
    cu["year"] = cu["year"].astype(int)
    value_cols = [c for c in cu.columns
                  if c.startswith("statnuri_completed_unsold_")]
    if not value_cols:
        raise ValueError(
            "completed-unsold panel has no statnuri_completed_unsold_* "
            f"value columns. Available: {sorted(cu.columns)[:8]}")
    out = df.merge(
        cu[["lawd_cd", "year", *value_cols]],
        on=["lawd_cd", "year"],
        how="left",
    )
    out["completed_unsold_status"] = np.where(
        out[value_cols].notna().any(axis=1), "live", "missing_join_row")
    return out


def merge_optional_landuse(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Merge the StatNuri 2300/2 gu-year land-use panel (Block 4c) if its
    parquet is present. The panel is at gu × year grain — 25 Seoul gus × 8
    years — so the merge broadcasts each gu's land-use shares to every
    dong in that gu via lawd_cd × year. This is **gu-level broadcast /
    context**, not within-gu spatial variation; the dashboard MUST label
    these signals accordingly (per docs/dashboard_mvp_spec.md §5).

    Exposed columns are the four descriptive shares plus the gu's total
    area + parcel count (audit totals only). The 56 per-category raw
    retention columns stay in the panel parquet for audit but are NOT
    merged into the contract; they would dilute the dashboard surface
    and are not policy-selectable.

    When the merge succeeds, also flips
    `development_pressure_spatial_variation` from 'none' to 'gu' — Block
    4c (the spatial development companion in the four-block model) has
    landed at gu-year grain. This is NOT a dong-grain claim."""
    if not path.exists():
        return df
    lu = pd.read_parquet(path).copy()
    required = {"lawd_cd", "year"}
    missing = required - set(lu.columns)
    if missing:
        raise ValueError(
            f"landuse panel missing required columns: {sorted(missing)}")
    lu["lawd_cd"] = lu["lawd_cd"].astype(str)
    lu["year"] = lu["year"].astype(int)
    surface_cols = [
        "landuse_built_share",
        "landuse_vegetation_share",
        "landuse_infrastructure_share",
        "landuse_transport_share",
        "area_total_m2",
        "parcels_total",
    ]
    keep = [c for c in surface_cols if c in lu.columns]
    if not keep:
        raise ValueError(
            "landuse panel has no recognised surface columns "
            f"(landuse_*_share / area_total_m2 / parcels_total). "
            f"Available: {sorted(lu.columns)[:8]}...")
    out = df.merge(
        lu[["lawd_cd", "year", *keep]],
        on=["lawd_cd", "year"],
        how="left",
    )
    out["landuse_status"] = np.where(
        out[keep].notna().any(axis=1), "live", "missing_join_row")
    out["development_pressure_spatial_variation"] = np.where(
        out["landuse_status"] == "live", "gu",
        out["development_pressure_spatial_variation"])
    return out


def merge_optional_redev(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    if not path.exists():
        return df
    redev = pd.read_parquet(path).copy()
    if "year" not in redev.columns:
        raise ValueError("redevelopment intensity panel missing required column: year")
    redev["year"] = redev["year"].astype(int)
    rename = {
        c: f"national_redevelopment_intensity_{c.removeprefix('redev_')}"
        for c in redev.columns
        if c.startswith("redev_")
    }
    redev = redev.rename(columns=rename)
    value_cols = [
        c for c in redev.columns
        if c.startswith("national_redevelopment_intensity_")
    ]
    if not value_cols:
        raise ValueError(
            "redevelopment intensity panel has no redev_* or "
            "national_redevelopment_intensity_* value columns")
    out = df.merge(redev[["year", *value_cols]], on="year", how="left")
    out["development_pressure_status"] = np.where(
        out[value_cols].notna().any(axis=1), "live", "missing_join_row")
    return out


def select_columns(df: pd.DataFrame) -> pd.DataFrame:
    id_cols = [
        "emd_cd",
        "dong_name_kr",
        "lawd_cd",
        "gu_name",
        "year",
        "centroid_lat",
        "centroid_lon",
        "polygon_effective_date",
    ]
    physical_cols = [
        "physical_embedding_norm",
        "physical_yoy_year_pair",
        "physical_yoy_angular",
        "physical_yoy_cosine_dist",
        "physical_yoy_euclid",
        "physical_yoy_angular_gu_z",
        "physical_yoy_angular_gu_rank_desc",
        "physical_yoy_angular_gu_percentile_desc",
        "physical_2022_artifact_flag",
        "physical_source",
        "physical_grain",
        "physical_status",
        "physical_artifact_policy",
    ]
    block_status_cols = [
        "tenure_source",
        "tenure_grain",
        "tenure_status",
        "vulnerability_source",
        "vulnerability_grain",
        "vulnerability_status",
        "housing_stress_source",
        "housing_stress_grain",
        "housing_stress_status",
        "development_pressure_source",
        "development_pressure_grain",
        "development_pressure_status",
        "development_pressure_spatial_variation",
        "landuse_source",
        "landuse_grain",
        "landuse_status",
        "completed_unsold_source",
        "completed_unsold_grain",
        "completed_unsold_status",
        "dashboard_claim_scope",
        "composite_score_status",
    ]
    landuse_surface = (
        "landuse_built_share",
        "landuse_vegetation_share",
        "landuse_infrastructure_share",
        "landuse_transport_share",
        "area_total_m2",
        "parcels_total",
    )
    optional_cols = [
        c for c in df.columns
        if c.startswith("statnuri_unsold_")
        or c.startswith("statnuri_completed_unsold_")
        or c.startswith("national_redevelopment_intensity_")
        or c in landuse_surface
    ]
    # Policy-suffixed physical-change columns from the residualized layer.
    policy_cols = [c for c in df.columns
                   if (c.startswith("physical_yoy_")
                       or c.startswith("physical_embedding_norm_"))
                   and any(c.endswith(f"_{p}")
                           for p in ("raw", "tokyo_taipei_offset",
                                     "metric_year_fe"))]
    if "metric_year_fe_scope" in df.columns:
        policy_cols.append("metric_year_fe_scope")
    emb_cols = [c for c in EMBED_COLS if c in df.columns]
    keep = [c for c in [*id_cols, *physical_cols, *policy_cols, *optional_cols,
                        *block_status_cols, *emb_cols] if c in df.columns]
    return df[keep].sort_values(["lawd_cd", "emd_cd", "year"]).reset_index(drop=True)


def validate_contract(df: pd.DataFrame, years: list[int]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    n_dongs = int(df["emd_cd"].nunique())
    expected_rows = n_dongs * len(years)
    duplicate_pairs = int(df.duplicated(["emd_cd", "year"]).sum())
    missing_pairs = {
        (emd, year)
        for emd in df["emd_cd"].unique()
        for year in years
    } - {
        (row.emd_cd, int(row.year))
        for row in df[["emd_cd", "year"]].itertuples(index=False)
    }

    if len(df) != expected_rows:
        errors.append(f"row count {len(df)} != expected {expected_rows}")
    if duplicate_pairs:
        errors.append(f"duplicate emd_cd/year rows: {duplicate_pairs}")
    if missing_pairs:
        errors.append(f"missing emd_cd/year pairs: {len(missing_pairs)}")
    if df[EMBED_COLS].isna().sum().sum() != 0:
        errors.append("embedding cells contain nulls")

    yoy_null = df["physical_yoy_angular"].isna()
    if set(df.loc[yoy_null, "year"].unique()) != {min(years)}:
        errors.append("YoY nulls are not limited to the first panel year")

    flag_count = int(df["physical_2022_artifact_flag"].sum())
    if flag_count != n_dongs:
        errors.append(
            f"2022 artifact flags {flag_count} != n_dongs {n_dongs}")

    prohibited_cols = [
        c for c in df.columns
        if any(token in c.lower() for token in PROHIBITED_SUBSTRINGS)
        and c != "composite_score_status"
    ]
    if prohibited_cols:
        errors.append(f"prohibited forecast/model columns present: {prohibited_cols}")

    expected_status = {
        "physical_status": "live",
        "tenure_status": "parked",
        "vulnerability_status": "not_scoped",
        "housing_stress_status": "missing_local_artifact",
        "development_pressure_status": "missing_local_artifact",
        "landuse_status": "missing_local_artifact",
        "completed_unsold_status": "missing_local_artifact",
        "composite_score_status": "not_computed",
    }
    for col, expected in expected_status.items():
        actual = sorted(df[col].astype(str).unique().tolist())
        if actual != [expected]:
            if expected == "missing_local_artifact" and actual == ["live"]:
                warnings.append(f"{col} includes live artifact rows: {actual}")
            else:
                errors.append(f"{col} expected {expected}, got {actual}")

    return {
        "rows": int(len(df)),
        "n_dongs": n_dongs,
        "years": sorted(df["year"].unique().astype(int).tolist()),
        "duplicate_pairs": duplicate_pairs,
        "missing_pairs_count": len(missing_pairs),
        "artifact_2022_flag_count": flag_count,
        "errors": errors,
        "warnings": warnings,
        "pass": not errors,
    }


def load_qa_summary(path: Path) -> dict:
    if not path.exists():
        return {"status": "missing"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive summary only
        return {"status": "unreadable", "error": str(exc)}


def print_summary(contract: pd.DataFrame, validation: dict, qa: dict,
                  output: Path) -> None:
    print("Dashboard pilot contract:")
    print(f"  rows: {validation['rows']}  dongs: {validation['n_dongs']}  "
          f"years: {validation['years']}")
    print(f"  2022 artifact flags: {validation['artifact_2022_flag_count']}")
    by_gu = (contract.groupby(["lawd_cd", "gu_name"])["emd_cd"]
             .nunique()
             .reset_index(name="n_dongs"))
    for row in by_gu.itertuples(index=False):
        print(f"  {row.gu_name} ({row.lawd_cd}): {row.n_dongs} dongs")

    if "artifact_2022" in qa:
        overall = qa["artifact_2022"]["overall"]
        print("  prior QA artifact ratio: "
              f"{overall['angular_ratio']:.3f} "
              f"(share max is 2021-2022: "
              f"{overall['share_max_is_2021_2022']:.3f})")

    for warning in validation["warnings"]:
        print(f"[warning] {warning}")
    if validation["errors"]:
        print("\nFATAL contract validation errors:", file=sys.stderr)
        for error in validation["errors"]:
            print(f"  - {error}", file=sys.stderr)
    else:
        print(f"\nContract written: {output}")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Build a non-forecast dashboard data contract from the "
                    "completed legal-dong AlphaEarth pilot.")
    ap.add_argument("--alphaearth", default=str(DEFAULT_ALPHAEARTH))
    ap.add_argument("--qa", default=str(DEFAULT_QA))
    ap.add_argument("--unsold", default=str(DEFAULT_UNSOLD))
    ap.add_argument("--completed-unsold", default=str(DEFAULT_COMPLETED_UNSOLD))
    ap.add_argument("--redev", default=str(DEFAULT_REDEV))
    ap.add_argument("--landuse", default=str(DEFAULT_LANDUSE))
    ap.add_argument("--residualized", default=str(DEFAULT_RESIDUALIZED))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = ap.parse_args(argv)

    panel = load_alphaearth(Path(args.alphaearth))
    contract = physical_metrics(panel)
    contract = add_status_columns(contract)
    contract = merge_optional_residualized(contract, Path(args.residualized))
    contract = merge_optional_unsold(contract, Path(args.unsold))
    contract = merge_optional_completed_unsold(contract,
                                               Path(args.completed_unsold))
    contract = merge_optional_redev(contract, Path(args.redev))
    contract = merge_optional_landuse(contract, Path(args.landuse))
    contract = select_columns(contract)

    validation = validate_contract(contract, YEARS)
    if validation["pass"]:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        contract.to_parquet(output, index=False)
    else:
        output = Path(args.output)

    qa = load_qa_summary(Path(args.qa))
    print_summary(contract, validation, qa, output)
    return 0 if validation["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
