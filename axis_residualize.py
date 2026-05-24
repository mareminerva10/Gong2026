"""
axis_residualize.py
===================

Common-mode adjustment of Seoul AlphaEarth embeddings using foreign
anchor cities, intended to be paired with prototype.py's axis learner.

Motivation
----------
The 2022 artifact audit (full N=30, 2026-05-15) found that the
year-pair 2021→2022 is a *hot* common-mode shift in random Seoul AND
Osaka polygons, but NOT in random Tokyo or Taipei polygons. That
identifies 2022 as a non-Seoul-specific AlphaEarth or sensor shift in
the region. Using Tokyo + Taipei as a clean geographic anchor set, we
subtract the cumulative year-by-year anchor drift (relative to a
baseline year) from every Seoul dong embedding. The Seoul-specific
component of drift is preserved; the regional common-mode is removed.

Math (per dim, applied to all 64 dims independently)
----------------------------------------------------
Let E(dong, y) be the raw embedding, and A(c, p, y) be one anchor
polygon's embedding at year y. The anchor offset relative to
baseline year y0 is:

    offset(y) = mean_{c in cities, p in polys(c)} A(c, p, y)
                - mean_{c in cities, p in polys(c)} A(c, p, y0)

so offset(y0) = 0 by construction. The residualized Seoul embedding is:

    E_tilde(dong, y) = E(dong, y) - offset(y)

This is the closed form of "subtract the year-to-year anchor delta at
every transition, baselining at y0". It is a *year fixed effect in
embedding space*, estimated from clean foreign anchors rather than
from the Seoul panel itself.

Cleanly fails closed
--------------------
- load_anchor_embeddings raises if no anchor cache exists for the
  requested cities (no silent zero offsets).
- residualize raises if any Seoul row's year is missing from the
  anchor offset table.
- Output rows preserve dong_code/year and use float32 for embedding
  columns, matching prototype.py's `alphaearth_ee.parquet` schema.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
AUDIT_CACHE = DATA / "audit_cache"

EMBED_DIM = 64
EMBED_COLS = [f"A{i:02d}" for i in range(EMBED_DIM)]
DEFAULT_ANCHOR_CITIES: tuple[str, ...] = ("Tokyo", "Taipei")


def load_anchor_embeddings(
        cities: Iterable[str] = DEFAULT_ANCHOR_CITIES,
        audit_cache: Path = AUDIT_CACHE,
) -> pd.DataFrame:
    """Load all cached random-polygon embeddings for the given anchor
    cities. Returns columns: poly_id, city, year, A00..A63.

    Raises FileNotFoundError if no matching cache files exist — refusing
    to silently produce a zero-offset adjustment that would look like a
    residualization but do nothing.
    """
    cities = tuple(cities)
    rows: list[pd.DataFrame] = []
    for f in sorted(audit_cache.glob("*.parquet")):
        # Audit-cache filenames are "{poly_id}_{year}.parquet" and
        # poly_id for random polygons is "{city}_r{NNN}".
        if not any(f.name.startswith(f"{c}_r") for c in cities):
            continue
        rows.append(pd.read_parquet(f))
    if not rows:
        raise FileNotFoundError(
            f"no cached anchor embeddings for cities={cities} under "
            f"{audit_cache}. Run audit_2022_artifact.py first.")
    df = pd.concat(rows, ignore_index=True)
    keep = ["poly_id", "city", "year", *EMBED_COLS]
    missing = set(keep) - set(df.columns)
    if missing:
        raise ValueError(
            f"anchor cache rows missing columns: {sorted(missing)}. "
            f"Available: {sorted(df.columns)}")
    return df[keep].copy()


def compute_anchor_offsets(
        anchor_df: pd.DataFrame,
        baseline_year: int = 2017,
) -> pd.DataFrame:
    """Mean anchor embedding per year, then per-year offset relative
    to `baseline_year` so offset[baseline_year] = 0. Pooling treats
    each polygon equally regardless of which anchor city it comes from
    (equal N per city in the audit set, so this matches an equal
    per-city weighting).

    Returns a (n_years, 64) DataFrame indexed by year.
    """
    if baseline_year not in anchor_df["year"].values:
        raise ValueError(
            f"baseline_year={baseline_year} is absent from anchor cache. "
            f"Available years: {sorted(anchor_df['year'].unique())}")
    mean_per_year = (anchor_df.groupby("year")[EMBED_COLS]
                     .mean()
                     .sort_index())
    baseline = mean_per_year.loc[baseline_year]
    return (mean_per_year - baseline).astype("float32")


def residualize(
        emb_df: pd.DataFrame,
        anchor_offsets: pd.DataFrame,
) -> pd.DataFrame:
    """Subtract the year-matched anchor offset from each Seoul row.

    Preserves all non-embedding columns and casts embedding columns to
    float32 to match prototype.py's `alphaearth_ee.parquet` schema.
    Raises ValueError if any Seoul row has a year missing from the
    anchor offset table (rather than producing a silently NaN row).
    """
    needed_years = set(emb_df["year"].unique())
    missing = needed_years - set(anchor_offsets.index)
    if missing:
        raise ValueError(
            f"anchor_offsets is missing year(s) present in emb_df: "
            f"{sorted(missing)}. Anchor years: "
            f"{sorted(anchor_offsets.index)}")
    df = emb_df.copy()
    offsets_arr = anchor_offsets.loc[df["year"].to_numpy(), EMBED_COLS].to_numpy()
    df[EMBED_COLS] = (df[EMBED_COLS].to_numpy() - offsets_arr).astype("float32")
    return df


def year_pair_contributions(
        emb_df: pd.DataFrame,
        axis: np.ndarray,
        active_cases: pd.DataFrame,
) -> pd.DataFrame:
    """For each consecutive year-pair (y, y+1), the mean (across
    active_panel cases) of cos(ΔE(case, y→y+1), axis). High |cosine|
    means the axis is heavily aligned with that year's transition — a
    diagnostic for the third acceptance criterion ("2021-22 no longer
    dominates the learned direction").
    """
    years = sorted(emb_df["year"].unique())
    rows: list[dict] = []
    axis_unit = axis / (np.linalg.norm(axis) + 1e-12)
    for y0, y1 in zip(years[:-1], years[1:]):
        cos_per_case: list[float] = []
        for _, c in active_cases.iterrows():
            sub = emb_df[(emb_df["dong_code"] == c["dong_code"])
                          & (emb_df["year"].isin([y0, y1]))]
            if len(sub) != 2:
                continue
            v0 = sub[sub["year"] == y0][EMBED_COLS].values[0]
            v1 = sub[sub["year"] == y1][EMBED_COLS].values[0]
            delta = v1 - v0
            n = np.linalg.norm(delta)
            if n < 1e-9:
                continue
            cos_per_case.append(float(np.dot(delta / n, axis_unit)))
        rows.append({
            "year_pair": f"{y0}-{y1}",
            "mean_cos_with_axis": (float(np.mean(cos_per_case))
                                    if cos_per_case else float("nan")),
            "n_cases": len(cos_per_case),
        })
    return pd.DataFrame(rows)
