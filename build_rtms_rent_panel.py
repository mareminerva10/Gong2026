"""Combined multi-housing-type RTMS rent panel builder.

Step 3 of the multi-housing tenure expansion workstream
(project-next-session-multi-housing-tenure-2026-06-09).

Two responsibilities:

1. **build** — produce a per-housing-type panel parquet by pulling
   the corresponding sibling RTMS endpoint. Thin orchestration over
   `molit_client.build_seoul_tenure_panel` plus a `--months` flag for
   surgical re-pulls (used for smoke tests and gap-fills without
   triggering a full 2,400-call year × 25-gu run).

2. **combine** — read whichever per-type parquets exist on disk and
   write the combined `data/rtms_rent_panel.parquet`. The combiner
   tolerates missing per-type panels (writes a partial combined panel
   and logs which housing types are absent), but validates the
   housing-type contract on each input (correct `housing_type`
   column value, no duplicate (lawd_cd, year, month, housing_type)
   keys, SH rows have NaN per-m² metrics per the area_kind rule from
   docs/rtms_siblings_probe_2026-06-09.md).

This module does NOT change the dashboard contract. That's step 4.
`data/wolse_molit.parquet` (apartment-only) remains the live
dashboard's tenure input until step 4 switches it to
`data/rtms_rent_panel.parquet`.

Conventions match other artifact builders in the repo
(`molit_unsold_client.py`, `molit_landuse_client.py`,
`molit_completed_unsold_client.py`): root-level module, two-subcommand
argparse CLI, default outputs gitignored under `data/`."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import pandas as pd

import molit_client as mc

DEFAULT_COMBINED_OUTPUT = Path("data/rtms_rent_panel.parquet")

# Schema asserted on every per-type parquet read by combine_panels.
# Matches build_seoul_tenure_panel's output column order so the
# combined panel preserves the same shape, just with housing_type
# now spanning multiple values.
EXPECTED_COMBINED_COLS: list[str] = [
    "lawd_cd", "gu_name", "year", "month", "year_month",
    "n_rent_deals", "n_wolse", "n_jeonse", "wolse_ratio",
    "median_deposit_per_m2", "median_monthly_rent_per_m2",
    "housing_type", "source",
]


# ===== build subcommand ==================================================

def build_panel_subset(
        housing_type: str,
        months: list[str],
        gus: list[str],
        *,
        service_key: str | None = None,
        polite_sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Pull-and-build a per-type panel for an explicit (gus, months) subset.

    Bypasses the year × 12-month expansion in
    `molit_client.build_seoul_tenure_panel` so we can pull a single
    smoke gu-month without paying the full year cost. Writes the
    per-type parquet under DEFAULT_TENURE_OUTPUTS[housing_type] and
    also returns the in-memory panel.

    `months` is a list of "YYYYMM" strings.
    `gus`    is a list of 5-digit LAWD_CD strings (Seoul gus)."""
    if housing_type not in mc.HOUSING_TYPE_REGISTRY:
        raise ValueError(
            f"Unknown housing_type {housing_type!r}; "
            f"valid: {sorted(mc.HOUSING_TYPE_REGISTRY.keys())}")
    spec = mc.HOUSING_TYPE_REGISTRY[housing_type]

    if service_key is None:
        service_key = os.getenv(mc.SERVICE_KEY_ENV)
    if not service_key:
        raise RuntimeError(
            f"MOLIT service key missing — set {mc.SERVICE_KEY_ENV} in "
            "your environment (decoded 일반 인증키 from data.go.kr 마이페이지).")

    bad_gus = [g for g in gus if g not in mc.SEOUL_LAWD_CD_TO_GU]
    if bad_gus:
        raise ValueError(
            f"unknown Seoul lawd_cd(s): {bad_gus}. "
            f"Valid: {sorted(mc.SEOUL_LAWD_CD_TO_GU.keys())}")
    bad_months = [m for m in months
                  if not (len(m) == 6 and m.isdigit())]
    if bad_months:
        raise ValueError(
            f"months must be 'YYYYMM' strings; got: {bad_months}")

    cache_dir = mc._default_cache_dir(housing_type)
    cache_dir.mkdir(parents=True, exist_ok=True)

    n_calls = len(gus) * len(months)
    print(f"Subset build [{housing_type}]: {len(gus)} gu × {len(months)} mo "
          f"= {n_calls} calls (cache at {cache_dir})")

    transactions: list[pd.DataFrame] = []
    for lawd_cd in gus:
        for ymd in months:
            y, m = int(ymd[:4]), int(ymd[4:])
            items, _ = mc._pull_month(lawd_cd, ymd, service_key, cache_dir,
                                       url=spec.url)
            if items:
                transactions.append(
                    mc._classify_and_normalize(items, lawd_cd, y, m, spec=spec))
            time.sleep(polite_sleep_s)

    if not transactions:
        raise RuntimeError(
            f"{spec.url.rsplit('/', 1)[-1]} returned zero transactions "
            f"across the requested subset (gus={gus}, months={months}).")

    raw = pd.concat(transactions, ignore_index=True)
    panel = mc._aggregate_to_gu_month(raw)
    panel["gu_name"] = panel["lawd_cd"].map(mc.SEOUL_LAWD_CD_TO_GU)
    panel["year_month"] = (panel["year"].astype(str).str.zfill(4)
                           + panel["month"].astype(str).str.zfill(2))
    panel["housing_type"] = spec.housing_type
    panel["source"] = spec.source_tag
    panel = panel[EXPECTED_COMBINED_COLS]

    output = mc.DEFAULT_TENURE_OUTPUTS[housing_type]
    output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(output, index=False)
    return panel


# ===== combine subcommand ================================================

def _validate_per_type_panel(df: pd.DataFrame, housing_type: str,
                              source_path: Path) -> None:
    """Per-type contract checks before the panel goes into the combined
    output. Failures here raise rather than warn — a mis-tagged panel
    polluting the combined artifact is exactly the kind of silent
    error the housing_type column was added to prevent."""
    missing = set(EXPECTED_COMBINED_COLS) - set(df.columns)
    if missing:
        raise ValueError(
            f"{source_path}: missing columns {sorted(missing)}. "
            f"Expected schema: {EXPECTED_COMBINED_COLS}")
    bad = set(df["housing_type"].astype(str).unique()) - {housing_type}
    if bad:
        raise ValueError(
            f"{source_path}: expected housing_type={housing_type!r}, "
            f"found other values: {sorted(bad)}. The combiner uses the "
            f"registry key as the source of truth — a per-type panel "
            f"file must contain only that one housing type.")
    # SH gap rule, asserted again at combine time so a regression in
    # the client (e.g. someone removing the area_kind branch) would
    # surface here rather than silently corrupting the combined panel.
    spec = mc.HOUSING_TYPE_REGISTRY[housing_type]
    if spec.area_kind == "total_floor":
        for col in ("median_deposit_per_m2", "median_monthly_rent_per_m2"):
            if df[col].notna().any():
                raise ValueError(
                    f"{source_path}: housing_type={housing_type} "
                    f"(area_kind=total_floor) must have NaN {col}. "
                    f"totalFloorAr (whole-building gross) is not "
                    f"comparable to per-unit excluUseAr — see "
                    f"docs/rtms_siblings_probe_2026-06-09.md.")


def combine_panels(
        output: Path | str = DEFAULT_COMBINED_OUTPUT,
        sources: dict[str, Path] | None = None,
        *,
        require_all: bool = False,
) -> pd.DataFrame:
    """Read per-type parquets and write the combined multi-housing
    tenure panel.

    `sources` defaults to DEFAULT_TENURE_OUTPUTS. If a per-type parquet
    is missing on disk, that type is logged and skipped — the combined
    panel will be partial. Pass `require_all=True` to fail instead
    when any per-type panel is absent.

    Returns the in-memory combined DataFrame."""
    if sources is None:
        sources = dict(mc.DEFAULT_TENURE_OUTPUTS)
    output = Path(output)

    frames: list[pd.DataFrame] = []
    present: list[str] = []
    missing: list[tuple[str, Path]] = []
    for ht, path in sources.items():
        path = Path(path)
        if not path.exists():
            missing.append((ht, path))
            continue
        df = pd.read_parquet(path)
        _validate_per_type_panel(df, ht, path)
        frames.append(df)
        present.append(ht)

    if not frames:
        raise RuntimeError(
            f"No per-type panels found on disk. Checked: "
            f"{ {ht: str(p) for ht, p in sources.items()} }")
    if missing and require_all:
        raise RuntimeError(
            f"require_all=True but missing per-type panels: "
            f"{[(ht, str(p)) for ht, p in missing]}")
    if missing:
        print(f"WARNING: combined panel is PARTIAL — missing per-type "
              f"panels: {[(ht, str(p)) for ht, p in missing]}")

    combined = pd.concat(frames, ignore_index=True)

    # Uniqueness of (lawd_cd, year, month, housing_type) — a duplicate
    # here would silently double-count one (gu, month, type) in any
    # downstream rollup.
    dup_mask = combined.duplicated(
        subset=["lawd_cd", "year", "month", "housing_type"], keep=False)
    if dup_mask.any():
        dup_rows = combined[dup_mask]
        raise ValueError(
            f"Combined panel has {dup_mask.sum()} duplicate "
            f"(lawd_cd, year, month, housing_type) rows. "
            f"First few keys: "
            f"{dup_rows[['lawd_cd', 'year', 'month', 'housing_type']].head(5).to_dict('records')}")

    combined = combined.sort_values(
        ["lawd_cd", "year", "month", "housing_type"]).reset_index(drop=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(output, index=False)

    print(f"Combined RTMS rent panel: {len(combined)} rows "
          f"({len(present)}/{len(sources)} housing types present: {present})")
    for ht in present:
        n = (combined["housing_type"] == ht).sum()
        print(f"  {ht:25s} {n:>6d} rows")
    print(f"written: {output}")
    return combined


# ===== CLI ===============================================================

def _cli_build(args: argparse.Namespace) -> int:
    gus = args.gus.split(",") if args.gus else None
    if args.months:
        months = args.months.split(",")
        if gus is None:
            raise SystemExit(
                "--months requires --gus (subset builds must scope both "
                "axes; otherwise use a full year range without --months).")
        panel = build_panel_subset(args.housing_type, months, gus)
        print(f"Subset panel [{args.housing_type}]: {len(panel)} rows "
              f"({panel['lawd_cd'].nunique()} gus × {len(months)} mo)")
        print(f"  wolse_ratio: "
              f"[{panel['wolse_ratio'].min():.3f}, "
              f"{panel['wolse_ratio'].max():.3f}]")
        if panel["median_deposit_per_m2"].notna().any():
            print(f"  median_deposit_per_m2: "
                  f"[{panel['median_deposit_per_m2'].min():.2f}, "
                  f"{panel['median_deposit_per_m2'].max():.2f}] 만원/m²")
        else:
            print(f"  median_deposit_per_m2: NaN (area_kind=total_floor)")
        print(f"written: {mc.DEFAULT_TENURE_OUTPUTS[args.housing_type]}")
    else:
        if args.start_year is None or args.end_year is None:
            raise SystemExit(
                "without --months, --start-year and --end-year are required.")
        years = list(range(args.start_year, args.end_year + 1))
        mc.build_seoul_tenure_panel(args.housing_type, years, gus=gus)
    return 0


def _cli_combine(args: argparse.Namespace) -> int:
    combine_panels(output=Path(args.output), require_all=args.require_all)
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Combined multi-housing RTMS rent panel — "
                    "per-type builder + combiner.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_b = sub.add_parser("build", help="Pull and build a per-type panel.")
    p_b.add_argument("--housing-type", required=True,
                     choices=sorted(mc.HOUSING_TYPE_REGISTRY.keys()))
    p_b.add_argument("--start-year", type=int)
    p_b.add_argument("--end-year", type=int)
    p_b.add_argument("--gus",
                     help="comma-separated lawd_cd subset (default: all "
                          "25 Seoul gus when --months omitted; required "
                          "when --months is given).")
    p_b.add_argument("--months",
                     help="comma-separated YYYYMM list; overrides "
                          "--start-year/--end-year and pulls only those "
                          "months. Useful for smoke tests and gap-fills.")

    p_c = sub.add_parser("combine",
                          help="Combine per-type panels into "
                               "data/rtms_rent_panel.parquet.")
    p_c.add_argument("--output", default=str(DEFAULT_COMBINED_OUTPUT))
    p_c.add_argument("--require-all", action="store_true",
                     help="fail if any per-type panel is missing on disk "
                          "(default: write a partial combined panel and "
                          "log which types are absent).")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "build":
            return _cli_build(args)
        if args.cmd == "combine":
            return _cli_combine(args)
    except (RuntimeError, ValueError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
