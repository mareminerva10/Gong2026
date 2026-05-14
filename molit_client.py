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

import os
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

# Hangul field names from the MOLIT rent XML response.
F_DEPOSIT = "보증금액"   # 만원, comma-formatted (e.g. "10,000")
F_MONTHLY = "월세금액"   # 만원, "0" or missing for jeonse
F_DONG = "법정동"         # legal-dong name; may carry whitespace
F_AREA = "전용면적"       # m²


def _check_cases_schema(cases: pd.DataFrame) -> None:
    missing = {"dong_code", "dong_name_kr"} - set(cases.columns)
    if missing:
        raise ValueError(
            f"labeled_cases is missing required column(s) for MOLIT: {sorted(missing)}. "
            "Add a `dong_name_kr` column with the Korean 법정동 name for each case "
            "so we can filter the gu-level response down to the labeled dong."
        )


def _parse_response(xml_text: str, lawd_cd: str, ymd: str) -> tuple[list[dict], int]:
    """Return (items, totalCount). Raises if resultCode != '00'."""
    root = ET.fromstring(xml_text)
    code_el = root.find(".//resultCode")
    msg_el = root.find(".//resultMsg")
    code = (code_el.text or "").strip() if code_el is not None else ""
    if code != "00":
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
                page_size: int = 1000, retries: int = 3,
                timeout: int = 20) -> pd.DataFrame:
    rows: list[dict] = []
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
        items: list[dict] = []
        total = 0
        for attempt in range(retries):
            try:
                r = requests.get(MOLIT_RENT_BASE_URL, params=params, timeout=timeout)
                r.raise_for_status()
                items, total = _parse_response(r.text, lawd_cd, ymd)
                last_err = None
                break
            except (requests.RequestException, ET.ParseError, RuntimeError) as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        if last_err is not None:
            raise RuntimeError(
                f"MOLIT pull failed after {retries} retries "
                f"(LAWD_CD={lawd_cd} DEAL_YMD={ymd}): {last_err}"
            )
        if not items:
            break
        rows.extend(items)
        if len(rows) >= total or len(items) < page_size:
            break
        page += 1
    return pd.DataFrame(rows)


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
        lawd_cd = f"{int(r.dong_code) // 100000:05d}"
        gu_to_dongs.setdefault(lawd_cd, []).append((int(r.dong_code), r.dong_name_kr))

    frames: list[pd.DataFrame] = []
    years = list(years)
    print(f"  MOLIT: pulling {len(gu_to_dongs)} gu × {len(years)} yrs × 12 mo "
          f"= {len(gu_to_dongs) * len(years) * 12} calls (cache reused where present)")
    for lawd_cd in sorted(gu_to_dongs):
        for y in years:
            for m in range(1, 13):
                ymd = f"{y}{m:02d}"
                chunk = cache_dir / f"{lawd_cd}_{ymd}.parquet"
                if chunk.exists():
                    df = pd.read_parquet(chunk)
                else:
                    df = _pull_month(lawd_cd, ymd, service_key)
                    df.to_parquet(chunk, index=False)
                    time.sleep(0.15)  # polite client
                if not df.empty:
                    frames.append(df.assign(_lawd_cd=lawd_cd, _ymd=ymd))

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
