# MOLIT StatNuri probe — 2026-06-07 (T1)

T1 of the post-MVP reassessment plan. Probes the two granted-but-not-yet-probed StatNuri endpoints identified in `reference-molit-granted-apis`. This is a **source-discovery probe**, not a panel build — no parquet, no dashboard wiring, no integration.

## 1. Targets

| # | Endpoint title (Korean) | Block it could feed | Status |
|---|---|---|---|
| A | 공사완료후 미분양현황 (post-completion unsold) | Block 4b (alongside `statnuri_unsold_*`); a more interpretable lagging-demand signal than pre-sale unsold | **ACCEPT — 5/5 gates closed (2026-06-08)**. `form_id=5328`, `style_num=1`, gu-month grain. |
| B | 행정구역별·지목별 국토이용현황_시군구 (gu-level land-use by category) | Block 4c (substitute for the parked KOGL-4 designation overlay), if granular enough | **ACCEPT — 5/5 gates closed (2026-06-08)**. `form_id=2300`, `style_num=2`, gu-year grain. |

Both targets resolved. No remaining user-side blockers for T1.

## 2. Why no blind-probe

Per the workstream rule documented in `docs/tenure_source_status.md` §2.4 (and in the project's open-blocker discipline), no random `hFormId` values may be probed against the StatNuri client. Discovery must come from the data.go.kr 마이페이지 granted-API ledger or the StatNuri portal catalog browse.

`docs/tenure_source_status.md` records that `hRsId=24` (지목별 국토이용현황) parents `hFormId=2300` and `hFormId=5408` as land-use breakdowns. One of these is likely the granted "행정구역별·지목별 국토이용현황_시군구" sub-form (target B), but the workstream rule disallows guessing between them — the user-side ledger is authoritative.

Public discovery via `data.go.kr` search was attempted on 2026-06-07 and returned socket-level connection resets (anti-bot at the search endpoint). The StatNuri catalog browse requires a logged-in session.

## 3. Acceptance / go-no-go gates

These mirror the user-confirmed criteria from the planning turn:

**Accept (unparks the relevant block) if:**

1. `result_status.status_code == "INFO-000"` on the probe call.
2. `result_data.formList` contains at least one row at the expected grain (gu-year or gu-month for A; gu-year-category for B).
3. Seoul-25-gu rows are present (not national-only, not province-only).
4. Numeric values are populated (not percentages-only, not header-only).
5. Period coverage spans 2017-01 through 2024-12 (monthly) or 2017–2024 (annual).

**Reject if any:**

- Empty `formList` after a 12-month probe window.
- National-aggregate-only (no `시군구` or equivalent).
- Header / category-name rows only, no value column.
- Period coverage gaps that prevent 2017–2024 panel construction.

A partial-pass (e.g. national-only granularity, or empty for some quarters) gets documented as **scout result** and the endpoint moves to `parked` with the specific gap recorded.

## 4. Probe plan once `form_id` / `style_num` are supplied

For each target, two probe calls:

```bash
# Single-month probe — schema discovery
python molit_stat_nuri_client.py \
    --form-id <ID_A> --style-num <STYLE_A> \
    --start-dt 202401 --end-dt 202401 \
    --out data/probe_completed_unsold_202401.json

# Bracket probe — confirm 2017 coverage
python molit_stat_nuri_client.py \
    --form-id <ID_A> --style-num <STYLE_A> \
    --start-dt 201701 --end-dt 201701 \
    --out data/probe_completed_unsold_201701.json
```

The same two-shot pattern for B with appropriate `start-dt` / `end-dt` if the grain is annual rather than monthly (`YYYY01` works for annual StatNuri tables).

The client already:

- Reads `MOLIT_STAT_NURI_KEY` from env (verified set, 32-char length).
- Scrubs `cert_id` from disk artifacts.
- Wraps transport errors with bounded retry (3 attempts, 1 s/2 s/3 s backoff).
- Prints the 7-point schema checklist (period field, region candidates, value/category fields, tenure-split heuristic, unitName, formName).

Outputs land under `data/` (gitignored). No panel build is wired up at this stage — the build step waits for a clean probe.

## 5. Open user-side action

Either:

(a) **From data.go.kr 마이페이지 → 개발계정 → 승인된 API**, open the detail card for each of the two endpoints. Each card lists the underlying StatNuri table name plus the OpenAPI sample request, which contains `form_id` and `style_num` query parameters. Paste both pairs back in chat as plain numbers.

(b) **OR** from the granted-APIs approval email (data.go.kr sends a notification per approval) — the same parameter pair is usually included in the sample request URL.

Once both pairs are pasted I will fire the four probe calls (two per endpoint), write the schema-checklist output and the cached JSON under `data/`, and update this doc with the verdict.

## 6. What this probe does NOT do

- It does not integrate any new field into the dashboard contract.
- It does not modify `molit_unsold_client.py` or `molit_redev_client.py`.
- It does not touch the parked Block 1 / Block 3 / Block 4c status docs.
- It does not produce a parquet panel.
- It does not run any modelling, clustering, or forecast work — per the Phase 0 → Phase 1 ordering in the 2026-06-07 reframe turn.

## 7. Verdict

### Target A — 공사완료후 미분양현황 — **ACCEPT**

공사완료후 미분양현황, `form_id=5328` / `style_num=1`, is fit for a future Block 4b post-completion unsold-housing companion. It is gu × month, covers all 25 Seoul gus, and has explicit zero rows. It is not a tenure-pressure source and is not integrated by this probe commit.

Resolved 2026-06-08 via user-supplied data.go.kr granted-API detail page. Two probes fired against `form_id=5328`, `style_num=1`:

```text
probe_completed_unsold_202604.json  →  status_code=INFO-000, 1729 rows, 25/25 Seoul gus
probe_completed_unsold_201701.json  →  status_code=INFO-000, 1482 rows, 25/25 Seoul gus
```

Acceptance gates (§3) closed:

| Gate | Criterion | Result |
|---|---|---|
| 1 | `status_code == "INFO-000"` | ✓ both probes |
| 2 | `formList` non-empty | ✓ 1729 / 1482 rows |
| 3 | Seoul 25-gu coverage | ✓ 25/25 in both probes |
| 4 | Numeric values (not %) | ✓ `호` is integer housing-unit count |
| 5 | 2017–2024 temporal coverage | ✓ 201701 returns populated rows |

Schema:

```text
{
  date:    YYYYMM (monthly)
  구분:    시도 (e.g. "서울", "부산", "전국", ...)
  시군구:  gu / si / gun name OR "계"/"합계" for sub-totals
  부문:    공공부문 / 민간부문 / 계
  규모:    40㎡이하 / 40~60㎡ / 60~85㎡ / 85㎡초과 / 소계 / 계
  호:      integer housing-unit count
}
unitName: 호
formName: 공사완료후 미분양현황
```

Pilot-gu sanity:

```text
201701  마포구 (계/계) = 0 호      강남구 (계/계) = 0 호
202604  마포구 (계/계) = 49 호     강남구 (계/계) = 0 호
        (마포구 매치 with user-pasted snapshot ✓)
```

API behaviour note (differs from existing `form_id=2082, style_num=128`): in form 5328/1, rows with value 0 are **present with explicit `호=0`** rather than omitted. The pre-completion unsold form omits the `미분양현황` field at value 0 (per `project-unsold-panel-built-2026-05-25`); the post-completion form is the opposite. The Seoul gu-year panel builder for this endpoint can therefore use a straight sum of `호` without zero-imputation logic, unlike the 2082/128 panel.

Relevance: post-completion unsold is a **lagging demand-stress** signal that pairs cleanly with the existing pre-completion `statnuri_unsold_{mean,max,dec}_units` (a *forward-looking* supply-stress signal). Both are demand-side Block-4b diagnostics. Post-completion specifically captures "developer-built but unsold after construction completed" — the canonical 'overhang' indicator. This is exactly the framing the user gave on the 신청목적 form ("주택시장 스트레스 지표 구축") and is downstream-safe for the dashboard's spatial-due-diligence framing (no credit signal, no PF underwriting).

Caveats / what is NOT decided here:

- **Integration into the dashboard contract** is out of scope for the probe. Adding `statnuri_completed_unsold_{mean,max,dec}_units` as Block-4b sub-rows would be a separate, user-authorised commit (own client module mirroring `molit_unsold_client.py`, builder, contract merge, dashboard metric option). The probe only establishes that the endpoint is fit for that work.
- The 1729→1482 row delta between 202604 and 201701 reflects 시군구 inventory changes over time (e.g. 군위군 absorption into 대구 in 2023, 세종 split-out) — confirmed by inspection. Not a data-quality red flag.

### Target B — 행정구역별·지목별 국토이용현황_시군구 — **ACCEPT**

행정구역별·지목별 국토이용현황_시군구, `form_id=2300` / `style_num=2`, is fit for a future Block 4c gu-year land-use / development-context companion. It resolves the licensed-source problem for gu-level land-use context, but it is **not a dong-grain designation overlay and should not be described as within-gu spatial variation**. It is not integrated by this probe commit.

Resolved 2026-06-08 via user-supplied data.go.kr granted-API detail page (URL key redacted at source). Two probes fired against `form_id=2300`, `style_num=2`:

```text
probe_landuse_2025.json  →  status_code=INFO-000, 282 rows, 25/25 Seoul gus
probe_landuse_2017.json  →  status_code=INFO-000, 279 rows, 25/25 Seoul gus
```

Acceptance gates (§3) closed:

| Gate | Criterion | Result |
|---|---|---|
| 1 | `status_code == "INFO-000"` | ✓ both probes |
| 2 | `formList` non-empty | ✓ 282 / 279 rows |
| 3 | Seoul 25-gu coverage | ✓ 25/25 in both probes |
| 4 | Numeric values (not %) | ✓ `면적` (m²) + `지번수` (parcel count), both integer / float |
| 5 | 2017–2024 temporal coverage | ✓ 2017 returns populated rows; period format `YYYY` (annual) |

Schema (28 land-use categories × {면적, 지번수} = 56 value columns per row):

```text
{
  date:    YYYY (annual)
  시도:    province (e.g. "서울", "경기", "전국", ...)
  시군구:  gu / si / gun name OR "합계"/"계" for province sub-totals
  계>면적, 계>지번수:       total area + parcel count
  대>면적, 대>지번수:       residential/business plot (built-up proxy)
  임야>면적, 임야>지번수:   forest
  공원>면적, 공원>지번수:   park
  ... 25 more categories (전, 답, 과수원, 목장용지, 광천지, 염전,
      공장용지, 학교용지, 주차장, 주유소용지, 창고용지, 도로,
      철도용지, 제방, 하천, 구거, 유지, 양어장, 수도용지,
      체육용지, 유원지, 종교용지, 사적지, 묘지, 잡종지)
}
unitName:  ㎡, 필지
formName:  행정구역별·지목별 국토이용현황_시군구
```

Pilot-gu sanity (2017 → 2025 deltas at `계,대,임야,공원` × m²):

```text
마포구  계      23,852,112  →  23,854,436  (administrative area essentially flat)
        대       8,577,145  →   8,549,842  (-27,303 m², slight decrease)
        임야       380,951  →     315,774  (-65,177 m², -17%)
        공원     2,723,115  →   2,833,495  (+110,380 m², +4%)

강남구  계      39,501,152  →  39,497,639  (flat)
        대      16,005,702  →  16,306,465  (+300,763 m², +1.9%)
        임야     6,076,431  →   6,022,886  (-53,545 m², -0.9%)
        공원     1,472,681  →   1,724,230  (+251,549 m², +17%)
```

`계` (total area) is flat by construction — administrative boundaries are stable across the panel. The interesting deltas are in the category breakdown. The 마포구 `임야 -17%` and `공원 +4%` versus the 강남구 `대 +1.9%` and `공원 +17%` are exactly the kind of descriptive built-environment companion signal Block 4c was scoped to carry. These signals are also independent of AlphaEarth (different data lineage), which is useful for triangulation when AlphaEarth's 2022 artifact policy is active.

Relevance to Block 4c:

- **Strengths**: clean KOGL-1-equivalent OpenAPI access (no derivative-use blocker like the KOGL-4 file source in `docs/development_spatial_companion_status.md`), gu-year grain matching `statnuri_unsold_*` (Block 4b), 28 mutually exclusive land-use categories enabling **built-up share**, **vegetation share**, **infrastructure share** as descriptive sub-features.
- **Caveats**: gu-year grain, **not dong-year** — per `docs/dashboard_mvp_spec.md` §5, rendering this on a per-dong fill requires explicit "gu-level broadcast" labeling (same rule as Block 4b's `statnuri_unsold_*`). This is NOT a dong-level designation overlay; it does not substitute Track 1 (의제처리구역 SHP, KOGL-4) at dong grain. It is a *different* product — a gu-year compositional view.
- **The 28-category breakdown is rich but not directly aligned to "development pressure" semantics.** Useful descriptive features will need to be defined explicitly (e.g. `built_share = (대 + 공장용지 + 학교용지 + 도로 + 철도용지 + 주차장) / 계`); the raw fields should not be exposed individually in the dashboard without aggregation.

Row count delta (279 → 282 between 2017 and 2025) reflects 시군구 administrative changes over the panel: 군위군 absorbed into 대구 in 2023 (transferred 시도 from 경북), and 세종특별자치시 status evolution. Not a data-quality red flag.

## 8. T1 closing state

Both targets resolved on the same day. The granted-APIs ledger (`reference-molit-granted-apis` memory) now records `form_id` / `style_num` and probe-OK status for all five originally-granted endpoints — leaving only the recon (6193/1) parked-empty case as a follow-up.

The four cached probe payloads under `data/probe_*.json` are gitignored and `cert_id`-scrubbed. No panel build, no dashboard wiring, no integration was performed in this work — those are separate, user-authorised commits.

What changes in the next session if you choose to integrate either endpoint:

- **Target A integration** would mean a new `molit_completed_unsold_client.py` mirroring `molit_unsold_client.py`, a builder producing `data/statnuri_completed_unsold_panel.parquet`, optional contract merge as `statnuri_completed_unsold_{mean,max,dec}_units` (Block 4b sub-rows), and a new dashboard metric option. Cost: 96 monthly pulls × 25 Seoul gus = 2,400 cache entries (similar to existing 2082/128). Different zero-handling logic (rows present with `호=0`, no omission).
- **Target B integration** would mean a new `molit_landuse_client.py` (annual not monthly — simpler), a builder producing `data/statnuri_landuse_panel.parquet` with derived `built_share`, `vegetation_share`, `infrastructure_share` aggregates rather than raw 56 columns, optional contract merge as Block 4c sub-rows. Cost: 8 annual pulls × 25 Seoul gus = 200 cache entries. The four-block dashboard's `development_pressure_spatial_variation` could move from `none` to `gu` when this lands.

Neither integration is in scope for this commit. The probe note exists to make the integration step a small, clearly-defined next workstream rather than a discovery exercise.
