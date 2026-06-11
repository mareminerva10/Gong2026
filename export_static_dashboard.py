"""Static export of the Gong2026 dashboard for Firebase Hosting (Spark).

Reads the dashboard contract + polygon manifest, produces a static
`public/` directory that can be served by Firebase Hosting on the Spark
plan with no Cloud Run, no billing account, no dynamic server.

Layout:

    public/index.html      — the dashboard HTML/CSS/JS, identical to the
                              localhost server's response except the
                              fetch URL is rewritten from `/api/contract`
                              to `./payload.json` so it loads as a
                              same-origin static file.
    public/payload.json    — JSON-serialized `load_payload()` result.

Guardrails baked into this script (asserted by tests/test_static_export.py):

  - The exported payload must NOT contain `A00..A63` raw AlphaEarth
    embedding bands. The 2026 reframe ships derived metrics only;
    embedding columns are uninterpretable to a public reader, add
    payload weight, and create licensing ambiguity around republishing.
  - The exported payload must be under PAYLOAD_BYTE_CEILING (2 MiB
    uncompressed). Firebase Spark gives 360 MB/day of egress; at this
    ceiling that's ≥180 first-loads/day before gzip, comfortably more
    after.
  - The exported index.html must NOT contain prohibited claim strings
    (forecast / risk_score / probability / composite_score /
    gentrification_score) — the same vocabulary the dashboard_app.py
    title/H1 already satisfies and the CI claim guard already enforces.
  - The fetch URL rewrite must succeed exactly once — failures and
    multiple rewrites both raise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import dashboard_app as da

DEFAULT_OUTPUT_DIR = Path("public")

# Curated public-deploy snapshot paths. The workflow_dispatch Firebase
# deploy reads from these so the public site is reproducible from any
# git commit SHA — independent of whatever data the developer's local
# machine has built. The snapshot is refreshed by copying the live
# data/dashboard_pilot_contract.parquet and data/pilot_legal_dong_manifest.parquet
# into data/snapshot/ as a deliberate commit. See
# project-next-session-step6-snapshot-deploy-2026-06-11 for the
# decision rationale (chosen over Git-LFS, in-CI data re-pull, and
# uploaded-artifact alternatives).
DEFAULT_SNAPSHOT_CONTRACT = Path("data/snapshot/dashboard_pilot_contract.parquet")
DEFAULT_SNAPSHOT_MANIFEST = Path("data/snapshot/pilot_legal_dong_manifest.parquet")

# 2 MiB ceiling on the uncompressed payload. At this size, after Firebase
# Hosting's gzip, the per-first-load egress is well under 1 MB, giving
# >360 first-loads/day on the Spark plan's 360 MB/day allowance.
PAYLOAD_BYTE_CEILING = 2 * 1024 * 1024

# Pattern for the raw embedding columns. AlphaEarth V1/ANNUAL exposes
# 64 bands named exactly A00..A63; the dashboard internally references
# them as `EMBED_COLS = [f"A{i:02d}" for i in range(64)]` in
# dashboard_pilot_contract.py. Any row key matching this pattern is a
# raw embedding band and must not be exported.
EMBEDDING_KEY_PATTERN = re.compile(r"^A\d{2}$")

# Vocabulary the 2026 reframe forbids in user-facing surfaces. Matches
# the substrings asserted by tests/test_claim_strings.py for the live
# dashboard HTML; this script asserts the same against the static-export
# output so a future regression here can't sneak past the existing CI
# guard (which checks the source string, not the export artifact).
PROHIBITED_CLAIM_SUBSTRINGS = (
    "forecast",
    "prediction",
    "probability",
    "risk_score",
    "composite_score",
    "gentrification_score",
    "displacement_score",
    "alarm",
)

# The original fetch URL in dashboard_app.INDEX_HTML. Single occurrence
# verified at the time of step-5 design; the rewrite asserts that the
# original string is gone after substitution and that the new string is
# present, so any future drift (a second fetch added, the path renamed)
# surfaces as a failure rather than a silent export.
FETCH_URL_FROM = 'fetch("/api/contract")'
FETCH_URL_TO = 'fetch("./payload.json")'


def export(contract_path: Path,
           manifest_path: Path,
           output_dir: Path) -> dict:
    """Build the static dashboard. Returns a small summary dict for the
    CLI to print; raises on any guardrail violation."""
    if not contract_path.exists():
        raise FileNotFoundError(
            f"contract parquet missing: {contract_path}. Run "
            "`python dashboard_pilot_contract.py` first to build it.")

    payload = da.load_payload(contract_path, manifest_path)
    _assert_no_embedding_leakage(payload)

    payload_json = json.dumps(payload, ensure_ascii=False,
                              allow_nan=False, separators=(",", ":"))
    payload_bytes = payload_json.encode("utf-8")
    if len(payload_bytes) > PAYLOAD_BYTE_CEILING:
        raise RuntimeError(
            f"payload {len(payload_bytes):,} bytes exceeds ceiling "
            f"{PAYLOAD_BYTE_CEILING:,} bytes ({PAYLOAD_BYTE_CEILING // 1024 // 1024} MiB). "
            "Trim DISPLAY_COLS or check polygon count.")

    html = _rewrite_fetch_url(da.INDEX_HTML)
    _assert_no_prohibited_claim_strings(html)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    (output_dir / "payload.json").write_bytes(payload_bytes)

    return {
        "contract": str(contract_path),
        "manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "html_bytes": len(html.encode("utf-8")),
        "payload_bytes": len(payload_bytes),
        "row_count": len(payload["rows"]),
        "polygon_count": payload["summary"].get("polygon_count", 0),
    }


def _assert_no_embedding_leakage(payload: dict) -> None:
    """If a future schema change accidentally puts the raw A00..A63
    bands back into the row dicts, refuse to export."""
    if not payload.get("rows"):
        return
    sample = payload["rows"][0]
    bad = [k for k in sample if EMBEDDING_KEY_PATTERN.match(k)]
    if bad:
        raise RuntimeError(
            f"raw AlphaEarth embedding bands present in export payload: "
            f"{bad[:5]}{'...' if len(bad) > 5 else ''}. These must not "
            "be republished — strip them upstream in DISPLAY_COLS / "
            "select_columns before exporting.")


def _assert_no_prohibited_claim_strings(html: str) -> None:
    """Mirror of tests/test_claim_strings.py's scope: check only the
    page-metadata surfaces (`<title>` and the first `<h1>`) for
    forecast/risk/score vocabulary. The body legitimately uses these
    words in negation copy ('No forecast, probability, or composite
    score.') and as status field names (`composite_score_status`,
    initialized as `not_computed`); a body-wide scan would create
    false positives on the existing reframe disclaimers."""
    title_match = re.search(r"<title>(.*?)</title>",
                            html, flags=re.IGNORECASE | re.DOTALL)
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>",
                         html, flags=re.IGNORECASE | re.DOTALL)
    surfaces = {
        "<title>": (title_match.group(1) if title_match else "").lower(),
        "<h1>": (h1_match.group(1) if h1_match else "").lower(),
    }
    fatal: list[tuple[str, str]] = []
    for where, content in surfaces.items():
        for s in PROHIBITED_CLAIM_SUBSTRINGS:
            if s in content:
                fatal.append((where, s))
    if fatal:
        raise RuntimeError(
            f"exported HTML page metadata contains prohibited claim "
            f"substrings: {fatal}. The 2026 reframe removed this "
            "vocabulary from the product surface; see "
            "docs/dashboard_mvp_spec.md and tests/test_claim_strings.py.")


def _rewrite_fetch_url(html: str) -> str:
    """Rewrite `/api/contract` → `./payload.json`. Asserts exactly one
    occurrence was rewritten; any drift (zero or multiple) raises."""
    count = html.count(FETCH_URL_FROM)
    if count != 1:
        raise RuntimeError(
            f"expected exactly one occurrence of {FETCH_URL_FROM!r} in "
            f"INDEX_HTML, found {count}. The static export rewrite is "
            "no longer safe; inspect dashboard_app.INDEX_HTML.")
    rewritten = html.replace(FETCH_URL_FROM, FETCH_URL_TO, 1)
    if FETCH_URL_FROM in rewritten:
        raise RuntimeError("fetch URL rewrite left behind a stale "
                            f"{FETCH_URL_FROM!r} substring.")
    if FETCH_URL_TO not in rewritten:
        raise RuntimeError(f"fetch URL rewrite did not produce "
                            f"{FETCH_URL_TO!r} in the output.")
    return rewritten


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Build a static export of the Gong2026 dashboard for "
                    "Firebase Hosting (Spark plan, no Cloud Run). "
                    "Defaults read from the live local artifacts "
                    "(data/*.parquet) so the existing local export flow "
                    "is unchanged; the CI/CD deploy workflow passes "
                    "--contract data/snapshot/... explicitly so it is "
                    "reproducible from the committed snapshot.")
    ap.add_argument("--contract", default=str(da.DEFAULT_CONTRACT),
                    help="dashboard contract parquet "
                         "(default: data/dashboard_pilot_contract.parquet)")
    ap.add_argument("--manifest", default=str(da.DEFAULT_MANIFEST),
                    help="legal-dong polygon manifest parquet "
                         "(default: data/pilot_legal_dong_manifest.parquet)")
    ap.add_argument("--out-dir", "--output", dest="out_dir",
                    default=str(DEFAULT_OUTPUT_DIR),
                    help="static-export output directory (default: public/). "
                         "`--output` accepted as a backward-compat alias.")
    args = ap.parse_args(argv)

    try:
        result = export(Path(args.contract), Path(args.manifest),
                        Path(args.out_dir))
    except (FileNotFoundError, RuntimeError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    print(f"Static dashboard exported to {result['output_dir']}/")
    print(f"  index.html:   {result['html_bytes']:>9,} bytes")
    print(f"  payload.json: {result['payload_bytes']:>9,} bytes "
          f"({result['payload_bytes'] / 1024 / 1024:.2f} MiB; "
          f"ceiling {PAYLOAD_BYTE_CEILING / 1024 / 1024:.0f} MiB)")
    print(f"  rows:         {result['row_count']:>9,}")
    print(f"  polygons:     {result['polygon_count']:>9,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
