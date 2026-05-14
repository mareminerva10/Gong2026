"""
audit_2022_artifact.py
======================

Diagnostic audit for the suspected universal 2022 YoY peak in AlphaEarth
satellite embeddings across Seoul's labeled dongs.

Question
--------
Is the 2022 peak a Google AlphaEarth pipeline artifact, a Seoul-wide urban
event, or specific to the labeled gentrified neighborhoods?

Decision rule (printed at end of run, applied to the three cuts below)
---------------------------------------------------------------------
- peak in random Seoul AND comparison cities  -> AlphaEarth pipeline artifact
- peak in Seoul (all) but not comparison      -> Seoul-wide urban event
- peak in labeled Seoul subset only           -> axis is directionally real
- peak vanishes under angular / cosine but
  persists in Euclidean                       -> stop using raw Euclidean YoY

Distance metrics per (polygon, year-pair)
-----------------------------------------
- angular      : arccos( a.b / (||a|| ||b||) )    primary
- cosine_dist  : 1 - a.b / (||a|| ||b||)          primary
- euclid       : ||a - b||                        secondary

Run
---
    python audit_2022_artifact.py --gcp-project YOUR_PROJECT
    python audit_2022_artifact.py --gcp-project YOUR_PROJECT --n-random-seoul 50 --n-random-city 30
    python audit_2022_artifact.py --gcp-project YOUR_PROJECT --no-comparison

Outputs
-------
- data/audit_cache/{poly_id}_{year}.parquet   (per-call chunks, resumable)
- data/alphaearth_2022_audit.parquet          (aggregated YoY metrics)
- outputs/alphaearth_2022_audit.png           (4-panel diagnostic)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from shapely.geometry import box, mapping

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "outputs"
AUDIT_CACHE = DATA / "audit_cache"
AUDIT_OUT_PARQUET = DATA / "alphaearth_2022_audit.parquet"
AUDIT_OUT_PNG = OUT / "alphaearth_2022_audit.png"

YEARS = list(range(2017, 2025))
EMBED_DIM = 64
EMBED_COLS = [f"A{i:02d}" for i in range(EMBED_DIM)]
BOX_HALF_DEG = 0.005           # ~0.5 km half-side, matches prototype.py
SEED = 42

# Approximate city bounding boxes for random polygon sampling.
# Format: (lat_min, lat_max, lon_min, lon_max)
CITY_BBOX = {
    "Seoul":  (37.46, 37.68, 126.83, 127.15),
    "Tokyo":  (35.55, 35.82, 139.55, 139.90),
    "Osaka":  (34.60, 34.75, 135.40, 135.62),
    "Taipei": (24.98, 25.16, 121.48, 121.62),
}

COMPARISON_CITIES = ["Tokyo", "Osaka", "Taipei"]

# Year-pair label for the suspect pair, used in every summary.
SUSPECT_PAIR = "2021-2022"


# --- Polygon construction ------------------------------------------------

def load_labeled_cases() -> pd.DataFrame:
    csv = DATA / "labeled_cases.csv"
    if not csv.exists():
        sys.exit(f"missing {csv}")
    return pd.read_csv(csv)


def random_polygons(city: str, n: int, seed: int) -> list[dict]:
    lat_lo, lat_hi, lon_lo, lon_hi = CITY_BBOX[city]
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        lat = float(rng.uniform(lat_lo, lat_hi))
        lon = float(rng.uniform(lon_lo, lon_hi))
        poly = box(lon - BOX_HALF_DEG, lat - BOX_HALF_DEG,
                   lon + BOX_HALF_DEG, lat + BOX_HALF_DEG)
        out.append({
            "poly_id": f"{city}_r{i:03d}",
            "city": city,
            "kind": "random",
            "lat": lat,
            "lon": lon,
            "geometry": mapping(poly),
        })
    return out


def labeled_polygons(cases: pd.DataFrame) -> list[dict]:
    out = []
    for r in cases.itertuples():
        poly = box(r.lon - BOX_HALF_DEG, r.lat - BOX_HALF_DEG,
                   r.lon + BOX_HALF_DEG, r.lat + BOX_HALF_DEG)
        out.append({
            "poly_id": f"Seoul_lbl_{r.dong_code}",
            "city": "Seoul",
            "kind": f"labeled_{r.label}",
            "lat": r.lat,
            "lon": r.lon,
            "geometry": mapping(poly),
        })
    return out


def bucket_of(city: str, kind: str) -> str:
    """Coarse grouping used in summary tables and plots."""
    if city != "Seoul":
        return f"{city}/random"
    if kind == "random":
        return "Seoul/random"
    return f"Seoul/{kind.replace('labeled_', '')}"


# --- Earth Engine extraction ---------------------------------------------

def initialize_ee(gcp_project: str, service_account_key: str | None) -> None:
    import ee
    if service_account_key:
        info = json.loads(Path(service_account_key).read_text(encoding="utf-8"))
        creds = ee.ServiceAccountCredentials(info["client_email"], service_account_key)
        ee.Initialize(creds, project=gcp_project)
    else:
        ee.Initialize(project=gcp_project)


def fetch_one(poly_geom: dict, year: int) -> np.ndarray | None:
    """Mean AlphaEarth embedding over one polygon for one annual mosaic.

    TODO: For samples > ~150 polygons, replace this per-(polygon, year)
    reduceRegion().getInfo() pattern with per-year reduceRegions() over a
    FeatureCollection (or an Earth Engine Task export to Cloud Storage /
    Drive followed by a local table read). At audit scale (~1k calls) the
    current pattern is acceptable; at production scale it is a quota and
    latency bottleneck.
    """
    import ee
    geom = ee.Geometry(poly_geom)
    coll = (ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
            .select(EMBED_COLS)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .filterBounds(geom))
    img = coll.mosaic()
    stats = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=geom,
                              scale=10, maxPixels=1e8).getInfo()
    if not stats or stats.get("A00") is None:
        return None
    return np.array([float(stats.get(b, 0.0)) for b in EMBED_COLS], dtype="float32")


def fetch_embeddings(polys: list[dict], years: list[int],
                     offline: bool = False) -> pd.DataFrame:
    """Pull or load AlphaEarth annual means for each (polygon, year).

    Per-call results are cached as Parquet under AUDIT_CACHE/, so partial runs
    survive restarts. offline=True skips the network entirely and only
    returns what's already cached -- (poly, year) pairs without a cache file
    are reported as missing per bucket.
    """
    if not offline:
        AUDIT_CACHE.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    missing_by_bucket: dict[str, int] = {}
    total = len(polys) * len(years)
    pulled = 0
    cached = 0
    for p in polys:
        bucket = bucket_of(p["city"], p["kind"])
        for y in years:
            cache = AUDIT_CACHE / f"{p['poly_id']}_{y}.parquet"
            if cache.exists():
                rows.append(pd.read_parquet(cache).iloc[0].to_dict())
                cached += 1
                continue
            if offline:
                missing_by_bucket[bucket] = missing_by_bucket.get(bucket, 0) + 1
                continue
            vec = fetch_one(p["geometry"], y)
            if vec is None:
                missing_by_bucket[bucket] = missing_by_bucket.get(bucket, 0) + 1
                print(f"  ! no embedding {p['poly_id']} {y}", file=sys.stderr)
                continue
            rec = {"poly_id": p["poly_id"], "city": p["city"],
                   "kind": p["kind"], "year": int(y),
                   **dict(zip(EMBED_COLS, vec.tolist()))}
            pd.DataFrame([rec]).to_parquet(cache, index=False)
            rows.append(rec)
            pulled += 1
            if pulled % 25 == 0:
                print(f"  ... pulled {pulled}/{total}")
            time.sleep(0.05)
    print(f"  embeddings: {pulled} fresh, {cached} cached, "
          f"{sum(missing_by_bucket.values())} missing  (expected {total})")
    if missing_by_bucket:
        print("  missing by bucket:")
        for b, n in sorted(missing_by_bucket.items()):
            print(f"    {b:<24} {n}")
    return pd.DataFrame(rows)


# --- YoY distance metrics -------------------------------------------------

def _unit(v: np.ndarray) -> np.ndarray:
    """L2-normalize a vector. Returns the zero vector unchanged."""
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def yoy_distances(emb_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (poly_id, year_pair) with angular / cosine / Euclidean.

    Vectors are L2-normalized before computing angular and cosine. AlphaEarth
    pixel embeddings are designed for cosine comparison, but a polygon mean
    over many pixels is NOT guaranteed to remain unit-length, so without
    explicit normalization the angular metric would silently confound
    direction change with magnitude drift. Euclidean is intentionally left on
    the raw (un-normalized) mean vectors so it captures magnitude drift the
    directional metrics ignore -- that divergence is one branch of the
    decision rule in print_decision().
    """
    out: list[dict] = []
    for pid, sub in emb_df.groupby("poly_id"):
        sub = sub.sort_values("year")
        vecs = sub[EMBED_COLS].to_numpy("float32")
        years = sub["year"].to_numpy()
        city = sub["city"].iloc[0]
        kind = sub["kind"].iloc[0]
        for i in range(len(years) - 1):
            a, b = vecs[i], vecs[i + 1]
            a_n, b_n = _unit(a), _unit(b)
            cos = float(np.clip(np.dot(a_n, b_n), -1.0, 1.0))
            out.append({
                "poly_id": pid,
                "city": city,
                "kind": kind,
                "bucket": bucket_of(city, kind),
                "year_from": int(years[i]),
                "year_to": int(years[i + 1]),
                "year_pair": f"{years[i]}-{years[i + 1]}",
                "angular": float(np.arccos(cos)),
                "cosine_dist": float(1.0 - cos),
                "euclid": float(np.linalg.norm(a - b)),
            })
    return pd.DataFrame(out)


# --- Per-bucket summary ---------------------------------------------------

@dataclass
class BucketVerdict:
    bucket: str
    n_polys: int
    share_max_is_suspect: float
    angular_suspect_med: float
    angular_other_med: float
    angular_ratio: float
    cosine_suspect_med: float
    cosine_other_med: float
    cosine_ratio: float
    euclid_suspect_med: float
    euclid_other_med: float
    euclid_ratio: float


def _ratio(a: float, b: float) -> float:
    return a / b if b > 1e-12 else float("nan")


def summarize(yoy: pd.DataFrame) -> list[BucketVerdict]:
    verdicts: list[BucketVerdict] = []
    for bucket, sub in yoy.groupby("bucket"):
        polys = sub["poly_id"].unique()
        # Find each polygon's hottest year-pair (by angular distance).
        idx = sub.groupby("poly_id")["angular"].idxmax()
        max_pair = sub.loc[idx, ["poly_id", "year_pair"]]
        share = float((max_pair["year_pair"] == SUSPECT_PAIR).mean())

        is_suspect = sub["year_pair"] == SUSPECT_PAIR
        med = lambda mask, col: float(sub.loc[mask, col].median()) if mask.any() else float("nan")

        ang_s, ang_o = med(is_suspect, "angular"), med(~is_suspect, "angular")
        cos_s, cos_o = med(is_suspect, "cosine_dist"), med(~is_suspect, "cosine_dist")
        euc_s, euc_o = med(is_suspect, "euclid"), med(~is_suspect, "euclid")

        verdicts.append(BucketVerdict(
            bucket=bucket, n_polys=len(polys),
            share_max_is_suspect=share,
            angular_suspect_med=ang_s, angular_other_med=ang_o,
            angular_ratio=_ratio(ang_s, ang_o),
            cosine_suspect_med=cos_s, cosine_other_med=cos_o,
            cosine_ratio=_ratio(cos_s, cos_o),
            euclid_suspect_med=euc_s, euclid_other_med=euc_o,
            euclid_ratio=_ratio(euc_s, euc_o),
        ))
    verdicts.sort(key=lambda v: v.bucket)
    return verdicts


# --- Plotting -------------------------------------------------------------

PALETTE = {
    "Seoul/active_panel": "#d23",
    "Seoul/post_peak":    "#f08838",
    "Seoul/control":      "#3a6",
    "Seoul/random":       "#888",
    "Tokyo/random":       "#39e",
    "Osaka/random":       "#a3f",
    "Taipei/random":      "#0bd",
}


def _color(bucket: str) -> str:
    return PALETTE.get(bucket, "#444")


def plot_audit(yoy: pd.DataFrame, out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 10))
    metrics = [("angular", "Angular distance (rad)"),
               ("cosine_dist", "Cosine distance (1 - cos)"),
               ("euclid", "Euclidean distance")]
    year_pairs = sorted(yoy["year_pair"].unique())
    buckets = sorted(yoy["bucket"].unique())
    suspect_x = year_pairs.index(SUSPECT_PAIR) if SUSPECT_PAIR in year_pairs else None

    for ax, (col, label) in zip(axes.flatten()[:3], metrics):
        for bucket in buckets:
            sub = yoy[yoy["bucket"] == bucket]
            medians = [float(sub.loc[sub["year_pair"] == yp, col].median())
                       for yp in year_pairs]
            ax.plot(year_pairs, medians, marker="o", color=_color(bucket),
                    label=bucket, lw=1.5, alpha=0.9, ms=5)
        if suspect_x is not None:
            ax.axvline(suspect_x, color="black", lw=0.6, alpha=0.35, ls="--")
        ax.set_xticks(range(len(year_pairs)))
        ax.set_xticklabels(year_pairs, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel(label)
        ax.set_title(f"Median YoY {col} by year-pair, per bucket")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="best", ncol=2, framealpha=0.85)

    # Panel D: share of polygons whose max-YoY is the suspect pair, by bucket.
    ax = axes.flatten()[3]
    idx = yoy.groupby("poly_id")["angular"].idxmax()
    max_pair_per_poly = yoy.loc[idx, ["poly_id", "bucket", "year_pair"]]
    share = (max_pair_per_poly.groupby("bucket")["year_pair"]
             .apply(lambda s: (s == SUSPECT_PAIR).mean())
             .reindex(buckets))
    bar_colors = [_color(b) for b in share.index]
    ax.bar(range(len(share)), share.values, color=bar_colors,
           edgecolor="white", linewidth=1)
    ax.set_xticks(range(len(share)))
    ax.set_xticklabels(share.index, rotation=30, ha="right", fontsize=8)
    chance = 1.0 / (len(YEARS) - 1)
    ax.axhline(chance, color="black", lw=0.7, alpha=0.5, ls="--",
               label=f"chance = 1/{len(YEARS) - 1} = {chance:.2f}")
    ax.set_ylabel(f"Share with max-YoY = {SUSPECT_PAIR}")
    ax.set_title("Concentration of the 2022 peak (angular distance)")
    ax.set_ylim(0, max(0.5, float(share.max()) * 1.15 if share.notna().any() else 1.0))
    ax.legend(fontsize=8)

    fig.suptitle("AlphaEarth 2022 YoY artifact audit", fontsize=13, y=0.995)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


# --- Console output -------------------------------------------------------

def print_table(verdicts: list[BucketVerdict]) -> None:
    print(f"\nPer-bucket YoY summary  (suspect year-pair: {SUSPECT_PAIR}; "
          f"chance share = {1 / (len(YEARS) - 1):.2f})")
    print("-" * 110)
    print(f"{'bucket':<24} {'N':>3}  "
          f"{'share max=21-22':>15}  "
          f"{'ang 21-22':>10}  {'ang oth':>9}  {'a-ratio':>7}  "
          f"{'cos 21-22':>10}  {'cos oth':>9}  {'c-ratio':>7}  "
          f"{'euc-ratio':>9}")
    for v in verdicts:
        print(f"{v.bucket:<24} {v.n_polys:>3}  "
              f"{v.share_max_is_suspect:>15.2f}  "
              f"{v.angular_suspect_med:>10.4f}  {v.angular_other_med:>9.4f}  "
              f"{v.angular_ratio:>7.2f}  "
              f"{v.cosine_suspect_med:>10.4f}  {v.cosine_other_med:>9.4f}  "
              f"{v.cosine_ratio:>7.2f}  "
              f"{v.euclid_ratio:>9.2f}")
    print("-" * 110)


def print_decision(verdicts: list[BucketVerdict]) -> None:
    by = {v.bucket: v for v in verdicts}
    chance = 1.0 / (len(YEARS) - 1)
    HOT = chance * 2.0  # 2x chance level = bucket is "hot" on the suspect pair

    seoul_buckets = [b for b in by if b.startswith("Seoul/")]
    seoul_labeled = [by[b] for b in seoul_buckets if b != "Seoul/random"]
    seoul_random = by.get("Seoul/random")
    comparison = [by[b] for b in by if not b.startswith("Seoul/")]

    seoul_lab_hot = any(v.share_max_is_suspect > HOT for v in seoul_labeled)
    seoul_rand_hot = seoul_random is not None and seoul_random.share_max_is_suspect > HOT
    comp_hot = any(v.share_max_is_suspect > HOT for v in comparison)

    angular_quiet_euclid_loud = any(
        v.angular_ratio < 1.2 and v.euclid_ratio > 1.5
        for v in verdicts
    )

    print("\n[decision]")
    print(f"  threshold for 'hot' on suspect pair: share > {HOT:.2f}  "
          f"(2x chance = {chance:.2f})")

    if angular_quiet_euclid_loud:
        print("  - The 2022 peak appears in EUCLIDEAN distance but NOT in "
              "angular/cosine.")
        print("    -> Stop using raw Euclidean YoY magnitude. Use angular or "
              "cosine for change detection.")

    if seoul_rand_hot and comp_hot:
        print("  - 2022 peak appears in random Seoul AND in comparison cities.")
        print("    -> Likely an AlphaEarth pipeline artifact. Either exclude "
              "the 2022 transition from the learned axis, or add a year fixed "
              "effect at the centroid level before computing the within-panel drift.")
    elif seoul_rand_hot and not comp_hot:
        print("  - 2022 peak appears Seoul-wide (random + labeled) but NOT in "
              "comparison cities.")
        print("    -> Investigate a Seoul-specific event (COVID-19 mobility "
              "rebound, planning ordinance, satellite tasking change). The "
              "axis can still be used but the 2022 contribution needs "
              "interpretation, not exclusion.")
    elif seoul_lab_hot and not seoul_rand_hot and not comp_hot:
        print("  - 2022 peak is concentrated in labeled Seoul cases only.")
        print("    -> Axis is at least directionally real on the labeled set. "
              "The next question is mechanism specificity (active_panel vs "
              "post_peak vs control).")
    elif not seoul_lab_hot and not seoul_rand_hot and not comp_hot:
        print("  - No bucket exceeds the hot threshold for the suspect pair.")
        print("    -> The 2022 peak observed in prior runs does not survive "
              "the per-bucket aggregation. Re-check the original Hwagok-audit "
              "logic that flagged it.")
    else:
        print("  - Inconclusive. See per-bucket table above and the plot for "
              "year-pair trajectories.")


# --- CLI ------------------------------------------------------------------

def parse_argv(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="AlphaEarth 2022 YoY artifact audit")
    ap.add_argument("--gcp-project", default=None, required=False,
                    help="GCP project id for Earth Engine. Required unless --offline.")
    ap.add_argument("--service-account-key",
                    default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
                    help="Earth Engine service-account JSON path; may also be set via "
                         "GOOGLE_APPLICATION_CREDENTIALS")
    ap.add_argument("--n-random-seoul", type=int, default=50,
                    help="random Seoul polygons (default 50)")
    ap.add_argument("--n-random-city", type=int, default=30,
                    help="random polygons per comparison city (default 30)")
    ap.add_argument("--no-comparison", action="store_true",
                    help="skip Tokyo/Osaka/Taipei comparison sample")
    ap.add_argument("--offline", action="store_true",
                    help="re-run summary from cached data; skip EE entirely")
    return ap.parse_args(argv)


def build_polygon_set(args: argparse.Namespace) -> list[dict]:
    cases = load_labeled_cases()
    polys = labeled_polygons(cases)
    polys += random_polygons("Seoul", args.n_random_seoul, seed=SEED)
    if not args.no_comparison:
        for i, city in enumerate(COMPARISON_CITIES):
            polys += random_polygons(city, args.n_random_city, seed=SEED + 1 + i)
    return polys


def main(argv: list[str] | None = None) -> int:
    args = parse_argv(argv)
    DATA.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

    polys = build_polygon_set(args)
    n_by_kind: dict[str, int] = {}
    for p in polys:
        n_by_kind[bucket_of(p["city"], p["kind"])] = n_by_kind.get(
            bucket_of(p["city"], p["kind"]), 0) + 1
    print("Polygon set:")
    for b, n in sorted(n_by_kind.items()):
        print(f"  {b:<24} {n}")

    if not args.offline:
        if not args.gcp_project:
            sys.exit("EE fetch needs --gcp-project (or pass --offline to use cache only)")
        print(f"Initializing Earth Engine with project={args.gcp_project} ...")
        initialize_ee(args.gcp_project, args.service_account_key)
    else:
        print("Offline mode: assembling from cache only.")
    emb_df = fetch_embeddings(polys, YEARS, offline=args.offline)

    if emb_df.empty:
        sys.exit("no embeddings available; nothing to audit")

    yoy = yoy_distances(emb_df)
    yoy.to_parquet(AUDIT_OUT_PARQUET, index=False)
    print(f"YoY metrics: {len(yoy)} rows  ->  {AUDIT_OUT_PARQUET}")

    verdicts = summarize(yoy)
    print_table(verdicts)

    plot_audit(yoy, AUDIT_OUT_PNG)
    print(f"Plot written: {AUDIT_OUT_PNG}")

    print_decision(verdicts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
