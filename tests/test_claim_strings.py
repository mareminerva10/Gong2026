"""
tests/test_claim_strings.py
===========================

Regression guard for the 2026-06-07 reframe (commit `2b15771`). The MVP
explicitly does not claim to forecast gentrification, displacement, or
risk — see `docs/mvp_state_2026.md` and `docs/dashboard_mvp_spec.md`.

These tests fail if any of those forbidden product-claim words reappear
in load-bearing public-claim positions:

  - the dashboard HTML <title>
  - the dashboard HTML <h1> in the sidebar
  - the README first paragraph (the tagline directly under the H1)
  - the "Product name:" line in docs/mvp_state_2026.md
  - the "Product name:" line in docs/dashboard_mvp_spec.md

False-positive defenses:
- We do NOT scan whole files. README's "What this MVP does not claim"
  section legitimately uses words like "forecast" and "EWS" in negation.
  Scoping each test to a specific element / line is what makes the guard
  robust.
- Each test reports a clear failure message naming the offending word
  and the offending location, so regressions are easy to diagnose.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

# Words that must not appear in product-claim positions.
# Aligned with PROHIBITED_SUBSTRINGS in dashboard_pilot_contract.py but
# expanded to cover the spelled-out forms that would overclaim the
# product (e.g. "risk", "alarm" in a title/h1, not just "risk_score").
FORBIDDEN_IN_TITLE_OR_H1 = (
    "forecast",
    "prediction",
    "predict",
    "probability",
    "risk",
    "alarm",
    "score",
)

# Tighter list for prose lines (README tagline, "Product name" lines).
# We keep "score" out here so phrases like "not a single score" remain
# legal in the tagline if a future editor reintroduces them as a
# negation; the dashboard title/h1 guard above is stricter.
FORBIDDEN_IN_PROSE_CLAIM = (
    "forecast",
    "prediction",
    "predict",
    "probability",
    "risk_score",
    "composite_score",
    "gentrification_score",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dashboard_title_clean() -> None:
    """The dashboard HTML <title> must not reintroduce forecast vocabulary."""
    content = _read(REPO_ROOT / "dashboard_app.py")
    match = re.search(r"<title>(.*?)</title>", content, flags=re.DOTALL)
    assert match, "no <title> tag found in dashboard_app.py"
    title = match.group(1).lower()
    bad = [w for w in FORBIDDEN_IN_TITLE_OR_H1 if w in title]
    assert not bad, (
        f"forbidden word(s) {bad} reappeared in dashboard <title>: "
        f"{match.group(1).strip()!r}. The 2026-06-07 reframe specifically "
        "removed forecast/risk/probability/score language from this position."
    )


def test_dashboard_h1_clean() -> None:
    """The dashboard sidebar <h1> must not reintroduce forecast vocabulary."""
    content = _read(REPO_ROOT / "dashboard_app.py")
    match = re.search(r"<h1>(.*?)</h1>", content, flags=re.DOTALL)
    assert match, "no <h1> tag found in dashboard_app.py"
    h1 = match.group(1).lower()
    bad = [w for w in FORBIDDEN_IN_TITLE_OR_H1 if w in h1]
    assert not bad, (
        f"forbidden word(s) {bad} reappeared in dashboard <h1>: "
        f"{match.group(1).strip()!r}. See docs/mvp_state_2026.md product-name "
        "discipline."
    )


def _extract_readme_tagline(content: str) -> str:
    """First non-empty, non-heading line block after the top-level H1.
    That is the project tagline."""
    in_tagline = False
    out: list[str] = []
    for line in content.splitlines():
        if line.startswith("# ") and not in_tagline:
            in_tagline = True
            continue
        if in_tagline:
            if line.startswith("#") or line.strip().startswith("```"):
                if out:
                    break
                continue
            if line.strip():
                out.append(line.strip())
            elif out:
                break
    return " ".join(out)


def test_readme_tagline_clean() -> None:
    """The README tagline (first paragraph under the H1) must not reintroduce
    forecast vocabulary."""
    tagline = _extract_readme_tagline(_read(REPO_ROOT / "README.md")).lower()
    assert tagline, "README has no extractable tagline under its H1"
    bad = [w for w in FORBIDDEN_IN_PROSE_CLAIM if w in tagline]
    assert not bad, (
        f"forbidden word(s) {bad} reappeared in README tagline: "
        f"{tagline[:240]!r}"
    )


def _extract_product_name_claim(content: str) -> str:
    """The product-claim sentence right after '**Product name:**' — i.e.,
    the first period-terminated sentence on that line. The remainder of
    the paragraph may legitimately contain meta-discussion (e.g. the
    explicit prohibition list in mvp_state_2026.md) which quotes words
    like 'forecast' or 'risk' in italics; that meta-discussion is
    excluded from this guard so the *claim* is what gets checked, not
    the *negation*."""
    for line in content.splitlines():
        s = line.strip()
        if not s.lower().startswith("**product name:**"):
            continue
        after = s.split("**Product name:**", 1)[1].strip()
        # First period terminates the claim sentence.
        if "." in after:
            return after.split(".", 1)[0].strip()
        return after
    return ""


@pytest.mark.parametrize("doc_path", [
    "docs/mvp_state_2026.md",
    "docs/dashboard_mvp_spec.md",
])
def test_product_name_claim_clean(doc_path: str) -> None:
    """The product-claim sentence after '**Product name:**' in the
    canonical state and spec docs must not reintroduce forecast
    vocabulary."""
    content = _read(REPO_ROOT / doc_path)
    claim = _extract_product_name_claim(content)
    assert claim, f"no product-name claim found in {doc_path}"
    lowered = claim.lower()
    bad = [w for w in FORBIDDEN_IN_PROSE_CLAIM if w in lowered]
    assert not bad, (
        f"forbidden word(s) {bad} reappeared in {doc_path} product-name "
        f"claim: {claim!r}"
    )
