"""
seoul_physical_residualized.py
==============================

Compare three artifact-handling policies for the Seoul AlphaEarth
legal-dong pilot physical-change metrics:

    raw                    — no adjustment
    tokyo_taipei_offset    — subtract cumulative Tokyo+Taipei anchor drift
                             from the raw embedding before recomputing
                             metrics. Valid for axis-projection metrics,
                             NOT translation-invariant for YoY angular.
    metric_year_fe         — subtract cross-dong median of the metric at
                             the same year-pair (or year, for norm).
                             A panel year fixed effect at the metric
                             level. Interpretation:
                             "anomaly relative to other pilot dongs in
                              the same year-pair", not "artifact-free
                              physical change".

This module deliberately does NOT compute an EWS index, forecast,
composite score, or any other downstream alarm scalar. It builds the
feature layer that future modules would consume and runs a five-question
diagnostic comparing the three policies.

Output
------
    data/seoul_pilot_physical_residualized.parquet
    data/seoul_pilot_physical_residualized_report.json

Columns
-------
    emd_cd, dong_name_kr, lawd_cd, gu_name, year, year_pair_label

    physical_embedding_norm_raw
    physical_embedding_norm_tokyo_taipei_offset
    physical_embedding_norm_metric_year_fe

    physical_yoy_angular_raw
    physical_yoy_angular_tokyo_taipei_offset
    physical_yoy_angular_metric_year_fe

    physical_yoy_cosine_dist_raw
    physical_yoy_cosine_dist_tokyo_taipei_offset
    physical_yoy_cosine_dist_metric_year_fe

    physical_yoy_euclid_raw
    physical_yoy_euclid_tokyo_taipei_offset
    physical_yoy_euclid_metric_year_fe

    artifact_transition_flag    (bool; True iff year == 2022)
    physical_artifact_policy    (string; recommended default for
                                 downstream consumption: "metric_year_fe")
    metric_year_fe_scope        ("pilot_cross_dong"; widen to
                                 "seoul_cross_dong" once full-Seoul
                                 extraction lands)
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
DEFAULT_METRIC_YEAR_FE_SCOPE = "pilot_cross_dong"
DEFAULT_POLICY_RECOMMENDATION = "metric_year_fe"
LABELED_OVERLAP = ("연남동", "망원동", "압구정동", "대치동")
POLICIES = ("raw", "tokyo_taipei_offset", "metric_year_fe")
YOY_METRICS = ("angular", "cosine_dist", "euclid")


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


# --- Metric primitives ---------------------------------------------------

def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def _per_dong_metrics(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-(emd_cd, year): embedding_norm + YoY angular / cosine_dist /
    euclid. Returns one row per (emd_cd, year)."""
    rows: list[dict] = []
    for emd_cd, sub in panel.groupby("emd_cd", sort=False):
        sub = sub.sort_values("year").reset_index(drop=True)
        vecs = sub[EMBED_COLS].to_numpy("float64")
        years = sub["year"].to_numpy("int64")
        for i in range(len(sub)):
            row: dict = {
                "emd_cd": str(emd_cd),
                "year": int(years[i]),
                "embedding_norm": float(np.linalg.norm(vecs[i])),
                "year_pair_label": None,
                "yoy_angular": np.nan,
                "yoy_cosine_dist": np.nan,
                "yoy_euclid": np.nan,
            }
            if i > 0:
                a, b = vecs[i - 1], vecs[i]
                au, bu = _unit(a), _unit(b)
                cos = float(np.clip(np.dot(au, bu), -1.0, 1.0))
                row["year_pair_label"] = f"{years[i - 1]}-{years[i]}"
                row["yoy_angular"] = float(np.arccos(cos))
                row["yoy_cosine_dist"] = float(1.0 - cos)
                row["yoy_euclid"] = float(np.linalg.norm(b - a))
            rows.append(row)
    return pd.DataFrame(rows)


# --- Policy application --------------------------------------------------

def metrics_for_raw(panel: pd.DataFrame) -> pd.DataFrame:
    """Compute metrics on the raw panel. The returned frame uses bare
    metric names (e.g. 'yoy_angular') that the caller renames per policy."""
    return _per_dong_metrics(panel)


def metrics_for_tokyo_taipei_offset(panel: pd.DataFrame,
                                     anchor_offsets: pd.DataFrame
                                     ) -> pd.DataFrame:
    """Subtract cumulative anchor offset from each embedding, then
    recompute metrics on the residualized vectors."""
    residualized = residualize(panel, anchor_offsets)
    return _per_dong_metrics(residualized)


def metrics_for_metric_year_fe(raw_metrics: pd.DataFrame) -> pd.DataFrame:
    """Subtract cross-dong median of each metric within the same
    group (year_pair_label for YoY metrics, year for embedding_norm).
    The median is computed across the dongs *present in the pilot*,
    which is the pilot_cross_dong scope.

    Returns a copy of raw_metrics where each metric is replaced by its
    centered (year-FE) residual."""
    out = raw_metrics.copy()
    # Embedding norm centered by year
    out["embedding_norm"] = (
        out["embedding_norm"]
        - out.groupby("year")["embedding_norm"].transform("median"))
    # YoY metrics centered by year_pair_label
    for m in YOY_METRICS:
        col = f"yoy_{m}"
        # When year_pair_label is NaN (the first panel year), the transform
        # still produces NaN, which is the right behaviour.
        out[col] = (
            out[col]
            - out.groupby("year_pair_label", dropna=False)[col]
                 .transform("median"))
    return out


def assemble_feature_layer(panel: pd.DataFrame,
                           per_policy: dict[str, pd.DataFrame],
                           metric_year_fe_scope: str,
                           recommended_policy: str) -> pd.DataFrame:
    """Merge the three per-policy metric frames into one wide feature
    table keyed on (emd_cd, year), and attach metadata columns."""
    ident = panel[["emd_cd", "dong_name_kr", "lawd_cd", "gu_name", "year"]].copy()
    ident["emd_cd"] = ident["emd_cd"].astype(str)
    ident["year"] = ident["year"].astype(int)

    # Use the raw metrics' year_pair_label as canonical (same across policies).
    year_pair = (per_policy["raw"][["emd_cd", "year", "year_pair_label"]]
                 .copy())

    out = ident.merge(year_pair, on=["emd_cd", "year"], how="left")

    metric_bases = ("embedding_norm", *(f"yoy_{m}" for m in YOY_METRICS))
    for policy in POLICIES:
        pm = per_policy[policy][["emd_cd", "year", *metric_bases]].copy()
        rename = {m: f"physical_{m}_{policy}" for m in metric_bases}
        pm = pm.rename(columns=rename)
        out = out.merge(pm, on=["emd_cd", "year"], how="left")

    out["artifact_transition_flag"] = out["year"].eq(2022)
    out["physical_artifact_policy"] = recommended_policy
    out["metric_year_fe_scope"] = metric_year_fe_scope
    out = out.sort_values(["lawd_cd", "emd_cd", "year"]).reset_index(drop=True)
    return out


# --- Diagnostic ----------------------------------------------------------

def _share_max_is_suspect_per_policy(layer: pd.DataFrame,
                                      policy: str) -> pd.DataFrame:
    """Per-(lawd_cd, gu_name) share of dongs whose maximum YoY angular
    (under this policy) lands at the 2021-2022 transition."""
    angular_col = f"physical_yoy_angular_{policy}"
    valid = layer[layer[angular_col].notna()].copy()
    idx = valid.groupby("emd_cd")[angular_col].idxmax()
    max_pair = valid.loc[idx, ["emd_cd", "lawd_cd", "gu_name",
                                "year_pair_label"]]
    summary = (max_pair.groupby(["lawd_cd", "gu_name"])["year_pair_label"]
                       .apply(lambda s: float((s == SUSPECT_PAIR).mean()))
                       .reset_index(name="share_max_is_suspect"))
    counts = (max_pair.groupby(["lawd_cd", "gu_name"])["emd_cd"]
                       .nunique()
                       .reset_index(name="n_dongs"))
    return summary.merge(counts, on=["lawd_cd", "gu_name"])


def _suspect_ratio_per_policy(layer: pd.DataFrame, policy: str) -> dict:
    angular_col = f"physical_yoy_angular_{policy}"
    suspect_mask = layer["year_pair_label"] == SUSPECT_PAIR
    suspect = layer.loc[suspect_mask, angular_col].dropna()
    other = layer.loc[~suspect_mask & layer[angular_col].notna(), angular_col]
    s_med = float(suspect.median()) if len(suspect) else float("nan")
    o_med = float(other.median()) if len(other) else float("nan")
    # For metric_year_fe, the median by construction is 0; report this
    # explicitly so the ratio isn't read as meaningful.
    ratio_meaningful = abs(o_med) > 1e-9
    ratio = (s_med / o_med) if ratio_meaningful else float("nan")
    return {"policy": policy,
            "suspect_median": s_med,
            "other_median": o_med,
            "ratio": ratio,
            "ratio_meaningful": ratio_meaningful,
            "n_suspect": int(len(suspect)),
            "n_other": int(len(other))}


def _per_year_pair_median_per_policy(layer: pd.DataFrame, policy: str) -> dict:
    angular_col = f"physical_yoy_angular_{policy}"
    grouped = (layer.dropna(subset=["year_pair_label", angular_col])
                    .groupby("year_pair_label")[angular_col])
    return {str(k): float(v) for k, v in grouped.median().to_dict().items()}


def _top_n_at_suspect_per_policy(layer: pd.DataFrame, policy: str,
                                  n: int = 5) -> list[dict]:
    angular_col = f"physical_yoy_angular_{policy}"
    sub = layer[layer["year_pair_label"] == SUSPECT_PAIR].copy()
    top = sub.nlargest(n, angular_col)
    return [
        {"dong_name_kr": str(r.dong_name_kr),
         "gu_name": str(r.gu_name),
         "angular": float(getattr(r, angular_col))}
        for r in top.itertuples(index=False)
    ]


def _labeled_trajectories(layer: pd.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name in LABELED_OVERLAP:
        sub = layer[layer["dong_name_kr"] == name].sort_values("year")
        if sub.empty:
            out[name] = []
            continue
        trail: list[dict] = []
        for r in sub.itertuples(index=False):
            if r.year_pair_label is None:
                continue
            trail.append({
                "year_pair": str(r.year_pair_label),
                "raw": float(r.physical_yoy_angular_raw),
                "tokyo_taipei_offset": float(
                    r.physical_yoy_angular_tokyo_taipei_offset),
                "metric_year_fe": float(r.physical_yoy_angular_metric_year_fe),
            })
        out[name] = trail
    return out


def diagnostic_report(layer: pd.DataFrame) -> dict:
    chance_share = 1.0 / 7.0  # seven year-pairs over 2017-2024
    share_tables = {policy: _share_max_is_suspect_per_policy(layer, policy)
                    .to_dict("records")
                    for policy in POLICIES}
    suspect_ratios = {policy: _suspect_ratio_per_policy(layer, policy)
                      for policy in POLICIES}
    per_year_pair_medians = {policy: _per_year_pair_median_per_policy(layer, policy)
                             for policy in POLICIES}
    top5 = {policy: _top_n_at_suspect_per_policy(layer, policy)
            for policy in POLICIES}
    trajectories = _labeled_trajectories(layer)

    # Did metric_year_fe drop share-max to chance?
    fe_passed = {}
    for row in share_tables["metric_year_fe"]:
        gu = row["gu_name"]
        share = row["share_max_is_suspect"]
        fe_passed[gu] = bool(share <= 2 * chance_share)

    return {
        "chance_share": chance_share,
        "share_max_is_2021_2022": share_tables,
        "suspect_pair_ratio": suspect_ratios,
        "per_year_pair_median_angular": per_year_pair_medians,
        "top5_at_2021_2022": top5,
        "labeled_overlap_trajectories": trajectories,
        "metric_year_fe_neutralised_2022": fe_passed,
    }


def print_report(report: dict) -> None:
    print("Three-policy artifact-handling diagnostic")
    print("=" * 72)
    chance = report["chance_share"]
    print(f"(chance share-max-on-any-year-pair = 1/7 = {chance:.3f})")

    print("\n1. share-max-is-2021-2022 per gu, per policy")
    print("   (drop to ~chance ⇒ artifact was purely common-mode at that "
          "metric)")
    raw = {(r["lawd_cd"], r["gu_name"]): r for r in
           report["share_max_is_2021_2022"]["raw"]}
    res = {(r["lawd_cd"], r["gu_name"]): r for r in
           report["share_max_is_2021_2022"]["tokyo_taipei_offset"]}
    fe = {(r["lawd_cd"], r["gu_name"]): r for r in
          report["share_max_is_2021_2022"]["metric_year_fe"]}
    for key in sorted(raw):
        r0 = raw[key]
        r1 = res.get(key, {"share_max_is_suspect": float("nan")})
        r2 = fe.get(key, {"share_max_is_suspect": float("nan")})
        print(f"   {r0['gu_name']:<8} ({r0['lawd_cd']}, {r0['n_dongs']} dongs)")
        print(f"     raw                  {r0['share_max_is_suspect']:.3f}")
        print(f"     tokyo_taipei_offset  {r1['share_max_is_suspect']:.3f}")
        print(f"     metric_year_fe       {r2['share_max_is_suspect']:.3f}")

    print("\n2. Suspect-pair angular ratio (2021-2022 median / other median)")
    print("   (for metric_year_fe the median is 0 by construction; ratio "
          "is not meaningful — flagged below)")
    for policy in POLICIES:
        d = report["suspect_pair_ratio"][policy]
        flag = "" if d["ratio_meaningful"] else "  (NOT meaningful — fe-centred)"
        print(f"   {policy:<22}  suspect_med={d['suspect_median']:+.4f}  "
              f"other_med={d['other_median']:+.4f}  "
              f"ratio={d['ratio']:.3f}{flag}")

    print("\n3. Per-year-pair median angular per policy")
    for policy in POLICIES:
        meds = report["per_year_pair_median_angular"][policy]
        sorted_pairs = sorted(meds.keys())
        print(f"   {policy}:")
        for p in sorted_pairs:
            marker = "  <— suspect" if p == SUSPECT_PAIR else ""
            print(f"     {p}  {meds[p]:+.4f}{marker}")

    print("\n4. Top 5 dongs at the 2021-2022 transition, per policy")
    for policy in POLICIES:
        print(f"   {policy}:")
        for entry in report["top5_at_2021_2022"][policy]:
            print(f"     {entry['dong_name_kr']:<8} ({entry['gu_name']})  "
                  f"angular={entry['angular']:+.4f}")

    print("\n5. Labeled-case YoY angular trajectories across all three policies")
    print("   (active_panel: 연남동, 망원동;  controls: 압구정동, 대치동)")
    for name, trail in report["labeled_overlap_trajectories"].items():
        if not trail:
            print(f"   {name}: not in pilot panel")
            continue
        print(f"   {name}:")
        for t in trail:
            print(f"     {t['year_pair']}  "
                  f"raw={t['raw']:+.4f}  "
                  f"tt_offset={t['tokyo_taipei_offset']:+.4f}  "
                  f"fe={t['metric_year_fe']:+.4f}")

    print("\n[acceptance] metric_year_fe drops share-max-2021-2022 to "
          f"≤ 2×chance ({2 * chance:.3f}):")
    for gu, ok in report["metric_year_fe_neutralised_2022"].items():
        verdict = "PASS" if ok else "FAIL"
        print(f"   {gu}: {verdict}")


# --- Main ----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Compare three artifact-handling policies "
                    "(raw / tokyo_taipei_offset / metric_year_fe) for the "
                    "Seoul AlphaEarth pilot physical-change metrics. "
                    "No EWS, no forecast, no composite score.")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL),
                    help=f"input AlphaEarth pilot panel (default {DEFAULT_PANEL})")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT),
                    help=f"output feature parquet (default {DEFAULT_OUTPUT})")
    ap.add_argument("--report", default=str(DEFAULT_REPORT),
                    help=f"output diagnostic JSON (default {DEFAULT_REPORT})")
    ap.add_argument("--anchor-cities", nargs="+",
                    default=list(DEFAULT_ANCHOR_CITIES),
                    help="anchor city set for tokyo_taipei_offset policy "
                         "(default: Tokyo Taipei)")
    ap.add_argument("--metric-year-fe-scope",
                    default=DEFAULT_METRIC_YEAR_FE_SCOPE,
                    help="scope label recorded with the metric_year_fe "
                         "policy (default: pilot_cross_dong)")
    ap.add_argument("--recommended-policy",
                    default=DEFAULT_POLICY_RECOMMENDATION,
                    choices=POLICIES,
                    help="recommended default policy for downstream "
                         "consumers (default: metric_year_fe)")
    args = ap.parse_args(argv)

    raw_panel = load_pilot(Path(args.panel))
    print(f"Pilot panel: {len(raw_panel)} rows  "
          f"dongs={raw_panel['emd_cd'].nunique()}  "
          f"years={sorted(raw_panel['year'].unique().tolist())}")

    anchor_df = load_anchor_embeddings(cities=tuple(args.anchor_cities))
    print(f"Anchor cache: {anchor_df['poly_id'].nunique()} polygons  "
          f"cities={sorted(anchor_df['city'].unique())}")
    offsets = compute_anchor_offsets(anchor_df, baseline_year=BASELINE_YEAR)

    per_policy = {
        "raw": metrics_for_raw(raw_panel),
        "tokyo_taipei_offset": metrics_for_tokyo_taipei_offset(raw_panel, offsets),
    }
    per_policy["metric_year_fe"] = metrics_for_metric_year_fe(per_policy["raw"])

    layer = assemble_feature_layer(
        raw_panel, per_policy,
        metric_year_fe_scope=args.metric_year_fe_scope,
        recommended_policy=args.recommended_policy)
    report = diagnostic_report(layer)
    print_report(report)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    layer.to_parquet(out, index=False)
    print(f"\nFeature layer written: {out}  "
          f"rows={len(layer)}  cols={len(layer.columns)}")

    rep_out = Path(args.report)
    rep_out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"Diagnostic report written: {rep_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
