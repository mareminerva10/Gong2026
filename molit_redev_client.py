"""
molit_redev_client.py
=====================

Redevelopment (재개발) and reconstruction (재건축) annual project-status
clients. Both tables live on the 국토교통부 통계누리 OpenAPI at
stat.molit.go.kr — the same endpoint family as `molit_stat_nuri_client`,
which this module reuses for transport, retry, key handling, and
credential scrubbing.

Tables (granted on 2026-05-18 via the data.go.kr OpenAPI approval flow,
fulfilled against stat.molit.go.kr)
-----------------------------------
  2-1. 연도별 재개발사업 현황      — nationwide, region-keyed
  3-3. 연도별 서울시 재건축사업 현황 — Seoul-only, region-keyed

What's known vs. what must be probed
------------------------------------
KNOWN:
  - Transport: GET stat.molit.go.kr/portal/openapi/service/rest/getList.do
  - Auth: MOLIT_STAT_NURI_KEY (reuse — same portal as StatNuri probe client)
  - Envelope: result_status / result_data.formList (success = INFO-000)
  - Period format for these tables: YYYY (annual), NOT YYYYMM. The
    transport layer accepts either; only this module's chunking and probe
    wrappers need to know. Use chunk_years() — do not reuse the monthly
    chunk_period() from molit_stat_nuri_client for these tables.
  - 5-year span cap still applies.

UNKNOWN until first probe (do not guess):
  - form_id / style_num for each table. Configure via env after checking
    the portal's apiList.do page for the approved entries:
        MOLIT_REDEV_FORM_ID, MOLIT_REDEV_STYLE_NUM
        MOLIT_RECON_FORM_ID, MOLIT_RECON_STYLE_NUM
    Or pass `--form-id` / `--style-num` to the CLI for one-off probes
    (CLI overrides env). The reconstruction table is referred to as
    `recon` everywhere; `rebuild` is avoided because it reads as
    "rebuild the cache/panel".
  - Field names inside each formList row. Specifically: which key carries
    region, which carries year, and which carry the project-status counts
    (조합설립인가, 사업시행인가, 관리처분인가, 착공, 준공, …).
  - Whether annual data is returned as one row per (region, year) or as
    a single row per region with per-year columns. The aggregation
    function below treats this as a parameter (`row_orientation`).

Workflow
--------
1. Set form_id/style_num env vars from the portal's approved-table page
   (or pass --form-id / --style-num on the CLI for one-off probes).
2. Run `probe_redevelopment()` or `probe_reconstruction()` and write the
   payload to data/ for offline inspection.
3. Call `fetch_table_raw(...)` to pull the full year range (cached
   per-window as JSON under cache_dir).
4. For redev: call `build_national_redev_panel(payloads)` to produce the
   year-keyed national panel. For recon: schema not yet known (style_num=1
   returns empty rows as of 2026-05-24) — re-probe when alternative
   style_num values are confirmed on the portal, then write a dedicated
   builder against the populated layout.

Field names for redev are anchored to the 2026-05-24 probe of 6189/1.
The defaults on `FieldMap` match that probe exactly; override via the
constructor only if the upstream schema later changes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from molit_stat_nuri_client import (
    StatNuriError,
    probe as _probe,
    request_one,
)


def chunk_years(start_year: int, end_year: int,
                max_years: int = 5) -> list[tuple[str, str]]:
    """Split [start_year, end_year] inclusive into <=max_years YYYY
    windows. Annual analogue of molit_stat_nuri_client.chunk_period, for
    tables (like redev/recon) whose period format is YYYY not YYYYMM.

    >>> chunk_years(2017, 2024, max_years=5)
    [('2017', '2021'), ('2022', '2024')]
    """
    if start_year > end_year:
        raise ValueError(f"start_year {start_year} > end_year {end_year}")
    chunks: list[tuple[str, str]] = []
    y = start_year
    while y <= end_year:
        e = min(y + max_years - 1, end_year)
        chunks.append((str(y), str(e)))
        y = e + 1
    return chunks


# --- Table registry ---------------------------------------------------------
# Env-var-driven so the user can fill these in from the portal after approval
# without editing the module. No defaults — leaving them unset is intentional:
# a wrong guess at form_id would silently pull a different table.

@dataclass(frozen=True)
class TableSpec:
    name: str            # human-readable label
    form_id_env: str     # env var holding the form_id
    style_num_env: str   # env var holding the style_num

    def resolve(self, form_id: str | None = None,
                style_num: str | None = None) -> tuple[str, str]:
        """Resolve table identifiers. Explicit args win over env vars,
        env vars win over nothing. Raises if neither source supplies a
        value — never guesses."""
        fid = form_id or os.getenv(self.form_id_env)
        snum = style_num or os.getenv(self.style_num_env)
        missing = [v for v, x in
                   ((self.form_id_env, fid), (self.style_num_env, snum)) if not x]
        if missing:
            raise StatNuriError(
                f"{self.name}: missing identifier(s) {missing}. "
                "Look up the approved table's form_id and style_num on "
                "stat.molit.go.kr/portal/api/apiList.do (the approved-keys "
                "page). Pass via CLI flags (--form-id / --style-num) for a "
                "one-off probe, or set env vars for repeat use:\n"
                f"  setx {self.form_id_env} \"<form_id>\"\n"
                f"  setx {self.style_num_env} \"<style_num>\"\n"
                "Open a fresh shell after setx, then retry."
            )
        return fid, snum


REDEVELOPMENT = TableSpec(
    name="2-1 연도별 재개발사업 현황",
    form_id_env="MOLIT_REDEV_FORM_ID",
    style_num_env="MOLIT_REDEV_STYLE_NUM",
)
RECONSTRUCTION = TableSpec(
    name="3-3 연도별 서울시 재건축사업 현황",
    form_id_env="MOLIT_RECON_FORM_ID",
    style_num_env="MOLIT_RECON_STYLE_NUM",
)


# --- Probe wrappers ---------------------------------------------------------

def probe_redevelopment(year: int, *, form_id: str | None = None,
                        style_num: str | None = None,
                        out_path: Path | None = None) -> dict:
    """One-shot probe of the redevelopment table for a single year. The
    redev/recon tables use a YYYY period format (not YYYYMM). Writes the
    credential-scrubbed payload to `out_path` if given. `form_id` and
    `style_num` override env vars when supplied."""
    fid, snum = REDEVELOPMENT.resolve(form_id, style_num)
    y = str(year)
    return _probe(fid, snum, y, y, out_path=out_path)


def probe_reconstruction(year: int, *, form_id: str | None = None,
                         style_num: str | None = None,
                         out_path: Path | None = None) -> dict:
    """As `probe_redevelopment`, but for the Seoul reconstruction table."""
    fid, snum = RECONSTRUCTION.resolve(form_id, style_num)
    y = str(year)
    return _probe(fid, snum, y, y, out_path=out_path)


# --- Bulk fetch -------------------------------------------------------------

def fetch_table_raw(
        table: TableSpec,
        start_year: int,
        end_year: int,
        cache_dir: Path,
        *,
        form_id: str | None = None,
        style_num: str | None = None,
) -> list[dict]:
    """Pull (start_year..end_year) inclusive from `table`, chunked into
    <=5-year YYYYMM windows. Each window's payload is cached as JSON
    under cache_dir/{table-slug}_{start}_{end}.json so a partial run
    survives restarts. Returns the list of parsed payloads in chunk order.

    `form_id` and `style_num` override env vars when supplied.

    Raises StatNuriError on any irrecoverable API failure (no silent
    empty returns)."""
    fid, snum = table.resolve(form_id, style_num)
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = f"form{fid}_style{snum}"

    payloads: list[dict] = []
    windows = chunk_years(start_year, end_year, max_years=5)
    for start_dt, end_dt in windows:
        cached = cache_dir / f"{slug}_{start_dt}_{end_dt}.json"
        if cached.exists():
            payloads.append(json.loads(cached.read_text(encoding="utf-8")))
            continue
        payload = request_one(fid, snum, start_dt, end_dt)
        cached.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payloads.append(payload)
    return payloads


# --- Aggregation ------------------------------------------------------------
# Field names below are anchored to the 2026-05-24 probe of redev 6189/1 — see
# project memory `project-molit-redev-recon-probe-2026-05-24`. The defaults
# match exactly what the API returned for that table; override via
# constructor args if the response shape changes in a future revision.

@dataclass(frozen=True)
class FieldMap:
    """Field-name mapping for the redev table (national annual aggregate).

    `region` is documented as optional because this table has no region
    dimension — it is national-only. Set it only if a future redev variant
    introduces a 시도/시군구 field; leave None for the current 6189/1 shape.
    """
    year: str = "date"
    region: str | None = None
    zone_count: str = "구역수>구역수"
    area_m2: str = "시행면적>시행면적"
    demolition_targets: str = "철거대상>철거대상"
    units_total: str = "건립가구>계"
    units_member: str = "건립가구>조 합 원"   # response key has spaces around 합
    units_general_sale: str = "건립가구>일반분양"
    units_rental: str = "건립가구>임대주택"


def _rows_from_payloads(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        rd = p.get("result_data") or {}
        rows = rd.get("formList") or []
        if isinstance(rows, list):
            out.extend(r for r in rows if isinstance(r, dict))
    return out


def _to_num(s: pd.Series) -> pd.Series:
    cleaned = (s.astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
                .replace({"": None, "-": None, "nan": None}))
    return pd.to_numeric(cleaned, errors="coerce")


def build_national_redev_panel(payloads: Iterable[dict],
                               field_map: FieldMap | None = None,
                               ) -> pd.DataFrame:
    """Flatten redev payloads into an annual national-level panel:

        year
        redev_zone_count
        redev_area_m2
        redev_demolition_targets
        redev_units_total
        redev_units_member
        redev_units_general_sale
        redev_units_rental

    Intended use: cross-join by `year` onto a dong-year panel as a
    *national_redevelopment_intensity* control. This is NOT local
    redevelopment exposure — the source table has no geographic dimension.
    Label downstream columns accordingly so the distinction survives merges.

    Raises StatNuriError if payloads are empty or any expected field is
    missing — surfacing schema drift loudly rather than producing a panel
    with silently-NaN columns.
    """
    fm = field_map or FieldMap()
    rows = _rows_from_payloads(payloads)
    if not rows:
        raise StatNuriError(
            "build_national_redev_panel: zero rows across cached payloads. "
            "Check that the probe artifacts contain a populated formList.")
    df = pd.DataFrame(rows)

    src_to_panel = {
        fm.year: "year",
        fm.zone_count: "redev_zone_count",
        fm.area_m2: "redev_area_m2",
        fm.demolition_targets: "redev_demolition_targets",
        fm.units_total: "redev_units_total",
        fm.units_member: "redev_units_member",
        fm.units_general_sale: "redev_units_general_sale",
        fm.units_rental: "redev_units_rental",
    }
    missing = set(src_to_panel) - set(df.columns)
    if missing:
        raise StatNuriError(
            f"redev response missing expected field(s): {sorted(missing)}. "
            f"Available columns: {sorted(df.columns)}. "
            "If the upstream schema has changed, pass an explicit FieldMap "
            "with the new key names.")

    out = pd.DataFrame({"year": _to_num(df[fm.year]).astype("Int16")})
    for src, panel_col in src_to_panel.items():
        if panel_col == "year":
            continue
        out[panel_col] = _to_num(df[src]).astype("Int64")
    out = out.dropna(subset=["year"]).sort_values("year").reset_index(drop=True)

    # Additive sanity check on the 건립가구 categories. Warn (don't fail) so
    # that if MOLIT later revises category boundaries we still produce a
    # panel — but the caller is notified the semantic assumption changed.
    parts_sum = (out["redev_units_member"]
                 + out["redev_units_general_sale"]
                 + out["redev_units_rental"])
    mismatch = (parts_sum != out["redev_units_total"]).fillna(False)
    if mismatch.any():
        bad = out.loc[mismatch, ["year", "redev_units_total"]]
        bad = bad.assign(parts_sum=parts_sum[mismatch].values)
        warnings.warn(
            "redev unit additive invariant failed "
            "(member + general_sale + rental != total) for year(s): "
            f"{bad['year'].tolist()}. MOLIT may have revised the 건립가구 "
            "category boundaries; verify before treating cross-year counts "
            f"as comparable. Details:\n{bad.to_string(index=False)}",
            RuntimeWarning,
            stacklevel=2,
        )

    return out


# --- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe / fetch MOLIT redev & rebuild annual tables")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _add_id_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--form-id", default=None,
                       help="table form_id; overrides MOLIT_*_FORM_ID env var")
        p.add_argument("--style-num", default=None,
                       help="table style_num; overrides MOLIT_*_STYLE_NUM env var")

    p_probe = sub.add_parser("probe", help="single-year probe to inspect schema")
    p_probe.add_argument("table", choices=("redev", "recon"))
    p_probe.add_argument("--year", type=int, required=True,
                         help="probe year (annual table; period passed as YYYY)")
    p_probe.add_argument("--out", default=None,
                         help="optional path to write scrubbed payload")
    _add_id_flags(p_probe)

    p_fetch = sub.add_parser("fetch-raw",
                             help="bulk-fetch a year range; cache per window")
    p_fetch.add_argument("table", choices=("redev", "recon"))
    p_fetch.add_argument("--start-year", type=int, required=True)
    p_fetch.add_argument("--end-year", type=int, required=True)
    p_fetch.add_argument("--cache-dir", required=True,
                         help="directory for per-window JSON cache")
    _add_id_flags(p_fetch)

    args = ap.parse_args(argv)
    spec = REDEVELOPMENT if args.table == "redev" else RECONSTRUCTION

    try:
        if args.cmd == "probe":
            out = Path(args.out) if args.out else None
            fn = probe_redevelopment if args.table == "redev" else probe_reconstruction
            fn(args.year, form_id=args.form_id, style_num=args.style_num,
               out_path=out)
            return 0
        if args.cmd == "fetch-raw":
            payloads = fetch_table_raw(
                spec, args.start_year, args.end_year, Path(args.cache_dir),
                form_id=args.form_id, style_num=args.style_num)
            n_rows = sum(len((p.get("result_data") or {}).get("formList") or [])
                         for p in payloads)
            print(f"fetched {len(payloads)} window payload(s), "
                  f"{n_rows} total formList rows")
            return 0
    except StatNuriError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
