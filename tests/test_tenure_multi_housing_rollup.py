"""tests/test_tenure_multi_housing_rollup.py

Offline guards for the multi-housing tenure rollup in
`dashboard_pilot_contract.merge_optional_tenure`. The combined
RTMS rent panel is the load-bearing artifact for Block 1, so any
silent drift in how the contract layer rolls it up — wrong
denominator for the all-residential ratio, wrong NaN handling for
SH per-m², missing per-type column — would directly degrade what
the dashboard shows under `tenure_status = live`.

These tests synthesize a tiny gu-month-housing_type panel and call
`merge_optional_tenure` against it. No network, no real data
files, no AlphaEarth panel required.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

import dashboard_pilot_contract as dpc


REQUIRED_WOLSE_RATIO_COLS = (
    "tenure_wolse_ratio_all_residential",
    "tenure_wolse_ratio_apt",
    "tenure_wolse_ratio_rowhouse_multifamily",
    "tenure_wolse_ratio_single_detached",
    "tenure_wolse_ratio_officetel",
)

PER_M2_TYPES = ("apt", "rowhouse_multifamily", "officetel")
REQUIRED_PER_M2_COLS = tuple(
    f"tenure_median_{stat}_per_m2_{ht}"
    for stat in ("deposit", "monthly_rent")
    for ht in PER_M2_TYPES
)


def _make_source_row(housing_type, lawd_cd="11440", year=2024, month=1,
                     n_rent_deals=100, n_wolse=40,
                     median_deposit_per_m2=200.0,
                     median_monthly_rent_per_m2=1.0):
    """One row of the source rtms_rent_panel schema."""
    return {
        "lawd_cd": lawd_cd, "year": year, "month": month,
        "housing_type": housing_type,
        "n_rent_deals": n_rent_deals,
        "n_wolse": n_wolse,
        "n_jeonse": n_rent_deals - n_wolse,
        "wolse_ratio": n_wolse / n_rent_deals if n_rent_deals else float("nan"),
        "median_deposit_per_m2": median_deposit_per_m2,
        "median_monthly_rent_per_m2": median_monthly_rent_per_m2,
    }


def _write_source_panel(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "rtms_rent_panel.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _dong_year_skeleton(lawd_cd="11440", year=2024):
    """Minimal dong-year skeleton the merge attaches tenure_* columns to."""
    return pd.DataFrame([{
        "emd_cd": "1144010100", "lawd_cd": lawd_cd,
        "gu_name": "마포구", "year": year,
    }])


# ----- 1. All-residential wolse_ratio is sum-based, not averaged ---------

def test_all_residential_wolse_ratio_recomputed_from_annual_sums(tmp_path):
    """The required rule: tenure_wolse_ratio_all_residential is sum
    of n_wolse across types divided by sum of n_rent_deals across
    types. NOT the mean of per-type wolse_ratios (which would
    overweight low-deal-count types like SH)."""
    # Construct a panel where the simple-mean and sum-based ratio
    # give materially different answers.
    rows = [
        # apt: 1000 deals, 400 wolse → ratio 0.40
        _make_source_row("apt", n_rent_deals=1000, n_wolse=400),
        # rowhouse_multifamily: 1000 deals, 500 wolse → ratio 0.50
        _make_source_row("rowhouse_multifamily", n_rent_deals=1000, n_wolse=500),
        # single_detached: 100 deals, 90 wolse → ratio 0.90
        _make_source_row("single_detached", n_rent_deals=100, n_wolse=90,
                         median_deposit_per_m2=float("nan"),
                         median_monthly_rent_per_m2=float("nan")),
        # officetel: 1000 deals, 800 wolse → ratio 0.80
        _make_source_row("officetel", n_rent_deals=1000, n_wolse=800),
    ]
    src = _write_source_panel(tmp_path, rows)

    merged = dpc.merge_optional_tenure(_dong_year_skeleton(), src)
    actual_ratio = merged["tenure_wolse_ratio_all_residential"].iloc[0]

    # Sum-based (correct): (400+500+90+800) / (1000+1000+100+1000) = 1790/3100
    expected_sum_based = 1790 / 3100  # = 0.5774...
    # Mean of per-type ratios (WRONG): (0.4+0.5+0.9+0.8) / 4 = 0.65
    wrong_mean_based = (0.4 + 0.5 + 0.9 + 0.8) / 4

    assert abs(actual_ratio - expected_sum_based) < 1e-9, (
        f"all_residential ratio expected {expected_sum_based:.6f} "
        f"(sum-based), got {actual_ratio:.6f}")
    # Sanity check: the two formulas really do disagree on this fixture.
    assert abs(expected_sum_based - wrong_mean_based) > 0.05


def test_all_residential_counts_are_sums_across_types(tmp_path):
    rows = [
        _make_source_row("apt", n_rent_deals=1000, n_wolse=400),
        _make_source_row("rowhouse_multifamily", n_rent_deals=1000, n_wolse=500),
        _make_source_row("single_detached", n_rent_deals=100, n_wolse=90,
                         median_deposit_per_m2=float("nan"),
                         median_monthly_rent_per_m2=float("nan")),
        _make_source_row("officetel", n_rent_deals=1000, n_wolse=800),
    ]
    src = _write_source_panel(tmp_path, rows)
    merged = dpc.merge_optional_tenure(_dong_year_skeleton(), src)
    row = merged.iloc[0]
    assert row["tenure_n_rent_deals"] == 3100
    assert row["tenure_n_wolse"] == 1790
    assert row["tenure_n_jeonse"] == 3100 - 1790


# ----- 2. Per-type wolse_ratio rollups present and correct ---------------

def test_per_type_wolse_ratio_columns_present(tmp_path):
    rows = [_make_source_row(ht) for ht in dpc.TENURE_HOUSING_TYPES]
    src = _write_source_panel(tmp_path, rows)
    merged = dpc.merge_optional_tenure(_dong_year_skeleton(), src)
    for col in REQUIRED_WOLSE_RATIO_COLS:
        assert col in merged.columns, (
            f"merge_optional_tenure must expose {col}")


def test_per_type_wolse_ratio_values_recomputed_from_sums(tmp_path):
    # Each type's per-type ratio = n_wolse / n_rent_deals for that type alone.
    rows = [
        _make_source_row("apt", n_rent_deals=1000, n_wolse=400),
        _make_source_row("rowhouse_multifamily", n_rent_deals=1000, n_wolse=500),
        _make_source_row("single_detached", n_rent_deals=100, n_wolse=90,
                         median_deposit_per_m2=float("nan"),
                         median_monthly_rent_per_m2=float("nan")),
        _make_source_row("officetel", n_rent_deals=1000, n_wolse=800),
    ]
    src = _write_source_panel(tmp_path, rows)
    merged = dpc.merge_optional_tenure(_dong_year_skeleton(), src)
    row = merged.iloc[0]
    assert row["tenure_wolse_ratio_apt"] == 400 / 1000
    assert row["tenure_wolse_ratio_rowhouse_multifamily"] == 500 / 1000
    assert row["tenure_wolse_ratio_single_detached"] == 90 / 100
    assert row["tenure_wolse_ratio_officetel"] == 800 / 1000


# ----- 3. SH per-m² stays NaN, others computed ---------------------------

def test_sh_per_m2_columns_not_exposed(tmp_path):
    """SH per-m² is structurally undefined (totalFloorAr vs
    excluUseAr gap). The contract layer must NOT expose
    tenure_median_*_per_m2_single_detached columns at all — not
    'expose them as NaN', not exposed."""
    rows = [_make_source_row(ht) for ht in dpc.TENURE_HOUSING_TYPES]
    src = _write_source_panel(tmp_path, rows)
    merged = dpc.merge_optional_tenure(_dong_year_skeleton(), src)
    assert "tenure_median_deposit_per_m2_single_detached" not in merged.columns
    assert "tenure_median_monthly_rent_per_m2_single_detached" not in merged.columns


def test_per_m2_columns_for_apt_rh_offi_present(tmp_path):
    rows = [_make_source_row(ht) for ht in dpc.TENURE_HOUSING_TYPES]
    src = _write_source_panel(tmp_path, rows)
    merged = dpc.merge_optional_tenure(_dong_year_skeleton(), src)
    for col in REQUIRED_PER_M2_COLS:
        assert col in merged.columns


def test_per_m2_columns_carry_real_values_for_three_types(tmp_path):
    rows = [
        _make_source_row("apt", median_deposit_per_m2=300.0,
                         median_monthly_rent_per_m2=2.0),
        _make_source_row("rowhouse_multifamily",
                         median_deposit_per_m2=400.0,
                         median_monthly_rent_per_m2=3.0),
        _make_source_row("single_detached",
                         median_deposit_per_m2=float("nan"),
                         median_monthly_rent_per_m2=float("nan")),
        _make_source_row("officetel", median_deposit_per_m2=60.0,
                         median_monthly_rent_per_m2=1.5),
    ]
    src = _write_source_panel(tmp_path, rows)
    merged = dpc.merge_optional_tenure(_dong_year_skeleton(), src)
    row = merged.iloc[0]
    assert row["tenure_median_deposit_per_m2_apt"] == 300.0
    assert row["tenure_median_deposit_per_m2_rowhouse_multifamily"] == 400.0
    assert row["tenure_median_deposit_per_m2_officetel"] == 60.0
    assert row["tenure_median_monthly_rent_per_m2_apt"] == 2.0


# ----- 4. tenure_status flip and scope/source defaults -------------------

def test_status_flips_to_live_when_panel_present(tmp_path):
    rows = [_make_source_row(ht) for ht in dpc.TENURE_HOUSING_TYPES]
    src = _write_source_panel(tmp_path, rows)
    skel = _dong_year_skeleton()
    # Start from add_status_columns default
    skel = dpc.add_status_columns(skel)
    assert (skel["tenure_status"] == "missing_local_artifact").all()
    merged = dpc.merge_optional_tenure(skel, src)
    # Flipped to live (not live_partial — that label was retired)
    assert (merged["tenure_status"] == "live").all()


def test_status_stays_missing_when_panel_absent(tmp_path):
    skel = dpc.add_status_columns(_dong_year_skeleton())
    merged = dpc.merge_optional_tenure(skel, tmp_path / "does_not_exist.parquet")
    assert (merged["tenure_status"] == "missing_local_artifact").all()


def test_tenure_scope_records_all_four_housing_types():
    """The scope string is load-bearing: a future regression that
    narrowed the panel to one housing type would be visible in this
    field. Must enumerate all four types and never collapse to a
    short label like 'multi_housing'."""
    skel = dpc.add_status_columns(_dong_year_skeleton())
    scope = skel["tenure_scope"].iloc[0]
    for ht in dpc.TENURE_HOUSING_TYPES:
        assert ht in scope, (
            f"tenure_scope must enumerate {ht!r}; got {scope!r}")


def test_tenure_source_is_multi_housing():
    skel = dpc.add_status_columns(_dong_year_skeleton())
    assert skel["tenure_source"].iloc[0] == "data_go_kr_rtms_multi_housing"


def test_tenure_grain_is_gu_year():
    skel = dpc.add_status_columns(_dong_year_skeleton())
    assert skel["tenure_grain"].iloc[0] == "gu-year"


# ----- 5. Schema rejects ---------------------------------------------------

def test_missing_required_columns_raises(tmp_path):
    """If the source panel is missing housing_type, the merge must
    raise — this prevents a stale apartment-only wolse_molit.parquet
    from silently feeding the new code path."""
    rows = [{"lawd_cd": "11440", "year": 2024, "month": 1,
              "n_rent_deals": 100, "n_wolse": 40,
              "median_deposit_per_m2": 200.0,
              "median_monthly_rent_per_m2": 1.0}]
    src = _write_source_panel(tmp_path, rows)
    try:
        dpc.merge_optional_tenure(_dong_year_skeleton(), src)
        raise AssertionError("expected ValueError for missing housing_type")
    except ValueError as e:
        assert "housing_type" in str(e)


def test_unexpected_housing_type_value_raises(tmp_path):
    """If an unknown housing_type value sneaks in (typo, schema
    drift, accidentally concatenated cross-source panel), the merge
    must refuse."""
    rows = [_make_source_row("apt"),
             _make_source_row("commercial",  # not a valid type
                              median_deposit_per_m2=200.0,
                              median_monthly_rent_per_m2=1.0)]
    src = _write_source_panel(tmp_path, rows)
    try:
        dpc.merge_optional_tenure(_dong_year_skeleton(), src)
        raise AssertionError("expected ValueError for unknown housing_type")
    except ValueError as e:
        assert "commercial" in str(e)
