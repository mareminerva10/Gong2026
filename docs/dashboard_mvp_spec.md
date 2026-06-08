# Dashboard MVP spec — four-block, descriptive, non-forecasting

This is a descriptive four-block housing-pressure dashboard spec, not a supervised gentrification forecasting spec. It captures what the MVP is, what it is not, and what must land before any forecasting layer is even attempted.

All design decisions below are grounded in the project memory tracked under `~/.claude/projects/.../memory/`; the most load-bearing ones are the residualized-axis audit (`project-residualized-axis-audit-2026-05-25`) and the small-N framing (`project-dashboard-framing-small-n-constraints-2026-05-25`).

## 1. Product claim

**Product name:** Seoul built-environment change tracker with housing-supply pressure controls.

> **A descriptive Seoul neighborhood dashboard for observed housing-pressure and development-pressure evidence, organized into four blocks with explicit per-block data-status badges. Gentrification is an interpretation layer, not a model output.**

Concretely, the MVP:

- Surfaces observed indicators per Seoul dong (or gu where dong is unavailable), 2017–2024.
- Organizes those indicators into four evidence blocks (see §3).
- Marks every block with provenance and status so users can see at a glance which evidence is live, parked, or mocked.
- Renders only signals whose native grain matches the rendering surface (see §5).

The MVP does **not**:

- Forecast next-year wolse_ratio, gentrification entry, or any other future outcome.
- Output a single calibrated probability or composite score.
- Train a supervised model on the current 96-row labeled panel.
- Treat any AlphaEarth-derived feature as primary signal.

## 2. Non-claims / prohibited claims

The dashboard MUST NOT make any of the following claims, either in UI copy, documentation, or downstream presentations:

1. "Gentrification forecast" / "predicts gentrification" / "displacement risk score".
2. "Calibrated probability of X" for any X — calibration requires a probabilistic target and a labeled validation set we do not have.
3. "Validated against ground truth" — the 12-dong labeled set is for case-study QA, not validation.
4. "PF credit signal" / "credit-scoring input" — see `feedback-pf-spatial-due-diligence`. Spatial due diligence is the framing, not credit scoring.
5. "Composite housing-pressure score" rendered as a single number — keep blocks separated; if a top-level triage flag is needed, it is a transparent rule (e.g., "block-2 anomaly AND block-4 high-pressure") with the rule visible.
6. "AlphaEarth gentrification axis" — explicitly rejected by the 2026-05-25 residualization audit.
7. Rendering a national-only variable on a per-dong map fill — see §5.

## 3. Four evidence blocks

| # | Block | What it shows | Native grain |
|---|---|---|---|
| 1 | Tenure pressure | wolse/jeonse split, deposit/m², monthly rent/m² | gu or dong × month (when tenure source lands) |
| 2 | Physical change | AlphaEarth drift / anomaly, NOT the rejected 1-D axis | dong × year |
| 3 | Vulnerability | demographic / socioeconomic indicators | not yet scoped |
| 4 | Development pressure | redevelopment intensity + housing-market stress (supply-side + demand-side) | mixed; see §4 |

Block 4 deliberately contains both supply-side (redev intensity) and demand-side (unsold) signals because they are joint diagnostics of housing-market dynamics. They are presented as sub-rows within the block, with their own grain and status.

## 4. Current data status by block

| Block | Variable(s) | Source | Grain | Status (2026-05-27) |
|---|---|---|---|---|
| 1 Tenure pressure | `wolse_ratio`, `n_wolse`, `n_jeonse`, `median_deposit_per_m2`, `median_monthly_rent_per_m2` | data.go.kr `RTMSDataSvcAptRent` OR StatNuri tenure-split form | gu × month (target) | **PARKED** — see [B1] |
| 2 Physical change | `physical_embedding_norm`, `physical_yoy_angular`, `physical_yoy_cosine_dist`, `physical_yoy_euclid`, within-gu anomaly rank/z-score; **not** the rejected 1-D axis | Earth Engine `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL` | legal-dong × year | **LIVE — completed 마포구+강남구 pilot cache and dashboard contract**; full-Seoul expansion requires explicit authorization |
| 3 Vulnerability | TBD (KOSIS demographics, household income, age structure, etc.) | TBD | TBD | **NOT SCOPED** — see [B2] |
| 4a Redev intensity | `national_redevelopment_intensity_*` (7 vars) | StatNuri 6189/1 | **national × year** | **LIVE — but no spatial variation**; see §5 |
| 4b Unsold housing stress (pre-completion) | `statnuri_unsold_{mean,max,dec}_units` | StatNuri 2082/128 | gu × month, aggregated to gu × year | **LIVE** (Seoul gus, 2017–2024). Pre-completion / pre-sale unsold — 'inventory waiting to sell'. |
| 4b Unsold housing stress (post-completion) | `statnuri_completed_unsold_{mean,max,dec}_units` | StatNuri 5328/1 | gu × month, aggregated to gu × year | **LIVE** (Seoul gus, 2017–2024). Post-completion unsold — 'inventory built but unsold' (canonical overhang indicator). Tracked under separate `completed_unsold_status` to avoid conflation. |
| 4c Spatial development companion | `landuse_{built,vegetation,infrastructure,transport}_share` + raw 56-column audit retention | StatNuri 2300/2 | gu × year | **LIVE — gu-level broadcast / context** (not within-gu spatial variation; resolved 2026-06-08 via `molit_landuse_client.py`) |

## 5. Grain-mismatch and map-rendering rules

A signal's native grain dictates where and how it may be rendered:

- **Per-dong map fill**: only signals at dong × year native grain.
  - Allowed: Block 2 (AlphaEarth, where available).
  - Disallowed: Blocks 4a, 4b. Rendering 4b at dong fill is acceptable only if explicitly labeled "gu-level broadcast" with all dongs in a gu colored uniformly.
- **Per-gu map fill**: signals at gu × month/year native grain.
  - Allowed: Block 4b. Block 1 (when available).
  - Disallowed: Block 4a — rendering a single national value as a 25-gu choropleth implies spatial variation that does not exist.
- **National-level annotation/temporal axis**: Block 4a only.
  - Render as a single year-axis trend line or a uniform legend annotation. Not as a map fill, not as a per-region color.
- **Parked blocks** (1, 3, 4c at present): render an explicit "data parked / not yet integrated" badge with the relevant blocker ID from §9. Do not zero-fill, do not interpolate, do not render an empty map as if it were a zero value.

## 6. Provenance / status columns (panel-level)

Following the established pattern (`embed_mode`, `wolse_source` already on every model-panel row), every panel row should carry the following block-level provenance fields. The pilot implementation now populates these fields in `data/dashboard_pilot_contract.parquet` via `dashboard_pilot_contract.py`.

Per-row fields, by block:

- `physical_source` — e.g. `alphaearth_ee`, `synth`
- `physical_grain` — e.g. `dong-year`
- `physical_artifact_policy` — analytical policy: one of `{raw, tokyo_taipei_offset, metric_year_fe}` (default `metric_year_fe` at `pilot_cross_dong` scope; see §7). The strict downstream `drop_2022` rule and the UI `flag_2022` rule are consumer/renderer conventions layered on top of the analytical policy, not stored values of this field.
- `tenure_source` — `data_go_kr_rtms`, `statnuri_<form>`, `synth`, `parked`
- `tenure_grain` — `gu-month`, `dong-month`, `parked`
- `tenure_status` — `live`, `mock`, `parked`
- `housing_stress_source` — `statnuri_2082_128`, `parked`
- `housing_stress_grain` — `gu-year`
- `housing_stress_status` — `live`
- `development_pressure_source` — `statnuri_6189_1`
- `development_pressure_grain` — `national-year`
- `development_pressure_status` — `live`
- `development_pressure_spatial_variation` — `none`, `gu`, `dong` (currently `none`)

These should be string-valued columns so they survive merges and round-trip through parquet without dtype acrobatics. They are deliberately not enums.

### Pilot contract implementation (2026-05-27)

`dashboard_pilot_contract.py` is the first concrete dashboard handoff table. It reads the completed legal-dong AlphaEarth pilot and writes `data/dashboard_pilot_contract.parquet` (gitignored). The contract has 320 rows (40 dongs × 8 years), keeps 64 embedding bands for auditability, and adds descriptive Block 2 metrics plus explicit status columns for every other block.

`dashboard_app.py` serves the first localhost dashboard over that contract. It exposes year, gu, and metric controls; legal-dong centroid visualization; top-dong rankings; selected-dong timelines; and block status badges. It is a UI for the descriptive contract only, not a scoring or forecasting app.

Critical implementation choices:

- 2017 has null YoY metrics by construction; all later years have YoY metrics.
- 2022 rows carry `physical_2022_artifact_flag=True` because they represent the 2021→2022 transition.
- Block 1 tenure and Block 3 vulnerability are status-marked, not zero-filled.
- Block 4a/4b merge into the contract when their local parquet artifacts are present at build time; otherwise they remain status-marked as `missing_local_artifact`.
- No forecast, probability, composite score, or displacement-risk output is computed.

## 7. AlphaEarth 2022 artifact policy

All Block 2 metrics inherit the 2021→2022 regional common-mode shift documented in `project-2022-artifact-audit-findings` (full N=30: 2022 hot in Seoul + Osaka, clean in Tokyo + Taipei). Drift magnitude, angular change, and local-anomaly metrics all inherit this contamination, not only the rejected 1-D axis.

### Three-tier consumption policy

The project keeps `raw`, `tokyo_taipei_offset`, and `metric_year_fe` physical-change metrics side by side in `seoul_physical_residualized.py`. The **default analytical policy is `metric_year_fe` at `pilot_cross_dong` scope**. However, because 2021–2022 remains partially elevated in 강남구 after centering (5 of 14 dongs along the Tehran Road corridor still top the 2021–22 list under `metric_year_fe`, while 압구정동 goes negative — see `312693e`), the project enforces a layered policy:

- **Default analytical policy** — `metric_year_fe` at `pilot_cross_dong` scope. The relative-anomaly feature exposed to descriptive consumers.
- **Strict downstream / alarm policy** — any EWS, alarm, or forecast-like consumer **must drop 2021–2022 transition rows** before producing its scalar. This is a consumer-layer rule (`drop_2022`), not a stored value of `physical_artifact_policy`.
- **Dashboard display policy** — 2021–2022 rows may be rendered only with an explicit artifact flag (`physical_2022_artifact_flag=True`, red badge in `dashboard_app.py`).

Per-gu centering (`pilot_gu_cross_dong` scope) is **parked as sensitivity analysis only** — with N=14 강남구 dongs the per-gu median is too noisy to be the production default.

### Available analytical policies

- **`raw`** — no adjustment. Surfaces the artifact unmodified; only safe as a comparison reference.
- **`tokyo_taipei_offset`** — subtract cumulative Tokyo+Taipei anchor drift from each Seoul row before computing the metric. **Recorded negative result** in `c22390c`: valid for axis-projection metrics but does not neutralize YoY angular distance, because angular distance is not translation-invariant. Do not consume for alarm/EWS purposes. Infrastructure cached under `data/audit_cache/` (60 polygons × 8 years), implemented in `axis_residualize.py`.
- **`metric_year_fe`** — subtract cross-dong median of each metric within the same year-pair (YoY) or year (norm), at `pilot_cross_dong` scope. Interpretation: "anomaly relative to other pilot dongs in the same year-pair", not "artifact-free physical change". Verified at pilot in `312693e`: 마포구 share-max-2021-2022 drops to ~chance (0.115); 강남구 partial (0.357, above the strict 2×chance threshold 0.286).

The `physical_artifact_policy` provenance field on every row records the active analytical policy. The strict `drop_2022` and UI `flag_2022` rules are layered on top by the consumer / renderer, not by the feature-layer producer.

## 8. Small-N constraints (binding)

The current labeled panel has 12 dongs × 8 years = 96 rows, with 2 active_panel, 4 post_peak, 6 controls. Against ~83 model columns this is insufficient for: supervised forecasting, boosted trees / random forests, gu fixed effects, calibration, feature-selection sweeps, multi-feature model comparison.

The 96-row labeled panel is appropriate for: case-study QA, false-positive audits, rank audits, negative-result documentation, qualitative dashboard example tiles.

The **descriptive layer of the dashboard is independent of label count** — it runs over the full Seoul dong/gu universe with no supervised step. The labeled set is used only as worked-example tiles and as a QA harness, not as training data.

### Pilot expansion (Block 2 / full-Seoul)

Before expanding AlphaEarth coverage to all ~424 Seoul dongs, the MVP requires a pilot. The pilot is **complete-gu coverage of 마포구 and 강남구**, not 25 arbitrary dongs.

- 마포구 contains known gentrification-relevant neighborhoods (Yeonnam, Mangwon) and tests within-gu heterogeneity.
- 강남구 is a high-price comparison gu with a different mechanism profile (Apgujeong, Daechi).

Use the final chosen dong geography end-to-end (either 법정동 or 행정동, **not mixed**). The pilot exists to test polygon sourcing, metadata joins, EE reduction cost, cache layout, within-gu variance, and the 2022 artifact handling before any all-Seoul attempt. Accept slightly above 25 dongs if a complete gu requires it; completeness beats hitting a target count.

## 9. Open blockers

| ID | Blocker | Type | Resolves |
|---|---|---|---|
| B1 | 전월세 source not settled — data.go.kr key absent, no validated StatNuri tenure form yet | USER-SIDE | Block 1 unparked |
| B2 | Vulnerability block has no source candidate | PROJECT-SCOPING | Block 3 unparked |
| B3 | Spatial companion for Block 4 at **gu-year context** grain (StatNuri 2300/2 `landuse_*_share`) | RESOLVED 2026-06-08 (gu-year only) | Block 4 has gu-level spatial variation; `development_pressure_spatial_variation = "gu"`. Dong-grain overlay (designation polygons) remains parked under KOGL-4 / empty file slots; not in B3 scope. |
| B4 | Polygon source and pilot manifest | RESOLVED 2026-05-27 | D001 AL EMD legal-dong source selected; 마포구+강남구 pilot manifest implemented |
| B5 | Earth Engine reduction cost not estimated; ~3,392 polygon-year reductions implied at full Seoul | RESOLVED 2026-05-27 | Pilot run completed: 320 polygon-years in 677.9s (~2.12s each); full-Seoul runtime still needs explicit authorization |
| B6 | Dashboard handoff contract missing | RESOLVED 2026-05-27 | `dashboard_pilot_contract.py` emits a non-forecast, provenance-rich pilot table for UI/API work |

Resolving B1 unparks Block 1. Resolving B3 unlocked a gu-year Block 4c layer on 2026-06-08 (the dong-grain designation overlay remains parked separately). B4, B5, and B6 are resolved for the pilot; the remaining gate for any full-Seoul Block 2 expansion is explicit product authorization after reviewing the completed pilot QA. B2 is independent and lowest-priority for the MVP.

The MVP can ship descriptively with Blocks 4a + 4b + 4c (gu-context) live and Blocks 1, 2, 3 marked parked/not-scoped. Whether that shippable state is desirable is a separate product decision.
