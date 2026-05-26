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

Each candidate has different licensing, update cadence, projection, and authoritativeness. Document the choice before downloading.

| Candidate | Format | Geography support | Update cadence | License | Notes |
|---|---|---|---|---|---|
| NSDI (`nsdi.go.kr` / 국가공간정보포털) | SHP, GeoJSON | 법정동, 행정동, 시군구 | Annual or on-demand | Government open | Default reference for administrative boundaries; large bundles |
| SGIS (`sgis.kostat.go.kr`) | SHP / API | 법정동, 행정동, 통계지구 | Aligned with census cycles | KOSIS open | Statistical-lineage; matches KOSIS demographic joins downstream |
| 서울 열린데이터광장 (`data.seoul.go.kr`) | SHP, GeoJSON, API | 행정동 (primarily) | Periodic | Seoul-specific open | Smaller scope, faster to download; not nationally consistent |
| MOIS 법정동 코드 (no geometry, code table only) | CSV | 법정동 code reference | Quarterly | Government open | Code-table-only; pair with a geometry source above |

A typical workflow is **MOIS code table + NSDI or SGIS geometries**, joined by code.

**Pilot default proposed**: `TBD`. Resolution requires committing to §3 first (geometry layer follows from geography choice).

CRS handling: all candidates ship in EPSG:5179 (Korea TM) or similar projected CRS. Earth Engine requires EPSG:4326 (or accepts polygons in any CRS via `ee.Geometry` if the CRS is declared). The pilot extractor must reproject before the `reduceRegion` call. This is a known-solved step; not a TBD.

## 5. Join keys and metadata requirements

For each pilot polygon row, the loader must emit:

- `dong_code` — full code (8 or 10 digits depending on geography choice). Must round-trip through `lawd_cd_from_dong_code` to the correct `lawd_cd` (5-digit gu code) for the gu it belongs to.
- `dong_name_kr` — Korean dong name (matches the MOLIT 법정동 field when Block 1 unparks).
- `name_roman` — optional but consistent with `labeled_cases.csv` for the four overlapping cases.
- `gu` — Korean gu name (matches `labeled_cases.csv` and the unsold panel).
- `lawd_cd` — derived. Must agree with `gu` per the existing data-QA validation in `prototype.build_model_panel`.
- `lat`, `lon` — polygon centroid in EPSG:4326. Required for any downstream visualization and as a fallback for cases where polygon ops fail.
- `geometry` — polygon in EPSG:4326. Required by EE.

The existing data-QA print (`[data-QA] N case(s) override the dong_code-derived gu via the CSV lawd_cd column`) should also fire for pilot polygons whose derived gu disagrees with the polygon-source gu. Surface, do not silently re-key.

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
data/seoul_pilot_alphaearth_cache/{geography}_{dong_code}_{year}.parquet
```

with `geography ∈ {bjd, hjd}` (법정동 / 행정동) so a future re-pull at a different geography lives in a different cache without collisions.

`data/seoul_pilot_alphaearth_cache/` should be added to `.gitignore` proactively (or covered by the existing `data/*` rule — it is already, but worth confirming during implementation).

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

## 9. TBD decision table

| Decision | Options | Current default | Needed before code? |
|---|---|---|---|
| Dong geography | 법정동 / 행정동 | **법정동** (resolved 2026-05-26, §3) | Resolved |
| Polygon source | NSDI / SGIS / 서울 열린데이터광장 / MOIS+geometry | TBD | **Yes** — next gate |
| Authoritative dong list | NSDI / SGIS / MOIS 법정동 코드 표 / `labeled_cases.csv` (insufficient) | TBD | **Yes** — drives universe count and §8 (1) |
| GCP project for EE | (same as audit module) / new project | TBD | **Yes** — affects quota and billing |
| Artifact policy (per-row default) | flag / residualize / drop | flag for MVP | No, but must be recorded in panel |
| EE reduction strategy | per-call `reduceRegion` / batch `reduceRegions` / Task export | per-call | No — recommendation pending pilot result |
| Cache layout | `{geography}_{dong_code}_{year}.parquet` (proposed) / batch parquet | per-call parquet | No |
| Polygon CRS handling | reproject at load / keep native | reproject to EPSG:4326 at load | No |
| Crosswalk requirement | 법정동↔행정동 crosswalk needed? | Depends on geography choice | Conditional on §3 |
| Full-Seoul authorization | Manual / automatic on pilot pass | Manual | No |

Each `TBD` becomes a one-line follow-up commit when resolved, keeping decisions auditable in git rather than buried in chat.

## Notes

- This document is intentionally light on code references and heavy on decisions. The pilot module itself does not yet exist; it would live as a new file (`seoul_pilot_extract.py` or similar) that consumes a chosen polygon source and writes to the proposed cache layout.
- The companion full-Seoul-cost section will be added once §9 row "GCP project for EE" is resolved and a minute-per-polygon-year wall-clock figure exists from a small live test.
- Do not begin pilot extraction until at least the four bold `TBD`s in §9 are resolved.
