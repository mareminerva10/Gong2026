"""
audit_residualized_axis.py
==========================

Empirical test of whether the AlphaEarth-learned gentrification axis
carries Seoul-specific signal after the regional 2021→2022 common-mode
drift is removed using Tokyo + Taipei polygons as geographic anchors.

Reads the cached live Seoul embeddings (`data/alphaearth_ee.parquet`)
and the Tokyo/Taipei polygons from `data/audit_cache/` — no Earth
Engine calls. Re-pulling embeddings is therefore not required to
re-run this audit.

Three variants
--------------
  V1  raw                  baseline; current prototype behavior
  V2  Tokyo/Taipei resid.  raw minus cumulative anchor drift from 2017
  V3  drop 2021+2022       raw with those two years removed from
                           axis learning AND projection slope
                           computation

Acceptance criteria (echoed at end of run)
------------------------------------------
  C1  active_panel LOO precision@1 stays at 1.0 after residualization
  C2  Hwagok no longer scores as a top false-positive control
  C3  2021–22 no longer dominates the learned direction
  C4  Mullae / Mangwon stay above all controls without relying on
      the artifact year

A failing C1+C3 combination is a clean negative result: the prior
axis was mostly regional common-mode drift. C2 and C4 sharpen the
interpretation of either outcome.

Output
------
  outputs/residualized_axis_comparison.png
  console: per-variant LOO summary, year-pair contributions, full
  composite ranking, and the four-criterion verdict
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import prototype as P
from axis_residualize import (
    load_anchor_embeddings,
    compute_anchor_offsets,
    residualize,
    year_pair_contributions,
)

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs"
OUT_PNG = OUT / "residualized_axis_comparison.png"

DROP_YEARS = (2021, 2022)


# ─── Helpers ─────────────────────────────────────────────────────────────

def composite_ranks(emb_df: pd.DataFrame, cases: pd.DataFrame,
                    axis: np.ndarray) -> pd.DataFrame:
    """Per-case axis projection slope, sorted descending. Returns the
    same columns as P.composite_ranking but without invoking wolse (which
    is irrelevant for the axis audit)."""
    rows: list[dict] = []
    for _, c in cases.iterrows():
        slope = P.projection_slope(
            P.project_trajectory(emb_df, c["dong_code"], axis))
        rows.append({"name_roman": c["name_roman"], "label": c["label"],
                     "dong_code": c["dong_code"], "axis_slope": float(slope)})
    df = pd.DataFrame(rows).sort_values("axis_slope", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    return df.reset_index(drop=True)


def hwagok_metrics(rank_df: pd.DataFrame, n_controls: int) -> dict:
    """Where does Hwagok sit relative to other controls? Lower rank-
    among-controls means more false-positive-like (i.e. Hwagok is
    looking gentrified-ish). 1 = most gentrified-looking control."""
    if "Hwagok" not in rank_df["name_roman"].values:
        return {"hwagok_global_rank": None,
                "hwagok_rank_among_controls": None,
                "n_controls": n_controls}
    controls = rank_df[rank_df["label"] == "control"].sort_values(
        "axis_slope", ascending=False).reset_index(drop=True)
    h_ctrl_rank = int(controls.index[controls["name_roman"] == "Hwagok"][0]) + 1
    h_global = int(rank_df.index[rank_df["name_roman"] == "Hwagok"][0]) + 1
    return {"hwagok_global_rank": h_global,
            "hwagok_rank_among_controls": h_ctrl_rank,
            "n_controls": n_controls}


def loo_summary(loo: list, n_ctrl: int) -> dict:
    if not loo:
        return {"precision_at_1": float("nan"), "mean_auc": float("nan"),
                "ranks": {}}
    p_at_1 = sum(1 for r in loo if r.rank_among_controls == 1) / len(loo)
    mean_auc = sum(r.auc_pair for r in loo) / len(loo)
    ranks = {r.held_out: r.rank_among_controls for r in loo}
    return {"precision_at_1": p_at_1, "mean_auc": mean_auc, "ranks": ranks}


def run_variant(name: str, emb_df: pd.DataFrame, cases: pd.DataFrame,
                drop_years: tuple[int, ...] = ()) -> dict:
    """Learn axis on active_panel cases, run LOO, compute composite
    ranks and Hwagok diagnostics, compute year-pair contribution.
    `drop_years` removes rows from BOTH axis learning and projection
    so the slope reflects only the kept years."""
    if drop_years:
        emb_df = emb_df[~emb_df["year"].isin(drop_years)].reset_index(drop=True)
    active = cases[cases["label"] == P.AXIS_TRAIN_LABEL]
    n_ctrl = int((cases["label"] == "control").sum())

    axis = P.learn_axis(emb_df, active)
    loo = P.leave_one_out(cases, emb_df)
    rank_df = composite_ranks(emb_df, cases, axis)
    hwa = hwagok_metrics(rank_df, n_ctrl)
    yp = year_pair_contributions(emb_df, axis, active)

    return {
        "name": name,
        "axis": axis,
        "emb_df": emb_df,
        "loo": loo_summary(loo, n_ctrl),
        "rank_df": rank_df,
        "hwagok": hwa,
        "year_pair_contribs": yp,
    }


# ─── Console output ──────────────────────────────────────────────────────

def print_variant(v: dict) -> None:
    print(f"\n=== {v['name']} ===")
    loo = v["loo"]
    print(f"  LOO  precision@1 = {loo['precision_at_1']:.2f}   "
          f"mean AUC-pair = {loo['mean_auc']:.2f}")
    for held, rank in loo["ranks"].items():
        print(f"    held-out {held:<12} rank {rank}")
    hwa = v["hwagok"]
    if hwa["hwagok_global_rank"] is None:
        print("  Hwagok: not found in cases")
    else:
        print(f"  Hwagok  global rank {hwa['hwagok_global_rank']}/12   "
              f"rank-among-controls {hwa['hwagok_rank_among_controls']}/"
              f"{hwa['n_controls']}  (1 = most gentrified-looking control)")
    print("  Year-pair cosine with axis (mean over active_panel):")
    for _, r in v["year_pair_contribs"].iterrows():
        bar = "#" * int(round(abs(r["mean_cos_with_axis"]) * 20))
        sign = "+" if r["mean_cos_with_axis"] >= 0 else "-"
        print(f"    {r['year_pair']}  {sign}{abs(r['mean_cos_with_axis']):.3f}  {bar}")
    print("  Composite ranking:")
    for _, r in v["rank_df"].iterrows():
        print(f"    {r['rank']:>2}  {r['name_roman']:<14} {r['label']:<13} "
              f"slope={r['axis_slope']:+.4f}")


def print_verdict(v_raw: dict, v_res: dict, v_drop: dict) -> None:
    print("\n" + "=" * 60)
    print("Acceptance-criterion verdict (residualized vs raw)")
    print("=" * 60)

    c1_pass = v_res["loo"]["precision_at_1"] >= v_raw["loo"]["precision_at_1"]
    print(f"  C1  LOO precision@1 stays at 1.0      : "
          f"raw={v_raw['loo']['precision_at_1']:.2f}  "
          f"resid={v_res['loo']['precision_at_1']:.2f}  "
          f"{'PASS' if c1_pass and v_res['loo']['precision_at_1'] >= 1.0 else 'FAIL'}")

    # C2: Hwagok rank among controls — higher (worse rank) = better.
    hw_raw = v_raw["hwagok"]["hwagok_rank_among_controls"]
    hw_res = v_res["hwagok"]["hwagok_rank_among_controls"]
    n_ctrl = v_raw["hwagok"]["n_controls"]
    median_rank = (n_ctrl + 1) / 2
    c2_pass = hw_res is not None and hw_res >= median_rank
    print(f"  C2  Hwagok not a top false-positive   : "
          f"raw rank-among-controls={hw_raw}/{n_ctrl}  "
          f"resid={hw_res}/{n_ctrl}  "
          f"(target >= median {median_rank:.1f})  "
          f"{'PASS' if c2_pass else 'FAIL'}")

    # C3: 2021-22 cosine no longer dominates among year-pairs.
    def dominant_pair(yp: pd.DataFrame) -> tuple[str, float]:
        i = yp["mean_cos_with_axis"].abs().idxmax()
        return yp.loc[i, "year_pair"], float(yp.loc[i, "mean_cos_with_axis"])
    dp_raw = dominant_pair(v_raw["year_pair_contribs"])
    dp_res = dominant_pair(v_res["year_pair_contribs"])
    c3_pass = dp_res[0] != "2021-2022"
    print(f"  C3  2021-22 no longer dominates axis  : "
          f"raw top pair={dp_raw[0]}({dp_raw[1]:+.3f})  "
          f"resid top pair={dp_res[0]}({dp_res[1]:+.3f})  "
          f"{'PASS' if c3_pass else 'FAIL'}")

    # C4: Mullae and Mangwon still rank above every control after resid.
    def above_all_controls(rank_df: pd.DataFrame, name: str) -> bool:
        if name not in rank_df["name_roman"].values:
            return False
        target = rank_df[rank_df["name_roman"] == name]["axis_slope"].iloc[0]
        ctrl_max = rank_df[rank_df["label"] == "control"]["axis_slope"].max()
        return target > ctrl_max
    mu_ok = above_all_controls(v_res["rank_df"], "Mullae-3ga")
    ma_ok = above_all_controls(v_res["rank_df"], "Mangwon")
    c4_pass = mu_ok and ma_ok
    print(f"  C4  Mullae+Mangwon > all controls     : "
          f"Mullae={'PASS' if mu_ok else 'FAIL'}  "
          f"Mangwon={'PASS' if ma_ok else 'FAIL'}  "
          f"overall {'PASS' if c4_pass else 'FAIL'}")

    overall = c1_pass and c2_pass and c3_pass and c4_pass
    print("-" * 60)
    print(f"  OVERALL: {'PASS — axis survives common-mode residualization'  if overall else 'FAIL — residualization removed Seoul-specific signal OR criteria not all met'}")
    if not overall:
        print("  (A failing run is a clean negative result. If C1+C3 both fail, "
              "the prior axis was largely regional common-mode drift; the next "
              "research move is MOLIT/permits/mechanism data, not embedding-axis "
              "tuning.)")

    print("\nSensitivity (V3, drop 2021+2022 from both learning and slope):")
    print(f"  LOO precision@1 = {v_drop['loo']['precision_at_1']:.2f}   "
          f"mean AUC-pair = {v_drop['loo']['mean_auc']:.2f}")
    yp = v_drop["year_pair_contribs"]
    if not yp.empty:
        dp = dominant_pair(yp)
        print(f"  Dominant year-pair on truncated panel: {dp[0]} ({dp[1]:+.3f})")


# ─── Plot ────────────────────────────────────────────────────────────────

def plot_comparison(v_raw: dict, v_res: dict, v_drop: dict, out_path: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    for ax, v in zip(axes, [v_raw, v_res, v_drop]):
        yp = v["year_pair_contribs"]
        colors = ["#d23" if p == "2021-2022" else "#666"
                  for p in yp["year_pair"]]
        ax.bar(yp["year_pair"], yp["mean_cos_with_axis"], color=colors,
               edgecolor="white", linewidth=0.8)
        ax.axhline(0, color="black", lw=0.5, alpha=0.4)
        ax.set_title(v["name"])
        ax.set_ylabel("mean cos(ΔE, axis)  over active_panel")
        # set_xticks before set_xticklabels avoids matplotlib's
        # FixedLocator warning when labels are passed without explicit ticks.
        ax.set_xticks(range(len(yp)))
        ax.set_xticklabels(yp["year_pair"].tolist(), rotation=30,
                           ha="right", fontsize=9)
        ax.grid(True, alpha=0.3, axis="y")
    fig.suptitle("Year-pair contribution to the learned axis  "
                 "(red = the suspect 2021–22 transition)",
                 fontsize=12)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


# ─── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    OUT.mkdir(exist_ok=True)

    ee_cache = P.embed_cache("ee")
    if not ee_cache.exists():
        sys.exit(f"missing {ee_cache} — run `python prototype.py --mode ee "
                 "--gcp-project YOURS` first to populate live AlphaEarth.")
    cases = P.load_cases()
    emb_raw = pd.read_parquet(ee_cache)

    anchor_df = load_anchor_embeddings()
    n_anchors = anchor_df["poly_id"].nunique()
    n_anchor_years = anchor_df["year"].nunique()
    print(f"Anchor set: {n_anchors} polygons × {n_anchor_years} years from "
          f"{sorted(anchor_df['city'].unique())}")

    offsets = compute_anchor_offsets(anchor_df, baseline_year=2017)
    print(f"Anchor offsets (relative to 2017):")
    for y, row in offsets.iterrows():
        l2 = float(np.linalg.norm(row.values))
        print(f"  {y}   ||offset|| = {l2:.4f}")

    emb_res = residualize(emb_raw, offsets)

    v_raw = run_variant("V1 raw", emb_raw, cases)
    v_res = run_variant("V2 Tokyo/Taipei-residualized", emb_res, cases)
    v_drop = run_variant("V3 drop 2021+2022", emb_raw, cases,
                         drop_years=DROP_YEARS)

    for v in (v_raw, v_res, v_drop):
        print_variant(v)

    plot_comparison(v_raw, v_res, v_drop, OUT_PNG)
    print(f"\nPlot written: {OUT_PNG}")

    print_verdict(v_raw, v_res, v_drop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
