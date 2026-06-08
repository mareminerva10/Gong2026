"""
tests/test_no_raw_payloads.py
=============================

Hygiene guard: prevent raw API payloads, cached probe JSONs, and derived
parquet panels from being accidentally tracked in git.

The repo discipline (per `docs/mvp_state_2026.md` and the credential
memory) keeps `data/` entirely gitignored except for the hand-curated
`data/labeled_cases.csv`. A regression here would leak credentials
(payloads carry `cert_id` echoes from the API), bloat the repo, and
desynchronize cached panels from the live source of truth.

These checks operate at the git-index level (not the working tree), so
they catch the moment a file is added to the index, before it's
committed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# The only file under data/ that the project legitimately tracks.
ALLOWED_DATA_FILES = frozenset({
    "data/labeled_cases.csv",
})


def _git_tracked_under(prefix: str) -> list[str]:
    """Return the list of paths git is currently tracking under `prefix`.
    Uses the index (HEAD) snapshot — staged-but-uncommitted additions are
    visible to `git ls-files` so this catches them too."""
    result = subprocess.run(
        ["git", "ls-files", prefix],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    out = result.stdout.strip()
    return [p for p in out.split("\n") if p]


def test_no_data_payloads_tracked() -> None:
    """Nothing under data/ should be tracked except the allow-list."""
    tracked = _git_tracked_under("data/")
    illegal = [p for p in tracked if p not in ALLOWED_DATA_FILES]
    assert not illegal, (
        f"unexpected file(s) tracked under data/: {illegal}. The only "
        f"file the repo tracks under data/ is {sorted(ALLOWED_DATA_FILES)}. "
        "Raw probe payloads, cache files, and derived parquet panels must "
        "stay gitignored — they carry cert_id echoes from the API and "
        "would leak credentials."
    )


def test_no_parquet_files_tracked() -> None:
    """Belt-and-braces: no .parquet anywhere in the repo, in case a
    cache directory moves outside data/ in the future."""
    result = subprocess.run(
        ["git", "ls-files", "*.parquet"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tracked = [p for p in result.stdout.strip().split("\n") if p]
    assert not tracked, (
        f"unexpected .parquet file(s) tracked: {tracked}. Derived panels "
        "must be regenerated from source, not committed."
    )


def test_no_probe_json_tracked() -> None:
    """No data/probe_*.json should be tracked — these are credential-
    echoing diagnostic payloads even after cert_id scrubbing."""
    tracked = _git_tracked_under("data/")
    probe_files = [p for p in tracked
                   if Path(p).name.startswith("probe_")
                   and Path(p).suffix == ".json"]
    assert not probe_files, (
        f"probe payloads tracked: {probe_files}. These are diagnostic "
        "artifacts and stay gitignored."
    )
