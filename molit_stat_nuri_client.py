"""
molit_stat_nuri_client.py
=========================

Probe-only client for the 국토교통부 통계누리 OPEN API at stat.molit.go.kr.
Distinct from molit_client.py (which targets data.go.kr's transaction-level
endpoint). This module talks to the aggregated-statistics service at
si-gun-gu granularity.

API contract (from stat.molit.go.kr/portal/api/apiList.do)
----------------------------------------------------------
GET http://stat.molit.go.kr/portal/openapi/service/rest/getList.do
Required query parameters:
    key       OpenAPI authentication key
    form_id   statistics-table ID
    style_num form (yangshik) number
    start_dt  start period, YYYYMM
    end_dt    end period, YYYYMM
Response fields documented on the portal: status_code, message, unitName,
formName, date.

Constraints from portal documentation
-------------------------------------
- Maximum 5-year span per query. Use chunk_period() to split longer spans.
- Throttling is real -- repeated rapid calls can lock the key.

What this module does today
---------------------------
- request_one(): single API call with structured fail-loud error handling.
- probe(): wraps request_one() to print response shape for diagnostic
  inspection. Used to learn the response schema before writing aggregation.
- chunk_period(): splits a YYYYMM range into <= 5-yr windows for future
  multi-year pulls.

What this module does NOT do (yet)
----------------------------------
- Does not compute wolse_ratio or any derived metric. The response schema
  beyond the documented top-level fields is unknown -- first probe will
  reveal the row container key and per-row field names.
- Does not iterate across regions. Once probe shape is understood, the
  region iteration belongs here (or in a thin wrapper script).
- Does not integrate with prototype.py. The --wolse-source statnuri wiring
  comes after the response shape is known and an aggregation function is
  written.

Key handling
------------
The API key is read at call time from MOLIT_STAT_NURI_KEY in the local
environment. It is never accepted via CLI flag (would leak into shell
history), never logged, and never echoed in error messages.

Run a probe
-----------
    python molit_stat_nuri_client.py \\
        --form-id 37 --style-num 1 \\
        --start-dt 202401 --end-dt 202401 \\
        --out data/statnuri_probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests


MOLIT_STAT_NURI_BASE_URL = os.getenv(
    "MOLIT_STAT_NURI_BASE_URL",
    "http://stat.molit.go.kr/portal/openapi/service/rest/getList.do",
)
SERVICE_KEY_ENV = "MOLIT_STAT_NURI_KEY"

# The 통계누리 endpoint rejects the default `python-requests/X.Y.Z` User-Agent
# with a TLS-level connection reset (verified 2026-05-15). Sending a
# browser-like UA fixes it. Override via MOLIT_STAT_NURI_UA if a future
# server-side change requires a different string.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# The actual response envelope (learned from the first live probe on 2026-05-15)
# nests status inside `result_status` and rows inside `result_data.formList`.
# Success uses status_code "INFO-000" with message "정상 처리되었습니다.".
# Anything else — including other INFO-* codes and ERROR-* codes — is a failure.
SUCCESS_CODE = "INFO-000"

# `result_data` echoes the caller's `key` back as `cert_id`. Strip before any
# print or disk write so probe artefacts on disk and console output don't
# contain the credential.
SENSITIVE_FIELDS = ("cert_id",)


class StatNuriError(RuntimeError):
    """Raised on any non-success response or transport failure."""


def _read_key() -> str:
    k = os.getenv(SERVICE_KEY_ENV)
    if not k:
        raise StatNuriError(
            f"{SERVICE_KEY_ENV} is not set. In a fresh PowerShell window run "
            f"`setx {SERVICE_KEY_ENV} \"<your key>\"`, open another shell, then retry."
        )
    return k


def request_one(form_id: str, style_num: str, start_dt: str, end_dt: str,
                base_url: str | None = None, timeout: int = 20,
                retries: int = 3) -> dict:
    """Single 통계누리 API call (with bounded retry). Returns the parsed JSON.

    The 통계누리 endpoint exhibits intermittent connection resets that appear
    unrelated to rate-limiting (different runs of the same call alternate
    success/reset). We retry transport-level failures up to `retries` times
    with a short backoff. Application-level failures (non-success
    status_code) are NOT retried because the server is telling us
    something deterministic.

    Raises StatNuriError on persistent transport failure, non-JSON response,
    or a status_code other than INFO-000 success."""
    url = base_url or MOLIT_STAT_NURI_BASE_URL
    params = {
        "key": _read_key(),
        "form_id": form_id,
        "style_num": style_num,
        "start_dt": start_dt,
        "end_dt": end_dt,
    }
    headers = {"User-Agent": os.getenv("MOLIT_STAT_NURI_UA", DEFAULT_UA)}

    last_err: Exception | None = None
    r = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            last_err = None
            break
        except requests.RequestException as e:
            last_err = e
            # Backoff 1s, 2s, 3s ...
            import time
            time.sleep(attempt + 1)
    if last_err is not None or r is None:
        raise StatNuriError(
            f"transport failure after {retries} attempts "
            f"(form_id={form_id} {start_dt}-{end_dt}): "
            f"{type(last_err).__name__ if last_err else 'unknown'}: {last_err}"
        ) from None
    try:
        payload = r.json()
    except json.JSONDecodeError as e:
        snippet = r.text[:300].replace("\n", " ")
        raise StatNuriError(
            f"non-JSON response (form_id={form_id} {start_dt}-{end_dt}). "
            f"First 300 chars: {snippet!r}"
        ) from e

    # Status envelope sits under result_status, not top-level.
    rs = payload.get("result_status") or {}
    status = str(rs.get("status_code", "")).strip()
    message = str(rs.get("message", "")).strip()
    if status.upper() != SUCCESS_CODE:
        raise StatNuriError(
            f"API non-success (form_id={form_id} {start_dt}-{end_dt}): "
            f"status_code={status!r} message={message!r}"
        )
    _scrub_credentials(payload)
    return payload


def _scrub_credentials(payload: dict) -> None:
    """Remove key echoes (e.g. cert_id) from a parsed payload, in place."""
    rd = payload.get("result_data")
    if isinstance(rd, dict):
        for f in SENSITIVE_FIELDS:
            rd.pop(f, None)


def chunk_period(start_dt: str, end_dt: str, max_years: int = 5
                 ) -> list[tuple[str, str]]:
    """Split a YYYYMM range into <= max_years windows.

    The portal enforces a 5-year cap; pulling 2017-01 to 2024-12 in one
    request will fail. This helper returns inclusive month windows of length
    at most (max_years * 12) months, so callers can loop one window at a
    time without exceeding the cap.

    >>> chunk_period("201701", "202412", max_years=5)
    [('201701', '202112'), ('202201', '202412')]
    """
    sy, sm = int(start_dt[:4]), int(start_dt[4:6])
    ey, em = int(end_dt[:4]), int(end_dt[4:6])
    span_months = max_years * 12 - 1  # 59 -> 60-month inclusive window
    chunks: list[tuple[str, str]] = []
    cy, cm = sy, sm
    while True:
        remaining = (ey - cy) * 12 + (em - cm)
        step = min(span_months, remaining)
        wy = cy + (cm - 1 + step) // 12
        wm = (cm - 1 + step) % 12 + 1
        chunks.append((f"{cy}{cm:02d}", f"{wy}{wm:02d}"))
        if (wy, wm) >= (ey, em):
            break
        # advance to next month
        nm = wm + 1
        ny = wy + (nm - 1) // 12
        nm = (nm - 1) % 12 + 1
        cy, cm = ny, nm
    return chunks


def probe(form_id: str, style_num: str, start_dt: str, end_dt: str,
          out_path: Path | None = None) -> dict:
    """Single diagnostic call. Walks the 통계누리 response envelope
    (result_status / result_data.formList), reports the 7-point schema
    checklist, and optionally writes the credential-scrubbed payload to
    disk for offline re-inspection.

    The payload returned and written is already scrubbed of cert_id by
    request_one(); the disk artefact is safe to keep under data/ (which is
    gitignored) but should still not be shared outside the local machine."""
    print(f"probe: form_id={form_id} style_num={style_num} {start_dt}-{end_dt}")
    print(f"  base url: {MOLIT_STAT_NURI_BASE_URL}")

    payload = request_one(form_id, style_num, start_dt, end_dt)

    rs = payload.get("result_status") or {}
    rd = payload.get("result_data") or {}

    # 1. status / message
    print(f"  [1] status_code: {rs.get('status_code')!r}  "
          f"message: {rs.get('message')!r}")

    # 2-3. row container presence + location
    form_list = rd.get("formList") if isinstance(rd, dict) else None
    if not isinstance(form_list, list):
        print("  [2] rows: NOT a list under result_data.formList "
              "(envelope shape may have changed; inspect raw payload)")
        if out_path is not None:
            _write_payload(payload, out_path)
        return payload
    n_rows = len(form_list)
    print(f"  [2] rows present: {n_rows} items")
    print(f"  [3] row container: result_data.formList")

    if n_rows == 0:
        print("  [4-7] no rows to inspect schema from; try a broader period")
        if out_path is not None:
            _write_payload(payload, out_path)
        return payload

    # 4-7. field names from the first row, plus tenure-split heuristic.
    sample = form_list[0]
    if not isinstance(sample, dict):
        print(f"  [4-7] first row is {type(sample).__name__}, not a dict; "
              f"raw: {sample!r}")
        if out_path is not None:
            _write_payload(payload, out_path)
        return payload

    keys = sorted(sample.keys())
    print(f"  [4] period field: {'date' if 'date' in keys else 'NOT date — keys: ' + str(keys)}")
    region_candidates = [k for k in keys
                         if k in ("지역", "지역명", "시도", "시군구", "region", "area")]
    print(f"  [5] region field candidate(s): {region_candidates or '(none recognized)'}")
    value_candidates = [k for k in keys if k not in ("date", *region_candidates, "시기")]
    print(f"  [6] value/category fields: {value_candidates}")

    # 7. tenure split heuristic — look for jeonse / wolse markers in any field
    #    name or row value (search a sample of rows, not just the first).
    tenure_markers = ("전세", "월세", "보증부", "사글세")
    in_keys = [m for m in tenure_markers
               if any(m in k for k in keys)]
    sample_vals = [str(v) for row in form_list[: min(50, n_rows)]
                   for v in row.values()]
    in_values = [m for m in tenure_markers
                 if any(m in v for v in sample_vals)]
    if in_keys or in_values:
        print(f"  [7] tenure split DETECTED: keys={in_keys}, values={in_values}")
    else:
        print(f"  [7] tenure split NOT DETECTED in this table "
              f"(no 전세/월세/보증부/사글세 in keys or first-50 row values)")

    print(f"  unitName: {rd.get('unitName')!r}   formName: {rd.get('formName')!r}")
    print(f"  first row preview: "
          f"{json.dumps(sample, ensure_ascii=False)[:400]}")

    if out_path is not None:
        _write_payload(payload, out_path)
    return payload


def _write_payload(payload: dict, out_path: Path) -> None:
    """Persist the (already-scrubbed) payload as UTF-8 JSON."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"  payload written: {out_path}  (cert_id stripped)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MOLIT 통계누리 probe client")
    ap.add_argument("--form-id", required=True,
                    help="approved statistics-table ID (form_id) from the portal")
    ap.add_argument("--style-num", required=True,
                    help="form (yangshik) number, style_num")
    ap.add_argument("--start-dt", required=True,
                    help="start period YYYYMM, e.g. 202401")
    ap.add_argument("--end-dt", required=True,
                    help="end period YYYYMM, e.g. 202401")
    ap.add_argument("--out", default=None,
                    help="optional path to write the raw JSON for offline inspection")
    args = ap.parse_args(argv)

    try:
        probe(args.form_id, args.style_num, args.start_dt, args.end_dt,
              out_path=Path(args.out) if args.out else None)
    except StatNuriError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
