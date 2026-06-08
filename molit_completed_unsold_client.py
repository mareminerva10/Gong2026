"""
molit_completed_unsold_client.py
================================

Client for the 공사완료후 미분양현황 (post-completion unsold housing
inventory) table on stat.molit.go.kr. Companion to `molit_unsold_client`
(form 2082/128, pre-completion / pre-sale unsold). Sits on top of
`molit_stat_nuri_client` for transport, auth, retry, and credential
scrubbing — same shape as the other StatNuri clients in this repo.

Probed identifiers (2026-06-08, see `docs/molit_probe_2026-06-07.md`)
--------------------------------------------------------------------
  form_id   = 5328
  style_num = 1
  period    = YYYYMM (monthly, same shape as 2082/128)

Response shape (per probe artifacts `data/probe_completed_unsold_{201701,202604}.json`)
---------------------------------------------------------------------------------------
  date       : YYYYMM string
  구분       : 시도 (province name, e.g. "서울", "부산", "전국")
  시군구    : gu / si / gun name OR a rollup token ("계", "합계")
  부문      : 공공부문 / 민간부문 / 계
  규모      : 40㎡이하 / 40~60㎡ / 60~85㎡ / 85㎡초과 / 소계 / 계
  호        : integer count of post-completion unsold units

For each (gu, month), the API returns multiple rows — one per
(규모 × 부문) cross-product. To get a single canonical count per
gu-month, filter to `규모 == "계" AND 부문 == "계"`. Any other
combination is a sub-breakdown that would double-count if summed.

Key distinction from form 2082/128 (pre-completion unsold)
----------------------------------------------------------
Both endpoints are monthly StatNuri tables at gu grain. Two important
operational differences (recorded here so future readers don't conflate
them):

1. **Zero handling**. Form 2082/128 OMITS the `미분양현황` field entirely
   when the value is zero (verified against 2020 vs 2024 cache files);
   the unsold builder treats omitted-field as zero. Form 5328/1 is the
   opposite: rows are PRESENT with explicit `호=0`. Verified empirically
   at probe time across both 201701 and 202604 (e.g. 종로구, 서대문구
   etc. at 호=0 in both years). This builder therefore does NOT need
   omission-handling logic and can rely on `호` being populated for every
   kept row.

2. **Row-per-(gu,month) multiplicity**. Form 2082/128 has one row per
   gu-month. Form 5328/1 has ~7 rows per gu-month due to 규모 × 부문
   breakdown. The filter to `규모=계 AND 부문=계` collapses this back to
   the canonical total.

Scope limitation
----------------
Seoul 25 gus only. The gu-name → LAWD_CD map (`SEOUL_GU_LAWD_CD`) is
imported from `molit_unsold_client`; nationwide extension would need
province-disambiguated names.

Output (`data/statnuri_completed_unsold_panel.parquet`, gitignored)
-------------------------------------------------------------------
Annual rollup with three metrics, mirroring the existing
`statnuri_unsold_*` shape so dashboard contract code can join the two
panels symmetrically by `lawd_cd × year`:

  lawd_cd, gu_name, year                            identity

  statnuri_completed_unsold_mean_units   Int64  per-gu annual mean across the 12 months
  statnuri_completed_unsold_max_units    Int64  per-gu annual max across the 12 months
  statnuri_completed_unsold_dec_units    Int64  per-gu December snapshot (NA if Dec missing)

Form/style identifiers are env-var driven (`MOLIT_COMPLETED_UNSOLD_FORM_ID`
and `MOLIT_COMPLETED_UNSOLD_STYLE_NUM`) or override-able via CLI flags.
Same "no defaults — refuses to guess" pattern as the other StatNuri
clients in this repo.
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
from molit_unsold_client import SEOUL_GU_LAWD_CD, chunk_months


COMPLETED_UNSOLD = TableSpec(
    name="공사완료후 미분양현황",
    form_id_env="MOLIT_COMPLETED_UNSOLD_FORM_ID",
    style_num_env="MOLIT_COMPLETED_UNSOLD_STYLE_NUM",
)


F_DATE = "date"
F_PROVINCE = "구분"      # 시도 (e.g. "서울", "부산", "전국")
F_SIGUNGU = "시군구"
F_SECTOR = "부문"        # 공공부문 / 민간부문 / 계
F_SIZE = "규모"          # 40㎡이하 / 40~60㎡ / 60~85㎡ / 85㎡초과 / 소계 / 계
F_UNITS = "호"           # integer count of unsold units

# Canonical "total for this gu-month" row selector. Any other 규모/부문
# combination is a sub-breakdown that would double-count if summed.
TOTAL_SIZE_TOKEN = "계"
TOTAL_SECTOR_TOKEN = "계"

# Rollups in the 시군구 column that must be filtered out before any gu-level
# merge (province-aggregate rows).
SIGUNGU_ROLLUP_TOKENS = ("계", "합계")


DEFAULT_OUTPUT = Path("data/statnuri_completed_unsold_panel.parquet")


# --- Probe wrapper --------------------------------------------------------

def probe_completed_unsold(year_month: str, *, form_id: str | None = None,
                           style_num: str | None = None,
                           out_path: Path | None = None) -> dict:
    """One-shot probe of the completed-unsold table at a given YYYYMM.
    Writes a credential-scrubbed payload to `out_path` if given."""
    fid, snum = COMPLETED_UNSOLD.resolve(form_id, style_num)
    return _probe(fid, snum, year_month, year_month, out_path=out_path)


# --- Bulk fetch -----------------------------------------------------------

def fetch_completed_unsold_raw(start_ym: str, end_ym: str, cache_dir: Path,
                               *, form_id: str | None = None,
                               style_num: str | None = None) -> list[dict]:
    """Pull (start_ym..end_ym) inclusive month-by-month. Each month is one
    API call; payloads cached as JSON under cache_dir/{slug}_{ym}.json.
    Returns the parsed payloads in chronological order.

    Raises StatNuriError on any irrecoverable API failure (no silent
    empty returns)."""
    fid, snum = COMPLETED_UNSOLD.resolve(form_id, style_num)
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = f"form{fid}_style{snum}"

    payloads: list[dict] = []
    for ym in chunk_months(start_ym, end_ym):
        cached = cache_dir / f"{slug}_{ym}.json"
        if cached.exists():
            payloads.append(json.loads(cached.read_text(encoding="utf-8")))
            continue
        payload = request_one(fid, snum, ym, ym)
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


def build_seoul_completed_unsold_panel(payloads: Iterable[dict]) -> pd.DataFrame:
    """Flatten payloads into a (lawd_cd, year_month, completed_unsold_units)
    panel for Seoul gus only.

    Filters in order:
      1. Drop rows missing any of the required fields.
      2. Keep only province=서울 (not 전국, 부산, 등).
      3. Drop province-aggregate rollups in 시군구 (계 / 합계).
      4. Keep only canonical total rows: 규모=계 AND 부문=계.
      5. Map gu_name to LAWD_CD via SEOUL_GU_LAWD_CD; fail loud if any
         kept row's gu name is unknown.

    Form 5328/1 returns rows with explicit `호=0` (NOT omitted, unlike
    form 2082/128). Zero values are therefore preserved as-is."""
    rows = _rows_from_payloads(payloads)
    if not rows:
        raise StatNuriError(
            "build_seoul_completed_unsold_panel: zero rows across cached "
            "payloads. Check that fetch_completed_unsold_raw populated the "
            "cache.")
    df = pd.DataFrame(rows)
    needed = {F_DATE, F_PROVINCE, F_SIGUNGU, F_SECTOR, F_SIZE, F_UNITS}
    missing = needed - set(df.columns)
    if missing:
        raise StatNuriError(
            f"completed-unsold response missing field(s): {sorted(missing)}. "
            f"Available: {sorted(df.columns)}")

    seoul = df[
        (df[F_PROVINCE] == "서울")
        & (~df[F_SIGUNGU].isin(SIGUNGU_ROLLUP_TOKENS))
        & (df[F_SIZE] == TOTAL_SIZE_TOKEN)
        & (df[F_SECTOR] == TOTAL_SECTOR_TOKEN)
    ].copy()
    if seoul.empty:
        raise StatNuriError(
            "no Seoul gu rows after filtering. Province values seen: "
            f"{sorted(df[F_PROVINCE].dropna().astype(str).unique())[:8]}; "
            f"sizes: {sorted(df[F_SIZE].dropna().astype(str).unique())[:8]}; "
            f"sectors: {sorted(df[F_SECTOR].dropna().astype(str).unique())[:8]}")

    seoul["lawd_cd"] = seoul[F_SIGUNGU].map(SEOUL_GU_LAWD_CD)
    unmapped = seoul[seoul["lawd_cd"].isna()][F_SIGUNGU].unique().tolist()
    if unmapped:
        raise StatNuriError(
            f"unmapped Seoul gu name(s): {unmapped}. Update SEOUL_GU_LAWD_CD "
            "or check for whitespace / renamed admin units.")

    # 호 is integer-valued and present on every kept row (5328/1 does not
    # omit zero rows). Cast through float once to handle any stray
    # string-formatted values defensively, then to nullable Int64.
    units = (pd.to_numeric(seoul[F_UNITS], errors="coerce")
                .astype("Int64"))
    panel = pd.DataFrame({
        "lawd_cd":                 seoul["lawd_cd"].astype("string"),
        "gu_name":                 seoul[F_SIGUNGU].astype("string"),
        "year_month":              seoul[F_DATE].astype("string"),
        "completed_unsold_units":  units,
    })
    panel["year"] = panel["year_month"].str[:4].astype("Int16")
    panel["month"] = panel["year_month"].str[4:6].astype("Int8")
    return panel.sort_values(["lawd_cd", "year_month"]).reset_index(drop=True)


def aggregate_to_annual(monthly: pd.DataFrame) -> pd.DataFrame:
    """Collapse the monthly panel to (lawd_cd, year) with mean / max / Dec
    snapshots. December rows that aren't in the cache produce NA for
    `statnuri_completed_unsold_dec_units` rather than silent substitution.

    Output column names deliberately mirror the existing
    `statnuri_unsold_{mean,max,dec}_units` shape (pre-completion unsold),
    distinguished by the `completed_` infix."""
    by = monthly.groupby(["lawd_cd", "year"])
    agg = pd.DataFrame({
        "statnuri_completed_unsold_mean_units": by["completed_unsold_units"].mean(),
        "statnuri_completed_unsold_max_units":  by["completed_unsold_units"].max(),
    }).reset_index()

    dec = monthly[monthly["month"] == 12][
        ["lawd_cd", "year", "completed_unsold_units"]
    ].rename(columns={"completed_unsold_units":
                        "statnuri_completed_unsold_dec_units"})
    out = agg.merge(dec, on=["lawd_cd", "year"], how="left")
    out["statnuri_completed_unsold_mean_units"] = (
        out["statnuri_completed_unsold_mean_units"].round().astype("Int64"))
    out["statnuri_completed_unsold_max_units"] = (
        out["statnuri_completed_unsold_max_units"].astype("Int64"))
    out["statnuri_completed_unsold_dec_units"] = (
        out["statnuri_completed_unsold_dec_units"].astype("Int64"))
    out["year"] = out["year"].astype("Int16")
    return out.sort_values(["lawd_cd", "year"]).reset_index(drop=True)


def build_panel_file(start_ym: str, end_ym: str, cache_dir: Path,
                     output: Path, *, form_id: str | None = None,
                     style_num: str | None = None) -> pd.DataFrame:
    """End-to-end: bulk-fetch -> build Seoul monthly panel -> aggregate to
    annual -> write parquet. Returns the in-memory annual panel."""
    payloads = fetch_completed_unsold_raw(start_ym, end_ym, cache_dir,
                                          form_id=form_id, style_num=style_num)
    monthly = build_seoul_completed_unsold_panel(payloads)
    annual = aggregate_to_annual(monthly)
    output.parent.mkdir(parents=True, exist_ok=True)
    annual.to_parquet(output, index=False)
    return annual


# --- CLI ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Probe / fetch / build StatNuri 공사완료후 미분양현황 "
                    "(form_id 5328, style_num 1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _add_id_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--form-id", default=None,
            help="form_id override (default: MOLIT_COMPLETED_UNSOLD_FORM_ID)")
        p.add_argument(
            "--style-num", default=None,
            help="style_num override (default: MOLIT_COMPLETED_UNSOLD_STYLE_NUM)")

    p_probe = sub.add_parser("probe", help="single-month probe")
    p_probe.add_argument("--year-month", required=True, help="YYYYMM")
    p_probe.add_argument("--out", default=None,
                         help="path to write scrubbed JSON payload")
    _add_id_flags(p_probe)

    p_fetch = sub.add_parser(
        "fetch-raw", help="bulk-fetch YYYYMM range; cache per month")
    p_fetch.add_argument("--start-ym", required=True)
    p_fetch.add_argument("--end-ym", required=True)
    p_fetch.add_argument("--cache-dir", required=True)
    _add_id_flags(p_fetch)

    p_build = sub.add_parser(
        "build-panel",
        help="end-to-end: fetch-raw -> Seoul monthly panel -> annual rollup "
             "-> write parquet")
    p_build.add_argument("--start-ym", required=True)
    p_build.add_argument("--end-ym", required=True)
    p_build.add_argument("--cache-dir", required=True)
    p_build.add_argument("--output", default=str(DEFAULT_OUTPUT))
    _add_id_flags(p_build)

    args = ap.parse_args(argv)

    try:
        if args.cmd == "probe":
            out = Path(args.out) if args.out else None
            probe_completed_unsold(args.year_month, form_id=args.form_id,
                                   style_num=args.style_num, out_path=out)
            return 0

        if args.cmd == "fetch-raw":
            payloads = fetch_completed_unsold_raw(
                args.start_ym, args.end_ym, Path(args.cache_dir),
                form_id=args.form_id, style_num=args.style_num)
            n_rows = sum(len((p.get("result_data") or {}).get("formList") or [])
                         for p in payloads)
            print(f"fetched {len(payloads)} month payload(s), "
                  f"{n_rows} total formList rows")
            return 0

        if args.cmd == "build-panel":
            annual = build_panel_file(
                args.start_ym, args.end_ym, Path(args.cache_dir),
                Path(args.output),
                form_id=args.form_id, style_num=args.style_num)
            print(f"completed-unsold panel: {len(annual)} annual rows "
                  f"({annual['lawd_cd'].nunique()} gus × "
                  f"{annual['year'].nunique()} years)")
            print(f"  mean_units range:  "
                  f"[{int(annual['statnuri_completed_unsold_mean_units'].min())}, "
                  f"{int(annual['statnuri_completed_unsold_mean_units'].max())}]")
            print(f"  max_units range:   "
                  f"[{int(annual['statnuri_completed_unsold_max_units'].min())}, "
                  f"{int(annual['statnuri_completed_unsold_max_units'].max())}]")
            print(f"  dec_units coverage: "
                  f"{annual['statnuri_completed_unsold_dec_units'].notna().sum()}/"
                  f"{len(annual)}")
            print(f"written: {args.output}")
            return 0
    except StatNuriError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
