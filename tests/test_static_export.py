"""tests/test_static_export.py

Offline guards for `export_static_dashboard.export`. No real contract
parquet required — these tests synthesize tiny inputs in a tmp
directory and exercise each guardrail directly.

The static export is the artifact that goes public on Firebase
Hosting. Any regression here would either:

  - leak raw AlphaEarth embedding bands (A00..A63),
  - publish a payload too large for the Spark 360 MB/day egress
    budget at usable first-load counts,
  - re-introduce forecast/risk/score vocabulary banned by the 2026
    reframe,
  - or break the localhost → static fetch URL rewrite so the
    deployed page can't load its own data.

Each test below corresponds to one of those failure modes.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import export_static_dashboard as exp


# ----- helpers -----

def _write_skeleton_contract(tmp_path: Path,
                              extra_columns: dict | None = None) -> Path:
    """Build the minimum dashboard contract parquet that load_payload
    can read end-to-end. Mirrors the columns dashboard_app.load_payload
    actually reads via DISPLAY_COLS — anything else is irrelevant to the
    export."""
    base = {
        "emd_cd": ["1144010100", "1144010100", "1168010100", "1168010100"],
        "dong_name_kr": ["공덕동", "공덕동", "역삼동", "역삼동"],
        "lawd_cd": ["11440", "11440", "11680", "11680"],
        "gu_name": ["마포구", "마포구", "강남구", "강남구"],
        "year": [2023, 2024, 2023, 2024],
        "centroid_lat": [37.55, 37.55, 37.50, 37.50],
        "centroid_lon": [126.95, 126.95, 127.04, 127.04],
        "physical_yoy_angular": [0.01, 0.02, 0.03, 0.04],
        "physical_2022_artifact_flag": [False, False, False, False],
        "physical_status": ["live"] * 4,
        "physical_artifact_policy": ["metric_year_fe"] * 4,
        "tenure_status": ["live"] * 4,
        "vulnerability_status": ["not_scoped"] * 4,
        "housing_stress_status": ["live"] * 4,
        "development_pressure_status": ["live"] * 4,
        "completed_unsold_status": ["live"] * 4,
        "landuse_status": ["live"] * 4,
        "composite_score_status": ["not_computed"] * 4,
        "dashboard_claim_scope": ["descriptive_physical_change_only"] * 4,
        "development_pressure_spatial_variation": ["gu"] * 4,
        "metric_year_fe_scope": ["pilot_cross_dong"] * 4,
    }
    if extra_columns:
        base.update(extra_columns)
    df = pd.DataFrame(base)
    path = tmp_path / "contract.parquet"
    df.to_parquet(path, index=False)
    return path


# ----- 1. No raw embedding bands in the export payload -----

def test_export_rejects_payload_with_embedding_bands(tmp_path):
    """Future-proofing: if someone adds A00..A63 back to DISPLAY_COLS
    or the contract, the export must refuse rather than republish raw
    AlphaEarth vectors to a public URL."""
    extra = {f"A{i:02d}": [0.1, 0.2, 0.3, 0.4] for i in range(64)}
    contract = _write_skeleton_contract(tmp_path, extra_columns=extra)
    # Patch DISPLAY_COLS in-memory so load_payload retains the Axx
    # columns (simulating the regression).
    import dashboard_app as da
    original = da.DISPLAY_COLS
    try:
        da.DISPLAY_COLS = original + [f"A{i:02d}" for i in range(64)]
        with pytest.raises(RuntimeError, match="embedding bands"):
            exp.export(contract,
                       manifest_path=tmp_path / "no_manifest.parquet",
                       output_dir=tmp_path / "public")
    finally:
        da.DISPLAY_COLS = original


def test_export_passes_when_embedding_bands_already_excluded(tmp_path):
    """Sanity: a normal contract (no A00..A63 carried through
    DISPLAY_COLS) should export without tripping the embedding
    guardrail. This is the production path."""
    contract = _write_skeleton_contract(tmp_path)
    result = exp.export(contract,
                        manifest_path=tmp_path / "no_manifest.parquet",
                        output_dir=tmp_path / "public")
    assert result["row_count"] == 4


# ----- 2. Payload size below 2 MiB ceiling -----

def test_export_rejects_oversize_payload(tmp_path, monkeypatch):
    """If a future change inflates the payload past 2 MiB (e.g.
    polygon explosion, new high-cardinality fields), the export must
    refuse so we don't quietly burn the Spark egress budget."""
    contract = _write_skeleton_contract(tmp_path)
    monkeypatch.setattr(exp, "PAYLOAD_BYTE_CEILING", 100)
    with pytest.raises(RuntimeError, match="exceeds ceiling"):
        exp.export(contract,
                   manifest_path=tmp_path / "no_manifest.parquet",
                   output_dir=tmp_path / "public")


def test_export_payload_written_under_real_ceiling(tmp_path):
    """The skeleton contract is tiny; assert the produced
    payload.json is well below the real 2 MiB ceiling, so the test
    fixture itself is not load-bearing on the ceiling value."""
    contract = _write_skeleton_contract(tmp_path)
    result = exp.export(contract,
                        manifest_path=tmp_path / "no_manifest.parquet",
                        output_dir=tmp_path / "public")
    assert result["payload_bytes"] < exp.PAYLOAD_BYTE_CEILING


# ----- 3. No prohibited claim strings in the exported HTML -----

def test_export_rejects_prohibited_claim_vocabulary(tmp_path, monkeypatch):
    """If a future copy edit reintroduces `composite_score`,
    `risk_score`, `gentrification_score`, etc. into INDEX_HTML, the
    export must refuse. The negation-context exception (`alarm`,
    `forecast`) is documented in the export module."""
    contract = _write_skeleton_contract(tmp_path)
    import dashboard_app as da
    original = da.INDEX_HTML
    try:
        # Inject a banned substring into the template body.
        da.INDEX_HTML = original.replace(
            "<h1>Seoul built-environment change tracker</h1>",
            "<h1>Seoul gentrification_score map</h1>")
        with pytest.raises(RuntimeError, match="prohibited claim substrings"):
            exp.export(contract,
                       manifest_path=tmp_path / "no_manifest.parquet",
                       output_dir=tmp_path / "public")
    finally:
        da.INDEX_HTML = original


def test_export_allows_existing_negation_uses(tmp_path):
    """The dashboard's current copy uses 'alarm' and 'forecast' in
    NEGATION contexts ('not a forecast', 'no alarm/EWS
    interpretation'). The export must not refuse these."""
    contract = _write_skeleton_contract(tmp_path)
    # No monkeypatch — use the real INDEX_HTML which DOES contain
    # "alarm/EWS/forecast" in the artifact notice. This must succeed.
    result = exp.export(contract,
                        manifest_path=tmp_path / "no_manifest.parquet",
                        output_dir=tmp_path / "public")
    assert result["html_bytes"] > 0


# ----- 4. Fetch URL rewrite -----

def test_export_rewrites_fetch_url_from_api_to_static_json(tmp_path):
    """The deployed page must call ./payload.json, not the localhost
    server's /api/contract."""
    contract = _write_skeleton_contract(tmp_path)
    output_dir = tmp_path / "public"
    exp.export(contract,
               manifest_path=tmp_path / "no_manifest.parquet",
               output_dir=output_dir)
    html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert 'fetch("./payload.json")' in html
    assert 'fetch("/api/contract")' not in html


def test_export_rejects_template_without_expected_fetch_url(tmp_path, monkeypatch):
    """If a future edit to dashboard_app.INDEX_HTML changes or
    duplicates the fetch URL, the export must surface that rather
    than silently writing a broken HTML file."""
    contract = _write_skeleton_contract(tmp_path)
    import dashboard_app as da
    original = da.INDEX_HTML
    try:
        # Remove the expected occurrence entirely.
        da.INDEX_HTML = original.replace('fetch("/api/contract")',
                                          'fetch("/somewhere_else")')
        with pytest.raises(RuntimeError, match="expected exactly one"):
            exp.export(contract,
                       manifest_path=tmp_path / "no_manifest.parquet",
                       output_dir=tmp_path / "public")
    finally:
        da.INDEX_HTML = original


def test_export_rejects_template_with_duplicate_fetch_url(tmp_path, monkeypatch):
    contract = _write_skeleton_contract(tmp_path)
    import dashboard_app as da
    original = da.INDEX_HTML
    try:
        da.INDEX_HTML = original + '\n// duplicate fetch("/api/contract")'
        with pytest.raises(RuntimeError, match="expected exactly one"):
            exp.export(contract,
                       manifest_path=tmp_path / "no_manifest.parquet",
                       output_dir=tmp_path / "public")
    finally:
        da.INDEX_HTML = original


# ----- 5. Output structure -----

def test_export_writes_both_index_and_payload(tmp_path):
    contract = _write_skeleton_contract(tmp_path)
    output_dir = tmp_path / "public"
    exp.export(contract,
               manifest_path=tmp_path / "no_manifest.parquet",
               output_dir=output_dir)
    assert (output_dir / "index.html").exists()
    assert (output_dir / "payload.json").exists()


def test_export_payload_is_valid_json_with_expected_top_keys(tmp_path):
    import json
    contract = _write_skeleton_contract(tmp_path)
    output_dir = tmp_path / "public"
    exp.export(contract,
               manifest_path=tmp_path / "no_manifest.parquet",
               output_dir=output_dir)
    payload = json.loads(
        (output_dir / "payload.json").read_text(encoding="utf-8"))
    assert {"summary", "rows", "polygons"} <= set(payload.keys())


def test_export_missing_contract_raises_clear_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="contract parquet missing"):
        exp.export(tmp_path / "nonexistent.parquet",
                   manifest_path=tmp_path / "no_manifest.parquet",
                   output_dir=tmp_path / "public")
