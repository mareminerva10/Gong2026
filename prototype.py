"""
Gentrification Prototype — AlphaEarth methodology core
======================================================

A focused rebuild of the rent-gap radar idea, scoped to what is actually
demonstrable and validatable. Replaces the v2.0 file's hand-picked
"affluent archetype" with a *learned* gentrification direction from
labeled before/after Seoul cases, drops the unvalidated transit/commute
layers, and adds leave-one-out validation against control dongs.

Method
------
1. Pick 6 well-documented Seoul gentrified dongs (before/after years known)
   and 6 stable control dongs.
2. For each dong-year, extract a 64-D AlphaEarth embedding centroid
   (mean over the dong polygon at 10m scale).
3. Compute, for each gentrified case, the difference vector
   (mean(after_years) - mean(before_years)) in embedding space.
4. Average those difference vectors → the *learned gentrification axis*.
5. Score any dong-year by projecting its embedding centroid onto the axis.
6. The slope of (projection vs year) is the gentrification trajectory score.
7. Validate by leave-one-out: hold out each labeled case, relearn the
   axis from the rest, check whether the held-out case's slope ranks
   above all controls.
8. Combine with one Korea-specific tenure signal (wolse-ratio *change*).

Run
---
    python prototype.py                       # mock data, end-to-end
    python prototype.py --mode ee --gcp-project YOUR_PROJECT
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import linregress
from shapely.geometry import box, mapping

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "outputs"
CASES_CSV = DATA / "labeled_cases.csv"
POLY_GEOJSON = DATA / "dong_polygons.geojson"


def embed_cache(mode: str) -> Path:
    """Mode-tagged cache path so a mock run doesn't poison a later --mode ee run."""
    return DATA / f"alphaearth_{mode}.parquet"


def wolse_cache(mode: str) -> Path:
    return DATA / f"wolse_{mode}.parquet"

YEARS = list(range(2017, 2025))          # AlphaEarth annual coverage 2017-
EMBED_DIM = 64
EMBED_COLS = [f"A{i:02d}" for i in range(EMBED_DIM)]
SEED = 7
BOX_HALF_DEG = 0.005                     # ~0.5 km half-side bounding box


# ─── Data scaffolding ─────────────────────────────────────────────────────

def load_cases() -> pd.DataFrame:
    if not CASES_CSV.exists():
        sys.exit(f"missing {CASES_CSV} — see repo README")
    return pd.read_csv(CASES_CSV)


def write_polygons_if_absent(cases: pd.DataFrame) -> None:
    """Approximate dong polygons as 1km bounding boxes around centroids.
    For real EE runs, replace with NSDI / SGIS admin boundary polygons."""
    if POLY_GEOJSON.exists():
        return
    features = []
    for row in cases.itertuples():
        poly = box(row.lon - BOX_HALF_DEG, row.lat - BOX_HALF_DEG,
                   row.lon + BOX_HALF_DEG, row.lat + BOX_HALF_DEG)
        features.append({
            "type": "Feature",
            "properties": {"dong_code": row.dong_code, "name_roman": row.name_roman},
            "geometry": mapping(poly),
        })
    POLY_GEOJSON.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2
    ), encoding="utf-8")


# ─── Mock data generator (default mode) ───────────────────────────────────

def synth_embeddings(cases: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    """Synthesise plausible 64-D AlphaEarth centroids per (dong, year).

    Design: a single shared 'true gentrification direction' v_gent.
    Gentrified dongs drift along v_gent between start_year and end_year,
    plus continued post-peak intensification (commercial deepening) so
    cases that finished gentrifying before 2017 still carry within-panel
    signal — realistic, since these neighbourhoods don't freeze.
    Controls drift in a random, uncorrelated direction with smaller magnitude.
    Per-year noise is added so LOO recovery isn't trivial.
    """
    rng = np.random.default_rng(seed)
    v_gent = rng.standard_normal(EMBED_DIM)
    v_gent /= np.linalg.norm(v_gent)

    rows: list[dict] = []
    for r in cases.itertuples():
        base = rng.standard_normal(EMBED_DIM) * 0.5            # per-dong baseline
        if r.label == "gentrified":
            alpha = rng.uniform(0.35, 0.65)                    # peak drift magnitude
            for y in YEARS:
                if y <= r.start_year:
                    progress = 0.0
                elif y >= r.end_year:
                    # Continued post-peak commercial intensification (~40% slope)
                    post = (y - r.end_year) / max(1, YEARS[-1] - r.end_year)
                    progress = 1.0 + 0.4 * post
                else:
                    progress = (y - r.start_year) / (r.end_year - r.start_year)
                noise = rng.standard_normal(EMBED_DIM) * 0.07
                emb = base + alpha * progress * v_gent + noise
                rows.append({"dong_code": r.dong_code, "year": y,
                             **dict(zip(EMBED_COLS, emb))})
        else:
            # Random uncorrelated drift, smaller magnitude
            v_ctrl = rng.standard_normal(EMBED_DIM)
            v_ctrl /= np.linalg.norm(v_ctrl)
            beta = rng.uniform(0.05, 0.15)
            for y in YEARS:
                progress = (y - YEARS[0]) / (YEARS[-1] - YEARS[0])
                noise = rng.standard_normal(EMBED_DIM) * 0.07
                emb = base + beta * progress * v_ctrl + noise
                rows.append({"dong_code": r.dong_code, "year": y,
                             **dict(zip(EMBED_COLS, emb))})
    return pd.DataFrame(rows)


def synth_wolse(cases: pd.DataFrame, seed: int = SEED + 1) -> pd.DataFrame:
    """Mock wolse-ratio time series.

    Gentrified dongs: wolse ratio rises from ~0.35 → ~0.65 over their window.
    Controls: roughly flat around a dong-specific baseline.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for r in cases.itertuples():
        if r.label == "gentrified":
            wolse_start = rng.uniform(0.30, 0.40)
            wolse_end = rng.uniform(0.60, 0.72)
            for y in YEARS:
                if y <= r.start_year:
                    w = wolse_start
                elif y >= r.end_year:
                    # Continued landlord wolse-conversion after gentrification peaks
                    post = (y - r.end_year) / max(1, YEARS[-1] - r.end_year)
                    w = wolse_end + 0.1 * post * (wolse_end - wolse_start)
                else:
                    p = (y - r.start_year) / (r.end_year - r.start_year)
                    w = wolse_start + p * (wolse_end - wolse_start)
                w += rng.normal(0, 0.025)
                rows.append({"dong_code": r.dong_code, "year": y, "wolse_ratio": float(np.clip(w, 0, 1))})
        else:
            baseline = rng.uniform(0.30, 0.55)
            for y in YEARS:
                w = baseline + rng.normal(0, 0.025)
                rows.append({"dong_code": r.dong_code, "year": y, "wolse_ratio": float(np.clip(w, 0, 1))})
    return pd.DataFrame(rows)


# ─── Real Earth Engine extractor (--mode ee) ──────────────────────────────

def extract_ee_embeddings(cases: pd.DataFrame, gcp_project: str) -> pd.DataFrame:
    """Pull AlphaEarth annual mean embeddings per dong polygon at 10m scale.

    Real run: requires `earthengine-api` configured + a GCP project. Polygons
    used here are 1km boxes from `dong_polygons.geojson`; replace with NSDI
    admin polygons for production results.
    """
    import ee
    ee.Initialize(project=gcp_project)
    polys = json.loads(POLY_GEOJSON.read_text(encoding="utf-8"))
    poly_by_code = {f["properties"]["dong_code"]: f["geometry"] for f in polys["features"]}

    coll = ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL").select(EMBED_COLS)
    rows: list[dict] = []
    for r in cases.itertuples():
        geom = ee.Geometry(poly_by_code[r.dong_code])
        for y in YEARS:
            img = coll.filterDate(f"{y}-01-01", f"{y + 1}-01-01").filterBounds(geom).mosaic()
            stats = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=geom,
                                     scale=10, maxPixels=1e8).getInfo()
            if not stats or stats.get("A00") is None:
                print(f"  ! no embedding for {r.name_roman} {y}", file=sys.stderr)
                continue
            rows.append({"dong_code": r.dong_code, "year": y,
                         **{b: float(stats.get(b, 0.0)) for b in EMBED_COLS}})
    return pd.DataFrame(rows)


# ─── Caching layer ────────────────────────────────────────────────────────

def _shrink_embeddings(df: pd.DataFrame) -> pd.DataFrame:
    # dong_code stays int — labeled_cases.csv reads it as int64, and downstream
    # filters compare against case["dong_code"]; switching dtypes here breaks them.
    df = df.copy()
    df["year"] = df["year"].astype("int16")
    for c in EMBED_COLS:
        df[c] = df[c].astype("float32")
    return df


def _shrink_wolse(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["year"] = df["year"].astype("int16")
    df["wolse_ratio"] = df["wolse_ratio"].astype("float32")
    return df


def get_embeddings(cases: pd.DataFrame, mode: str, gcp_project: str | None) -> pd.DataFrame:
    path = embed_cache(mode)
    if path.exists():
        return pd.read_parquet(path)
    if mode == "ee":
        if not gcp_project:
            sys.exit("--mode ee requires --gcp-project")
        df = extract_ee_embeddings(cases, gcp_project)
    else:
        df = synth_embeddings(cases)
    df = _shrink_embeddings(df)
    df.to_parquet(path, index=False)
    return df


def get_wolse(cases: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Wolse is mocked in both modes — a real MOLIT client is out of scope
    for this prototype. The cache is still mode-tagged so that when a real
    MOLIT client lands, ee-mode wolse won't be silently served from mock."""
    path = wolse_cache(mode)
    if path.exists():
        return pd.read_parquet(path)
    df = _shrink_wolse(synth_wolse(cases))
    df.to_parquet(path, index=False)
    return df


# ─── Core analysis ────────────────────────────────────────────────────────

def emb_vector(emb_df: pd.DataFrame, dong_code: str, year: int) -> np.ndarray:
    row = emb_df[(emb_df["dong_code"] == dong_code) & (emb_df["year"] == year)]
    if row.empty:
        return np.zeros(EMBED_DIM)
    return row[EMBED_COLS].values[0]


def within_panel_delta(emb_df: pd.DataFrame, case: pd.Series, window: int = 2) -> np.ndarray:
    """Mean(last window panel years) - mean(first window panel years).

    IMPORTANT: this is *not* a historical before/after for the case's full
    gentrification window — AlphaEarth's panel starts in 2017, and our six
    labeled cases all began gentrifying before that. What this measures
    for every dong is the direction it drifted between 2017-18 and 2023-24.
    For mid-cycle cases (Mangwon, Mullae) that overlaps active gentrification
    drift; for earlier cases (Yeonnam, Seongsu) it captures post-peak
    commercial intensification. The learned axis therefore points along
    "ongoing gentrification-style drift", not "transition from pre-gent baseline".
    """
    before_years = YEARS[:window]
    after_years = YEARS[-window:]
    before = np.stack([emb_vector(emb_df, case["dong_code"], y) for y in before_years]).mean(axis=0)
    after = np.stack([emb_vector(emb_df, case["dong_code"], y) for y in after_years]).mean(axis=0)
    return after - before


def learn_axis(emb_df: pd.DataFrame, training_cases: pd.DataFrame) -> np.ndarray:
    """Mean of per-case within-panel drift vectors, unit-normalised."""
    deltas = [within_panel_delta(emb_df, c) for _, c in training_cases.iterrows()]
    axis = np.stack(deltas).mean(axis=0)
    n = np.linalg.norm(axis)
    return axis / n if n > 1e-9 else axis


def project_trajectory(emb_df: pd.DataFrame, dong_code: str, axis: np.ndarray) -> pd.DataFrame:
    sub = emb_df[emb_df["dong_code"] == dong_code].sort_values("year")
    proj = sub[EMBED_COLS].values @ axis
    return pd.DataFrame({"year": sub["year"].values, "projection": proj})


def projection_slope(traj: pd.DataFrame) -> float:
    if len(traj) < 2:
        return 0.0
    return float(linregress(traj["year"], traj["projection"]).slope)


def wolse_change(wolse_df: pd.DataFrame, dong_code: str) -> float:
    """Annualised slope of wolse_ratio over the panel."""
    sub = wolse_df[wolse_df["dong_code"] == dong_code].sort_values("year")
    if len(sub) < 2:
        return 0.0
    return float(linregress(sub["year"], sub["wolse_ratio"]).slope)


# ─── Leave-one-out validation ─────────────────────────────────────────────

@dataclass
class LOOResult:
    held_out: str
    held_out_slope: float
    control_slopes: dict
    rank_among_controls: int          # 1 = best (highest slope)
    auc_pair: float                   # fraction of controls beaten


def leave_one_out(cases: pd.DataFrame, emb_df: pd.DataFrame) -> list[LOOResult]:
    gentrified = cases[cases["label"] == "gentrified"].reset_index(drop=True)
    controls = cases[cases["label"] == "control"].reset_index(drop=True)
    results: list[LOOResult] = []
    for i in range(len(gentrified)):
        held = gentrified.iloc[i]
        training = gentrified.drop(index=i)
        axis = learn_axis(emb_df, training)

        held_slope = projection_slope(project_trajectory(emb_df, held["dong_code"], axis))
        ctrl_slopes = {
            row["name_roman"]: projection_slope(project_trajectory(emb_df, row["dong_code"], axis))
            for _, row in controls.iterrows()
        }
        rank = 1 + sum(1 for s in ctrl_slopes.values() if s > held_slope)
        auc = sum(1 for s in ctrl_slopes.values() if held_slope > s) / len(ctrl_slopes)
        results.append(LOOResult(
            held_out=held["name_roman"],
            held_out_slope=held_slope,
            control_slopes=ctrl_slopes,
            rank_among_controls=rank,
            auc_pair=auc,
        ))
    return results


# ─── Plotting ─────────────────────────────────────────────────────────────

def plot_trajectories(emb_df: pd.DataFrame, cases: pd.DataFrame,
                      axis: np.ndarray, mode: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    for _, c in cases.iterrows():
        traj = project_trajectory(emb_df, c["dong_code"], axis)
        is_gent = c["label"] == "gentrified"
        ax.plot(traj["year"], traj["projection"],
                marker="o", lw=2 if is_gent else 1.2,
                color="#d23" if is_gent else "#6a737d",
                alpha=0.95 if is_gent else 0.55,
                label=c["name_roman"])
        # annotate gentrified line at its end
        if is_gent:
            ax.text(traj["year"].iloc[-1] + 0.1, traj["projection"].iloc[-1],
                    c["name_roman"], fontsize=8, color="#d23", va="center")
    src = "MOCK synthetic embeddings" if mode == "mock" else "AlphaEarth (live EE)"
    ax.set_xlabel("Year")
    ax.set_ylabel("Projection onto learned within-panel drift axis")
    ax.set_title(f"Per-dong trajectory in embedding space  [{src}]\n"
                 "axis learned from 6 labeled Seoul cases; controls in grey")
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=2, fontsize=7, loc="upper left", framealpha=0.9)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_projection_scatter(emb_df: pd.DataFrame, wolse_df: pd.DataFrame,
                            cases: pd.DataFrame, axis: np.ndarray,
                            mode: str, wolse_is_mock: bool, out_path: Path) -> None:
    rows = []
    for _, c in cases.iterrows():
        slope = projection_slope(project_trajectory(emb_df, c["dong_code"], axis))
        wch = wolse_change(wolse_df, c["dong_code"])
        rows.append({"name_roman": c["name_roman"], "label": c["label"],
                     "axis_slope": slope, "wolse_slope": wch})
    df = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    for label, color in [("gentrified", "#d23"), ("control", "#6a737d")]:
        sub = df[df["label"] == label]
        ax.scatter(sub["axis_slope"], sub["wolse_slope"],
                   c=color, s=110, edgecolors="white", linewidths=1.2,
                   label=label, alpha=0.9)
        for _, r in sub.iterrows():
            ax.annotate(r["name_roman"], (r["axis_slope"], r["wolse_slope"]),
                        xytext=(6, 4), textcoords="offset points", fontsize=8.5,
                        color=color)
    ax.axhline(0, color="black", lw=0.5, alpha=0.4)
    ax.axvline(0, color="black", lw=0.5, alpha=0.4)
    emb_src = "mock embeddings" if mode == "mock" else "AlphaEarth EE"
    wolse_src = "MOCK wolse" if wolse_is_mock else "MOLIT wolse"
    ax.set_xlabel(f"Embedding-axis slope  [{emb_src}]")
    ax.set_ylabel(f"Wolse-ratio slope  [{wolse_src}]")
    ax.set_title(f"Two-signal separation  ({emb_src}  ×  {wolse_src})\n"
                 "Scaffold check: gentrified cases should cluster upper-right")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", framealpha=0.9)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


# ─── Reporting ────────────────────────────────────────────────────────────

def z(x: np.ndarray) -> np.ndarray:
    s = x.std()
    return (x - x.mean()) / s if s > 1e-9 else x - x.mean()


def composite_ranking(emb_df: pd.DataFrame, wolse_df: pd.DataFrame,
                      cases: pd.DataFrame, axis: np.ndarray) -> pd.DataFrame:
    rows = []
    for _, c in cases.iterrows():
        slope = projection_slope(project_trajectory(emb_df, c["dong_code"], axis))
        wch = wolse_change(wolse_df, c["dong_code"])
        rows.append({"name_roman": c["name_roman"], "label": c["label"],
                     "axis_slope": slope, "wolse_slope": wch})
    df = pd.DataFrame(rows)
    df["axis_z"] = z(df["axis_slope"].values)
    df["wolse_z"] = z(df["wolse_slope"].values)
    df["composite_z"] = df["axis_z"] + df["wolse_z"]
    return df.sort_values("composite_z", ascending=False).reset_index(drop=True)


def print_loo_summary(loo: list[LOOResult]) -> None:
    print("\nLeave-one-out validation")
    print("-" * 60)
    print(f"{'held-out':<14} {'slope':>8}  {'rank/7':>7}  {'AUC-pair':>9}")
    for r in loo:
        print(f"{r.held_out:<14} {r.held_out_slope:>+8.4f}  "
              f"{r.rank_among_controls:>3} / 7  {r.auc_pair:>9.2f}")
    p_at_1 = sum(1 for r in loo if r.rank_among_controls == 1) / len(loo)
    mean_auc = sum(r.auc_pair for r in loo) / len(loo)
    print("-" * 60)
    print(f"precision@1 = {p_at_1:.2f}    mean AUC-pair = {mean_auc:.2f}")


def print_ranking(df: pd.DataFrame) -> None:
    print("\nComposite ranking (axis_z + wolse_z)")
    print("-" * 70)
    print(f"{'rank':>4}  {'dong':<14} {'label':<11} {'axis_z':>7} {'wolse_z':>8} {'composite':>10}")
    for i, r in df.iterrows():
        print(f"{i + 1:>4}  {r['name_roman']:<14} {r['label']:<11} "
              f"{r['axis_z']:>+7.2f} {r['wolse_z']:>+8.2f} {r['composite_z']:>+10.2f}")


# ─── CLI ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--mode", choices=["mock", "ee"], default="mock",
                    help="data source: synthetic (default) or live Earth Engine")
    ap.add_argument("--gcp-project", default=None,
                    help="GCP project id (required for --mode ee)")
    ap.add_argument("--rebuild-cache", action="store_true",
                    help="delete cached embeddings/wolse parquet files and regenerate")
    args = ap.parse_args(argv)

    DATA.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

    if args.rebuild_cache:
        for p in (embed_cache(args.mode), wolse_cache(args.mode)):
            p.unlink(missing_ok=True)

    cases = load_cases()
    write_polygons_if_absent(cases)
    emb_df = get_embeddings(cases, args.mode, args.gcp_project)
    wolse_df = get_wolse(cases, args.mode)
    wolse_is_mock = True  # no real MOLIT client yet — always mocked, see get_wolse()

    print(f"Loaded {len(cases)} cases  "
          f"({(cases['label'] == 'gentrified').sum()} gentrified, "
          f"{(cases['label'] == 'control').sum()} controls)")
    print(f"Embeddings: {len(emb_df)} rows  ({args.mode} mode)")
    if args.mode == "mock":
        print("  [scaffold check] mock data has a planted shared drift direction;\n"
              "  perfect LOO is expected and is NOT empirical validation.")
    if wolse_is_mock:
        print("  [scaffold check] wolse is mocked - no MOLIT client yet.")

    # Full-data axis for display and ranking
    gentrified = cases[cases["label"] == "gentrified"]
    axis = learn_axis(emb_df, gentrified)
    print(f"Learned gentrification axis: ||a|| = {np.linalg.norm(axis):.4f}  "
          f"(unit-normalised)")

    # Leave-one-out validation
    loo = leave_one_out(cases, emb_df)
    print_loo_summary(loo)

    # Composite ranking
    rank_df = composite_ranking(emb_df, wolse_df, cases, axis)
    print_ranking(rank_df)

    # Plots
    plot_trajectories(emb_df, cases, axis, args.mode, OUT / "trajectories.png")
    plot_projection_scatter(emb_df, wolse_df, cases, axis, args.mode, wolse_is_mock,
                            OUT / "projection_scatter.png")
    print(f"\nPlots written:\n  {OUT / 'trajectories.png'}\n  {OUT / 'projection_scatter.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
