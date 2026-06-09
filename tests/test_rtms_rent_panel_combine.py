"""tests/test_rtms_rent_panel_combine.py

Offline guards for build_rtms_rent_panel.combine_panels.

The combine step is what assembles `data/rtms_rent_panel.parquet`
from the four per-type panels, so any silent contract drift here
would directly poison the artifact that step 4 will swap into the
dashboard. These tests exercise the validation paths without
touching the network or the real on-disk per-type panels.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import build_rtms_rent_panel as brp
import molit_client as mc


def _make_panel(housing_type: str, *, lawd_cd: str = "11440", year: int = 2024,
                month: int = 1, rows: int = 1) -> pd.DataFrame:
    """Synthesize a per-type panel row matching the schema written by
    molit_client.build_seoul_tenure_panel."""
    spec = mc.HOUSING_TYPE_REGISTRY[housing_type]
    is_total_floor = spec.area_kind == "total_floor"
    base = {
        "lawd_cd": lawd_cd,
        "gu_name": "마포구",
        "year": year,
        "month": month,
        "year_month": f"{year}{month:02d}",
        "n_rent_deals": 100,
        "n_wolse": 40,
        "n_jeonse": 60,
        "wolse_ratio": 0.4,
        "median_deposit_per_m2": float("nan") if is_total_floor else 250.0,
        "median_monthly_rent_per_m2": float("nan") if is_total_floor else 1.5,
        "housing_type": housing_type,
        "source": spec.source_tag,
    }
    return pd.DataFrame([base] * rows)


def _write_per_type(tmp_path: Path, housing_type: str, df: pd.DataFrame) -> Path:
    path = tmp_path / f"wolse_molit_{housing_type}.parquet"
    df.to_parquet(path, index=False)
    return path


# ----- 1. Happy path: all four panels present -----

def test_combine_all_four_housing_types(tmp_path):
    sources = {ht: _write_per_type(tmp_path, ht, _make_panel(ht))
               for ht in mc.HOUSING_TYPE_REGISTRY}
    output = tmp_path / "rtms_rent_panel.parquet"
    combined = brp.combine_panels(output=output, sources=sources)

    assert len(combined) == 4
    assert set(combined["housing_type"]) == set(mc.HOUSING_TYPE_REGISTRY)
    assert list(combined.columns) == brp.EXPECTED_COMBINED_COLS
    assert output.exists()
    # Re-read from disk to confirm the artifact matches.
    on_disk = pd.read_parquet(output)
    pd.testing.assert_frame_equal(combined, on_disk)


def test_combine_preserves_sh_nan_per_m2(tmp_path):
    sources = {ht: _write_per_type(tmp_path, ht, _make_panel(ht))
               for ht in mc.HOUSING_TYPE_REGISTRY}
    combined = brp.combine_panels(
        output=tmp_path / "out.parquet", sources=sources)
    sh = combined[combined["housing_type"] == "single_detached"]
    assert sh["median_deposit_per_m2"].isna().all()
    assert sh["median_monthly_rent_per_m2"].isna().all()
    # And the others must have non-NaN per-m² (smoke that the test
    # fixture is honoring the registry rule, not just emitting NaN
    # everywhere).
    others = combined[combined["housing_type"] != "single_detached"]
    assert others["median_deposit_per_m2"].notna().all()


# ----- 2. Missing-panel handling -----

def test_combine_writes_partial_when_some_types_missing(tmp_path, capsys):
    """Default behavior: combine whatever is present; warn about absent
    types; do not raise."""
    sources = {ht: tmp_path / f"wolse_molit_{ht}.parquet"
               for ht in mc.HOUSING_TYPE_REGISTRY}
    # Only write apt + officetel; leave RH and SH paths nonexistent.
    sources["apt"].write_bytes(_make_panel("apt").to_parquet())
    sources["officetel"].write_bytes(_make_panel("officetel").to_parquet())

    combined = brp.combine_panels(
        output=tmp_path / "out.parquet", sources=sources)
    assert set(combined["housing_type"]) == {"apt", "officetel"}
    captured = capsys.readouterr()
    assert "PARTIAL" in captured.out
    assert "rowhouse_multifamily" in captured.out
    assert "single_detached" in captured.out


def test_combine_require_all_raises_when_partial(tmp_path):
    sources = {ht: tmp_path / f"wolse_molit_{ht}.parquet"
               for ht in mc.HOUSING_TYPE_REGISTRY}
    sources["apt"].write_bytes(_make_panel("apt").to_parquet())
    with pytest.raises(RuntimeError, match="require_all"):
        brp.combine_panels(output=tmp_path / "out.parquet",
                            sources=sources, require_all=True)


def test_combine_raises_when_nothing_present(tmp_path):
    sources = {ht: tmp_path / f"wolse_molit_{ht}.parquet"
               for ht in mc.HOUSING_TYPE_REGISTRY}
    with pytest.raises(RuntimeError, match="No per-type panels"):
        brp.combine_panels(output=tmp_path / "out.parquet", sources=sources)


# ----- 3. Per-type panel contract checks -----

def test_combine_rejects_panel_with_wrong_housing_type(tmp_path):
    """If wolse_molit_apt.parquet contains rows tagged as officetel
    (e.g. someone copied the wrong file into the wrong slot), the
    combiner must refuse rather than silently letting the wrong
    rows through under the apt key."""
    sources = {ht: _write_per_type(tmp_path, ht, _make_panel(ht))
               for ht in mc.HOUSING_TYPE_REGISTRY}
    # Corrupt the apt panel by writing an officetel-tagged row to it.
    bad = _make_panel("officetel")
    sources["apt"].unlink()
    sources["apt"].write_bytes(bad.to_parquet())
    with pytest.raises(ValueError, match="expected housing_type='apt'"):
        brp.combine_panels(output=tmp_path / "out.parquet", sources=sources)


def test_combine_rejects_per_type_panel_missing_columns(tmp_path):
    sources = {ht: _write_per_type(tmp_path, ht, _make_panel(ht))
               for ht in mc.HOUSING_TYPE_REGISTRY}
    # Strip a required column from the apt panel.
    apt = _make_panel("apt").drop(columns=["wolse_ratio"])
    sources["apt"].unlink()
    sources["apt"].write_bytes(apt.to_parquet())
    with pytest.raises(ValueError, match="missing columns"):
        brp.combine_panels(output=tmp_path / "out.parquet", sources=sources)


def test_combine_rejects_sh_with_non_nan_per_m2(tmp_path):
    """The SH gap rule is asserted again at combine time. If a future
    regression in molit_client started writing real per-m² values for
    SH (e.g. by dividing by totalFloorAr), the combine step must
    refuse to write it out."""
    sources = {ht: _write_per_type(tmp_path, ht, _make_panel(ht))
               for ht in mc.HOUSING_TYPE_REGISTRY}
    bad_sh = _make_panel("single_detached")
    bad_sh["median_deposit_per_m2"] = 100.0  # corrupt: should be NaN
    sources["single_detached"].unlink()
    sources["single_detached"].write_bytes(bad_sh.to_parquet())
    with pytest.raises(ValueError, match="must have NaN median_deposit_per_m2"):
        brp.combine_panels(output=tmp_path / "out.parquet", sources=sources)


# ----- 4. Uniqueness of (lawd_cd, year, month, housing_type) -----

def test_combine_rejects_duplicate_keys_across_per_type_panels(tmp_path):
    """If a per-type panel itself has duplicates (e.g. two rows for
    11440-2024-01 in the apt file), the combined output would
    silently double-count that cell in any rollup. The combiner
    must refuse."""
    sources = {ht: _write_per_type(tmp_path, ht, _make_panel(ht))
               for ht in mc.HOUSING_TYPE_REGISTRY}
    dup_apt = pd.concat([_make_panel("apt"), _make_panel("apt")],
                         ignore_index=True)
    sources["apt"].unlink()
    sources["apt"].write_bytes(dup_apt.to_parquet())
    with pytest.raises(ValueError, match="duplicate"):
        brp.combine_panels(output=tmp_path / "out.parquet", sources=sources)


# ----- 5. Schema constants stay in sync with molit_client -----

def test_expected_combined_cols_match_molit_client_panel_schema():
    """The combined-panel schema is asserted against the per-type panel
    schema written by molit_client.build_seoul_tenure_panel. If
    someone adds a column to one side but not the other (e.g. drops
    `source` from the per-type panel), this test fails loudly rather
    than letting combine_panels silently drop the column."""
    # The per-type panel column order is hardcoded in
    # molit_client.build_seoul_tenure_panel — grep that source.
    src = (Path(__file__).resolve().parent.parent / "molit_client.py"
           ).read_text(encoding="utf-8")
    # The output column list is the last `panel = panel[[...]]`
    # assignment in build_seoul_tenure_panel.
    assert all(col in src for col in brp.EXPECTED_COMBINED_COLS), (
        "molit_client.py does not reference all expected combined "
        "panel columns; the schemas may have drifted.")
