"""
molit_landuse_client.py
=======================

Client for the 행정구역별·지목별 국토이용현황_시군구 (gu-level land use by
category) table on stat.molit.go.kr. Sits on top of `molit_stat_nuri_client`
for transport, auth, retry, and credential scrubbing — same shape as
`molit_unsold_client` and `molit_redev_client`.

Probed identifiers (2026-06-08, see `docs/molit_probe_2026-06-07.md`)
--------------------------------------------------------------------
  form_id   = 2300
  style_num = 2
  period    = YYYY (annual)

Response shape (per probe artifacts `data/probe_landuse_{2017,2025}.json`)
-------------------------------------------------------------------------
  date       : YYYY string
  시도       : province name (e.g. "서울", "부산", "전국")
  시군구     : gu / si / gun name; OR a rollup token ("계", "합계", or
               the province name itself for province-level summary rows).
  {category}>면적   : float, square metres
  {category}>지번수 : integer, parcel count
  Plus "계>면적" and "계>지번수" totals at every administrative level.

Twenty-eight land-use categories are returned (전, 답, 과수원, 목장용지,
임야, 광천지, 염전, 대, 공장용지, 학교용지, 주차장, 주유소용지, 창고용지,
도로, 철도용지, 제방, 하천, 구거, 유지, 양어장, 수도용지, 공원, 체육용지,
유원지, 종교용지, 사적지, 묘지, 잡종지). The full set is enumerated in
`CATEGORIES`.

Output (`data/statnuri_landuse_panel.parquet`, gitignored)
----------------------------------------------------------
Per (lawd_cd, year) row, 25 Seoul gus × 8 years = 200 rows:

  Identity:
    lawd_cd, gu_name, year

  Audit totals:
    area_total_m2, parcels_total

  Raw per-category retention (audit, NOT exposed by default in the
  dashboard surface):
    area_<category> × 28  (square metres, float)
    parcels_<category> × 28  (parcel count, Int64)

  Computed shares (descriptive proxies; **not** an orthogonal partition,
  the four categories overlap — see formulas below):

    landuse_built_share        = (대 + 공장용지 + 학교용지 + 창고용지 +
                                   주유소용지 + 종교용지 + 사적지 +
                                   잡종지) / 계>면적
        Developed-plot fraction. Picks up residential, commercial,
        industrial, and institutional built-up area regardless of use
        intensity. Sites with explicit "non-built" or natural cover are
        excluded.

    landuse_vegetation_share   = (임야 + 전 + 답 + 과수원 + 목장용지 +
                                   공원) / 계>면적
        Natural-and-agricultural land-cover fraction. Includes managed
        green (parks) but excludes water and infrastructure.

    landuse_infrastructure_share = (도로 + 철도용지 + 주차장 + 수도용지 +
                                     제방 + 하천 + 구거 + 유지 + 양어장) /
                                     계>면적
        Transport + utility + water-management fraction. Includes
        hydrography (rivers, ditches, reservoirs) which is structurally
        infrastructure-adjacent but not built.

    landuse_transport_share    = (도로 + 철도용지 + 주차장) / 계>면적
        Strict transport subset of infrastructure_share. Useful when the
        question is specifically "what fraction of the gu is paved
        right-of-way?".

  WHY four overlapping shares instead of a partition:
  - A partition would force a single category per land-use class and
    invite false precision. The four shares are *descriptive proxies*
    intended to be read together with the per-category raw retention.
  - Never treat (built + vegetation + infrastructure + transport) as
    summing to 1.

Scope limitation
----------------
- Seoul 25 gus only. The gu-name → LAWD_CD map (`SEOUL_GU_LAWD_CD`) is
  imported from `molit_unsold_client`; nationwide extension would need
  province-disambiguated names.

Form/style identifiers are env-var driven (`MOLIT_LANDUSE_FORM_ID` and
`MOLIT_LANDUSE_STYLE_NUM`) or override-able via CLI flags. Same
"no defaults — refuses to guess" pattern as `molit_redev_client.TableSpec`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

from molit_stat_nuri_client import (
    StatNuriError,
    probe as _probe,
    request_one,
)
from molit_redev_client import TableSpec
from molit_unsold_client import SEOUL_GU_LAWD_CD


LANDUSE = TableSpec(
    name="행정구역별·지목별 국토이용현황_시군구",
    form_id_env="MOLIT_LANDUSE_FORM_ID",
    style_num_env="MOLIT_LANDUSE_STYLE_NUM",
)


F_DATE = "date"
F_SIDO = "시도"
F_SIGUNGU = "시군구"
# Province-level summary rows surface under either "계" / "합계" or under
# the province name in the 시군구 column. Drop all of these.
ROLLUP_TOKENS = ("계", "합계", "서울")


CATEGORIES: tuple[str, ...] = (
    "전", "답", "과수원", "목장용지", "임야", "광천지", "염전", "대",
    "공장용지", "학교용지", "주차장", "주유소용지", "창고용지", "도로",
    "철도용지", "제방", "하천", "구거", "유지", "양어장", "수도용지",
    "공원", "체육용지", "유원지", "종교용지", "사적지", "묘지", "잡종지",
)


# Share definitions — see module docstring. Overlap is intentional.
BUILT: tuple[str, ...] = (
    "대", "공장용지", "학교용지", "창고용지", "주유소용지",
    "종교용지", "사적지", "잡종지",
)
VEGETATION: tuple[str, ...] = (
    "임야", "전", "답", "과수원", "목장용지", "공원",
)
INFRASTRUCTURE: tuple[str, ...] = (
    "도로", "철도용지", "주차장", "수도용지", "제방",
    "하천", "구거", "유지", "양어장",
)
TRANSPORT: tuple[str, ...] = (
    "도로", "철도용지", "주차장",
)


DEFAULT_OUTPUT = Path("data/statnuri_landuse_panel.parquet")


# --- Period chunking ------------------------------------------------------

def chunk_years(start_y: int, end_y: int) -> list[str]:
    """Inclusive list of YYYY strings.

    >>> chunk_years(2017, 2019)
    ['2017', '2018', '2019']
    """
    if start_y > end_y:
        raise ValueError(f"start {start_y} > end {end_y}")
    return [str(y) for y in range(start_y, end_y + 1)]


# --- Probe wrapper --------------------------------------------------------

def probe_landuse(year: str, *, form_id: str | None = None,
                  style_num: str | None = None,
                  out_path: Path | None = None) -> dict:
    """One-shot probe of the land-use table at a given YYYY. Writes a
    credential-scrubbed payload to `out_path` if given."""
    fid, snum = LANDUSE.resolve(form_id, style_num)
    return _probe(fid, snum, year, year, out_path=out_path)


# --- Bulk fetch -----------------------------------------------------------

def fetch_landuse_raw(start_y: int, end_y: int, cache_dir: Path,
                     *, form_id: str | None = None,
                     style_num: str | None = None) -> list[dict]:
    """Pull (start_y..end_y) inclusive year-by-year. One API call per year.
    Payloads cached as JSON under cache_dir/{slug}_{year}.json. Returns
    the list of parsed payloads in chronological order.

    Raises StatNuriError on any irrecoverable API failure (no silent
    empty returns)."""
    fid, snum = LANDUSE.resolve(form_id, style_num)
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = f"form{fid}_style{snum}"

    payloads: list[dict] = []
    for y in chunk_years(int(start_y), int(end_y)):
        cached = cache_dir / f"{slug}_{y}.json"
        if cached.exists():
            payloads.append(json.loads(cached.read_text(encoding="utf-8")))
            continue
        payload = request_one(fid, snum, y, y)
        cached.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payloads.append(payload)
    return payloads


# --- Panel build ----------------------------------------------------------

def _rows_from_payloads(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        rd = p.get("result_data") or {}
        rows = rd.get("formList") or []
        if isinstance(rows, list):
            out.extend(r for r in rows if isinstance(r, dict))
    return out


def build_seoul_landuse_panel(payloads: Iterable[dict]) -> pd.DataFrame:
    """Flatten payloads into a (lawd_cd, year) panel for Seoul gus only.

    Drops province-level rollup rows. Raises if any kept row's gu name is
    unknown — surfaces map gaps loudly rather than producing a silently
    NaN merge column. Computes the four named shares from the per-category
    raw totals (also retained on each row for audit)."""
    rows = _rows_from_payloads(payloads)
    if not rows:
        raise StatNuriError(
            "build_seoul_landuse_panel: zero rows across cached payloads. "
            "Check that fetch_landuse_raw actually populated the cache.")
    df = pd.DataFrame(rows)
    needed = {F_DATE, F_SIDO, F_SIGUNGU, "계>면적", "계>지번수"}
    missing = needed - set(df.columns)
    if missing:
        sample_cols = sorted(df.columns)[:12]
        raise StatNuriError(
            f"landuse response missing required field(s): {sorted(missing)}. "
            f"Sample available columns: {sample_cols}")

    seoul = df[(df[F_SIDO] == "서울")
               & (~df[F_SIGUNGU].isin(ROLLUP_TOKENS))].copy()
    if seoul.empty:
        raise StatNuriError(
            "no Seoul gu rows after filtering. 시도 values seen: "
            f"{sorted(df[F_SIDO].dropna().astype(str).unique().tolist())[:8]}")

    seoul["lawd_cd"] = seoul[F_SIGUNGU].map(SEOUL_GU_LAWD_CD)
    unmapped = seoul[seoul["lawd_cd"].isna()][F_SIGUNGU].unique().tolist()
    if unmapped:
        raise StatNuriError(
            f"unmapped Seoul gu name(s): {unmapped}. Update SEOUL_GU_LAWD_CD "
            "or check for whitespace / renamed admin units.")

    out = pd.DataFrame({
        "lawd_cd":       seoul["lawd_cd"].astype("string"),
        "gu_name":       seoul[F_SIGUNGU].astype("string"),
        "year":          seoul[F_DATE].astype(int).astype("Int16"),
        "area_total_m2": pd.to_numeric(seoul["계>면적"], errors="coerce"),
        "parcels_total": (pd.to_numeric(seoul["계>지번수"], errors="coerce")
                            .astype("Int64")),
    })

    # Raw per-category retention. Categories absent from a response row
    # are treated as zero (matches StatNuri's behaviour of omitting
    # all-zero columns in some legacy years).
    for cat in CATEGORIES:
        area_src = seoul.get(f"{cat}>면적")
        parcels_src = seoul.get(f"{cat}>지번수")
        out[f"area_{cat}"] = (pd.to_numeric(area_src, errors="coerce")
                                .fillna(0.0)
                                .astype(float)) if area_src is not None else 0.0
        out[f"parcels_{cat}"] = ((pd.to_numeric(parcels_src, errors="coerce")
                                    .fillna(0).astype("Int64"))
                                  if parcels_src is not None else 0)

    # Computed shares — descriptive proxies. See module docstring for the
    # formulas and the explicit warning that these overlap.
    def _share(category_tuple: tuple[str, ...]) -> pd.Series:
        total = sum(out[f"area_{c}"] for c in category_tuple)
        return (total / out["area_total_m2"]).where(out["area_total_m2"] > 0)

    out["landuse_built_share"] = _share(BUILT)
    out["landuse_vegetation_share"] = _share(VEGETATION)
    out["landuse_infrastructure_share"] = _share(INFRASTRUCTURE)
    out["landuse_transport_share"] = _share(TRANSPORT)

    return out.sort_values(["lawd_cd", "year"]).reset_index(drop=True)


def build_panel_file(start_y: int, end_y: int, cache_dir: Path,
                     output: Path, *, form_id: str | None = None,
                     style_num: str | None = None) -> pd.DataFrame:
    """End-to-end: bulk-fetch -> build Seoul panel -> write parquet.

    Returns the in-memory panel."""
    payloads = fetch_landuse_raw(start_y, end_y, cache_dir,
                                 form_id=form_id, style_num=style_num)
    panel = build_seoul_landuse_panel(payloads)
    output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(output, index=False)
    return panel


# --- CLI ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Probe / fetch / build StatNuri 행정구역별·지목별 "
                    "국토이용현황_시군구 (form_id 2300, style_num 2)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _add_id_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--form-id", default=None,
                       help="form_id override (default: MOLIT_LANDUSE_FORM_ID)")
        p.add_argument("--style-num", default=None,
                       help="style_num override (default: MOLIT_LANDUSE_STYLE_NUM)")

    p_probe = sub.add_parser("probe", help="single-year probe")
    p_probe.add_argument("--year", required=True, help="YYYY")
    p_probe.add_argument("--out", default=None,
                         help="path to write scrubbed JSON payload")
    _add_id_flags(p_probe)

    p_fetch = sub.add_parser(
        "fetch-raw", help="bulk-fetch YYYY range; cache per year")
    p_fetch.add_argument("--start-year", required=True, type=int)
    p_fetch.add_argument("--end-year", required=True, type=int)
    p_fetch.add_argument("--cache-dir", required=True)
    _add_id_flags(p_fetch)

    p_build = sub.add_parser(
        "build-panel",
        help="end-to-end: fetch-raw -> build Seoul panel -> write parquet")
    p_build.add_argument("--start-year", required=True, type=int)
    p_build.add_argument("--end-year", required=True, type=int)
    p_build.add_argument("--cache-dir", required=True)
    p_build.add_argument("--output", default=str(DEFAULT_OUTPUT))
    _add_id_flags(p_build)

    args = ap.parse_args(argv)

    try:
        if args.cmd == "probe":
            out = Path(args.out) if args.out else None
            probe_landuse(args.year, form_id=args.form_id,
                          style_num=args.style_num, out_path=out)
            return 0

        if args.cmd == "fetch-raw":
            payloads = fetch_landuse_raw(
                args.start_year, args.end_year, Path(args.cache_dir),
                form_id=args.form_id, style_num=args.style_num)
            n_rows = sum(len((p.get("result_data") or {}).get("formList") or [])
                         for p in payloads)
            print(f"fetched {len(payloads)} year payload(s), "
                  f"{n_rows} total formList rows")
            return 0

        if args.cmd == "build-panel":
            panel = build_panel_file(
                args.start_year, args.end_year, Path(args.cache_dir),
                Path(args.output),
                form_id=args.form_id, style_num=args.style_num)
            print(f"land-use panel: {len(panel)} rows  "
                  f"({panel['lawd_cd'].nunique()} gus × "
                  f"{panel['year'].nunique()} years)")
            print(f"  built_share range:        "
                  f"[{panel['landuse_built_share'].min():.3f}, "
                  f"{panel['landuse_built_share'].max():.3f}]")
            print(f"  vegetation_share range:   "
                  f"[{panel['landuse_vegetation_share'].min():.3f}, "
                  f"{panel['landuse_vegetation_share'].max():.3f}]")
            print(f"  infrastructure_share range: "
                  f"[{panel['landuse_infrastructure_share'].min():.3f}, "
                  f"{panel['landuse_infrastructure_share'].max():.3f}]")
            print(f"  transport_share range:    "
                  f"[{panel['landuse_transport_share'].min():.3f}, "
                  f"{panel['landuse_transport_share'].max():.3f}]")
            print(f"written: {args.output}")
            return 0
    except StatNuriError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
