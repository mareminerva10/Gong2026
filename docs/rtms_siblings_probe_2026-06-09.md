# RTMS sibling rent endpoints — smoke probe verdict

**Date:** 2026-06-09
**Workstream:** multi-housing tenure expansion (step 1 of the next-session plan)
**Predecessor:** `docs/molit_probe_2026-06-07.md` (apartment + StatNuri sibling probe)
**Status:** all three sibling endpoints live; ready for the client-generalization step.

## Scope

One-shot smoke per endpoint using the same `MOLIT_SERVICE_KEY` decoded key that powers the existing apartment integration (`molit_client.py`, dataset 15126474):

```text
LAWD_CD  = 11440   (마포구)
DEAL_YMD = 202401
numOfRows = 1000
pageNo = 1
```

## Acceptance summary

All three siblings PASS the step-1 acceptance criteria from
`project-next-session-multi-housing-tenure-2026-06-09`:

| Check | RHRent (15126473) | SHRent (15126472) | OffiRent (15126475) |
|---|---|---|---|
| HTTP status = 200 | ✓ | ✓ | ✓ |
| resultCode ∈ {`00`, `000`} | `000` | `000` | `000` |
| resultMsg | `OK` | `OK` | `OK` |
| totalCount > 0 | 835 | 715 | 600 |
| items parsed = totalCount (numOfRows=1000 single-shots) | 835/835 | 715/715 | 600/600 |
| schema fields understood | ✓ (18) | ✓ (15) | ✓ (18) |
| no service key in any output | ✓ | ✓ | ✓ |

The probe used the apartment endpoint's URL convention (`/1613000/RTMSDataSvc<X>Rent/getRTMSDataSvc<X>Rent`) and the candidate `<X>` slugs `RH`, `SH`, `Offi`. All three resolved on first try — no 404s, no auth surprises.

## Endpoint URLs (verified live)

```text
15126473  https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent
15126472  https://apis.data.go.kr/1613000/RTMSDataSvcSHRent/getRTMSDataSvcSHRent
15126475  https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent
```

## Per-endpoint schema (first-item field set)

### 15126473 RHRent — 연립다세대 (rowhouse_multifamily) — 18 fields

```text
buildYear, contractTerm, contractType, dealDay, dealMonth, dealYear,
deposit, excluUseAr, floor, houseType, jibun, mhouseNm, monthlyRent,
preDeposit, preMonthlyRent, sggCd, umdNm, useRRRight
```

- Per-m² calculus: **directly comparable** to apartment (uses `excluUseAr` per-unit exclusive area).
- `houseType` value seen: `'다세대'` (also expect `'연립'`).
- `mhouseNm`: building name (e.g. `'꽃밭선호빌'`).
- First item sample: 망원동, 2018-built 다세대, deposit `'23,300'` 만원, monthlyRent `'5'` 만원, excluUseAr `'27.04'` m², floor 3.

### 15126472 SHRent — 단독/다가구 (single_detached) — 15 fields

```text
buildYear, contractTerm, contractType, dealDay, dealMonth, dealYear,
deposit, houseType, monthlyRent, preDeposit, preMonthlyRent, sggCd,
totalFloorAr, umdNm, useRRRight
```

- **CRITICAL SCHEMA GAP:** no `excluUseAr`, no `floor`, no `jibun`, no `mhouseNm`. The area field is `totalFloorAr` — **whole-building total floor area**, not per-unit exclusive area.
- Implication: median_deposit_per_m2 and median_monthly_rent_per_m2 computed from SHRent are NOT comparable to apt/rh/offi per-m² metrics. For 다가구 (multi-generation, several rented units in one building under one landlord), the API surface only exposes the whole-building denominator, so a per-unit per-m² figure cannot be reconstructed from a single transaction record.
- `houseType` value seen: `'다가구'` (also expect `'단독'`).
- First item sample: 성산동, 1969-built 다가구, deposit `'1,000'` 만원, monthlyRent `'45'` 만원, totalFloorAr `'18'` (units appear to be m², not 평 — to be verified).

### 15126475 OffiRent — 오피스텔 (officetel) — 18 fields

```text
buildYear, contractTerm, contractType, dealDay, dealMonth, dealYear,
deposit, excluUseAr, floor, jibun, monthlyRent, offiNm, preDeposit,
preMonthlyRent, sggCd, sggNm, umdNm, useRRRight
```

- Per-m² calculus: **directly comparable** to apartment (uses `excluUseAr`).
- `offiNm`: building name (e.g. `'상암 스위트포레'`).
- `sggNm`: gu name echoed (`'마포구'`) — apartment endpoint does NOT echo this.
- No `houseType` (officetel is its own type; no sub-category).
- First item sample: 성산동, 2016-built 오피스텔, deposit `'12,100'` 만원, monthlyRent `'15'` 만원, excluUseAr `'18.55'` m², floor 5.

## Comparison to apartment endpoint (15126474)

Apartment field set (verified 2026-06-08 against the same gu-month, see `molit_client.py` lines 65–73): `deposit, monthlyRent, excluUseAr, umdNm, sggCd, dealYear, dealMonth`.

All three siblings carry the same five core fields (`deposit, monthlyRent, umdNm, sggCd, dealYear, dealMonth`) — only `excluUseAr` is conditional (present on RH and Offi, replaced by `totalFloorAr` on SH).

Non-obvious extras present on all three siblings but NOT on the apartment endpoint:

- `contractTerm` — contract period string (e.g. `'24.02~26.02'`).
- `contractType` — contract category (e.g. `'신규'` new vs `'갱신'` renewal).
- `preDeposit` / `preMonthlyRent` — previous contract terms (renewal cases).
- `useRRRight` — 갱신요구권 use flag.

These support a renewal-vs-new-contract sub-analysis that the apartment integration does not currently surface. Out of scope for the current expansion (single `housing_type` column per the registry) but worth flagging as a future enhancement.

## Findings that affect step 2 / step 3 design

1. **Endpoint URL convention is uniform** — the `RTMSDataSvc<X>Rent/getRTMSDataSvc<X>Rent` pattern holds. The housing-type registry can encode endpoints by slug substitution; no per-type URL exceptions needed.

2. **`resultCode` is `'000'` across the family.** No per-endpoint success-code branching needed beyond the existing `{"00", "000"}` accept set.

3. **`numOfRows=1000` page size is sufficient at the 마포구 202401 baseline** for all three (max observed = 835 for RH). Apartment had ~1,137 at the same baseline. Other gu-months may push higher; the existing apt pagination loop in `_pull_month` should be reused unchanged.

4. **Per-m² metrics are NOT homogeneous across housing types.** For SHRent, only `totalFloorAr` is available. Two acceptable resolutions for step 3:
   - **Recommended:** compute `median_deposit_per_m2` and `median_monthly_rent_per_m2` ONLY for apt / rh / offi rows; emit `NaN` for SH; document explicitly in the dashboard pilot contract. The `tenure_wolse_ratio` and the deal-count metrics (`n_rent_deals`, `n_wolse`, `n_jeonse`) remain comparable across all four types — those are unit-count metrics, not per-area.
   - **Rejected:** silently compute per-m² for SH using `totalFloorAr`. Mixes a whole-building denominator with per-unit denominators from the other types and would produce systematically smaller per-m² figures for SH — looks like a real signal but is a definitional artifact.

5. **`houseType` sub-categorization exists for RH and SH.** Per the next-session plan, the top-level `housing_type` column takes `{apartment, rowhouse_multifamily, single_detached, officetel}`. The endpoint's own `houseType` field (다세대/연립 for RH; 다가구/단독 for SH) can be retained as a secondary column for audit but should NOT replace the registry-level type.

6. **No credential leak risk surfaced.** All errors were trapped through a `redact()` wrapper that scrubs `MOLIT_SERVICE_KEY` from any output text. The pattern from commit `986c472` should be carried into the generalized client from the start, not retro-fitted.

## Status carried back into the workstream

```text
Step 1 (smoke-test sibling RTMS rent endpoints):     COMPLETE
Step 2 (generalize molit_client.py registry):        ready to start
Step 3 (build data/rtms_rent_panel.parquet):         blocked on step 2
Step 4 (dashboard promotion to live):                blocked on step 3 + step 2
```

The probe script itself (`probe_rtms_siblings.py`) is transient and not part of the live pipeline; it should be deleted or moved out of the working tree before the next commit. Its findings live here.
