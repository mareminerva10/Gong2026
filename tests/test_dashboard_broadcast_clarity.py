"""tests/test_dashboard_broadcast_clarity.py

Locks the dashboard's gu-broadcast comprehension fix from the
2026-06-11 review (peer report: 'Unsold mean on current year table
is weirdly consistent: 0 or 64'). The data was correct — both pilot
gus are high-demand and the metric is gu × year grain broadcast onto
every dong in a gu by construction — but the rendering didn't make
that visible. The presentation fix:

  1. Every metric <option> carries a data-grain attribute in
     {dong-year, gu-year, national-year}.
  2. The current-year table column header reads
     "Metric value (gu broadcast)" / "(national broadcast)" when the
     active metric is non-dong-grain.
  3. A "Collapse gu-broadcast metric to one row per gu" toggle
     appears in the toolbar (hidden when a dong-grain metric is
     active), so the visitor can see one row per gu instead of 26
     identical rows for 마포구 and 14 identical rows for 강남구.
  4. The two companion unsold metrics (statnuri_unsold_max_units and
     statnuri_unsold_dec_units) are exposed in the dropdown so the
     mean's ambiguity ('sustained 64 vs one-month spike') can be
     disambiguated.

These tests are static (read INDEX_HTML as text) so they run without
a browser and without the export pipeline. The static-export tests
(tests/test_static_export.py) cover the export-time guarantees on
top of these.
"""
from __future__ import annotations

import re
from pathlib import Path

import dashboard_app as da


VALID_GRAINS = {"dong-year", "gu-year", "national-year"}

# Metrics that we expect to exist in the dropdown by data-grain.
# Not an exhaustive list — just the ones whose grain assignment is
# load-bearing for the broadcast labeling.
EXPECTED_BY_GRAIN = {
    "dong-year": {
        "physical_yoy_angular",
        "physical_yoy_cosine_dist",
        "physical_yoy_euclid",
        "physical_embedding_norm",
    },
    "gu-year": {
        "statnuri_unsold_mean_units",
        "statnuri_unsold_max_units",
        "statnuri_unsold_dec_units",
        "statnuri_completed_unsold_mean_units",
        "tenure_wolse_ratio_all_residential",
        "landuse_built_share",
    },
    "national-year": {
        "national_redevelopment_intensity_zone_count",
    },
}


def _option_lines() -> list[str]:
    """Pull every <option> ... </option> line out of metricSelect."""
    html = da.INDEX_HTML
    # Find the metric select block specifically — there are other
    # <select> elements (year, policy).
    m = re.search(
        r'<select id="metricSelect">(.*?)</select>',
        html, flags=re.DOTALL)
    assert m, "metricSelect not found in INDEX_HTML"
    return re.findall(r"<option[^>]*>[^<]*</option>", m.group(1))


def _option_attr(line: str, name: str) -> str | None:
    m = re.search(rf'\b{re.escape(name)}="([^"]*)"', line)
    return m.group(1) if m else None


# ----- 1. Every metric option has data-grain -----

def test_every_metric_option_has_data_grain():
    """No option may be missing the grain attribute — the JS path
    for the header label and the collapse-toggle visibility both
    read it. A future option added without data-grain would
    silently default to 'dong-year' and the user would never see
    the (gu broadcast) marker for that metric."""
    options = _option_lines()
    assert options, "no metric options found"
    missing = [opt for opt in options if "data-grain=" not in opt]
    assert not missing, (
        f"{len(missing)} metric option(s) missing data-grain: "
        f"{[_option_attr(o, 'value') for o in missing]}. Every "
        "option must specify its grain in "
        "{dong-year, gu-year, national-year}.")


def test_data_grain_values_are_valid():
    options = _option_lines()
    bad = []
    for opt in options:
        grain = _option_attr(opt, "data-grain")
        if grain not in VALID_GRAINS:
            bad.append((_option_attr(opt, "value"), grain))
    assert not bad, (
        f"options with invalid data-grain: {bad}. Valid values: "
        f"{sorted(VALID_GRAINS)}.")


def test_expected_metrics_have_expected_grain():
    """Cross-check the most important metrics' grain assignments
    explicitly. A future regression that misclassified
    statnuri_unsold_* as dong-year (and lost the gu-broadcast
    marker) would fail this test."""
    options_by_value = {}
    for opt in _option_lines():
        v = _option_attr(opt, "value")
        g = _option_attr(opt, "data-grain")
        if v:
            options_by_value[v] = g
    for grain, expected_metrics in EXPECTED_BY_GRAIN.items():
        for m in expected_metrics:
            assert m in options_by_value, (
                f"expected metric {m!r} missing from dropdown")
            assert options_by_value[m] == grain, (
                f"metric {m!r} has grain "
                f"{options_by_value[m]!r}, expected {grain!r}")


# ----- 2. New unsold companion metrics exposed -----

def test_unsold_max_metric_in_dropdown():
    options = _option_lines()
    values = [_option_attr(o, "value") for o in options]
    assert "statnuri_unsold_max_units" in values, (
        "statnuri_unsold_max_units must appear in the metric dropdown — "
        "it disambiguates the annual mean (sustained vs single-month "
        "spike) per the 2026-06-11 review.")


def test_unsold_dec_metric_in_dropdown():
    options = _option_lines()
    values = [_option_attr(o, "value") for o in options]
    assert "statnuri_unsold_dec_units" in values, (
        "statnuri_unsold_dec_units (December snapshot) must appear in "
        "the metric dropdown — it captures end-of-year inventory state, "
        "different from the annual mean.")


def test_unsold_companion_options_marked_gu_broadcast():
    options_by_value = {}
    for opt in _option_lines():
        v = _option_attr(opt, "value")
        g = _option_attr(opt, "data-grain")
        if v:
            options_by_value[v] = g
    for m in ("statnuri_unsold_max_units", "statnuri_unsold_dec_units"):
        assert options_by_value[m] == "gu-year", (
            f"{m} must have data-grain='gu-year' so the (gu broadcast) "
            "label fires in the table header.")


# ----- 3. Collapse toggle markup exists -----

def test_collapse_toggle_markup_present():
    html = da.INDEX_HTML
    assert 'id="collapseGuLabel"' in html, (
        "collapseGuLabel container missing — the toggle visibility "
        "logic in updateCollapseToggleVisibility() depends on it.")
    assert 'id="collapseGuToggle"' in html, (
        "collapseGuToggle checkbox missing — renderTable() reads its "
        "checked state to decide whether to collapse rows.")


def test_collapse_toggle_starts_hidden():
    """The default metric is dong-year, so the collapse toggle must
    NOT be visible on first paint. updateCollapseToggleVisibility()
    handles the dynamic case; the initial inline style handles the
    first-render-before-JS case."""
    html = da.INDEX_HTML
    # The collapseGuLabel must have style="display:none" as inline
    # default. Match a span of HTML that's roughly the opening tag.
    m = re.search(r'<label\s+id="collapseGuLabel"[^>]*>', html)
    assert m, "collapseGuLabel <label> opening tag not found"
    assert "display:none" in m.group(0).replace(" ", ""), (
        f"collapseGuLabel must start hidden via inline style; "
        f"got: {m.group(0)!r}.")


# ----- 4. Table header markup ready for dynamic labels -----

def test_metric_column_header_has_id():
    """renderTable() rewrites the metric column header text to add
    '(gu broadcast)' / '(national broadcast)'. The header needs an
    id so the JS can target it. Without the id, the suffix never
    appears."""
    assert 'id="thMetric"' in da.INDEX_HTML, (
        "Metric value <th> must carry id='thMetric' so renderTable "
        "can rewrite its text content to add the broadcast suffix.")


def test_dong_column_header_has_id():
    """When collapse is on, the Dong column shows '(N dongs)'
    counts grouped by gu — relabel the header accordingly."""
    assert 'id="thDong"' in da.INDEX_HTML, (
        "Dong <th> must carry id='thDong' so renderTable can switch "
        "it to 'Gu (collapsed)' when the gu-collapse toggle is on.")


# ----- 5. JS helpers wired in -----

def test_metric_grain_helper_defined():
    """metricGrain() is the load-bearing JS helper. If it's removed
    or renamed, the (gu broadcast) suffix and the collapse-toggle
    visibility both break silently."""
    assert "function metricGrain(" in da.INDEX_HTML
    assert "function metricIsBroadcast(" in da.INDEX_HTML
    assert "function metricBroadcastLabel(" in da.INDEX_HTML
    assert "function updateCollapseToggleVisibility(" in da.INDEX_HTML


def test_render_table_reads_collapse_toggle():
    """renderTable() must read collapseGuToggle.checked. The grep
    check is loose but catches the case where the toggle handler is
    removed during a future refactor."""
    assert "collapseGuToggle" in da.INDEX_HTML
    assert "metricIsBroadcast" in da.INDEX_HTML


# ----- 6. Claim guard remains clean — no new prohibited tokens -----

PROHIBITED_TOKENS = (
    "forecast", "prediction", "probability", "risk_score",
    "composite_score", "gentrification_score", "displacement_score",
)


def test_new_clarity_markup_introduces_no_prohibited_tokens():
    """The new option labels (max / December snapshot), the
    collapse-toggle wording, and the broadcast suffix must not
    accidentally include forecast / risk / score / probability
    vocabulary."""
    # Extract just the new strings added in this commit by checking
    # the option labels and the toggle copy.
    options = _option_lines()
    surfaces = []
    for opt in options:
        v = _option_attr(opt, "value") or ""
        if "unsold" in v or "Collapse" in opt:
            surfaces.append(opt)
    surfaces.append("Collapse gu-broadcast metric to one row per gu")
    surfaces.append("gu broadcast")
    surfaces.append("national broadcast")
    hits = []
    blob = " ".join(surfaces).lower()
    for tok in PROHIBITED_TOKENS:
        if tok in blob:
            hits.append(tok)
    assert not hits, (
        f"new clarity markup contains prohibited token(s): {hits}. "
        "Review the option labels and toggle copy.")
