"""
Gentrification Prototype — AlphaEarth methodology core
======================================================

A focused rebuild of the rent-gap radar idea, scoped to what is actually
demonstrable and validatable. Replaces the v2.0 file's hand-picked
"affluent archetype" with a *learned* gentrification direction from
labeled before/after Seoul cases, drops the unvalidated transit/commute
layers, and adds leave-one-out validation against control dongs.

Model panel
-----------
Each run also assembles a derived per-(dong, year) panel at
`data/dong_year_model_panel.parquet`, joining case metadata, embeddings,
their axis projection, wolse, and two MOLIT 통계누리 controls:

  - `national_redevelopment_intensity_*` from redev 6189/1 — a YEAR-LEVEL
    NATIONAL redevelopment intensity (no geographic dimension). NOT a
    gu-level or dong-level announcement-exposure variable.
  - `statnuri_unsold_{mean,max,dec}_units` from unsold 2082/128 — a
    GU-LEVEL monthly unsold-housing inventory aggregated to annual
    (mean / max / December snapshot), joined by `lawd_cd × year`. This
    is a housing-market stress / weak-demand proxy, NOT a wolse_ratio
    replacement and NOT a tenure signal.

Downstream interpretation must keep both distinctions (national vs. gu,
demand vs. tenure). The raw caches (`alphaearth_*.parquet`,
`wolse_*.parquet`) are read but not mutated.

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
import os
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
MOLIT_CACHE_DIR = DATA / "molit_cache"
MOLIT_RAW_PARQUET = DATA / "molit_rent_raw.parquet"
REDEV_PANEL_PATH = DATA / "national_redevelopment_intensity.parquet"
UNSOLD_PANEL_PATH = DATA / "statnuri_unsold_panel.parquet"
MODEL_PANEL_PATH = DATA / "dong_year_model_panel.parquet"

# Mapping from build_national_redev_panel's column names to the panel-side
# `national_redevelopment_intensity_*` framing. The long prefix makes the
# non-local nature of the variable survive future re-merges and notebook
# inspections; do not shorten it.
REDEV_COLUMN_RENAME = {
    "redev_zone_count":         "national_redevelopment_intensity_zone_count",
    "redev_area_m2":            "national_redevelopment_intensity_area_m2",
    "redev_demolition_targets": "national_redevelopment_intensity_demolition_targets",
    "redev_units_total":        "national_redevelopment_intensity_units_total",
    "redev_units_member":       "national_redevelopment_intensity_units_member",
    "redev_units_general_sale": "national_redevelopment_intensity_units_general_sale",
    "redev_units_rental":       "national_redevelopment_intensity_units_rental",
}


def embed_cache(mode: str) -> Path:
    """Mode-tagged cache path so a mock run doesn't poison a later --mode ee run."""
    return DATA / f"alphaearth_{mode}.parquet"


def wolse_cache(source: str) -> Path:
    return DATA / f"wolse_{source}.parquet"

YEARS = list(range(2017, 2025))          # AlphaEarth annual coverage 2017-
EMBED_DIM = 64
EMBED_COLS = [f"A{i:02d}" for i in range(EMBED_DIM)]
SEED = 7
BOX_HALF_DEG = 0.005                     # ~0.5 km half-side bounding box

# Three-way label scheme — see labeled_cases.csv:
#   active_panel : gentrification cycle plausibly still active during 2017-24
#   post_peak    : cycle finished mostly before the panel; useful for morphology
#   control      : no major redevelopment/gentrification shock
# Axis is learned only from active_panel cases (the within-panel hypothesis).
GENT_LABELS = ("active_panel", "post_peak")
AXIS_TRAIN_LABEL = "active_panel"


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
        if r.label in GENT_LABELS:
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
        if r.label in GENT_LABELS:
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

def extract_ee_embeddings(
        cases: pd.DataFrame,
        gcp_project: str,
        service_account_key: str | None = None,
) -> pd.DataFrame:
    """Pull AlphaEarth annual mean embeddings per dong polygon at 10m scale.

    Real run: requires `earthengine-api` configured + a GCP project. Polygons
    used here are 1km boxes from `dong_polygons.geojson`; replace with NSDI
    admin polygons for production results.
    """
    import ee
    if service_account_key:
        info = json.loads(Path(service_account_key).read_text(encoding="utf-8"))
        credentials = ee.ServiceAccountCredentials(info["client_email"], service_account_key)
        ee.Initialize(credentials, project=gcp_project)
    else:
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


def get_embeddings(
        cases: pd.DataFrame,
        mode: str,
        gcp_project: str | None,
        service_account_key: str | None = None,
) -> pd.DataFrame:
    path = embed_cache(mode)
    if path.exists():
        return pd.read_parquet(path)
    if mode == "ee":
        if not gcp_project:
            sys.exit("--mode ee requires --gcp-project")
        df = extract_ee_embeddings(cases, gcp_project, service_account_key)
    else:
        df = synth_embeddings(cases)
    df = _shrink_embeddings(df)
    df.to_parquet(path, index=False)
    return df


def get_wolse(cases: pd.DataFrame, source: str) -> pd.DataFrame:
    """source ∈ {'mock', 'molit'}. Independent of --mode: you can pair real
    AlphaEarth with mock wolse for axis debugging, or mock embeddings with
    a live MOLIT pull to shake out the rent pipeline. Cache is source-tagged
    so a mock run never gets silently served when --wolse-source molit is
    later requested.

    molit branch raises (does not silently return empty) on any irrecoverable
    API failure — see molit_client.fetch_rent_panel for guardrails."""
    path = wolse_cache(source)
    if path.exists():
        return pd.read_parquet(path)
    if source == "molit":
        from molit_client import fetch_rent_panel
        df = fetch_rent_panel(cases, YEARS, MOLIT_CACHE_DIR, raw_out=MOLIT_RAW_PARQUET)
    else:
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
    """LOO over active_panel cases only — these are the ones whose gentrification
    cycle plausibly overlaps the AlphaEarth panel (2017-24). post_peak cases are
    excluded from training and from the held-out set; they're evaluated separately
    via evaluate_post_peak()."""
    active = cases[cases["label"] == AXIS_TRAIN_LABEL].reset_index(drop=True)
    controls = cases[cases["label"] == "control"].reset_index(drop=True)
    results: list[LOOResult] = []
    for i in range(len(active)):
        held = active.iloc[i]
        training = active.drop(index=i)
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


def evaluate_post_peak(
        cases: pd.DataFrame, emb_df: pd.DataFrame, axis: np.ndarray,
) -> list[LOOResult]:
    """Project each post_peak case onto the full active_panel axis and rank
    against the same controls. If post_peak cases also rank above controls,
    the axis is picking up morphological signatures (commercial density, etc.)
    that linger beyond the gentrification cycle — useful but a different
    claim than 'detecting active transition'."""
    post_peak = cases[cases["label"] == "post_peak"].reset_index(drop=True)
    controls = cases[cases["label"] == "control"].reset_index(drop=True)
    results: list[LOOResult] = []
    for _, row in post_peak.iterrows():
        s = projection_slope(project_trajectory(emb_df, row["dong_code"], axis))
        ctrl_slopes = {
            r["name_roman"]: projection_slope(project_trajectory(emb_df, r["dong_code"], axis))
            for _, r in controls.iterrows()
        }
        rank = 1 + sum(1 for cs in ctrl_slopes.values() if cs > s)
        auc = sum(1 for cs in ctrl_slopes.values() if s > cs) / len(ctrl_slopes)
        results.append(LOOResult(
            held_out=row["name_roman"], held_out_slope=s,
            control_slopes=ctrl_slopes, rank_among_controls=rank, auc_pair=auc,
        ))
    return results


# ─── Plotting ─────────────────────────────────────────────────────────────

LABEL_STYLE = {
    "active_panel": {"color": "#d23",   "lw": 2.2, "alpha": 0.95, "annotate": True},
    "post_peak":    {"color": "#f0883e", "lw": 1.6, "alpha": 0.85, "annotate": True},
    "control":      {"color": "#6a737d", "lw": 1.2, "alpha": 0.55, "annotate": False},
}


def plot_trajectories(emb_df: pd.DataFrame, cases: pd.DataFrame,
                      axis: np.ndarray, mode: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    for _, c in cases.iterrows():
        traj = project_trajectory(emb_df, c["dong_code"], axis)
        style = LABEL_STYLE.get(c["label"], LABEL_STYLE["control"])
        ax.plot(traj["year"], traj["projection"],
                marker="o", lw=style["lw"], color=style["color"],
                alpha=style["alpha"], label=c["name_roman"])
        if style["annotate"]:
            ax.text(traj["year"].iloc[-1] + 0.1, traj["projection"].iloc[-1],
                    c["name_roman"], fontsize=8, color=style["color"], va="center")
    src = "MOCK synthetic embeddings" if mode == "mock" else "AlphaEarth (live EE)"
    ax.set_xlabel("Year")
    ax.set_ylabel("Projection onto learned within-panel drift axis")
    ax.set_title(f"Per-dong trajectory in embedding space  [{src}]\n"
                 "axis learned from active_panel cases only; post_peak in orange, controls in grey")
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
    for label in ("active_panel", "post_peak", "control"):
        color = LABEL_STYLE[label]["color"]
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
                 "active_panel = red, post_peak = orange, control = grey  "
                 f"{'(wolse axis is MOCKED — diagnostic only)' if wolse_is_mock else ''}")
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
                      cases: pd.DataFrame, axis: np.ndarray,
                      include_wolse: bool = False) -> pd.DataFrame:
    """Rank by axis_z only by default; wolse is mocked at the moment and
    pulling it into the composite makes the ordering misleading. The wolse
    slope and z-score are still computed for inspection."""
    rows = []
    for _, c in cases.iterrows():
        slope = projection_slope(project_trajectory(emb_df, c["dong_code"], axis))
        wch = wolse_change(wolse_df, c["dong_code"])
        rows.append({"name_roman": c["name_roman"], "label": c["label"],
                     "axis_slope": slope, "wolse_slope": wch})
    df = pd.DataFrame(rows)
    df["axis_z"] = z(df["axis_slope"].values)
    df["wolse_z"] = z(df["wolse_slope"].values)
    df["composite_z"] = df["axis_z"] + df["wolse_z"] if include_wolse else df["axis_z"]
    return df.sort_values("composite_z", ascending=False).reset_index(drop=True)


def print_loo_summary(loo: list[LOOResult], title: str, n_controls: int) -> None:
    print(f"\n{title}")
    print("-" * 60)
    print(f"{'held-out':<14} {'slope':>8}  {'rank/'+str(n_controls+1):>7}  {'AUC-pair':>9}")
    for r in loo:
        print(f"{r.held_out:<14} {r.held_out_slope:>+8.4f}  "
              f"{r.rank_among_controls:>3} / {n_controls+1}  {r.auc_pair:>9.2f}")
    if not loo:
        print("(no cases in this group)")
        return
    p_at_1 = sum(1 for r in loo if r.rank_among_controls == 1) / len(loo)
    mean_auc = sum(r.auc_pair for r in loo) / len(loo)
    print("-" * 60)
    print(f"precision@1 = {p_at_1:.2f}    mean AUC-pair = {mean_auc:.2f}")


def print_ranking(df: pd.DataFrame, include_wolse: bool) -> None:
    title = ("Composite ranking (axis_z + wolse_z)" if include_wolse
             else "Ranking by axis_z  [wolse shown for diagnostic only — MOCKED]")
    print(f"\n{title}")
    print("-" * 70)
    print(f"{'rank':>4}  {'dong':<14} {'label':<13} {'axis_z':>7} {'wolse_z*':>9} {'rank_z':>8}")
    for i, r in df.iterrows():
        print(f"{i + 1:>4}  {r['name_roman']:<14} {r['label']:<13} "
              f"{r['axis_z']:>+7.2f} {r['wolse_z']:>+9.2f} {r['composite_z']:>+8.2f}")


# ─── Model panel assembly ────────────────────────────────────────────────

def load_redev_panel() -> pd.DataFrame | None:
    """Load the cached national_redevelopment_intensity parquet built by
    `molit_redev_client.build_national_redev_panel`. Returns None (with a
    warning) if the parquet is missing — the prototype's core LOO analysis
    does not depend on this control, so missing redev should not abort
    the run. Rebuild via `molit_redev_client fetch-raw` + the build
    function in that module."""
    if not REDEV_PANEL_PATH.exists():
        print(f"  WARNING: {REDEV_PANEL_PATH.name} missing — model panel will "
              "be built without national_redevelopment_intensity_* columns. "
              "Build with molit_redev_client.fetch_table_raw + "
              "build_national_redev_panel.", file=sys.stderr)
        return None
    df = pd.read_parquet(REDEV_PANEL_PATH)
    # Cast year to match the prototype's caches so the join doesn't widen.
    df["year"] = df["year"].astype("int16")
    return df


def load_unsold_panel() -> pd.DataFrame | None:
    """Load the cached gu-level StatNuri unsold-housing panel built by
    `molit_unsold_client.build_seoul_unsold_panel` + `aggregate_to_annual`.
    Returns None (with a warning) if the parquet is missing — the core
    LOO does not depend on it, so missing unsold should not abort the
    run. Rebuild via `molit_unsold_client.fetch_unsold_raw` over the
    desired YYYYMM range, then the build + aggregate functions in that
    module."""
    if not UNSOLD_PANEL_PATH.exists():
        print(f"  WARNING: {UNSOLD_PANEL_PATH.name} missing — model panel "
              "will be built without statnuri_unsold_* columns. "
              "Build with molit_unsold_client.fetch_unsold_raw + "
              "build_seoul_unsold_panel + aggregate_to_annual.",
              file=sys.stderr)
        return None
    df = pd.read_parquet(UNSOLD_PANEL_PATH)
    df["year"] = df["year"].astype("int16")
    df["lawd_cd"] = df["lawd_cd"].astype("string")
    return df


def build_model_panel(cases: pd.DataFrame, emb_df: pd.DataFrame,
                      wolse_df: pd.DataFrame, axis: np.ndarray,
                      redev_panel: pd.DataFrame | None,
                      unsold_panel: pd.DataFrame | None,
                      embed_mode: str, wolse_source: str) -> pd.DataFrame:
    """Assemble the derived (dong_code, year) model panel.

    Columns:
      dong_code, name_roman, label, lawd_cd, year
      <whatever wolse columns are present in wolse_df>
      A00..A63 (raw embedding centroids)
      axis_projection (scalar embedding · axis for that row)
      national_redevelopment_intensity_* (joined by year only — same value
                                          for every dong within a year,
                                          which is correct: this is a
                                          NATIONAL control, not local)
      statnuri_unsold_{mean,max,dec}_units (joined by lawd_cd + year —
                                           gu-level housing-market stress
                                           proxy; NOT a tenure signal)
      embed_mode, wolse_source (provenance — auditable from the panel alone)
    """
    from molit_client import lawd_cd_from_dong_code
    if "lawd_cd" not in cases.columns:
        raise ValueError(
            "labeled_cases.csv must carry an explicit `lawd_cd` column "
            "for gu-level joins. Derive from the `gu` Korean column with "
            "a canonical Seoul map; do not infer from dong_code (some "
            "labeled rows have inconsistent dong_code values — see "
            "Ikseon).")
    # Surface any case whose CSV-stated lawd_cd differs from the
    # dong_code-derived value. This is a one-line data-QA note printed
    # every run so that overrides (currently: Ikseon) stay visible
    # rather than silently propagating.
    derived = cases["dong_code"].map(lawd_cd_from_dong_code).astype("string")
    csv_lawd = cases["lawd_cd"].astype("string")
    mism = cases[derived.values != csv_lawd.values]
    if not mism.empty:
        details = [f"{r.name_roman}: dong_code={r.dong_code} -> "
                   f"derived {lawd_cd_from_dong_code(r.dong_code)}, "
                   f"CSV gu={r.gu} -> lawd_cd={r.lawd_cd}"
                   for r in mism.itertuples()]
        print(f"  [data-QA] {len(mism)} case(s) override the dong_code-"
              f"derived gu via the CSV `lawd_cd` column:")
        for d in details:
            print(f"    {d}")

    base = (cases[["dong_code", "name_roman", "label", "lawd_cd"]]
            .merge(pd.DataFrame({"year": pd.array(YEARS, dtype="int16")}),
                   how="cross"))
    base["lawd_cd"] = base["lawd_cd"].astype("string")
    panel = base.merge(wolse_df, on=["dong_code", "year"], how="left")
    panel = panel.merge(emb_df, on=["dong_code", "year"], how="left")
    panel["axis_projection"] = (panel[EMBED_COLS].values @ axis).astype("float32")
    if redev_panel is not None:
        renamed = redev_panel.rename(columns=REDEV_COLUMN_RENAME)
        panel = panel.merge(renamed, on="year", how="left")
    if unsold_panel is not None:
        # gu_name from the unsold panel is informational and would shadow
        # any case-level gu label, so drop it before the merge.
        keep = ["lawd_cd", "year", "statnuri_unsold_mean_units",
                "statnuri_unsold_max_units", "statnuri_unsold_dec_units"]
        panel = panel.merge(unsold_panel[keep], on=["lawd_cd", "year"],
                            how="left")
    panel["embed_mode"] = embed_mode
    panel["wolse_source"] = wolse_source
    return panel


# ─── CLI ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 on stdout/stderr so non-ASCII characters in the ranking
    # output (em-dashes, Korean dong names, etc.) don't crash the run on
    # Windows consoles defaulting to cp949. No-op on platforms already
    # using UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--mode", choices=["mock", "ee"], default="mock",
                    help="embedding source: synthetic (default) or live Earth Engine")
    ap.add_argument("--wolse-source", choices=["mock", "molit"], default="mock",
                    help="wolse source: synthetic (default) or live MOLIT (data.go.kr); "
                         "molit requires MOLIT_SERVICE_KEY env var and a dong_name_kr "
                         "column in labeled_cases.csv. Independent of --mode.")
    ap.add_argument("--gcp-project", default=None,
                    help="GCP project id (required for --mode ee)")
    ap.add_argument("--service-account-key",
                    default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
                    help="path to Earth Engine service-account JSON key; "
                         "may also be set via GOOGLE_APPLICATION_CREDENTIALS")
    ap.add_argument("--rebuild-cache", action="store_true",
                    help="delete cached embeddings/wolse parquet files and regenerate")
    args = ap.parse_args(argv)

    DATA.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

    if args.rebuild_cache:
        for p in (embed_cache(args.mode), wolse_cache(args.wolse_source)):
            p.unlink(missing_ok=True)

    cases = load_cases()
    write_polygons_if_absent(cases)
    emb_df = get_embeddings(cases, args.mode, args.gcp_project, args.service_account_key)
    wolse_df = get_wolse(cases, args.wolse_source)
    wolse_is_mock = (args.wolse_source == "mock")

    n_active = (cases["label"] == "active_panel").sum()
    n_post = (cases["label"] == "post_peak").sum()
    n_ctrl = (cases["label"] == "control").sum()
    print(f"Loaded {len(cases)} cases  "
          f"({n_active} active_panel, {n_post} post_peak, {n_ctrl} controls)")
    print(f"Embeddings: {len(emb_df)} rows  ({args.mode} mode)")
    print(f"Wolse: {len(wolse_df)} rows  (source={args.wolse_source})")
    if args.mode == "mock":
        print("  [scaffold check] mock data has a planted shared drift direction;\n"
              "  perfect LOO is expected and is NOT empirical validation.")
    if wolse_is_mock:
        print("  [scaffold check] wolse is mocked "
              "(excluded from composite ranking by default).")
    elif "n_rent_deals" in wolse_df.columns:
        n_deals = int(wolse_df["n_rent_deals"].sum())
        print(f"  MOLIT panel: {n_deals} total rent deals "
              f"across {wolse_df['dong_code'].nunique()} dongs.")

    # Axis learned from active_panel cases only (the within-panel hypothesis)
    active_cases = cases[cases["label"] == AXIS_TRAIN_LABEL]
    if len(active_cases) < 2:
        print(f"  WARNING: only {len(active_cases)} active_panel cases — LOO "
              "trains on a single delta vector per fold.")
    axis = learn_axis(emb_df, active_cases)
    print(f"Learned gentrification axis (n={len(active_cases)} active_panel cases): "
          f"||a|| = {np.linalg.norm(axis):.4f}  (unit-normalised)")

    # Leave-one-out validation on active_panel cases
    loo = leave_one_out(cases, emb_df)
    print_loo_summary(loo, "Leave-one-out validation  [active_panel vs controls]", n_ctrl)

    # Separate evaluation: project post_peak cases onto the full active_panel axis
    post_peak_eval = evaluate_post_peak(cases, emb_df, axis)
    print_loo_summary(
        post_peak_eval,
        "Post-peak morphology evaluation  [post_peak vs controls, full active_panel axis]",
        n_ctrl,
    )

    # Composite ranking — axis only (wolse mocked, see prior note)
    rank_df = composite_ranking(emb_df, wolse_df, cases, axis, include_wolse=False)
    print_ranking(rank_df, include_wolse=False)

    # Plots
    plot_trajectories(emb_df, cases, axis, args.mode, OUT / "trajectories.png")
    plot_projection_scatter(emb_df, wolse_df, cases, axis, args.mode, wolse_is_mock,
                            OUT / "projection_scatter.png")
    print(f"\nPlots written:\n  {OUT / 'trajectories.png'}\n  {OUT / 'projection_scatter.png'}")

    # Derived model panel — joined (dong, year) table with national redev
    # control and gu-level StatNuri unsold-housing stress control.
    redev_panel = load_redev_panel()
    unsold_panel = load_unsold_panel()
    model_panel = build_model_panel(
        cases, emb_df, wolse_df, axis, redev_panel, unsold_panel,
        embed_mode=args.mode, wolse_source=args.wolse_source,
    )
    model_panel.to_parquet(MODEL_PANEL_PATH, index=False)
    n_redev_cols = sum(c.startswith("national_redevelopment_intensity_")
                       for c in model_panel.columns)
    n_unsold_cols = sum(c.startswith("statnuri_unsold_")
                        for c in model_panel.columns)
    print(f"Model panel written: {MODEL_PANEL_PATH}  "
          f"rows={len(model_panel)}  cols={len(model_panel.columns)}  "
          f"redev_cols={n_redev_cols}  unsold_cols={n_unsold_cols}  "
          f"embed_mode={args.mode}  wolse_source={args.wolse_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
