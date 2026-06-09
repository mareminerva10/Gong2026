"""Transient smoke probe for RTMS sibling rent endpoints.

Step 1 of the multi-housing tenure expansion workstream
(project-next-session-multi-housing-tenure-2026-06-09).

Probes three candidate endpoints with one (LAWD_CD, DEAL_YMD) call each:
  - LAWD_CD = 11440 (마포구)
  - DEAL_YMD = 202401

For each candidate, reports:
  - HTTP status
  - resultCode / resultMsg
  - totalCount (server-reported)
  - item count (parsed)
  - distinct child field names on the first item
  - first-item sample (truncated)

Service key is read from MOLIT_SERVICE_KEY. Never printed.
Errors scrub the key from any text. NOT committed.
"""
from __future__ import annotations

import io
import os
import sys
from xml.etree import ElementTree as ET

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  line_buffering=True)

SERVICE_KEY = os.environ.get("MOLIT_SERVICE_KEY")
if not SERVICE_KEY:
    print("MOLIT_SERVICE_KEY not set. Aborting.")
    sys.exit(1)

# Candidate sibling endpoint URLs, per the RTMS data.go.kr catalog
# naming convention (verified against the apartment endpoint:
# /1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent).
CANDIDATES = [
    ("15126473_rowhouse_multifamily",
     "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent"),
    ("15126472_single_detached",
     "https://apis.data.go.kr/1613000/RTMSDataSvcSHRent/getRTMSDataSvcSHRent"),
    ("15126475_officetel",
     "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent"),
]

LAWD_CD = "11440"
DEAL_YMD = "202401"
NUM_ROWS = 1000


def redact(text: str) -> str:
    if SERVICE_KEY and SERVICE_KEY in text:
        return text.replace(SERVICE_KEY, "<redacted>")
    return text


def probe(label: str, url: str) -> None:
    print("=" * 72)
    print(f"CANDIDATE: {label}")
    print(f"URL: {url}")
    print(f"Params: LAWD_CD={LAWD_CD} DEAL_YMD={DEAL_YMD} numOfRows={NUM_ROWS}")
    print("-" * 72)
    params = {
        "serviceKey": SERVICE_KEY,
        "LAWD_CD": LAWD_CD,
        "DEAL_YMD": DEAL_YMD,
        "numOfRows": NUM_ROWS,
        "pageNo": 1,
    }
    try:
        r = requests.get(url, params=params, timeout=25)
    except requests.RequestException as e:
        print(f"  HTTP ERROR: {type(e).__name__}: {redact(str(e))}")
        return

    print(f"  HTTP status: {r.status_code}")
    body = r.text
    if not body.lstrip().startswith("<"):
        print(f"  Non-XML body (first 200 chars): {redact(body[:200])!r}")
        return

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        print(f"  XML PARSE ERROR: {e}")
        print(f"  Body preview: {redact(body[:300])!r}")
        return

    code_el = root.find(".//resultCode")
    msg_el = root.find(".//resultMsg")
    total_el = root.find(".//totalCount")
    code = (code_el.text or "").strip() if code_el is not None else "<missing>"
    msg = (msg_el.text or "").strip() if msg_el is not None else "<missing>"
    total = (total_el.text or "").strip() if total_el is not None else "<missing>"

    items = list(root.iter("item"))
    print(f"  resultCode: {code!r}")
    print(f"  resultMsg : {msg!r}")
    print(f"  totalCount: {total!r}")
    print(f"  item count (parsed, page 1): {len(items)}")

    if items:
        first = items[0]
        fields = [c.tag for c in first]
        print(f"  distinct fields on first item ({len(fields)}):")
        for f in fields:
            print(f"    - {f}")
        sample = {c.tag: (c.text or "").strip()[:40] for c in first}
        print(f"  first item sample:")
        for k, v in sample.items():
            print(f"    {k}: {v!r}")
    else:
        print("  (no items returned)")
    print()


for label, url in CANDIDATES:
    probe(label, url)

print("=" * 72)
print("Probe complete.")
