"""
tests/test_contract_schema.py
=============================

Static schema guard for `dashboard_pilot_contract.py`. The dashboard
contract is the load-bearing handoff table for everything downstream
(the UI, the documented MVP claims, the four-block status badges). When
a sub-row is added to a block, the corresponding status field must be
present in both `add_status_columns()` (default initialization) and
`validate_contract()` (expected_status assertion); otherwise a new sub-
row can land without the validation gate noticing it.

This test reads the source verbatim and asserts both sites are in sync
with the canonical block-status field list. It does NOT actually build
a contract — that requires the AlphaEarth panel, which is gitignored
and not available in CI. Source-level checks give us the regression
guard without the data dependency.

For block-list changes, update `EXPECTED_BLOCK_STATUS_FIELDS` here and
ensure both `add_status_columns()` and `validate_contract()` are
updated consistently in the same commit.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_SRC = (REPO_ROOT / "dashboard_pilot_contract.py").read_text(encoding="utf-8")

# Block-status fields that the dashboard contract must initialize as
# defaults and validate as expected_status. Keep alphabetical for diff
# legibility.
EXPECTED_BLOCK_STATUS_FIELDS = (
    "completed_unsold_status",
    "composite_score_status",
    "development_pressure_status",
    "housing_stress_status",
    "landuse_status",
    "physical_status",
    "tenure_status",
    "vulnerability_status",
)


def test_add_status_columns_initializes_all_blocks() -> None:
    """Each status field is set in `add_status_columns()`."""
    # Extract the add_status_columns function body.
    m = re.search(
        r"def add_status_columns\(.*?\)[^\n]*:\n(.+?)(?=\ndef |\Z)",
        CONTRACT_SRC, flags=re.DOTALL)
    assert m, "add_status_columns() not found in dashboard_pilot_contract.py"
    body = m.group(1)
    missing = [f for f in EXPECTED_BLOCK_STATUS_FIELDS
               if f'"{f}"' not in body and f"'{f}'" not in body]
    assert not missing, (
        f"add_status_columns() does not initialize: {missing}. Each block "
        "must have a default status; otherwise the column is absent and "
        "validate_contract() will hard-fail."
    )


def test_validate_contract_asserts_all_block_statuses() -> None:
    """Each status field is enumerated in `expected_status` inside
    `validate_contract()`."""
    m = re.search(
        r"def validate_contract\(.*?\)[^\n]*:\n(.+?)(?=\ndef |\Z)",
        CONTRACT_SRC, flags=re.DOTALL)
    assert m, "validate_contract() not found in dashboard_pilot_contract.py"
    body = m.group(1)
    es = re.search(r"expected_status\s*=\s*\{(.+?)\}", body, flags=re.DOTALL)
    assert es, "expected_status dict not found in validate_contract()"
    es_body = es.group(1)
    missing = [f for f in EXPECTED_BLOCK_STATUS_FIELDS
               if f'"{f}"' not in es_body and f"'{f}'" not in es_body]
    assert not missing, (
        f"validate_contract() does not assert: {missing}. Without an "
        "expected_status entry, a regression could ship a contract with a "
        "block silently in the wrong state."
    )


def test_no_forecast_column_substrings_in_select_columns() -> None:
    """The `PROHIBITED_SUBSTRINGS` tuple at the top of the module must
    still cover the same forecast/score vocabulary that the 2026-06-07
    reframe removed. This is the runtime guard inside validate_contract
    that fails the contract if a forbidden column name ever appears."""
    m = re.search(
        r"PROHIBITED_SUBSTRINGS\s*=\s*\((.+?)\)",
        CONTRACT_SRC, flags=re.DOTALL)
    assert m, "PROHIBITED_SUBSTRINGS tuple not found"
    body = m.group(1).lower()
    must_contain = (
        "forecast",
        "prediction",
        "probability",
        "risk_score",
        "composite_score",
        "gentrification_score",
    )
    missing = [w for w in must_contain if w not in body]
    assert not missing, (
        f"PROHIBITED_SUBSTRINGS lost coverage of: {missing}. The contract's "
        "column-name guard depends on these strings; never narrow this "
        "list without an explicit reframe."
    )
