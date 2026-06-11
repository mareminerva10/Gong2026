"""tests/test_snapshot_and_scope.py

Two related guard suites for the Step 6 snapshot-based deploy:

  1. Snapshot purity. data/snapshot/ is the curated public-deploy
     input — exactly two whitelisted parquets, nothing else. A
     future commit that adds a third file (raw cache, oversize
     panel, accidental probe payload) must fail this test rather
     than silently shipping in the next workflow_dispatch deploy.

  2. Positive scope disclosure. The 2026 reframe's claim guard is
     negative (no forecast / risk / score vocabulary in title/H1).
     This is the missing POSITIVE guard: the exported HTML must
     *visibly* tell the visitor that the dashboard is a 40-dong
     pilot covering 마포구 + 강남구 over 2017–2024. Without it, a
     future copy edit could remove the scope disclosure entirely
     and ship an overclaimed product.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import export_static_dashboard as exp


REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshot"

# The exact set of files the snapshot directory is allowed to contain.
ALLOWED_SNAPSHOT_FILES = frozenset({
    "dashboard_pilot_contract.parquet",
    "pilot_legal_dong_manifest.parquet",
})

# Max sane size for either snapshot file. The two together are ~440 KB
# in practice; this cap stops a future regression that, e.g., committed
# the full 9,600-row rtms_rent_panel (which would be MB-scale and
# defeat the whole snapshot-as-public-deploy-input pattern).
SNAPSHOT_FILE_BYTE_CEILING = 4 * 1024 * 1024  # 4 MiB hard cap

# Substrings the exported page must positively contain to disclose
# pilot scope. Each line is one of three required disclosures.
# Multiple equivalents are accepted per disclosure so a future copy
# edit isn't a brittle string match.
REQUIRED_SCOPE_DISCLOSURES = (
    # 1. Pilot size — "40 dongs" or "40-dong" anywhere in the page.
    ("40-dong", "40 dong"),
    # 2. Gu coverage — Korean or Romanized form.
    ("마포구", "강남구", "mapo-gu", "gangnam-gu"),
    # 3. Year range — 2017 to 2024 with various dash characters.
    ("2017–2024", "2017-2024", "2017 to 2024"),
)


# ----- 1. Snapshot purity -----

def test_snapshot_directory_exists():
    assert SNAPSHOT_DIR.is_dir(), (
        f"data/snapshot/ must exist as a directory; it is the "
        "curated public-deploy input the workflow_dispatch deploy "
        "reads. See project-next-session-step6-snapshot-deploy-2026-06-11.")


def test_snapshot_contains_only_whitelisted_files():
    """Strict allowlist: only the two named parquets are allowed in
    data/snapshot/. Any other file (raw cache, oversize panel,
    accidental probe payload) fails this test."""
    actual = {p.name for p in SNAPSHOT_DIR.iterdir() if p.is_file()}
    extras = actual - ALLOWED_SNAPSHOT_FILES
    missing = ALLOWED_SNAPSHOT_FILES - actual
    assert not extras, (
        f"data/snapshot/ contains files outside the allowlist: {sorted(extras)}. "
        f"Only {sorted(ALLOWED_SNAPSHOT_FILES)} are allowed. The snapshot "
        "directory is the public-deploy input, not a data lake.")
    assert not missing, (
        f"data/snapshot/ missing required files: {sorted(missing)}. "
        f"Both {sorted(ALLOWED_SNAPSHOT_FILES)} are required by the "
        "workflow_dispatch deploy.")


def test_snapshot_files_are_small():
    """No snapshot file may exceed SNAPSHOT_FILE_BYTE_CEILING. Hard
    ceiling guards against committing the 9,600-row rtms_rent_panel
    or a full-Seoul AlphaEarth panel by mistake."""
    for name in ALLOWED_SNAPSHOT_FILES:
        path = SNAPSHOT_DIR / name
        size = path.stat().st_size
        assert size <= SNAPSHOT_FILE_BYTE_CEILING, (
            f"{path} is {size:,} bytes, exceeds {SNAPSHOT_FILE_BYTE_CEILING:,} "
            "byte ceiling. Snapshot is for the curated public deploy, "
            "not raw data panels.")


def test_snapshot_contract_loads_and_has_expected_shape():
    """End-to-end smoke that the snapshot contract is actually
    parseable and matches the production pilot shape (320 rows over
    40 dongs × 8 years). Catches the case where the snapshot file
    was overwritten by a different (e.g. mock) artifact by accident."""
    contract = pd.read_parquet(SNAPSHOT_DIR / "dashboard_pilot_contract.parquet")
    assert len(contract) == 320, (
        f"snapshot contract has {len(contract)} rows, expected 320 "
        "(40 dongs × 8 years). Snapshot may be stale or corrupt.")
    assert contract["emd_cd"].nunique() == 40, (
        f"snapshot contract has {contract['emd_cd'].nunique()} dongs, "
        "expected 40. Snapshot may be a different pilot scope.")
    years = sorted(contract["year"].unique().tolist())
    assert years == list(range(2017, 2025)), (
        f"snapshot contract years are {years}, expected 2017–2024. "
        "Snapshot may be a partial or extended pilot.")


def test_snapshot_manifest_loads_and_covers_pilot_polygons():
    """Smoke: the manifest is the polygon source. Catches an
    accidentally-replaced manifest covering the wrong area."""
    import geopandas as gpd
    manifest = gpd.read_parquet(SNAPSHOT_DIR / "pilot_legal_dong_manifest.parquet")
    assert len(manifest) == 40, (
        f"snapshot manifest has {len(manifest)} polygons, expected 40 "
        "(마포구 + 강남구 pilot).")


# ----- 2. End-to-end export from snapshot -----

def test_export_from_snapshot_succeeds(tmp_path):
    """Run the full export pipeline against the committed snapshot.
    Catches a regression where the snapshot and the export code drift
    apart (e.g. a new required column was added to load_payload but
    not to the snapshot)."""
    out_dir = tmp_path / "public"
    result = exp.export(SNAPSHOT_DIR / "dashboard_pilot_contract.parquet",
                        SNAPSHOT_DIR / "pilot_legal_dong_manifest.parquet",
                        out_dir)
    assert result["row_count"] == 320
    assert result["polygon_count"] == 40
    assert result["payload_bytes"] < exp.PAYLOAD_BYTE_CEILING
    assert (out_dir / "index.html").exists()
    assert (out_dir / "payload.json").exists()


# ----- 3. Positive scope-disclosure guard -----

def _exported_html() -> str:
    """Build the export once into a tmp-free path and read back the
    rendered HTML. Reuses the snapshot inputs."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "public"
        exp.export(SNAPSHOT_DIR / "dashboard_pilot_contract.parquet",
                   SNAPSHOT_DIR / "pilot_legal_dong_manifest.parquet",
                   out_dir)
        return (out_dir / "index.html").read_text(encoding="utf-8")


def test_exported_html_discloses_40_dong_pilot_scope():
    html = _exported_html().lower()
    pilot_size_alternates = REQUIRED_SCOPE_DISCLOSURES[0]
    assert any(s.lower() in html for s in pilot_size_alternates), (
        f"exported HTML must visibly state the pilot is 40-dong; "
        f"none of {pilot_size_alternates} found. A visitor must be "
        "able to tell within 5 seconds that this is a 40-dong pilot, "
        "not 'all of Seoul' or 'all of Korea'.")


def test_exported_html_names_pilot_gus():
    html = _exported_html().lower()
    gu_alternates = REQUIRED_SCOPE_DISCLOSURES[1]
    assert any(s.lower() in html for s in gu_alternates), (
        f"exported HTML must visibly name the pilot gus; none of "
        f"{gu_alternates} found. The product is geographically "
        "scoped to two specific Seoul gus and must say so.")


def test_exported_html_states_year_range():
    html = _exported_html().lower()
    year_alternates = REQUIRED_SCOPE_DISCLOSURES[2]
    assert any(s.lower() in html for s in year_alternates), (
        f"exported HTML must visibly state the year range; none of "
        f"{year_alternates} found. Temporal scope matters: 2017–2024 "
        "is materially different from 'current' or 'latest data'.")


def test_exported_payload_summary_year_range_matches_disclosure():
    """Belt-and-braces: the payload's `summary.years` must cover the
    range the HTML claims. A future divergence (HTML says 2017–2024
    but data is 2020–2024) would mislead the visitor without this
    test."""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "public"
        exp.export(SNAPSHOT_DIR / "dashboard_pilot_contract.parquet",
                   SNAPSHOT_DIR / "pilot_legal_dong_manifest.parquet",
                   out_dir)
        payload = json.loads((out_dir / "payload.json").read_text(encoding="utf-8"))
    assert payload["summary"]["years"] == list(range(2017, 2025)), (
        f"payload summary years {payload['summary']['years']} do not "
        "match the HTML's claimed 2017–2024 scope.")
