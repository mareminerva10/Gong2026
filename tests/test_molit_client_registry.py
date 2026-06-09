"""tests/test_molit_client_registry.py

Static + offline guards for the housing-type registry in
`molit_client.py`. No network access required.

Three things this test enforces:

1. The registry is complete and well-formed: all four housing types
   present, distinct dataset_ids, area_kind values constrained to the
   two known options, URLs match the live-verified probe doc.

2. `_classify_and_normalize` respects area_kind:
   - 'exclusive_use' → per-m² metrics computed normally.
   - 'total_floor'   → per-m² metrics are NaN by construction (the
                       SH gap; see docs/rtms_siblings_probe_2026-06-09.md).

3. Output rows carry the `housing_type` column so downstream
   consumers cannot silently lose the type after a concat.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

import molit_client as mc


# ----- 1. Registry shape -----

EXPECTED_DATASET_IDS = {
    "apt":                  "15126474",
    "rowhouse_multifamily": "15126473",
    "single_detached":      "15126472",
    "officetel":            "15126475",
}

EXPECTED_URLS = {
    "apt":                  "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
    "rowhouse_multifamily": "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
    "single_detached":      "https://apis.data.go.kr/1613000/RTMSDataSvcSHRent/getRTMSDataSvcSHRent",
    "officetel":            "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
}

EXPECTED_AREA = {
    "apt":                  ("excluUseAr",   "exclusive_use"),
    "rowhouse_multifamily": ("excluUseAr",   "exclusive_use"),
    "single_detached":      ("totalFloorAr", "total_floor"),
    "officetel":            ("excluUseAr",   "exclusive_use"),
}


def test_registry_has_all_four_housing_types():
    assert set(mc.HOUSING_TYPE_REGISTRY.keys()) == set(EXPECTED_DATASET_IDS.keys()), (
        "registry must cover exactly the four planned housing types")


def test_registry_dataset_ids_match_expected():
    for ht, spec in mc.HOUSING_TYPE_REGISTRY.items():
        assert spec.dataset_id == EXPECTED_DATASET_IDS[ht], (
            f"{ht}: dataset_id mismatch (got {spec.dataset_id!r})")


def test_registry_dataset_ids_are_distinct():
    ids = [spec.dataset_id for spec in mc.HOUSING_TYPE_REGISTRY.values()]
    assert len(ids) == len(set(ids)), (
        f"dataset_ids must be distinct across the registry; got {ids}")


def test_registry_urls_match_smoke_probe():
    """URLs must match the live-verified set in
    docs/rtms_siblings_probe_2026-06-09.md. Drift here means the
    smoke verdict no longer applies."""
    for ht, spec in mc.HOUSING_TYPE_REGISTRY.items():
        assert spec.url == EXPECTED_URLS[ht], (
            f"{ht}: URL drift vs. probe doc (got {spec.url!r})")


def test_registry_area_fields_and_kinds():
    for ht, spec in mc.HOUSING_TYPE_REGISTRY.items():
        exp_field, exp_kind = EXPECTED_AREA[ht]
        assert spec.area_field == exp_field, f"{ht}: area_field"
        assert spec.area_kind == exp_kind, f"{ht}: area_kind"


def test_registry_area_kinds_are_one_of_two_known():
    valid = {"exclusive_use", "total_floor"}
    for ht, spec in mc.HOUSING_TYPE_REGISTRY.items():
        assert spec.area_kind in valid, (
            f"{ht}: unknown area_kind {spec.area_kind!r}; "
            f"_classify_and_normalize only handles {sorted(valid)}")


def test_registry_source_tags_are_distinct():
    tags = [spec.source_tag for spec in mc.HOUSING_TYPE_REGISTRY.values()]
    assert len(tags) == len(set(tags)), (
        f"source_tags must be distinct; got {tags}")


def test_default_outputs_keyed_by_housing_type():
    assert set(mc.DEFAULT_TENURE_OUTPUTS.keys()) == set(
        mc.HOUSING_TYPE_REGISTRY.keys())


def test_apt_default_output_is_legacy_path():
    """Step-2 backwards-compat: the existing dashboard pipeline reads
    from data/wolse_molit.parquet via dashboard_pilot_contract.
    Changing this path here without updating the contract would break
    the live UI between commits 2 and 4."""
    assert mc.DEFAULT_TENURE_OUTPUTS["apt"].as_posix() == "data/wolse_molit.parquet"


# ----- 2. _classify_and_normalize area_kind branching -----

def _excl_items():
    """Two synthetic apt-shaped items: one jeonse, one wolse."""
    return [
        {"deposit": "10,000", "monthlyRent": "0",
         "excluUseAr": "50.00", "umdNm": "공덕동", "sggCd": "11440"},
        {"deposit": "5,000",  "monthlyRent": "30",
         "excluUseAr": "25.00", "umdNm": "공덕동", "sggCd": "11440"},
    ]


def _total_floor_items():
    """Two synthetic SH-shaped items: one jeonse, one wolse."""
    return [
        {"deposit": "8,000",  "monthlyRent": "0",
         "totalFloorAr": "120.00", "umdNm": "성산동", "sggCd": "11440"},
        {"deposit": "1,000",  "monthlyRent": "45",
         "totalFloorAr": "18.00",  "umdNm": "성산동", "sggCd": "11440"},
    ]


def test_classify_exclusive_use_computes_per_m2():
    spec = mc.HOUSING_TYPE_REGISTRY["apt"]
    df = mc._classify_and_normalize(_excl_items(), "11440", 2024, 1, spec=spec)
    # Row 0: 10,000 / 50 = 200; monthly 0/50 = 0
    # Row 1: 5,000 / 25 = 200; monthly 30/25 = 1.2
    assert df["deposit_per_m2"].iloc[0] == pytest.approx(200.0)
    assert df["deposit_per_m2"].iloc[1] == pytest.approx(200.0)
    assert df["monthly_per_m2"].iloc[0] == pytest.approx(0.0)
    assert df["monthly_per_m2"].iloc[1] == pytest.approx(1.2)


def test_classify_total_floor_emits_nan_per_m2():
    """SH gap rule: SHRent only exposes totalFloorAr (whole-building
    gross), not excluUseAr (per-unit). Per-m² metrics MUST be NaN, not
    silently computed from totalFloorAr."""
    spec = mc.HOUSING_TYPE_REGISTRY["single_detached"]
    df = mc._classify_and_normalize(_total_floor_items(), "11440", 2024, 1,
                                     spec=spec)
    assert df["deposit_per_m2"].isna().all(), (
        "total_floor housing types must emit NaN for deposit_per_m2 "
        "(totalFloorAr is not comparable to excluUseAr)")
    assert df["monthly_per_m2"].isna().all(), (
        "total_floor housing types must emit NaN for monthly_per_m2")


def test_classify_total_floor_still_emits_counts_and_classification():
    """The SH gap rule only blocks per-m² metrics. Count fields and
    the wolse/jeonse classification must still work, because those
    are what the wolse_ratio annual rollup depends on."""
    spec = mc.HOUSING_TYPE_REGISTRY["single_detached"]
    df = mc._classify_and_normalize(_total_floor_items(), "11440", 2024, 1,
                                     spec=spec)
    assert len(df) == 2
    assert df["is_wolse"].tolist() == [0, 1]
    assert df["deposit_manwon"].tolist() == [8000.0, 1000.0]
    assert df["monthly_rent_manwon"].tolist() == [0.0, 45.0]


def test_classify_carries_housing_type_column():
    for ht, spec in mc.HOUSING_TYPE_REGISTRY.items():
        items = _excl_items() if spec.area_kind == "exclusive_use" else _total_floor_items()
        df = mc._classify_and_normalize(items, "11440", 2024, 1, spec=spec)
        assert "housing_type" in df.columns
        assert (df["housing_type"] == ht).all(), (
            f"{ht}: housing_type column must be uniformly {ht!r}")


def test_classify_missing_area_field_raises():
    """If the response is missing the expected area field for the
    housing type, the parser must raise — silent NaN here would mask
    an upstream schema change."""
    spec = mc.HOUSING_TYPE_REGISTRY["single_detached"]
    # SH expects totalFloorAr; supplying excluUseAr alone should raise.
    items = [{"deposit": "1,000", "monthlyRent": "0",
              "excluUseAr": "30.0", "umdNm": "x", "sggCd": "11440"}]
    with pytest.raises(RuntimeError, match="totalFloorAr"):
        mc._classify_and_normalize(items, "11440", 2024, 1, spec=spec)


def test_classify_default_spec_preserves_legacy_apt_behavior():
    """Calling _classify_and_normalize without `spec=` (legacy callers)
    must behave exactly like apt — same per-m² semantics, same
    housing_type column value."""
    df_default = mc._classify_and_normalize(_excl_items(), "11440", 2024, 1)
    df_apt = mc._classify_and_normalize(_excl_items(), "11440", 2024, 1,
                                         spec=mc.HOUSING_TYPE_REGISTRY["apt"])
    pd.testing.assert_frame_equal(df_default, df_apt)


# ----- 3. Aggregation propagates NaN per-m² for SH -----

def test_aggregate_handles_all_nan_per_m2_without_raising():
    """When per-m² is NaN by construction (SH), the gu-month
    aggregation must still produce a panel row — counts, wolse_ratio,
    and NaN medians — without errors."""
    spec = mc.HOUSING_TYPE_REGISTRY["single_detached"]
    df = mc._classify_and_normalize(_total_floor_items(), "11440", 2024, 1,
                                     spec=spec)
    panel = mc._aggregate_to_gu_month(df)
    assert len(panel) == 1
    row = panel.iloc[0]
    assert row["n_rent_deals"] == 2
    assert row["n_wolse"] == 1
    assert row["n_jeonse"] == 1
    assert row["wolse_ratio"] == pytest.approx(0.5)
    assert math.isnan(row["median_deposit_per_m2"])
    assert math.isnan(row["median_monthly_rent_per_m2"])


# ----- 4. Builder validates the housing_type argument -----

def test_build_seoul_tenure_panel_rejects_unknown_housing_type(monkeypatch):
    monkeypatch.setenv("MOLIT_SERVICE_KEY", "dummy-not-used-test-only")
    with pytest.raises(ValueError, match="Unknown housing_type"):
        mc.build_seoul_tenure_panel("condo", [2024])
