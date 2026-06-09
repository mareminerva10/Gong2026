"""
MOLIT 전월세 client — drop-in real-data replacement for synth_wolse.

Pulls apartment rent transactions from the Korean Public Data Portal
(data.go.kr) and returns a per-(dong_code, year) panel with jeonse / wolse
counts, the wolse_ratio, and median deposit / monthly rent per m².

Endpoint
--------
Default base URL is the modern data.go.kr host. If your registration
confirmation page gives a different URL after approval, override via:

    set MOLIT_RENT_BASE_URL=...    (Windows)
    export MOLIT_RENT_BASE_URL=... (Unix)

Service key (URL-decoded form from the data.go.kr 인증키 page) is read from
the environment as MOLIT_SERVICE_KEY — never put it on argv.

Granularity
-----------
Each API call covers one (LAWD_CD, DEAL_YMD): one 5-digit gu × one YYYYMM.
Multiple labeled dongs in the same gu share calls. Dong-level filtering
happens on the response's 법정동 field, so each labeled case must carry a
`dong_name_kr` column matching MOLIT's legal-dong name.

Guardrails
----------
- Every API failure raises RuntimeError after bounded retry / backoff.
  No silent empty returns.
- Each (gu, month) response is cached as Parquet under cache_dir, so a
  partial run survives restarts.
- Missing required columns on `cases` raises immediately, before any
  network call.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

import pandas as pd
import requests

MOLIT_RENT_BASE_URL = os.getenv(
    "MOLIT_RENT_BASE_URL",
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
)
SERVICE_KEY_ENV = "MOLIT_SERVICE_KEY"

# Field names from the MOLIT rent XML response. The endpoint switched
# from Korean to English keys at some point (verified 2026-06-08 against
# the live endpoint, which returns deposit/monthlyRent/excluUseAr/umdNm
# instead of the legacy 보증금액/월세금액/전용면적/법정동). The Korean
# constants are kept for any future endpoint that still uses them; the
# Seoul-tenure builder below uses the English keys.
F_DEPOSIT = "보증금액"   # legacy 만원, comma-formatted
F_MONTHLY = "월세금액"   # legacy 만원, "0" or missing for jeonse
F_DONG = "법정동"         # legacy legal-dong name
F_AREA = "전용면적"       # legacy m²

# Live field names on RTMSDataSvcAptRent (2026-06-08 verified).
F_DEPOSIT_EN = "deposit"        # 만원, comma-formatted (e.g. "6,120")
F_MONTHLY_EN = "monthlyRent"    # 만원, "0" or "" for jeonse
F_AREA_EN = "excluUseAr"        # m², float-formatted (e.g. "17.93")
F_UMDNM_EN = "umdNm"            # legal-dong name (e.g. "공덕동")
F_SGGCD_EN = "sggCd"            # 5-digit lawd_cd echoed in each item
F_DEAL_YEAR_EN = "dealYear"
F_DEAL_MONTH_EN = "dealMonth"


def lawd_cd_from_dong_code(dong_code: int | str) -> str:
    """Return 5-digit MOLIT LAWD_CD from supported legal-dong code formats.

    Supported:
      - 5-digit gu code:       11200
      - 8-digit dong code:     11200110
      - 10-digit 법정동 code:  1120010100
    """
    s = str(dong_code).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s.isdigit():
        raise ValueError(f"dong_code must be numeric-like; got {dong_code!r}")

    if len(s) == 5:
        return s
    if len(s) in (8, 10):
        return s[:5]

    raise ValueError(
        f"Unsupported dong_code length for MOLIT LAWD_CD extraction: "
        f"{dong_code!r} -> {s!r}. Expected 5, 8, or 10 digits.")


def _check_cases_schema(cases: pd.DataFrame) -> None:
    missing = {"dong_code", "dong_name_kr"} - set(cases.columns)
    if missing:
        raise ValueError(
            f"labeled_cases is missing required column(s) for MOLIT: {sorted(missing)}. "
            "Add a `dong_name_kr` column with the Korean 법정동 name for each case "
            "so we can filter the gu-level response down to the labeled dong."
        )


def _parse_response(xml_text: str, lawd_cd: str, ymd: str) -> tuple[list[dict], int]:
    """Return (items, totalCount). Raises if resultCode is not a success code.

    data.go.kr endpoints vary on the success code: some return "00",
    others "000". Both mean OK on the RTMSDataSvc family. Verified
    against the live endpoint on 2026-06-08: RTMSDataSvcAptRent returns
    resultCode "000" + resultMsg "OK"."""
    root = ET.fromstring(xml_text)
    code_el = root.find(".//resultCode")
    msg_el = root.find(".//resultMsg")
    code = (code_el.text or "").strip() if code_el is not None else ""
    if code not in ("00", "000"):
        msg = (msg_el.text or "").strip() if msg_el is not None else ""
        raise RuntimeError(
            f"MOLIT API error for LAWD_CD={lawd_cd} DEAL_YMD={ymd}: "
            f"resultCode={code!r} resultMsg={msg!r}"
        )
    items = [{c.tag: (c.text or "").strip() for c in item}
             for item in root.iter("item")]
    total_el = root.find(".//totalCount")
    total = int(total_el.text) if total_el is not None and total_el.text else len(items)
    return items, total


def _pull_month(lawd_cd: str, ymd: str, service_key: str,
                cache_dir: Path | str | None = None,
                page_size: int = 1000, retries: int = 3, timeout: int = 20
                ) -> tuple[list[dict], int]:
    """Pull one (LAWD_CD, DEAL_YMD) call from RTMSDataSvcAptRent.

    Empirical contract verified 2026-06-08 against the live endpoint:

    - Required params: `serviceKey`, `LAWD_CD`, `DEAL_YMD`.
    - The endpoint **does** paginate. With no `numOfRows` sent, the
      default page size is 10, while real urban gus easily exceed
      1,000 transactions per month (마포구 202401 reported totalCount
      1,137). Always send `numOfRows` (defaults to 1000 here, which
      single-shots every Seoul gu-month observed in 2017–2024).
    - `pageNo` is sent only when we have to loop because a single
      gu-month exceeds page_size, which is rare. The endpoint accepts
      pageNo + numOfRows together; sending just numOfRows is treated
      as pageNo=1.
    - Success code is `resultCode` "00" OR "000" (see _parse_response).

    Returns (items, total) where items is a list of dicts (one per
    transaction row) and total is the parsed totalCount from the
    response body. items == flattened across pages; total == the
    server-reported totalCount on page 1.

    If cache_dir is given, caches the (multi-page-merged) result as
    parquet at `cache_dir/{lawd_cd}_{ymd}.parquet` and reads from
    cache on subsequent calls. Empty responses are NOT cached (a
    future month may populate; caching empty would lock in a bogus
    zero)."""
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{lawd_cd}_{ymd}.parquet"
        if cache_path.exists():
            df = pd.read_parquet(cache_path)
            items_cached = df.to_dict("records") if not df.empty else []
            return items_cached, len(items_cached)

    items: list[dict] = []
    total = 0
    page = 1
    while True:
        params = {
            "serviceKey": service_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": ymd,
            "numOfRows": page_size,
            "pageNo": page,
        }
        last_err: Exception | None = None
        page_items: list[dict] = []
        page_total = 0
        for attempt in range(retries):
            try:
                r = requests.get(MOLIT_RENT_BASE_URL, params=params, timeout=timeout)
                r.raise_for_status()
                page_items, page_total = _parse_response(r.text, lawd_cd, ymd)
                last_err = None
                break
            except (requests.RequestException, ET.ParseError, RuntimeError) as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        if last_err is not None:
            raise RuntimeError(
                f"MOLIT pull failed after {retries} retries "
                f"(LAWD_CD={lawd_cd} DEAL_YMD={ymd} pageNo={page}): {last_err}"
            )
        if page == 1:
            total = page_total
        items.extend(page_items)
        # Stop when we've accumulated >= server-reported total, or when a
        # page returned fewer rows than requested (the API will not
        # supply more after that).
        if not page_items or len(items) >= total or len(page_items) < page_size:
            break
        page += 1

    if cache_path is not None and items:
        pd.DataFrame(items).to_parquet(cache_path, index=False)

    return items, total


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False),
                         errors="coerce")


def _shrink_panel(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["year"] = panel["year"].astype("int16")
    for c in ("n_rent_deals", "n_wolse", "n_jeonse"):
        panel[c] = panel[c].astype("int32")
    for c in ("wolse_ratio", "median_deposit_per_m2", "median_monthly_rent_per_m2"):
        panel[c] = panel[c].astype("float32")
    return panel


# === Seoul-wide apartment-only tenure panel ==============================
#
# This section builds the gu-month live tenure panel from RTMSDataSvcAptRent
# directly (no labeled_cases dependency). Used by the Block 1 dashboard
# integration. Status is `live_partial` rather than `live` because the
# scope is apartment-only — single/multi-family and officetel rent flow
# through sibling endpoints not yet integrated.

# Reverse map gu_name lookup from lawd_cd. Sourced from the canonical
# mapping in molit_unsold_client so we don't keep two copies in drift.
try:
    from molit_unsold_client import SEOUL_GU_LAWD_CD as _SEOUL_GU_LAWD_CD
except ImportError:  # pragma: no cover - defensive import; module always present
    _SEOUL_GU_LAWD_CD = {}

SEOUL_LAWD_CD_TO_GU = {v: k for k, v in _SEOUL_GU_LAWD_CD.items()}

DEFAULT_TENURE_OUTPUT = Path("data/wolse_molit.parquet")
DEFAULT_TENURE_CACHE = Path("data/molit_rent_cache")


def _classify_and_normalize(items: list[dict], lawd_cd: str,
                            year: int, month: int) -> pd.DataFrame:
    """Turn raw API items into a structured DataFrame with parsed numeric
    fields and the wolse/jeonse classification.

    Classification rule (per user spec, 2026-06-08):
      - monthlyRent == 0 (parsed) OR blank → jeonse
      - monthlyRent > 0                    → wolse

    Numeric parsing strips thousands separators. Per-m² metrics fall to
    NaN where excluUseAr is 0 or missing (avoids divide-by-zero
    contaminating medians)."""
    if not items:
        return pd.DataFrame()
    df = pd.DataFrame(items)
    # Numeric casts — required for all downstream metrics.
    for col in (F_DEPOSIT_EN, F_MONTHLY_EN, F_AREA_EN):
        if col not in df.columns:
            raise RuntimeError(
                f"RTMSDataSvcAptRent response is missing expected field "
                f"{col!r}. LAWD_CD={lawd_cd} {year}-{month:02d}. "
                f"Columns seen: {sorted(df.columns)[:20]}...")
    df["deposit_manwon"] = _to_num(df[F_DEPOSIT_EN])
    df["monthly_rent_manwon"] = _to_num(df[F_MONTHLY_EN]).fillna(0)
    df["excl_use_m2"] = _to_num(df[F_AREA_EN])
    df["is_wolse"] = (df["monthly_rent_manwon"] > 0).astype("int8")
    # Per-m² (deposit / monthly_rent normalized by exclusive-use area).
    valid_area = df["excl_use_m2"].where(df["excl_use_m2"] > 0)
    df["deposit_per_m2"] = df["deposit_manwon"] / valid_area
    df["monthly_per_m2"] = df["monthly_rent_manwon"] / valid_area
    df["lawd_cd"] = str(lawd_cd)
    df["year"] = int(year)
    df["month"] = int(month)
    return df


def _aggregate_to_gu_month(transactions: pd.DataFrame) -> pd.DataFrame:
    """Collapse parsed transactions to (lawd_cd, year, month) panel rows.

    Six core metrics: n_rent_deals, n_wolse, n_jeonse, wolse_ratio,
    median_deposit_per_m2, median_monthly_rent_per_m2. The medians use
    the full population for deposit and the wolse-only sub-population
    for monthly rent (jeonse rows have monthly_rent_manwon=0 by
    construction and would otherwise zero-bias the monthly median)."""
    if transactions.empty:
        return pd.DataFrame(columns=[
            "lawd_cd", "year", "month",
            "n_rent_deals", "n_wolse", "n_jeonse", "wolse_ratio",
            "median_deposit_per_m2", "median_monthly_rent_per_m2",
        ])
    by = transactions.groupby(["lawd_cd", "year", "month"])
    panel = pd.DataFrame({
        "n_rent_deals": by.size(),
        "n_wolse": by["is_wolse"].sum().astype("int64"),
        "median_deposit_per_m2": by["deposit_per_m2"].median(),
    }).reset_index()
    panel["n_jeonse"] = panel["n_rent_deals"] - panel["n_wolse"]
    panel["wolse_ratio"] = panel["n_wolse"] / panel["n_rent_deals"]
    wolse_only = transactions[transactions["is_wolse"] == 1]
    if not wolse_only.empty:
        med_monthly = (wolse_only.groupby(["lawd_cd", "year", "month"])
                                  ["monthly_per_m2"].median()
                                  .rename("median_monthly_rent_per_m2"))
        panel = panel.merge(med_monthly.reset_index(),
                            on=["lawd_cd", "year", "month"], how="left")
    else:
        panel["median_monthly_rent_per_m2"] = float("nan")
    return panel.sort_values(["lawd_cd", "year", "month"]).reset_index(drop=True)


def build_seoul_tenure_panel(
        years: Iterable[int],
        cache_dir: Path | str = DEFAULT_TENURE_CACHE,
        output: Path | str = DEFAULT_TENURE_OUTPUT,
        *,
        gus: Iterable[str] | None = None,
        service_key: str | None = None,
        polite_sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Build the apartment-only Seoul live tenure panel at gu × month grain.

    For each (lawd_cd, ymd) in (gus × years × 12 months), pulls
    RTMSDataSvcAptRent, parses + classifies + computes per-m² metrics,
    aggregates to gu-month panel rows with the six core tenure metrics:

      n_rent_deals, n_wolse, n_jeonse, wolse_ratio,
      median_deposit_per_m2, median_monthly_rent_per_m2

    Carries housing_type='apt' and source='data_go_kr_rtms_apt' on every
    row so downstream consumers cannot conflate this with the missing
    single/multi-family or officetel tenure paths. The dashboard
    contract should join this with tenure_status='live_partial' and
    tenure_scope='apartment_only' (per the 2026-06-08 user spec).

    Defaults to all 25 Seoul gus. Pass `gus=[...lawd_cd strings...]`
    for a pilot subset.

    Output written to `output` (default data/wolse_molit.parquet,
    gitignored). Returns the in-memory panel."""
    if service_key is None:
        service_key = os.getenv(SERVICE_KEY_ENV)
    if not service_key:
        raise RuntimeError(
            f"MOLIT service key missing — set {SERVICE_KEY_ENV} in your "
            "environment (use the decoded 일반 인증키 from data.go.kr 마이페이지).")

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = Path(output)

    if gus is None:
        gu_list = sorted(SEOUL_LAWD_CD_TO_GU.keys())
    else:
        gu_list = sorted(str(g) for g in gus)
        bad = [g for g in gu_list if g not in SEOUL_LAWD_CD_TO_GU]
        if bad:
            raise ValueError(
                f"unknown Seoul lawd_cd(s) in `gus`: {bad}. "
                f"Valid: {sorted(SEOUL_LAWD_CD_TO_GU.keys())}")
    year_list = sorted(int(y) for y in years)
    if not year_list:
        raise ValueError("`years` is empty")

    n_calls = len(gu_list) * len(year_list) * 12
    print(f"MOLIT tenure panel: {len(gu_list)} gus × {len(year_list)} yrs × 12 mo "
          f"= {n_calls} calls (cache at {cache_dir})")

    transactions: list[pd.DataFrame] = []
    for lawd_cd in gu_list:
        for y in year_list:
            for m in range(1, 13):
                ymd = f"{y}{m:02d}"
                items, _ = _pull_month(lawd_cd, ymd, service_key, cache_dir)
                if items:
                    transactions.append(
                        _classify_and_normalize(items, lawd_cd, y, m))
                time.sleep(polite_sleep_s)

    if not transactions:
        raise RuntimeError(
            "RTMSDataSvcAptRent returned zero transactions across the "
            "requested gus × years. Check key, LAWD_CD, year coverage.")

    raw = pd.concat(transactions, ignore_index=True)
    panel = _aggregate_to_gu_month(raw)
    panel["gu_name"] = panel["lawd_cd"].map(SEOUL_LAWD_CD_TO_GU)
    panel["year_month"] = (panel["year"].astype(str).str.zfill(4)
                           + panel["month"].astype(str).str.zfill(2))
    panel["housing_type"] = "apt"
    panel["source"] = "data_go_kr_rtms_apt"
    panel = panel[[
        "lawd_cd", "gu_name", "year", "month", "year_month",
        "n_rent_deals", "n_wolse", "n_jeonse", "wolse_ratio",
        "median_deposit_per_m2", "median_monthly_rent_per_m2",
        "housing_type", "source",
    ]]
    output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(output, index=False)
    return panel


def tenure_annual_rollup(panel: pd.DataFrame) -> pd.DataFrame:
    """Collapse the gu-month tenure panel to (lawd_cd, year) for the
    dashboard contract. wolse_ratio is recomputed from annual totals
    (NOT averaged) so it stays consistent with the count fields."""
    by = panel.groupby(["lawd_cd", "year"])
    annual = pd.DataFrame({
        "tenure_n_rent_deals": by["n_rent_deals"].sum().astype("int64"),
        "tenure_n_wolse": by["n_wolse"].sum().astype("int64"),
        "tenure_median_deposit_per_m2": by["median_deposit_per_m2"].median(),
        "tenure_median_monthly_rent_per_m2": by["median_monthly_rent_per_m2"].median(),
    }).reset_index()
    annual["tenure_n_jeonse"] = (annual["tenure_n_rent_deals"]
                                  - annual["tenure_n_wolse"])
    annual["tenure_wolse_ratio"] = (annual["tenure_n_wolse"]
                                     / annual["tenure_n_rent_deals"])
    annual["year"] = annual["year"].astype(int)
    return annual.sort_values(["lawd_cd", "year"]).reset_index(drop=True)


# --- CLI ----------------------------------------------------------------

def _cli_build_panel(args: argparse.Namespace) -> int:
    years = list(range(args.start_year, args.end_year + 1))
    gus = args.gus.split(",") if args.gus else None
    panel = build_seoul_tenure_panel(
        years,
        cache_dir=Path(args.cache_dir),
        output=Path(args.output),
        gus=gus,
    )
    n_gus = panel["lawd_cd"].nunique()
    print(f"\nTenure panel (apartment-only): {len(panel)} gu-month rows  "
          f"({n_gus} gus × {len(years)} yrs × 12 mo)")
    print(f"  wolse_ratio range:           "
          f"[{panel['wolse_ratio'].min():.3f}, {panel['wolse_ratio'].max():.3f}]")
    print(f"  median_deposit_per_m2 range: "
          f"[{panel['median_deposit_per_m2'].min():.2f}, "
          f"{panel['median_deposit_per_m2'].max():.2f}] 만원/m²")
    print(f"  median_monthly_per_m2 range: "
          f"[{panel['median_monthly_rent_per_m2'].min():.3f}, "
          f"{panel['median_monthly_rent_per_m2'].max():.3f}] 만원/m²")
    print(f"  rows with wolse_ratio NaN:   "
          f"{panel['wolse_ratio'].isna().sum()} (gu-months with zero deals)")
    print(f"written: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="MOLIT RTMSDataSvcAptRent client — apartment-only Seoul "
                    "tenure panel")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser(
        "build-panel",
        help="end-to-end: pull → classify → aggregate → write parquet")
    p_build.add_argument("--start-year", required=True, type=int)
    p_build.add_argument("--end-year", required=True, type=int)
    p_build.add_argument("--gus", default=None,
                         help="comma-separated lawd_cd subset (default: all "
                              "25 Seoul gus)")
    p_build.add_argument("--cache-dir", default=str(DEFAULT_TENURE_CACHE))
    p_build.add_argument("--output", default=str(DEFAULT_TENURE_OUTPUT))

    args = ap.parse_args(argv)
    try:
        if args.cmd == "build-panel":
            return _cli_build_panel(args)
    except (RuntimeError, ValueError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


def fetch_rent_panel(
        cases: pd.DataFrame,
        years: Iterable[int],
        cache_dir: Path,
        raw_out: Path | None = None,
        service_key: str | None = None,
) -> pd.DataFrame:
    """Pull MOLIT rent transactions across (gu × year × month) for the labeled
    cases, cache per-call, then aggregate to one row per (dong_code, year)
    with jeonse/wolse counts, wolse_ratio, and median per-m² deposit and rent.

    Raises ValueError if `cases` lacks dong_name_kr / dong_code, RuntimeError
    on any irrecoverable API failure or if the run produced zero transactions
    (which almost certainly means the key, gu codes, or coverage window is wrong).
    """
    _check_cases_schema(cases)
    if service_key is None:
        service_key = os.getenv(SERVICE_KEY_ENV)
    if not service_key:
        raise RuntimeError(
            f"MOLIT service key missing — set {SERVICE_KEY_ENV} in your environment "
            "(use the decoded 일반 인증키 from the data.go.kr 마이페이지)."
        )

    cache_dir.mkdir(parents=True, exist_ok=True)

    # Group cases by gu (first 5 digits) so dongs in the same gu share API calls.
    gu_to_dongs: dict[str, list[tuple[int, str]]] = {}
    for r in cases.itertuples():
        lawd_cd = lawd_cd_from_dong_code(r.dong_code)
        gu_to_dongs.setdefault(lawd_cd, []).append((int(r.dong_code), r.dong_name_kr))

    frames: list[pd.DataFrame] = []
    years = list(years)
    print(f"  MOLIT: pulling {len(gu_to_dongs)} gu × {len(years)} yrs × 12 mo "
          f"= {len(gu_to_dongs) * len(years) * 12} calls (cache reused where present)")
    print("  MOLIT gu codes:", ", ".join(sorted(gu_to_dongs)))
    for lawd_cd in sorted(gu_to_dongs):
        for y in years:
            for m in range(1, 13):
                ymd = f"{y}{m:02d}"
                items, _ = _pull_month(lawd_cd, ymd, service_key, cache_dir)
                if items:
                    df = pd.DataFrame(items).assign(_lawd_cd=lawd_cd, _ymd=ymd)
                    frames.append(df)
                time.sleep(0.15)  # polite client; cheap on cache hits

    if not frames:
        raise RuntimeError(
            "MOLIT returned zero transactions across the requested cases × years. "
            "Likely causes: wrong LAWD_CD (check dong_code in labeled_cases.csv), "
            "out-of-coverage years, or a service-key quota / approval issue."
        )

    raw = pd.concat(frames, ignore_index=True).drop_duplicates()

    # Numeric casts.
    for col in (F_DEPOSIT, F_MONTHLY, F_AREA):
        if col in raw.columns:
            raw[col] = _to_num(raw[col])
        else:
            raise RuntimeError(
                f"MOLIT response is missing expected field {col!r}. "
                f"Columns seen: {sorted(raw.columns)[:20]}..."
            )

    # Filter to labeled dongs.
    name_to_code: dict[tuple[str, str], int] = {}
    for lawd_cd, dongs in gu_to_dongs.items():
        for code, name in dongs:
            name_to_code[(lawd_cd, name.strip())] = code
    raw["dong_name_kr"] = raw[F_DONG].astype(str).str.strip()
    raw["dong_code"] = [
        name_to_code.get((lc, dn))
        for lc, dn in zip(raw["_lawd_cd"], raw["dong_name_kr"])
    ]
    panel_rows = raw.dropna(subset=["dong_code"]).copy()
    if panel_rows.empty:
        raise RuntimeError(
            "MOLIT returned transactions but none matched the labeled dongs by "
            "(gu, 법정동). Verify dong_name_kr values against the MOLIT 법정동 names "
            "(spacing, '가' suffix, old vs new admin names)."
        )
    panel_rows["dong_code"] = panel_rows["dong_code"].astype("int64")
    panel_rows["year"] = panel_rows["_ymd"].str[:4].astype("int16")
    panel_rows["is_wolse"] = (panel_rows[F_MONTHLY].fillna(0) > 0).astype("int8")
    valid_area = panel_rows[F_AREA].where(panel_rows[F_AREA] > 0)
    panel_rows["deposit_per_m2"] = panel_rows[F_DEPOSIT] / valid_area
    panel_rows["monthly_per_m2"] = panel_rows[F_MONTHLY] / valid_area

    if raw_out is not None:
        panel_rows.to_parquet(raw_out, index=False)

    # Annual aggregation.
    gb = panel_rows.groupby(["dong_code", "year"], sort=True)
    panel = pd.DataFrame({
        "n_rent_deals": gb.size(),
        "n_wolse": gb["is_wolse"].sum(),
        "median_deposit_per_m2": gb["deposit_per_m2"].median(),
    })
    panel["n_jeonse"] = panel["n_rent_deals"] - panel["n_wolse"]
    panel["wolse_ratio"] = panel["n_wolse"] / panel["n_rent_deals"]

    wolse_only = panel_rows[panel_rows["is_wolse"] == 1]
    if not wolse_only.empty:
        med_monthly = (wolse_only.groupby(["dong_code", "year"])["monthly_per_m2"]
                       .median().rename("median_monthly_rent_per_m2"))
        panel = panel.join(med_monthly)
    else:
        panel["median_monthly_rent_per_m2"] = float("nan")

    name_lookup = panel_rows.groupby("dong_code")["dong_name_kr"].first()
    panel = panel.reset_index()
    panel["dong_name_kr"] = panel["dong_code"].map(name_lookup)

    panel = panel[[
        "dong_code", "dong_name_kr", "year",
        "n_rent_deals", "n_wolse", "n_jeonse",
        "wolse_ratio",
        "median_deposit_per_m2", "median_monthly_rent_per_m2",
    ]]
    return _shrink_panel(panel)
