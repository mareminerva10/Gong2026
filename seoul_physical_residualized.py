"""
seoul_physical_residualized.py
==============================

Build the artifact-adjusted physical-change feature layer for the Seoul
AlphaEarth legal-dong pilot.

Subtracts the cumulative Tokyo+Taipei anchor drift (the
`residualized_tokyo_taipei` artifact policy) from each Seoul embedding so
that the 2021→2022 regional common-mode shift documented in the 2022
artifact audit is removed before any downstream feature is built.
Per-dong / per-year metrics are then computed twice — raw and
residualized — and a diagnostic report compares the two so the operator
can decide whether the residualization is actually doing its job before
any downstream alarm/forecast module touches the data.

This module deliberately does NOT compute an EWS index, a forecast, a
composite score, or any other downstream alarm scalar. It only produces
the artifact-adjusted feature layer that those modules would consume.

Output
------
    data/seoul_pilot_physical_residualized.parquet

Columns
-------
    emd_cd, dong_name_kr, lawd_cd, gu_name, year
    raw_embedding_norm, residualized_embedding_norm
    raw_yoy_year_pair, residualized_yoy_year_pair
    raw_yoy_angular, residualized_yoy_angular
    raw_yoy_cosine_dist, residualized_yoy_cosine_dist
    raw_yoy_euclid, residualized_yoy_euclid
    artifact_transition_flag       (bool; True iff year == 2022)
    physical_artifact_policy       ("residualized_tokyo_taipei")
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from axis_residualize import (
    DEFAULT_ANCHOR_CITIES,
    EMBED_COLS,
    compute_anchor_offsets,
    load_anchor_embeddings,
    residualize,
)


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_PANEL = DATA / "seoul_pilot_alphaearth.parquet"
DEFAULT_OUTPUT = DATA / "seoul_pilot_physical_residualized.parquet"
DEFAULT_REPORT = DATA / "seoul_pilot_physical_residualized_report.json"

BASELINE_YEAR = 2017
SUSPECT_PAIR = "2021-2022"
ARTIFACT_POLICY = "residualized_tokyo_taipei"
LABELED_OVERLAP = ("연남동", "망원동", "압구정동", "대치동")


# --- Loaders -------------------------------------------------------------

def load_pilot(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"missing Seoul AlphaEarth pilot panel: {path}. "
            "Run seoul_pilot_extract.py first.")
    df = pd.read_parquet(path)
    required = {"emd_cd", "dong_name_kr", "lawd_cd", "gu_name", "year",
                *EMBED_COLS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"pilot panel missing required columns: {sorted(missing)}")
    df = df.copy()
    df["emd_cd"] = df["emd_cd"].astype(str)
    df["lawd_cd"] = df["lawd_cd"].astype(str)
    df["year"] = df["year"].astype(int)
    return df.sort_values(["emd_cd", "year"]).reset_index(drop=True)


# --- Metrics -------------------------------------------------------------

def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def compute_metrics(panel: pd.DataFrame, source_label: str) -> pd.DataFrame:
    """Per-(emd_cd, year) row with embedding norm and YoY angular /
    cosine / euclid. `source_label` is prepended to every metric name so
    the raw and residualized rows can be joined side-by-side without
    column collision.
    """
    rows: list[dict] = []
    for emd_cd, sub in panel.groupby("emd_cd", sort=False):
        sub = sub.sort_values("year").reset_index(drop=True)
        vecs = sub[EMBED_COLS].to_numpy("float64")
        years = sub["year"].to_numpy("int64")
        for i in range(len(sub)):
            row: dict = {
                "emd_cd": str(emd_cd),
                "year": int(years[i]),
                f"{source_label}_embedding_norm": float(np.linalg.norm(vecs[i])),
            }
            if i == 0:
                row[f"{source_label}_yoy_year_pair"] = None
                row[f"{source_label}_yoy_angular"] = np.nan
                row[f"{source_label}_yoy_cosine_dist"] = np.nan
                row[f"{source_label}_yoy_euclid"] = np.nan
            else:
                a, b = vecs[i - 1], vecs[i]
                au, bu = _unit(a), _unit(b)
                cos = float(np.clip(np.dot(au, bu), -1.0, 1.0))
                row[f"{source_label}_yoy_year_pair"] = (
                    f"{years[i - 1]}-{years[i]}")
                row[f"{source_label}_yoy_angular"] = float(np.arccos(cos))
                row[f"{source_label}_yoy_cosine_dist"] = float(1.0 - cos)
                row[f"{source_label}_yoy_euclid"] = float(
                    np.linalg.norm(b - a))
            rows.append(row)
    return pd.DataFrame(rows)


def build_feature_layer(raw_panel: pd.DataFrame,
                        residualized_panel: pd.DataFrame
                        ) -> pd.DataFrame:
    raw_metrics = compute_metrics(raw_panel, "raw")
    res_metrics = compute_metrics(residualized_panel, "residualized")
    merged = raw_metrics.merge(res_metrics, on=["emd_cd", "year"], how="inner")
    ident = raw_panel[["emd_cd", "dong_name_kr", "lawd_cd", "gu_name", "year"]].copy()
    ident["emd_cd"] = ident["emd_cd"].astype(str)
    ident["year"] = ident["year"].astype(int)
    out = ident.merge(merged, on=["emd_cd", "year"], how="inner")
    out["artifact_transition_flag"] = out["year"].eq(2022)
    out["physical_artifact_policy"] = ARTIFACT_POLICY
    out = out.sort_values(["lawd_cd", "emd_cd", "year"]).reset_index(drop=True)
    return out


# --- Diagnostics ---------------------------------------------------------

def _share_max_is_suspect(metric_df: pd.DataFrame,
                          pair_col: str, angular_col: str) -> pd.DataFrame:
    """Per-(lawd_cd, gu_name) share of dongs whose maximum YoY angular
    happens at the 2021-2022 transition."""
    valid = metric_df[metric_df[angular_col].notna()].copy()
    idx = valid.groupby("emd_cd")[angular_col].idxmax()
    max_pair = valid.loc[idx, ["emd_cd", "lawd_cd", "gu_name", pair_col]]
    summary = (max_pair.groupby(["lawd_cd", "gu_name"])[pair_col]
                       .apply(lambda s: float((s == SUSPECT_PAIR).mean()))
                       .reset_index(name="share_max_is_suspect"))
    counts = (max_pair.groupby(["lawd_cd", "gu_name"])["emd_cd"]
                       .nunique()
                       .reset_index(name="n_dongs"))
    return summary.merge(counts, on=["lawd_cd", "gu_name"])


def _suspect_ratio(merged: pd.DataFrame, source: str) -> dict:
    pair_col = f"{source}_yoy_year_pair"
    ang_col = f"{source}_yoy_angular"
    suspect_mask = merged[pair_col] == SUSPECT_PAIR
    suspect = merged.loc[suspect_mask, ang_col].dropna()
    other = merged.loc[~suspect_mask & merged[ang_col].notna(), ang_col]
    s_med = float(suspect.median()) if len(suspect) else float("nan")
    o_med = float(other.median()) if len(other) else float("nan")
    ratio = s_med / o_med if o_med > 1e-12 else float("nan")
    return {"source": source,
            "suspect_median": s_med,
            "other_median": o_med,
            "ratio": ratio,
            "n_suspect": int(len(suspect)),
            "n_other": int(len(other))}


def _top_n_at_suspect(merged: pd.DataFrame, source: str, n: int = 5) -> list[dict]:
    pair_col = f"{source}_yoy_year_pair"
    ang_col = f"{source}_yoy_angular"
    sub = merged[merged[pair_col] == SUSPECT_PAIR].copy()
    top = sub.nlargest(n, ang_col)
    return [
        {"dong_name_kr": str(r.dong_name_kr),
         "gu_name": str(r.gu_name),
         "angular": float(getattr(r, ang_col))}
        for r in top.itertuples(index=False)
    ]


def diagnostic_report(merged: pd.DataFrame) -> dict:
    """Four-question diagnostic answering whether the residualization
    actually removed the 2022 regional common-mode."""
    raw_shares = _share_max_is_suspect(
        merged, "raw_yoy_year_pair", "raw_yoy_angular")
    res_shares = _share_max_is_suspect(
        merged, "residualized_yoy_year_pair", "residualized_yoy_angular")
    raw_ratio = _suspect_ratio(merged, "raw")
    res_ratio = _suspect_ratio(merged, "residualized")
    raw_top = _top_n_at_suspect(merged, "raw")
    res_top = _top_n_at_suspect(merged, "residualized")

    label_rows: dict[str, list[dict]] = {}
    for name in LABELED_OVERLAP:
        sub = merged[merged["dong_name_kr"] == name].sort_values("year")
        if sub.empty:
            label_rows[name] = []
            continue
        trail: list[dict] = []
        for r in sub.itertuples(index=False):
            pair = getattr(r, "raw_yoy_year_pair")
            if pair is None:
                continue
            trail.append({
                "year_pair": str(pair),
                "raw_angular": float(getattr(r, "raw_yoy_angular")),
                "residualized_angular": float(getattr(r, "residualized_yoy_angular")),
            })
        label_rows[name] = trail

    return {
        "share_max_is_2021_2022": {
            "raw": raw_shares.to_dict("records"),
            "residualized": res_shares.to_dict("records"),
        },
        "suspect_pair_ratio": {"raw": raw_ratio, "residualized": res_ratio},
        "top5_at_2021_2022": {"raw": raw_top, "residualized": res_top},
        "labeled_overlap_trajectories": label_rows,
    }


def print_report(report: dict) -> None:
    print("Residualization diagnostic (raw vs residualized_tokyo_taipei)")
    print("=" * 70)

    print("\n1. Share of dongs whose max-YoY-angular pair == 2021-2022")
    print("   (a residualization that worked drops this share substantially)")
    raw = {(r["lawd_cd"], r["gu_name"]): r for r in
           report["share_max_is_2021_2022"]["raw"]}
    res = {(r["lawd_cd"], r["gu_name"]): r for r in
           report["share_max_is_2021_2022"]["residualized"]}
    for key in sorted(raw):
        r0 = raw[key]
        r1 = res.get(key, {"share_max_is_suspect": float("nan"), "n_dongs": 0})
        print(f"   {r0['gu_name']:<8} ({r0['lawd_cd']}, {r0['n_dongs']} dongs): "
              f"raw {r0['share_max_is_suspect']:.3f}  →  "
              f"residualized {r1['share_max_is_suspect']:.3f}")

    print("\n2. Suspect-pair angular ratio (2021-2022 median / other median)")
    print("   (ratio ≈ 1.0 means residualization neutralised the regional spike)")
    for s in ("raw", "residualized"):
        d = report["suspect_pair_ratio"][s]
        print(f"   {s:<14}  suspect_med={d['suspect_median']:.4f}  "
              f"other_med={d['other_median']:.4f}  ratio={d['ratio']:.3f}")

    print("\n3. Top 5 dongs at the 2021-2022 transition")
    for s in ("raw", "residualized"):
        print(f"   {s}:")
        for entry in report["top5_at_2021_2022"][s]:
            print(f"     {entry['dong_name_kr']:<8} ({entry['gu_name']})  "
                  f"angular={entry['angular']:.4f}")

    print("\n4. Labeled-case YoY trajectories (raw vs residualized)")
    print("   (active_panel cases — Yeonnam/Mangwon — should ideally still "
          "show distinct movement vs controls Apgujeong/Daechi after\n"
          "   residualization; if they all collapse together, residualization "
          "removed the signal of interest along with the artifact)")
    for name, trail in report["labeled_overlap_trajectories"].items():
        if not trail:
            print(f"   {name}: not in pilot panel")
            continue
        print(f"   {name}:")
        for t in trail:
            print(f"     {t['year_pair']}  raw={t['raw_angular']:.4f}  "
                  f"resid={t['residualized_angular']:.4f}")


# --- Main ----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Build artifact-adjusted physical-change feature layer "
                    "for the Seoul AlphaEarth pilot (P5). No EWS, no "
                    "forecast, no composite score.")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL),
                    help=f"input AlphaEarth pilot panel (default {DEFAULT_PANEL})")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT),
                    help=f"output feature parquet (default {DEFAULT_OUTPUT})")
    ap.add_argument("--report", default=str(DEFAULT_REPORT),
                    help=f"output diagnostic JSON (default {DEFAULT_REPORT})")
    ap.add_argument("--anchor-cities", nargs="+",
                    default=list(DEFAULT_ANCHOR_CITIES),
                    help="anchor city set (default: Tokyo Taipei)")
    args = ap.parse_args(argv)

    raw_panel = load_pilot(Path(args.panel))
    print(f"Pilot panel: {len(raw_panel)} rows  "
          f"dongs={raw_panel['emd_cd'].nunique()}  "
          f"years={sorted(raw_panel['year'].unique().tolist())}")

    anchor_df = load_anchor_embeddings(cities=tuple(args.anchor_cities))
    print(f"Anchor cache: {anchor_df['poly_id'].nunique()} polygons  "
          f"cities={sorted(anchor_df['city'].unique())}")
    offsets = compute_anchor_offsets(anchor_df, baseline_year=BASELINE_YEAR)
    offset_norms = np.linalg.norm(offsets.to_numpy(), axis=1)
    print("Anchor offset L2-norms (relative to "
          f"{BASELINE_YEAR}):")
    for y, n in zip(offsets.index, offset_norms):
        print(f"  {y}  ||offset|| = {float(n):.4f}")

    residualized_panel = residualize(raw_panel, offsets)
    print(f"Residualized panel: {len(residualized_panel)} rows  (same shape)")

    feature_layer = build_feature_layer(raw_panel, residualized_panel)
    report = diagnostic_report(feature_layer)
    print_report(report)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    feature_layer.to_parquet(out, index=False)
    print(f"\nFeature layer written: {out}  "
          f"rows={len(feature_layer)}  cols={len(feature_layer.columns)}")

    rep_out = Path(args.report)
    rep_out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"Diagnostic report written: {rep_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
