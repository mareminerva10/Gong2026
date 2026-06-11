"""tests/test_firebase_hosting_config.py

Static guards on firebase.json hosting config. The original
2026-06-11 config had source `"/index.html"`, which Firebase
matches against the URL path — and the URL path for the bare root
is `/`, not `/index.html`. The result was that the bare root URL
fell through to Firebase's default `max-age=3600` cache instead of
the intended `no-cache`, and a reframe / data refresh would not
roll out to existing visitors for up to an hour.

These tests parse firebase.json and assert that:
  - the bare root URL `/` is explicitly covered by a no-cache rule
  - HTML files in general are explicitly covered by a no-cache rule
  - payload.json carries a short max-age (not no-cache, since the
    payload is regenerated only when the user redeploys)

A future config edit that drops any of those guarantees fails this
test before the broken config ships.
"""
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "firebase.json"


def _load_headers() -> list[dict]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "hosting" in cfg, "firebase.json missing top-level 'hosting' key"
    return cfg["hosting"].get("headers", [])


def _value_for(headers_block: list[dict], key: str) -> str | None:
    for h in headers_block:
        if h.get("key", "").lower() == key.lower():
            return h.get("value")
    return None


def test_firebase_json_parses():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert cfg["hosting"]["public"] == "public"


def test_bare_root_url_has_no_cache():
    """The exact bug we hit: `source: '/index.html'` did NOT match the
    bare root URL `/`. There must be an explicit `/` rule (or an
    equivalent glob like `**`) that sets no-cache on the root."""
    headers = _load_headers()
    # Accept any of these source patterns as matching the bare root.
    valid_root_sources = {"/", "**", "**/*"}
    root_rule = next((r for r in headers
                      if r.get("source") in valid_root_sources), None)
    assert root_rule is not None, (
        "firebase.json must have a header rule whose source matches "
        f"the bare root URL '/'. Valid source patterns: "
        f"{sorted(valid_root_sources)}. Without this, the deployed "
        "index.html served at '/' falls through to Firebase's default "
        "max-age=3600 cache.")
    cc = _value_for(root_rule["headers"], "Cache-Control")
    assert cc is not None and "no-cache" in cc.lower(), (
        f"bare root rule must set Cache-Control: no-cache; got {cc!r}")


def test_html_files_have_no_cache():
    """All HTML files should be no-cache so a reframe / copy edit
    rolls out instantly to existing visitors."""
    headers = _load_headers()
    html_rule = next(
        (r for r in headers
         if r.get("source", "").endswith(".html")
         or r.get("source") in {"**/*", "**"}),
        None)
    assert html_rule is not None, (
        "firebase.json must have a header rule matching HTML files "
        "(e.g. source='**/*.html').")
    cc = _value_for(html_rule["headers"], "Cache-Control")
    assert cc is not None and "no-cache" in cc.lower(), (
        f"HTML files must be Cache-Control: no-cache; got {cc!r}")


def test_payload_json_has_short_max_age():
    """payload.json is the data file the dashboard fetches on load.
    It is regenerated only when the user redeploys, so a short
    max-age is fine and reduces repeat-visit egress — but it must
    NOT be no-cache (would defeat the purpose) and must NOT be a
    long max-age (would delay seeing fresh deploys)."""
    headers = _load_headers()
    payload_rule = next(
        (r for r in headers
         if "payload.json" in r.get("source", "")),
        None)
    assert payload_rule is not None, (
        "firebase.json must have a header rule covering payload.json.")
    cc = _value_for(payload_rule["headers"], "Cache-Control")
    assert cc is not None, "payload.json must have a Cache-Control header"
    cc_lower = cc.lower()
    assert "no-cache" not in cc_lower, (
        f"payload.json should not be no-cache (defeats the small "
        f"repeat-visit egress win); got {cc!r}")
    assert "max-age=" in cc_lower, (
        f"payload.json must set a max-age; got {cc!r}")
    # Extract and cap the max-age. 300s = 5min is the value we set;
    # anything up to ~1h is reasonable, but we hard-cap so a
    # `max-age=86400` typo would fail rather than silently delay
    # rollouts.
    import re
    m = re.search(r"max-age=(\d+)", cc_lower)
    assert m, f"failed to parse max-age from {cc!r}"
    seconds = int(m.group(1))
    assert seconds <= 3600, (
        f"payload.json max-age={seconds}s is too long; cap at 3600s "
        f"(1h). Got {cc!r}")
