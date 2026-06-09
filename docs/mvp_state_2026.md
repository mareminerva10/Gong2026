# MVP state (2026-06-04)

This is the freeze note for the current MVP ceiling under the no-approval public-data state. It states exactly what is currently live, what is parked and why, and what is forbidden until inputs change. The intent is to protect the project from scope creep and to give a clean handoff point.

**Product name:** Seoul built-environment change tracker with housing-supply pressure controls (40-dong pilot at present). Gentrification is an interpretation layer over these signals, not a model output. *Forecast*, *risk*, *probability*, *prediction*, *alarm*, and *score* are prohibited in titles, UI copy, and exported columns; see §Forbidden until inputs change.

Freeze baseline: `origin/master = 59be4d9` on `master`. The product-claim reframe lands in a subsequent T4 commit. If anything below claims "live" but the corresponding artifact is missing on a fresh clone, the source-of-truth docs cited per bullet are authoritative.

## Live

The product surface that is built, tested, and audit-trail-recorded:

- **40-dong 법정동 pilot map** for **마포구 + 강남구**, served by `dashboard_app.py` over `dashboard_pilot_contract.py`'s output (`data/dashboard_pilot_contract.parquet`). Choropleth fill, year/metric/policy/gu selectors, sidebar block-status badges, top-dongs / timeline / current-year-table panels.
- **Official D001 법정동 polygons** loaded from `data/pilot_legal_dong_manifest.parquet`, sourced via `legal_dong_polygons.py` from `AL_D001_00_<snapshot>(EMD).zip`. EPSG:4326, fill-rule:evenodd polygon rendering. See `docs/full_seoul_expansion_scope.md` §3–§4.
- **AlphaEarth physical-change layer** (40 dongs × 8 years = 320 rows), produced by `seoul_pilot_extract.py` and audited by `seoul_pilot_qa.py`. All §8 acceptance gates closed or recorded as deliberate spec-deviations. See `docs/full_seoul_expansion_scope.md` §8.
- **Three-policy artifact-handling feature layer** (`raw`, `tokyo_taipei_offset`, `metric_year_fe`) from `seoul_physical_residualized.py`, merged into the contract. Default analytical policy: `metric_year_fe` at `pilot_cross_dong` scope. See `docs/dashboard_mvp_spec.md` §7.
- **Artifact-aware policy selector + warning copy** in the dashboard. Strict consumption rule (no alarm / EWS / forecast on 2021–2022 transitions) is rendered as a red notice when a policy-aware metric is active in year=2022. See `docs/dashboard_mvp_spec.md` §7.
- **Block 4 controls live** (four sub-rows):
  - `statnuri_unsold_{mean,max,dec}_units` at gu × year grain via `molit_unsold_client.py` — Block 4b sub-row 1, **pre-completion / pre-sale unsold** ('inventory waiting to sell').
  - `statnuri_completed_unsold_{mean,max,dec}_units` at gu × year grain via `molit_completed_unsold_client.py` — Block 4b sub-row 2, **post-completion unsold** ('inventory built but unsold' — the canonical 'overhang' indicator). Tracked under a separate `completed_unsold_status` to avoid conflation with sub-row 1.
  - `national_redevelopment_intensity_*` at national × year grain via `molit_redev_client.py` (Block 4a, redevelopment intensity).
  - `landuse_{built,vegetation,infrastructure,transport}_share` at gu × year grain via `molit_landuse_client.py` (Block 4c, **gu-level land-use context / broadcast** — not within-gu spatial variation; the dong-grain designation overlay remains parked under KOGL-4). With Block 4c present, `development_pressure_spatial_variation` flips from `none` to `gu`.
- **Audit-trail caveats** for all §8 acceptance gates that were closed by deliberate change or partial coverage. See `docs/full_seoul_expansion_scope.md` §8 caveats.

## Parked

The product surface that is explicitly NOT in scope until the named input changes:

- **Block 1 (tenure pressure / wolse_ratio) — apartment sub-scope resolved 2026-06-09.** `RTMSDataSvcAptRent` (data.go.kr dataset 15126474) integrated via `molit_client.build_seoul_tenure_panel`; 2,400 gu-month rows for all 25 Seoul gus over 2017–2024 in `data/wolse_molit.parquet`. The dashboard contract flips `tenure_status` to `live_partial` with `tenure_scope='apartment_only'`. **Still partial**: single/multi-family rent (`RTMSDataSvcSingleHouseRent`, dataset 15126472) and officetel rent endpoints are not yet integrated. Promotion from `live_partial` to `live` requires building those sibling clients. See `docs/tenure_source_status.md`.
- **Block 3 (vulnerability)** — unscoped. No source identified. Unblocks on: source identification (KOSIS demographics / household income / age structure or equivalent) followed by a status doc analogous to `docs/tenure_source_status.md`.
- **Block 4c (spatial development companion) — resolved at gu-year grain on 2026-06-08.** StatNuri 2300/2 (행정구역별·지목별 국토이용현황_시군구) is live as a **gu-level land-use context layer**, broadcast to dong rows via `lawd_cd × year`. It is **not** a dong-grain designation overlay; the dong-grain Track 1 (의제처리구역 SHP, KOGL-4) and Track 2 (Seoul aggregate tables 235 / 10804 / 145, empty file slots) remain parked. 건축HUB OpenAPI also remains parked as post-MVP. See `docs/molit_probe_2026-06-07.md` for the probe verdict and `docs/development_spatial_companion_status.md` for the parked dong-grain tracks. The dong-grain overlay still unblocks on: KOGL-1 license clarification or a non-file access path for the Seoul aggregate tables.

## Forbidden until inputs change

The following are explicitly out of scope and must not be added under the current data + label constraints. Each item names the input change that would lift the prohibition:

- **EWS / alarm / forecast language in any UI, doc, or export column.** Reason: AlphaEarth gives only 8 annual observations per dong; critical-slowing-down or any forecast scalar built on this is mathematically dressed-up noise. Unblocks on: substantially longer trajectories (full-Seoul + more years) AND a defensible validation design that does not rely on the rejected 1-D axis.
- **Supervised ML on the 96-row labeled panel** (12 dongs × 8 years). Reason: structurally insufficient for boosted trees / random forests / gu fixed effects / calibration / feature-selection sweeps. See `project-dashboard-framing-small-n-constraints-2026-05-25`. Unblocks on: an order-of-magnitude larger labeled set or a different target definition based on observable proxies.
- **Composite gentrification / displacement / risk score** as a single calibrated probability or numeric scalar. Reason: small-N + missing tenure + missing vulnerability + small validation set make any composite uncalibrated by construction. Four-block typology framing stands (see `feedback-pf-spatial-due-diligence` and `docs/dashboard_mvp_spec.md` §1–§2). Unblocks on: live Block 1 + live Block 3 + a defensible validation design.
- **Resurrection of the AlphaEarth 1-D gentrification axis** as a primary signal. Reason: empirically rejected by the residualized-axis audit (`project-residualized-axis-audit-2026-05-25`). AlphaEarth remains usable only as a Block 2 descriptive physical-change feature family.
- **Ingesting the KOGL-4 polygon source (`data.go.kr 15082965` / Seoul OA-20957) into derived dashboard features.** Reason: the governing license (KOGL-4: 출처표시 + 상업적 이용금지 + 변경금지) forbids derivative use, which is what intersection with the pilot manifest and `agenda_zone_*` columns would constitute. Unblocks on: confirmation that the canonical license is KOGL-1 not KOGL-4, OR a no-derivative-safe substitute.

## Where each unblock lives

- Tenure source decisions: `docs/tenure_source_status.md`
- Spatial development companion decisions: `docs/development_spatial_companion_status.md`
- Dashboard claim scope + artifact policy: `docs/dashboard_mvp_spec.md`
- Pilot extraction + acceptance audit-trail: `docs/full_seoul_expansion_scope.md`
- Methodology and small-N constraints: `~/.claude/projects/.../memory/project-dashboard-framing-small-n-constraints-2026-05-25.md`
