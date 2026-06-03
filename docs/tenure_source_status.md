# Tenure source status (Block 1)

Status of Block 1 (tenure pressure / wolse_ratio) source resolution. Recorded
2026-06-04. Companion to the artifact-handling work in
`docs/dashboard_mvp_spec.md` §7 and the granted-API ledger in the project
memory.

This is a **source-discovery / smoke-test status doc**, not a model design.
Block 1 stays **parked** until a concrete (form_id, style_num) candidate
passes the strict acceptance criteria below or the data.go.kr key path is
unblocked.

## 1. Acceptance criteria

Any candidate source must clear all five gates before unparking Block 1:

1. Has a date or year/month field.
2. Has Seoul geography at gu level or finer.
3. Carries an explicit 전세 / 월세 split (or fields from which the
   wolse_ratio can be derived without inference).
4. Numeric counts or amounts (not percentages-only).
5. Usable 2017–2024 coverage.

## 2. StatNuri candidate discovery

Targeted search by Korean keyword on the 통계누리 portal
(`stat.molit.go.kr`) plus data.go.kr listings.

### 2.1 Search keywords used

`전월세`, `전세`, `월세`, `임대차`, `주택임대차`, `실거래`,
`전월세거래`, `전월세 거래량`, `임대차 신고`, `확정일자`.

### 2.2 Candidates evaluated

| `hRsId` | Stat title | Verdict | Notes |
|---|---|---|---|
| 37 | 임대주택통계 (Rental Housing Statistics) | **Rejected** | Supply / inventory only (rental housing supply by business approval, stock, sale-conversion results, landlord registrations). Province (시도) granularity only. No 전세/월세 split. No OpenAPI access exposed on the metadata page. Annual cadence. |
| 24 | 지목별 국토이용현황 (Land Use by Category) | **Rejected — wrong topic** | Surfaces in tenure searches because of co-mentioned URL params but is the land-use statistic; the `hFormId=2300` and `hFormId=5408` sub-tables under it are land-use breakdowns, not tenure. |
| 487 | unresolved | **Inconclusive** | Recurrently surfaced alongside rent/tenure context in searches, but `statView.do?hRsId=487` returned HTTP 500 from this connection and `statMetaView.do?hRsId=487` could not be rendered (JS-driven). The page must be opened from the user's authenticated StatNuri session to confirm title and any sub-table form_ids. |

### 2.3 Status against the granted-APIs ledger

The five endpoints approved for `gong2026` on 2026-05-18
(`reference-molit-granted-apis`) do not include any tenure / rent table:

- 시·군·구별 미분양현황 (integrated as form 2082 / style 128)
- 공사완료후 미분양현황 (not probed)
- 2-1. 연도별 재개발사업 현황 (integrated as form 6189 / style 1)
- 3-3. 연도별 서울시 재건축사업 현황 (probed empty at form 6193 / style 1)
- 행정구역별·지목별 국토이용현황_시군구 (not probed)

So even if a 전월세 hFormId exists on StatNuri, it is **not currently
covered by the user's existing approval**. A public, no-approval table
might exist but is not yet identified.

### 2.4 Why no blind probing was done

Per workstream policy ("Do not blind-sweep random form IDs"), no random
hFormId values were probed against the StatNuri client. Discovery must
come from the catalog browse, not from speculative API calls. The
StatNuri OpenAPI catalog (`stat.molit.go.kr/portal/openapi/apiList.do`)
is the right next browse target but requires a logged-in session.

## 3. data.go.kr `RTMSDataSvcAptRent` fallback

The transaction-level 아파트 전월세 실거래가 endpoint
(`apis.data.go.kr/.../RTMSDataSvcAptRent`) remains the **most direct**
path to a wolse_ratio panel because:

- It is per-transaction with per-month, per-LAWD_CD granularity.
- It yields all five contract fields needed
  (`보증금액`, `월세금액`, `법정동`, `전용면적`, deposit/area, etc.).
- Coverage extends back well before 2017.
- The client (`molit_client.py`) is already implemented end-to-end with
  pagination, retry, raw-cache, and a fixed LAWD_CD extractor for the
  project's 8-digit dong codes.
- The previous attempt failed with **HTTP 401** because the
  `MOLIT_SERVICE_KEY` env var held the StatNuri key, not the data.go.kr
  decoded key.

This is **the path most likely to land first** if the user can resolve
the credential. The blocker is purely on the credential side — code is
unchanged and ready.

## 4. Open user-side actions

In order of likely fastest-to-resolve:

1. **Browse the StatNuri OpenAPI catalog from a logged-in session** and
   note any approved or public table whose `formName` contains 전월세,
   전세, 월세, 임대차, or 주택임대차. If one exists, paste its `form_id`
   and `style_num` here for a probe.
2. **Open `stat.molit.go.kr/portal/cate/statView.do?hRsId=487`** in the
   browser and confirm its statistic title and listed hFormId values.
   The page 500'd on this connection but renders in an interactive
   session.
3. **Obtain the decoded `RTMSDataSvcAptRent` key from data.go.kr** and
   set it as `MOLIT_SERVICE_KEY` in a fresh shell. The existing
   `molit_client.fetch_rent_panel` will then produce the 9-column
   panel (`n_rent_deals`, `n_wolse`, `n_jeonse`, `wolse_ratio`,
   `median_deposit_per_m2`, `median_monthly_rent_per_m2`, plus IDs)
   that Block 1 needs.

## 5. Verdict

**StatNuri cannot currently unpark Block 1** without one of:

- A user-side portal browse identifying a public or already-granted
  tenure-split table (no candidate has been confirmed by web search
  alone), or
- A new endpoint-approval request to data.go.kr for a StatNuri
  tenure table.

**data.go.kr `RTMSDataSvcAptRent` remains the most direct path** and is
blocked only on credential acquisition. Recommended next user-side
action: pursue this path while keeping the StatNuri catalog browse as a
parallel discovery task.

**No panel integration, no dashboard change, no EWS work proceeds
against Block 1 until one of the above lands.**

## Sources

- [국토교통부 통계누리](https://stat.molit.go.kr/) — portal entry point.
- [임대주택통계 metadata (hRsId=37)](https://stat.molit.go.kr/portal/cate/statMetaView.do?hRsId=37) — rejected; supply/inventory only.
- [지목별 국토이용현황 (hRsId=24)](https://stat.molit.go.kr/portal/cate/statView.do?hRsId=24) — rejected; land-use, not tenure.
- [통계누리 OpenAPI info](https://stat.molit.go.kr/portal/openapi/apiInfoView.do) — confirms 5-year window cap; full catalog requires login.
- [국토교통부_통계누리_통계리스트(URL)](https://www.data.go.kr/data/15063216/fileData.do) — full StatNuri table directory as a downloadable file at data.go.kr.
- [국토교통부_아파트 전월세 실거래가 자료](https://www.data.go.kr/data/15126474/openapi.do) — the `RTMSDataSvcAptRent` endpoint our `molit_client.py` already targets; needs decoded key.
- [국토교통부_단독/다가구 전월세 실거래가 자료](https://www.data.go.kr/data/15126472/openapi.do) — sibling endpoint for non-apartment tenure if scope widens later.
- [부동산통계정보시스템 (REB R-ONE)](https://www.reb.or.kr/r-one) — Korea Real Estate Board OpenAPI; alternate authoritative source for monthly 전세/월세 거래량 if MOLIT paths both fail.
