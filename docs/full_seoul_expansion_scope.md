# Full-Seoul AlphaEarth expansion — pilot scope

This document scopes the AlphaEarth coverage expansion from the current 12 labeled dongs to a pilot of two complete Seoul gus (마포구 + 강남구), and to all of Seoul afterward conditional on pilot acceptance. It captures the infrastructure decisions that must be made before any code lands. Unresolved choices are deliberately left as `TBD` with explicit options, not guessed.

The companion product spec is `docs/dashboard_mvp_spec.md` §8 ("Small-N constraints — Pilot expansion") and §7 ("AlphaEarth 2022 artifact policy").

## 1. Purpose

Surface the polygon, geography, EE budget, cache, and artifact decisions required to extend AlphaEarth Block-2 coverage beyond the 12 labeled cases. The pilot is the validation harness for those decisions; the full-Seoul step is contingent on pilot acceptance (§8).

This is a *scoping* document. It does not authorize the pilot — that requires resolving the items in §9 first.

## 2. Pilot definition: 마포구 + 강남구 complete-gu coverage

The pilot extracts AlphaEarth annual embeddings for **every dong inside 마포구 and 강남구**, 2017–2024.

- 마포구: contains known gentrification-relevant neighborhoods (Yeonnam, Mangwon — already in the labeled set as `post_peak` and `active_panel` respectively).
- 강남구: high-price comparison gu with a different mechanism profile (Apgujeong, Daechi — already in the labeled set as controls).

These two gus together contain four of the twelve labeled cases (Yeonnam, Mangwon, Apgujeong, Daechi). The pilot therefore lets us sanity-check that the pilot extractor reproduces the existing `data/alphaearth_ee.parquet` rows for those four cases within tolerance.

Counts depend on the §3 geography choice:

| Geography | 마포구 count | 강남구 count | Pilot total | Pilot EE calls (×8 yrs) |
|---|---|---|---|---|
| 법정동 | ~27 | ~25 | ~52 | ~416 |
| 행정동 | 16 | 22 | 38 | ~304 |

Counts are approximate until the polygon source is fixed (§4).

Completeness by gu is more valuable than hitting a target count — accept slightly above 25 if a complete gu requires it.

## 3. Geography decision gate: 법정동 vs 행정동

Pick **one** for the pilot and for all subsequent expansion. Mixing produces unjoinable data and is explicitly prohibited by the MVP spec.

### 법정동 (legal-dong)

- **Pro**: matches `labeled_cases.csv` `dong_code` (8-digit, first-5-digits = LAWD_CD); matches MOLIT transaction-level data (`apis.data.go.kr/.../RTMSDataSvcAptRent`) directly when Block 1 unparks; matches `molit_unsold_client`'s `시군구 → lawd_cd` map; matches `molit_redev_client` geography (none — national).
- **Con**: 법정동 boundaries are sometimes legal-fiction (e.g., 익선동 is a small 종로구 legal-dong but the lat/lon for our labeled case currently sits inside a 8-digit code that maps to a different gu — see `[data-QA]` Ikseon flag, resolved via explicit `lawd_cd` CSV column); some 법정동 span multiple 행정동, making demographic joins ambiguous if Block 3 vulnerability data comes from KOSIS 행정동 tables.

### 행정동 (administrative-dong)

- **Pro**: matches actual local-government service areas; matches KOSIS population and household tables more naturally; better resolution for the eventual Block 3 vulnerability layer.
- **Con**: 행정동 boundaries change more frequently (occasional merges/splits); MOLIT transaction data is 법정동-keyed, so Block 1 joins would need a `법정동→행정동` crosswalk; the labeled cases would need re-coding.

**Resolved 2026-05-26: 법정동.**

Primary analytical geography for the pilot and for all downstream rent/housing joins is **법정동**. Reasons:

1. Existing case IDs in `labeled_cases.csv` are already 법정동-style 8-digit codes.
2. MOLIT/data.go.kr rent transactions (`RTMSDataSvcAptRent`) use legal-dong / LAWD geography natively.
3. StatNuri gu joins (unsold panel) use `lawd_cd`, which aligns with the 법정동 hierarchy.
4. Gentrification case narratives in the literature (`익선동`, `성수동1가`, `망원동`, `연남동`, `압구정동`) are expressed as 법정동 / neighborhood names, not 행정동 service areas.
5. 법정동 boundaries are more stable across years than 행정동, which can be reorganized for administrative reasons; longitudinal analysis benefits from the stability.

**Caveat — do not mix units.** Some public vulnerability and demographic indicators are published at 행정동 grain. The dashboard may later add a 법정동↔행정동 crosswalk for the Block 3 vulnerability layer, but the AlphaEarth pilot, the rent/housing joins, and the model panel **must not mix** units. Mixed-grain joins are explicitly prohibited (see `docs/dashboard_mvp_spec.md` §8).

## 4. Polygon source candidates

**Polygon source status (2026-05-27): on-disk schema confirmed; primary source revised.**

The actually-available official file family is the **NSDI D001 monthly AL EMD snapshot series** distributed through `data.go.kr 15045881` / VWorld `dsId=21` (국토교통부 일별 법정구역 정보) — not the static `dsId=30603` bulk-file we previously thought was primary. The user has six months of monthly snapshots (Dec 2025 – May 2026) plus the prior half-year on disk. The pilot pins to **`AL_D001_00_20260509(EMD)`** as the working snapshot.

The earlier `data.go.kr 15029173` LINK record remains useful as license/lineage attestation but is reclassified to a secondary tier.

Research outcome:

| Tier | Source | Geography | License | Reproducibility | Vintage | Decision |
|---|---|---|---|---|---|---|
| **Primary (working file source)** | **NSDI D001 monthly AL EMD snapshot via `data.go.kr 15045881` / VWorld `dsId=21`** | 법정동 (8-digit A1), national | KOGL — 이용허락범위 제한 없음 (per 15029173 attestation) | Monthly + daily distribution; user has 12 monthly snapshots on disk | Pinned snapshot: **`AL_D001_00_20260509(EMD)`** | **Resolved (on-disk schema confirmed 2026-05-27)** |
| Documented bulk-file alternate | VWorld 행정구역_읍면동(법정동), `dsId=30603` | 법정동, national | KOGL | VWorld bulk-download interface | Active | Use if D001 family becomes unavailable |
| LINK / lineage attestation | `data.go.kr` `15029173` — 전국법정구역(읍면동)정보표준데이터 | 법정동, national (LINK record) | KOGL attestation | Metadata page; LINK to VWorld | Modified 2024-10-30 | License/lineage record; not a download target |
| Backup | NSDI 오픈마켓 `dataset/15145` — 행정구역_읍면동(법정동) | 법정동, national | Government open | Portal navigation | Recent | Same authoritative source via a third portal |
| Higher-infra fallback | VWorld API (`LT_C_ADEMD_INFO`) | 법정동, national | API key registration required | API call flow | Active | Adds an extra credential flow vs. the bulk-file path; defer unless API is needed elsewhere |
| Emergency prototype-only | `southkorea/seoul-maps` (GitHub) | 법정동, Seoul-only | Apache 2.0 | Excellent (git clone) | 2015 (>10 years stale) | Use only if every official source is blocked; do **not** vendor into repo yet |
| Rejected | `data.seoul.go.kr/.../10080.do` — 행정구역 법정동 경계 | 법정동, Seoul | Login + 보안각서 (security agreement) | Manual approval flow | Unknown | Fails the reproducibility criterion |
| Out-of-scope | `data.go.kr` `15125045` — 행정구역시군구_경계_20250522 | **시군구 only** | KOGL | Portal nav | 2025-05-22 (current) | Useful for gu overlays; cannot satisfy the 법정동 requirement |

Rationale for the primary pick:

- D001 EMD is the actually-distributed monthly+daily official feed for legal-dong polygons.
- The user has 12 monthly snapshots on disk (Jun 2025 – May 2026), so the file lookup is fully reproducible without any further portal navigation.
- Pinning to a single monthly snapshot (`AL_D001_00_20260509(EMD)`) gives a stable boundary state across the panel; daily CH delta files exist for future date-specific reconstruction but are **not used for the MVP pilot**.
- License attestation on the 15029173 LINK page (`이용허락범위: 제한 없음`) covers the same data source — KOGL-compatible for repo/dashboard use.

### On-disk schema confirmation (2026-05-27)

Inspected `AL_D001_00_20260509(EMD)` (the most recent monthly snapshot — schema is consistent across the D001 series). Findings:

- **Features**: 5,369 polygons nationwide
- **Geometry type**: Polygon
- **CRS**: **EPSG:5186** (Korea 2000 Central Belt 2010, per `.prj`) — must reproject to EPSG:4326 before any `ee.Geometry` construction
- **Attribute encoding**: **CP949** (verified — Korean dong names read cleanly)
- **Attribute schema** (5 fields):

  | Field | Type | Mapped name | Description |
  |---|---|---|---|
  | `A0` | int32 | `_row_id` | internal feature id |
  | `A1` | str | `emd_cd` | **8-digit 법정동 code (canonical join key)** |
  | `A2` | str | `dong_name_kr` | Korean dong name |
  | `A3` | date | `effective_date` | snapshot effective date |
  | `A4` | str | `lawd_cd` | 5-digit 시군구 code |

- **Seoul rows** (`A4` in 25 Seoul gus): 467
- **Pilot rows**: 26 in 마포구 (`A4=11440`), 14 in 강남구 (`A4=11680`) → **40 dongs × 8 years = ~320 EE calls** at pilot scale

### Earlier 10-digit EMD_CD expectation was wrong

The earlier docs said `EMD_CD` would be a 10-digit code and the existing 8-digit `dong_code` would need zero-padding. The D001 EMD feed instead carries the canonical key directly as the 8-digit `A1`, which is the **same shape** as the existing `labeled_cases.csv.dong_code`. No padding step is needed.

### Crosswalk finding — `labeled_cases.csv.dong_code` was repaired against canonical A1

The initial D001 crosswalk found that all 12 labeled cases disagreed with the canonical `A1` when matched by `(A2 Korean name, A4 lawd_cd)`. The first-5-digit `lawd_cd` matched in 11 of 12 cases (Ikseon being the prior-known exception), but the last 3 digits diverged systematically — consistent with the CSV `dong_code` field carrying **행정동 (administrative-dong)** numbering while `dong_name_kr` carried **법정동 (legal-dong)** names. Historical examples before repair:

  - `Yeonnam`: CSV 11440710 → A1 11440124
  - `Mangwon`: CSV 11440730 → A1 11440123
  - `Apgujeong`: CSV 11680105 → A1 11680110
  - `Daechi`: CSV 11680117 → A1 11680106
  - `Ikseon`: CSV 11305680 → A1 11110133 (also the only `lawd_cd` mismatch)

**Implications for the loader and CSV repair:**

- The loader must crosswalk by `(dong_name_kr, lawd_cd)` against `A1`, **not** by `dong_code`. Trusting `dong_code` would fail to resolve any case.
- CSV repair landed in commit `2e322d1`: all 12 `dong_code` values now equal canonical `A1` and the loader reports **0/12 dong-code mismatches**.
- The existing `lawd_cd` column on `labeled_cases.csv` remains the authoritative gu join key. Do not infer gu from legacy coordinates.

### CH daily deltas — out of scope for MVP

The D001 distribution also ships daily CH delta files (`CH_D001_00_YYYYMMDD.zip`) for date-specific boundary reconstruction. These are not used for the MVP pilot. The pilot pins to one monthly AL snapshot and ignores all CH files.

CRS and encoding handling are now anchored to the on-disk inspection above: EPSG:5186 source CRS (reproject to EPSG:4326 before `ee.Geometry`), CP949 attribute encoding (decode to UTF-8 at load time). The earlier "standard `전국법정구역` schema" expectation (EMD_CD / EMD_KOR_NM, 10-digit) does not apply to this feed; the D001 series uses A0..A4 as documented in the on-disk schema confirmation.

## 5. Join keys and metadata requirements

For each pilot polygon row, the loader must emit:

- `emd_cd` — canonical 8-digit 법정동 code from `A1`. This is the join key. **Do not derive it from `labeled_cases.csv.dong_code`** during source loading — the CSV is now repaired, but the official D001 `A1` field remains canonical.
- `dong_name_kr` — Korean dong name from `A2`.
- `lawd_cd` — 5-digit 시군구 code from `A4`. Used for filtering to 마포구 (11440) and 강남구 (11680).
- `effective_date` — snapshot effective date from `A3`. Recorded for provenance.
- `lat`, `lon` — polygon centroid in EPSG:4326. Computed at load time; required for any downstream visualization.
- `geometry` — polygon in EPSG:4326. Required by EE.

**Labeled-case crosswalk rule** (peer-driven): when reconciling pilot polygons against `labeled_cases.csv`, key on `(dong_name_kr, lawd_cd)` against `(A2, A4)`. Surface any case whose CSV `dong_code` disagrees with the matched `A1` via a `[data-QA]` print. After the canonical-code repair, the expected mismatch count is 0/12; three lat/lon proxy centers remain outside their matched legal-dong polygons and are reported as non-fatal `[data-QA]` warnings.

## 6. Earth Engine call-count and cache plan

### Call counts

- Pilot: ~304 (행정동) – ~416 (법정동) `reduceRegion` calls.
- Full Seoul (post-pilot, conditional on §8): ~3,200 – ~3,400 calls depending on geography.

### Reduction strategies

| Strategy | Calls per (poly, year) | Trade-off |
|---|---|---|
| Per-call `reduceRegion().getInfo()` | 1 | Easier to checkpoint and debug; matches existing `audit_2022_artifact.py` and `prototype.extract_ee_embeddings` patterns; slow at scale |
| Batch `reduceRegions()` over `FeatureCollection` | ~1 per ~50–100 polygons | 50–100× fewer requests; needs polygon batching, cache adapted to multi-row writes; harder partial-run recovery |
| EE Task → Cloud Storage / Drive export | 1 task | Highest throughput; offline pickup; most infrastructure |

**Pilot default proposed**: per-call. Pilot scale is small enough that the existing pattern works without modification, and the audit module's `fetch_one` is a literal template.

**Full-Seoul recommendation (not authorized)**: switch to batch `reduceRegions` once the per-call path is proven on the pilot. Hold this decision until §8 acceptance.

### Cache layout

Follow the existing `data/audit_cache/{poly_id}_{year}.parquet` pattern from `audit_2022_artifact.py`. Per-call parquet survives partial runs and matches the EE-extract pattern in `prototype.py`. Naming proposed:

```
data/seoul_pilot_alphaearth_cache/{geography}_{emd_cd}_{year}.parquet
```

with `geography ∈ {bjd, hjd}` (법정동 / 행정동) so a future re-pull at a different geography lives in a different cache without collisions.

`data/seoul_pilot_alphaearth_cache/` should be added to `.gitignore` proactively (or covered by the existing `data/*` rule — it is already, but worth confirming during implementation).

### Live smoke status

`seoul_pilot_extract.py --gcp-project gong2026 --limit 1` succeeded on 2026-05-27 outside the sandbox after ADC token refresh, writing `data/seoul_pilot_alphaearth_cache/bjd_11440101_2017.parquet` in ~8.6 seconds wall clock. Follow-up bounded runs confirmed broader behavior: `--limit 8` completed one full legal dong (7 fresh + 1 cached) in 7.3 seconds, and `--limit 24` completed three full legal dongs (16 fresh + 8 cached) in 13.3 seconds, with an offline resume check reporting 24/24 cached. This confirms EE auth, geometry construction, reduction, per-call cache writing, and resume detection across multiple legal dongs. It is **not** a full B5 cost estimate; use the complete 320-row pilot before authorizing full-Seoul expansion.

Critical process correction: bounded `--limit` runs no longer write the default combined `data/seoul_pilot_alphaearth.parquet` panel unless `--output` is explicitly provided. This prevents a partial smoke-test panel from masquerading as the full 320-row pilot artifact; per-call cache files remain the canonical resume artifact.

### Full pilot extraction status (2026-05-27)

`seoul_pilot_extract.py --gcp-project gong2026` completed the full 40-dong × 8-year pilot:

- **Rows**: 320/320 complete
- **Cache**: 320 per-call parquet files under `data/seoul_pilot_alphaearth_cache/`
- **Fresh/cached split during full run**: 296 fresh + 24 cached
- **Missing**: 0
- **Wall clock**: 677.9 seconds
- **Observed per-target-row runtime**: 2.12 seconds
- **Combined panel**: `data/seoul_pilot_alphaearth.parquet` (gitignored)

At the observed pilot runtime, the 467-row Seoul legal-dong universe would imply roughly 3,736 polygon-years and about 2.2 hours of per-call extraction. Treat this as an order-of-magnitude planning estimate only; API load, retries, and geometry size can change full-Seoul runtime.

## 7. 2022 AlphaEarth artifact policy

Inherited from `docs/dashboard_mvp_spec.md` §7. For the pilot:

- **Primary outputs**: write raw embeddings to cache as the canonical artifact. Do not pre-residualize at write time — that destroys information.
- **Derived outputs**: at panel-build time, compute both raw and `residualized_tokyo_taipei` variants for the 2017–2024 span. The Tokyo/Taipei anchor cache already supports this (60 polygons × 8 years cached, `axis_residualize.py` implements the math).
- **Provenance**: record `physical_artifact_policy = flag` as the MVP default per spec §6. The residualized variant is available but not the active variable in the descriptive UI unless the operator switches it.
- **Pilot diagnostic**: verify the audit's full-N finding reproduces on the pilot — random pilot polygons in 마포구 and 강남구 should show the 2021→2022 hot transition with magnitude comparable to the audit's Seoul/random bucket. If not, the pilot has uncovered a regression in EE behavior or a polygon-source issue.

## 8. Acceptance criteria for the pilot

The pilot is accepted (and full-Seoul expansion is authorized) only when **all** of the following hold:

1. **Polygon source parses cleanly** at the chosen geography for both 마포구 and 강남구, with no missing dongs vs the MOIS code table or chosen authoritative dong list.
2. **All polygons survive `ee.Geometry()` construction** and complete a `reduceRegion` over `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL` for every year 2017–2024 within the bounded retry budget.
3. **Cache layout survives an artificially interrupted run** — kill the process mid-pull, restart, confirm the resume picks up only uncached `(poly, year)` pairs.
4. **Joins are valid end-to-end**: derived `lawd_cd` agrees with polygon-source `gu` for every pilot polygon, AND the four overlapping labeled cases (Yeonnam, Mangwon, Apgujeong, Daechi) reproduce within float32 tolerance against the existing `data/alphaearth_ee.parquet` rows.
5. **Within-gu variance is non-trivial** — at the dong granularity inside one gu, the embedding centroid is not collapsed to a single value. This is a sanity check, not a hypothesis test.
6. **2022 artifact reproduces** — the pilot's `2021–2022` year-pair angular-distance distribution shows a hot mode in 마포구 and 강남구 random polygons consistent with the audit's full-N finding (Seoul/random bucket: share-max share elevated above chance).
7. **Total EE cost is recorded** in seconds/minutes per polygon-year for both per-call and (if attempted) batch reductions. This is the budget input for the full-Seoul go/no-go.

Failing any of (1)–(4) blocks expansion outright. Failing (5) or (6) is a data-quality issue that requires re-investigation, not a green light. (7) is informational but required for the full-Seoul authorization.

### Pilot QA status (2026-05-27)

`seoul_pilot_qa.py` reads the completed pilot panel and writes `data/seoul_pilot_alphaearth_qa.json` (gitignored). Current findings:

- **Completeness**: pass — 320/320 rows, 40 dongs, 8 years, no duplicate `(emd_cd, year)` pairs, no missing embedding cells.
- **Gu counts**: 마포구 26 dongs, 강남구 14 dongs.
- **Within-gu variance**: pass — minimum per-gu/year embedding std-vector norm is 0.233323, so the dong embeddings are not collapsed within gu.
- **2022 artifact reproduction**: pass/confirmed — 95.0% of pilot dongs have `2021-2022` as their maximum angular YoY jump; median angular distance is 0.228960 for `2021-2022` vs 0.146747 for other year-pairs (ratio 1.56). By gu: 마포구 share 1.000, 강남구 share 0.857.
- **Overlap cases present**: pass — Yeonnam, Mangwon, Apgujeong, and Daechi all appear in the pilot.
- **Overlap comparison against legacy 12-dong EE panel**: run against `C:\Users\marem\PycharmProjects\Gong2026\data\alphaearth_ee.parquet`. The legacy panel used 1km proxy boxes and old pre-repair dong codes, so exact equality is not a valid criterion; the QA script maps the four overlap cases through the old-code map and reports deltas as diagnostic evidence. Results: Yeonnam max_abs_delta 0.043985 / median_l2_delta 0.136234; Mangwon 0.100842 / 0.310735; Apgujeong 0.152492 / 0.496147; Daechi 0.062134 / 0.191677. Non-zero deltas are expected from the geometry upgrade and should be documented, not treated as a blocker.

### §8 audit-trail caveats (2026-05-27)

Three §8 acceptance gates were not closed as written. The pilot science is sound (see Pilot QA status above); these are engineering / audit-trail observations recorded here so the full-Seoul authorization remains honest.

- **#1 — source-vs-panel completeness check. RESOLVED 2026-05-27 (with caveat).** The criterion read "Polygon source parses cleanly at the chosen geography for both 마포구 and 강남구, with no missing dongs vs the MOIS code table or chosen authoritative dong list." `seoul_pilot_qa.py.source_completeness` now optionally accepts a `--source-shp` path (outer ZIP, nested EMD ZIP, extracted dir, or `.shp`); when provided, it re-loads the D001 EMD shapefile via `legal_dong_polygons._resolve_shp_path` + `load_emd`, filters to `lawd_cd in {11440, 11680}`, and asserts the resulting `emd_cd` set equals the pilot panel's. The check is opt-in: skipping it does not cause a hard fail, but a positive check that fails (`checked=True`, `pass=False`) does. Catches manifest-filtering bugs, manually-dropped rows, and panel mutations.
  Honest caveat: the "authoritative dong list" here is the source SHP itself, not an independent 행정안전부 법정동 코드 표 (MOIS code table). This closes the **manifest-vs-source regression gap** but does not catch the case where the source SHP itself diverges from the MOIS canonical code table. The pinned snapshot (`AL_D001_00_20260509(EMD)`) is government-authoritative, so source-vs-MOIS divergence is low-probability but not zero; integrating a MOIS code-table check would be a separate workstream.

- **#3 — mid-write atomicity. RESOLVED 2026-05-27.** The criterion read "kill the process mid-pull, restart, confirm the resume picks up only uncached (poly, year) pairs." Two paths are now covered: `ae08346` empirically verified **clean-stop resume** (24/24 cached on an `--offline` rerun after a bounded `--limit 24` run), and `seoul_pilot_extract.py.fetch_embeddings` now writes each cache parquet to `cache.with_suffix(".tmp")` and then calls `tmp.replace(cache)` — an atomic rename on POSIX and Windows. A SIGKILL mid-flush can therefore leave a stale `.tmp` sibling but cannot leave a truncated `.parquet` that the next run's `cache.exists()` check would accept. Pre-existing per-call cache files written under the old non-atomic path remain valid; only new fresh pulls go through the atomic write.

- **#4a — lawd_cd ↔ gu_name consistency. RESOLVED 2026-05-27.** §8 criterion #4 included "derived `lawd_cd` agrees with polygon-source `gu` for every pilot polygon." This is enforced by construction in `legal_dong_polygons.build_pilot_manifest` (both fields derive from the shapefile's `A4`), but was not asserted in the QA output, so a manual panel mutation or a schema change that decoupled the two columns would slip through silently. `seoul_pilot_qa.py.lawd_gu_consistency` now compares every row's `gu_name` to `SEOUL_GU_NAME[lawd_cd]` (canonical map imported from `legal_dong_polygons`) and contributes to the QA hard-fail exit code; mismatches and unknown `lawd_cd` values are surfaced with up to ten examples each.

- **#4b — spec deviation, not pass.** The criterion read "the four overlapping labeled cases (Yeonnam, Mangwon, Apgujeong, Daechi) reproduce **within float32 tolerance** against the existing `data/alphaearth_ee.parquet` rows." `seoul_pilot_qa.py.overlap_summary` explicitly retires the equality criterion: "Diagnostic only: the legacy EE panel used 1km proxy boxes and old pre-repair dong codes, while the pilot uses official legal-dong polygons. Non-zero deltas are expected and should not block." The retirement is methodologically correct — the two panels use different geometry, so equality cannot hold — but it is a **change to the acceptance gate**, not a pass of the original wording. The reported deltas (Yeonnam max_abs 0.044 / L2 0.136; Mangwon 0.101 / 0.311; Apgujeong 0.152 / 0.496; Daechi 0.062 / 0.192) are diagnostic, not regression-test residuals.

- **#7 — partial cost record. RESOLVED 2026-05-27.** §8 asked for "seconds/minutes per polygon-year for both per-call and (if attempted) batch reductions." `seoul_pilot_extract.py.fetch_embeddings` now appends one row per successful fresh EE pull to `data/seoul_pilot_cost_log.csv` (gitignored under `data/*`) with header `timestamp,emd_cd,year,elapsed_s`. `elapsed_s` is wall-clock for the full attempt including any retries, so each row reflects the real cost of acquiring that `(emd_cd, year)`. The full-Seoul cost estimate is now a one-line aggregate over that log rather than prose extracted from bounded smokes. Disable with `--cost-log ""` if a run should not log. Batch reductions were intentionally not attempted (consistent with the per-call pilot plan; deferred to full-Seoul where `reduceRegions` or `Export` should be reconsidered per §6 and the methodology review).

None of the three blocks the current pilot. They are flagged because the full-Seoul run (3,400 calls, longer wall time, real budget exposure) is the load-bearing context where each one becomes material.

## 9. TBD decision table

| Decision | Options | Current default | Needed before code? |
|---|---|---|---|
| Dong geography | 법정동 / 행정동 | **법정동** (resolved 2026-05-26, §3) | Resolved |
| Polygon source | D001 monthly AL EMD (data.go.kr 15045881 / VWorld dsId=21) / dsId=30603 bulk-file alternate / NSDI 15145 backup / southkorea/seoul-maps emergency mirror | **D001 monthly AL EMD, pinned to `AL_D001_00_20260509(EMD)`** (resolved 2026-05-27, §4; on-disk schema confirmed) | Resolved |
| Authoritative dong list | Derived from the chosen polygon source above (`A1` 8-digit code) | A1 field of D001 AL EMD | Resolved |
| GCP project for EE | local user / gcloud ADC / service account | **`gong2026`** via gcloud ADC (resolved 2026-05-26, §10) | Resolved |
| Artifact policy (per-row default) | flag / residualize / drop | flag for MVP | No, but must be recorded in panel |
| EE reduction strategy | per-call `reduceRegion` / batch `reduceRegions` / Task export | per-call for pilot; batch/export still recommended for full Seoul if runtime or quota becomes painful | Resolved for pilot |
| Cache layout | `{geography}_{emd_cd}_{year}.parquet` / batch parquet | per-call parquet (`bjd_<emd_cd>_<year>.parquet`) | No |
| Polygon CRS handling | reproject at load / keep native | reproject to EPSG:4326 at load | No |
| Crosswalk requirement | 법정동↔행정동 crosswalk needed? | Depends on geography choice | Conditional on §3 |
| Full-Seoul authorization | Manual / automatic on pilot pass | Manual | No |

Each `TBD` becomes a one-line follow-up commit when resolved, keeping decisions auditable in git rather than buried in chat.

## 10. Earth Engine project + auth (resolved 2026-05-26)

- **Project**: `gong2026`
- **Auth mode**: Google Cloud SDK Application Default Credentials (ADC) via `gcloud auth application-default login`. Not the native `earthengine authenticate` flow.
- **Reason for deviation**: the native EE CLI OAuth client triggered Google's "This app is blocked" verification check on first attempt. Routing through gcloud's pre-verified OAuth client via ADC sidesteps the block without requiring OAuth-consent-screen configuration on the project.
- **Scope**: `cloud-platform` alone is sufficient for `ee.Initialize(project='gong2026')` and `ImageCollection.first().bandNames().getInfo()` against `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`. The explicit `earthengine` scope is optional.
- **ADC quota project**: `v0lare` (gcloud's default at credential-creation time). EE-side billing is routed via the `project=` argument in `ee.Initialize`, so the ADC quota-project mismatch is cosmetic for our case. Can be re-bound to `gong2026` with `gcloud auth application-default set-quota-project gong2026` if needed.
- **Runtime**: local Python from `.venv`. No service account in the MVP.
- **Quota policy**: pilot-only EE reductions until the 마포구 + 강남구 acceptance checks in §8 pass. Full-Seoul expansion requires explicit authorization after pilot completion.
- **Smoke test (2026-05-26)**: `ee.Initialize(project='gong2026')` + a live `bandNames` call on AlphaEarth 2024 returned `['A00']`. Recorded as the gate-resolution evidence.

## Notes

- This document is intentionally light on code references and heavy on decisions. The polygon manifest builder exists as `legal_dong_polygons.py`; the AlphaEarth pilot extractor exists as `seoul_pilot_extract.py` and consumes `data/pilot_legal_dong_manifest.parquet`.
- The companion full-Seoul-cost section will be added once `seoul_pilot_extract.py` has a minute-per-polygon-year wall-clock figure from a small live test.
- All four bold `TBD`s in the §9 table are now resolved. Pilot polygon-source download, canonical-code repair, pilot manifest builder, and pilot extractor scaffolding are complete; the next concrete work is a bounded live EE pilot pull plus the §8 QA report.
