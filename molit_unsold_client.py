"""
molit_unsold_client.py
======================

Client for the 시·군·구별 미분양현황 (unsold-housing inventory by si/gun/gu)
table on stat.molit.go.kr. Sits on top of `molit_stat_nuri_client` for
transport/auth/scrubbing, exactly like `molit_redev_client` does for the
재개발 table.

Probed identifiers (2026-05-25)
-------------------------------
  form_id   = 2082
  style_num = 128
  period    = YYYYMM (monthly, NOT YYYY)

Response shape (per probe artifact `data/unsold_probe_202603.json`)
-------------------------------------------------------------------
  date       : YYYYMM string
  미분양현황 : integer count of unsold units (호)
  시군구    : gu name (Korean), or "계" for province-level rollup
  구분       : province name (Korean), e.g. "서울", "부산"

  Rollup rows where `시군구 == "계"` must be filtered out before any
  gu-level merge.

What this module does
---------------------
  - probe_unsold(year_month) : one-shot probe wrapper.
  - fetch_unsold_raw(...)    : bulk pull over a YYYYMM range, cached per
                                month under cache_dir.
  - build_seoul_unsold_panel : flatten payloads, filter to province=서울,
                                drop rollups, map gu name to LAWD_CD.
  - aggregate_to_annual(...) : collapse monthly panel to a (lawd_cd,
                                year) table with mean / max / Dec metrics
                                ready to merge into the dong-year model
                                panel.

Scope limitation
----------------
The gu-name → LAWD_CD map covers Seoul's 25 gus only. Sigungu names
collide across provinces (e.g., "중구" exists in several cities), so a
nationwide map would require disambiguation by (province, gu_name).
Extend by adding a province-keyed dict if a non-Seoul case set comes in.

Form/style identifiers are env-var driven (MOLIT_UNSOLD_FORM_ID and
MOLIT_UNSOLD_STYLE_NUM) or override-able via CLI flags, matching the
redev client's "no defaults — refuses to guess" pattern.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

from molit_stat_nuri_client import (
    StatNuriError,
    probe as _probe,
    request_one,
)
from molit_redev_client import TableSpec  # reuse the env-driven resolver


UNSOLD = TableSpec(
    name="시·군·구별 미분양현황",
    form_id_env="MOLIT_UNSOLD_FORM_ID",
    style_num_env="MOLIT_UNSOLD_STYLE_NUM",
)


# Seoul 25 gus → 5-digit LAWD_CD. Canonical codes; verify against
# any future labeled_cases additions.
SEOUL_GU_LAWD_CD: dict[str, str] = {
    "종로구": "11110", "중구": "11140", "용산구": "11170",
    "성동구": "11200", "광진구": "11215", "동대문구": "11230",
    "중랑구": "11260", "성북구": "11290", "강북구": "11305",
    "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470",
    "강서구": "11500", "구로구": "11530", "금천구": "11545",
    "영등포구": "11560", "동작구": "11590", "관악구": "11620",
    "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740",
}

F_DATE = "date"
F_UNSOLD = "미분양현황"
F_SIGUNGU = "시군구"
F_PROVINCE = "구분"
ROLLUP_TOKEN = "계"


# --- Period chunking ------------------------------------------------------

def chunk_months(start_ym: str, end_ym: str) -> list[str]:
    """List of YYYYMM strings inclusive between start_ym and end_ym.

    >>> chunk_months("201712", "201802")
    ['201712', '201801', '201802']
    """
    sy, sm = int(start_ym[:4]), int(start_ym[4:6])
    ey, em = int(end_ym[:4]), int(end_ym[4:6])
    if (sy, sm) > (ey, em):
        raise ValueError(f"start {start_ym} > end {end_ym}")
    out: list[str] = []
    cy, cm = sy, sm
    while (cy, cm) <= (ey, em):
        out.append(f"{cy}{cm:02d}")
        cm += 1
        if cm > 12:
            cm = 1
            cy += 1
    return out


# --- Probe wrapper --------------------------------------------------------

def probe_unsold(year_month: str, *, form_id: str | None = None,
                 style_num: str | None = None,
                 out_path: Path | None = None) -> dict:
    """One-shot probe of the 미분양 table at a given YYYYMM. Writes a
    credential-scrubbed payload to `out_path` if given."""
    fid, snum = UNSOLD.resolve(form_id, style_num)
    return _probe(fid, snum, year_month, year_month, out_path=out_path)


# --- Bulk fetch -----------------------------------------------------------

def fetch_unsold_raw(start_ym: str, end_ym: str, cache_dir: Path,
                    *, form_id: str | None = None,
                    style_num: str | None = None) -> list[dict]:
    """Pull (start_ym..end_ym) inclusive month-by-month. Each month is
    one API call; payloads cached as JSON under cache_dir/{slug}_{ym}.json.
    Returns the list of parsed payloads in chronological order.

    Raises StatNuriError on any irrecoverable API failure (no silent
    empty returns)."""
    fid, snum = UNSOLD.resolve(form_id, style_num)
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = f"form{fid}_style{snum}"

    payloads: list[dict] = []
    months = chunk_months(start_ym, end_ym)
    for ym in months:
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


def build_seoul_unsold_panel(payloads: Iterable[dict]) -> pd.DataFrame:
    """Flatten payloads into a (lawd_cd, year_month, unsold_units) panel
    for Seoul gus only. Drops rollup rows and rows for non-Seoul
    provinces. Raises if any kept row's gu name is unknown — surfaces
    map gaps loudly rather than producing a silently NaN merge column.
    """
    rows = _rows_from_payloads(payloads)
    if not rows:
        raise StatNuriError(
            "build_seoul_unsold_panel: zero rows across cached payloads. "
            "Check that fetch_unsold_raw actually populated the cache.")
    df = pd.DataFrame(rows)
    needed = {F_DATE, F_UNSOLD, F_SIGUNGU, F_PROVINCE}
    missing = needed - set(df.columns)
    if missing:
        raise StatNuriError(
            f"unsold response missing field(s): {sorted(missing)}. "
            f"Available: {sorted(df.columns)}")

    # Filter: Seoul province, drop rollup rows.
    seoul = df[(df[F_PROVINCE] == "서울")
                & (df[F_SIGUNGU] != ROLLUP_TOKEN)].copy()
    if seoul.empty:
        raise StatNuriError(
            "no Seoul gu rows after filtering. The province label may "
            f"have changed; province values seen: "
            f"{sorted(df[F_PROVINCE].unique())}")

    seoul["lawd_cd"] = seoul[F_SIGUNGU].map(SEOUL_GU_LAWD_CD)
    unmapped = seoul[seoul["lawd_cd"].isna()][F_SIGUNGU].unique().tolist()
    if unmapped:
        raise StatNuriError(
            f"unmapped Seoul gu name(s): {unmapped}. Update "
            "SEOUL_GU_LAWD_CD or check for whitespace/old admin names.")

    # API convention (verified against 2020 vs 2024 cache files): when
    # 미분양현황 == 0 the field is omitted from the row entirely in some
    # years, and present-with-value-0 in others. Treat missing as zero —
    # this matches the implicit API contract and keeps the panel free of
    # ambiguous "is this missing or zero" NA values. Confirmed empirically:
    # 종로구 2020 has the key absent in every monthly response, while
    # 종로구 2024 has explicit "미분양현황": 0.
    unsold = (pd.to_numeric(seoul.get(F_UNSOLD), errors="coerce")
                .fillna(0)
                .astype("Int64"))
    panel = pd.DataFrame({
        "lawd_cd":     seoul["lawd_cd"].astype("string"),
        "gu_name":     seoul[F_SIGUNGU].astype("string"),
        "year_month":  seoul[F_DATE].astype("string"),
        "unsold_units": unsold,
    })
    panel["year"] = panel["year_month"].str[:4].astype("Int16")
    panel["month"] = panel["year_month"].str[4:6].astype("Int8")
    return panel.sort_values(["lawd_cd", "year_month"]).reset_index(drop=True)


def aggregate_to_annual(monthly: pd.DataFrame) -> pd.DataFrame:
    """Collapse the monthly panel to (lawd_cd, year) with mean / max /
    Dec snapshots. December rows that aren't in the cache produce NA
    for `statnuri_unsold_dec_units` rather than silent substitution.
    """
    by = monthly.groupby(["lawd_cd", "year"])
    agg = pd.DataFrame({
        "statnuri_unsold_mean_units": by["unsold_units"].mean(),
        "statnuri_unsold_max_units":  by["unsold_units"].max(),
    }).reset_index()

    dec = monthly[monthly["month"] == 12][["lawd_cd", "year", "unsold_units"]]
    dec = dec.rename(columns={"unsold_units": "statnuri_unsold_dec_units"})
    out = agg.merge(dec, on=["lawd_cd", "year"], how="left")
    # Cast to nullable Int64 — preserves NA for missing-Dec years.
    out["statnuri_unsold_mean_units"] = (
        out["statnuri_unsold_mean_units"].round().astype("Int64"))
    out["statnuri_unsold_max_units"] = (
        out["statnuri_unsold_max_units"].astype("Int64"))
    out["statnuri_unsold_dec_units"] = (
        out["statnuri_unsold_dec_units"].astype("Int64"))
    out["year"] = out["year"].astype("Int16")
    return out.sort_values(["lawd_cd", "year"]).reset_index(drop=True)


# --- CLI ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe / fetch MOLIT StatNuri 미분양 (unsold housing) table")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _add_id_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--form-id", default=None,
                       help="form_id override (default: MOLIT_UNSOLD_FORM_ID)")
        p.add_argument("--style-num", default=None,
                       help="style_num override (default: MOLIT_UNSOLD_STYLE_NUM)")

    p_probe = sub.add_parser("probe", help="single-month probe")
    p_probe.add_argument("--year-month", required=True, help="YYYYMM")
    p_probe.add_argument("--out", default=None)
    _add_id_flags(p_probe)

    p_fetch = sub.add_parser("fetch-raw",
                             help="bulk-fetch YYYYMM range; cache per month")
    p_fetch.add_argument("--start-ym", required=True)
    p_fetch.add_argument("--end-ym", required=True)
    p_fetch.add_argument("--cache-dir", required=True)
    _add_id_flags(p_fetch)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "probe":
            out = Path(args.out) if args.out else None
            probe_unsold(args.year_month, form_id=args.form_id,
                         style_num=args.style_num, out_path=out)
            return 0
        if args.cmd == "fetch-raw":
            payloads = fetch_unsold_raw(
                args.start_ym, args.end_ym, Path(args.cache_dir),
                form_id=args.form_id, style_num=args.style_num)
            n_rows = sum(len((p.get("result_data") or {}).get("formList") or [])
                         for p in payloads)
            print(f"fetched {len(payloads)} month payload(s), "
                  f"{n_rows} total formList rows")
            return 0
    except StatNuriError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
