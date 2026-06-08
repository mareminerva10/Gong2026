# Gong2026

Seoul built-environment change tracker with housing-supply pressure controls. Pairs Google AlphaEarth satellite embeddings with MOLIT (data.go.kr) StatNuri housing-supply and redevelopment-intensity records to surface descriptive physical neighborhood change and gu-level housing-supply pressure at the dong (administrative neighborhood) level, 2017–2024. Gentrification is an interpretation layer over these signals, not a model output.

## Status

**Research-design repair phase — not a validated model.** A within-panel audit found that the learned embedding axis is not gentrification-specific: Mullae (an active_panel case) is the strongest outlier rather than the labeled control Hwagok, and all dongs share a suspicious 2022 year-over-year peak that points to an AlphaEarth pipeline artifact rather than to urban change. Until that is resolved, mock and live runs are scaffold checks, not empirical evidence.

Current MVP ceiling, live/parked/forbidden inventory, and unblock paths are recorded in [`docs/mvp_state_2026.md`](docs/mvp_state_2026.md).

## What this is — and isn't

- **Is:** a descriptive screening layer for *physical* neighborhood change (AlphaEarth) at dong-year grain, paired with live gu-year housing-supply stress (StatNuri 미분양) and national-year redevelopment intensity (StatNuri 재개발) as Block-4 context. Tenure pressure (Block 1) and vulnerability (Block 3) remain parked; see [`docs/mvp_state_2026.md`](docs/mvp_state_2026.md).
- **Isn't:** a displacement predictor. AlphaEarth measures morphology, not who is displaced. The defensible framing is a four-block layered model — physical / tenure / vulnerability / development pressure — kept distinct so social and commercial risk don't get collapsed.
- **Not a PF credit signal.** Real-estate project-finance underwriting requires variables this model does not carry (acquisition cost, debt structure, pre-sale rate, exit liquidity). The intended downstream use, if and when validated, is spatial due-diligence input, not credit scoring.

## Methodology

### Live MVP pipeline (descriptive)

The current MVP is a descriptive built-environment change tracker over a 40-dong 마포구+강남구 pilot. It does not forecast, does not output a composite score, and does not resurrect the rejected 1-D axis. The pipeline runs `seoul_pilot_extract.py` → `seoul_physical_residualized.py` → `dashboard_pilot_contract.py` → `dashboard_app.py`, with Block-4 controls (`statnuri_unsold_*`, `national_redevelopment_intensity_*`) joined in when their local artifacts are present. See [`docs/mvp_state_2026.md`](docs/mvp_state_2026.md) and [`docs/dashboard_mvp_spec.md`](docs/dashboard_mvp_spec.md).

### Current control status

Live, non-mocked controls currently included in the model panel:

- `national_redevelopment_intensity_*`: live StatNuri redevelopment table `form_id=6189`, `style_num=1`, 2017–2024. This is a national year-level redevelopment pressure control, not a local treatment.
- `statnuri_unsold_mean_units`, `statnuri_unsold_max_units`, `statnuri_unsold_dec_units`: live StatNuri unsold-housing table `form_id=2082`, `style_num=128`, aggregated from monthly Seoul gu-level rows to annual gu-level housing-market stress controls.
- `wolse_ratio`: still synthetic/mock-shaped. The live data.go.kr apartment rent transaction pull remains parked pending a valid decoded data.go.kr key for `RTMSDataSvcAptRent`.
- Reconstruction controls remain parked: the granted StatNuri reconstruction table returns empty metric rows at `style_num=1`.

### Historical research design — steps 1–6 rejected by 2026-05-25 residualization audit; steps 7–8 still live as controls

The numbered steps below describe the original learned-axis approach. The 2026-05-25 audit (`project-residualized-axis-audit-2026-05-25`) rejected the 1-D drift axis as a gentrification signal, so steps 1–6 are kept here only to keep the audit trail legible — they are **not** the live pipeline. Steps 7–8 describe Block-4 controls that remain live and are summarized under §Current control status above.

1. Pick labeled Seoul cases: 2 active_panel gentrifying dongs, 4 post_peak (cycle finished before 2017), 6 controls.
2. For each (dong × year, 2017–2024), extract a 64-D AlphaEarth embedding mean over the dong polygon at 10 m scale.
3. Learn a within-panel drift axis: the mean of `embedding(last 2 yrs) − embedding(first 2 yrs)` across active_panel cases.
4. Score each dong-year by its projection onto that axis; trajectory slope is the gentrification score.
5. Validate by leave-one-out: hold each active_panel case out, relearn the axis from the rest, check whether the held-out case ranks above all controls.
6. Pair with one Korea-specific tenure signal — slope of the wolse (월세) ratio over the panel — computed from real MOLIT transactions when available.
7. Carry a year-level **national** redevelopment-intensity control from MOLIT 통계누리 (redev table 6189/1): zone count, area, demolition targets, and planned-unit categories. The variable enters the panel as `national_redevelopment_intensity_*`, joined by `year` only. This is a national-trend covariate, **not** dong-level or gu-level announcement exposure — the source table has no geographic dimension. Keep that distinction in any downstream interpretation.
8. Carry a **gu-level** monthly unsold-housing inventory control from MOLIT 통계누리 (unsold table 2082/128), aggregated to annual (`mean / max / Dec`). Enters the panel as `statnuri_unsold_{mean,max,dec}_units`, joined by `lawd_cd × year`. This is a housing-market stress / weak-demand proxy — **not** a tenure signal and **not** a `wolse_ratio` substitute.

## Running

Mock mode needs no external services and is the right starting point for reading the pipeline:

```bash
python prototype.py                                            # mock × mock — scaffold check
python prototype.py --mode ee --gcp-project YOURS              # live AlphaEarth × mock wolse
python prototype.py --wolse-source molit                       # mock × live MOLIT
python prototype.py --mode ee --gcp-project YOURS --wolse-source molit   # both live
```

`--mode` and `--wolse-source` are independent — AlphaEarth and MOLIT answer different questions, and you may want one live and one mocked for debugging.

Perfect leave-one-out on mock data is *expected* (the synthetic generator plants a shared drift direction). It is not validation.

For the pilot dashboard MVP — runnable surface, controls, non-claims, and unblock paths — see the five sections below.

## Run the dashboard

The dashboard is a localhost HTTP server with no external dependencies beyond the project's Python environment. It reads two artifacts under `data/`:

1. `data/pilot_legal_dong_manifest.parquet` — 40 legal-dong polygons (produced by `legal_dong_polygons.py`).
2. `data/seoul_pilot_alphaearth.parquet` — 40 dongs × 8 years embedding panel (produced by `seoul_pilot_extract.py`).
3. (Optional) `data/seoul_pilot_physical_residualized.parquet` — three-policy feature layer (produced by `seoul_physical_residualized.py`).
4. (Optional) `data/statnuri_unsold_panel.parquet`, `data/national_redevelopment_intensity.parquet`, `data/statnuri_landuse_panel.parquet` — Block 4 controls (housing-supply stress, redev intensity, gu-level land-use context).

Build the dashboard contract from whichever of those are present, then serve:

```bash
python dashboard_pilot_contract.py
python dashboard_app.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. The contract builder fails closed if the AlphaEarth pilot panel is missing; Block 4 controls and the residualized layer are merged opportunistically when present and status-marked as `missing_local_artifact` otherwise.

## What the pilot map shows

A descriptive choropleth over the 40-dong **마포구 + 강남구** pilot. Each polygon is the official D001 법정동 boundary, EPSG:4326. Polygon fill encodes the selected metric under the selected artifact policy. The dashboard is **descriptive only** — there is no forecast, no probability, no composite score.

- **Metric selector** lists policy-aware Block 2 metrics (`physical_yoy_angular`, `physical_yoy_cosine_dist`, `physical_yoy_euclid`, `physical_embedding_norm`) and policy-neutral Block 4 metrics (`statnuri_unsold_mean_units`, `national_redevelopment_intensity_zone_count`).
- **Year selector** picks the year. For YoY metrics, year *N* renders the *N−1 → N* transition.
- **Gu segmented control** filters between 마포구, 강남구, or both.
- **Top dongs / Selected timeline / Current-year table** panels reflect the same metric + policy selection as the map.
- **Sidebar block-status badges** show live / parked / not-scoped per evidence block.

## Artifact-policy controls

Per `docs/dashboard_mvp_spec.md` §7, the dashboard exposes three analytical policies for the AlphaEarth physical-change layer side-by-side. The selector is the third toolbar control on the map page:

- **`raw`** — no adjustment. Surfaces the 2021–2022 regional common-mode shift unmodified. Comparison reference only.
- **`tokyo_taipei_offset`** — subtracts cumulative Tokyo + Taipei anchor drift from each Seoul embedding before computing the metric. Valid for axis-projection metrics but **does not** neutralize YoY angular distance (recorded negative result in `c22390c`). Do not consume for alarm / EWS purposes.
- **`metric_year_fe`** *(default)* — subtracts the cross-dong median of each metric within the same year-pair, at `pilot_cross_dong` scope. Interpretation: "deviation from the pilot cross-dong median for the same year-pair." It removes common year-pair level shifts but **may also remove real Seoul-wide shocks** — it is a relative anomaly, not artifact-free truth.

When the active metric is policy-aware AND the selected year is **2022** (the 2021–2022 transition), a red notice appears in the dashboard repeating the strict rule: **2021–2022 is artifact-sensitive; values are displayed for transparency but must not be used for alarm / EWS / forecast-like interpretation.**

## What this MVP does not claim

The dashboard and the underlying contract deliberately do **not** ship any of the following. Each is forbidden until the named input changes; see `docs/mvp_state_2026.md` for the full inventory.

- **No EWS / alarm / forecast** in any UI element, column name, or exported value. AlphaEarth gives only 8 annual observations per dong; any forecast scalar built on this is dressed-up noise.
- **No supervised ML on the 96-row labeled panel.** 12 dongs × 8 years is structurally insufficient for boosted trees / random forests / gu fixed effects / calibration; see `project-dashboard-framing-small-n-constraints-2026-05-25`.
- **No composite gentrification / displacement / risk score** as a single number. Four-block typology framing only.
- **No resurrection of the AlphaEarth 1-D gentrification axis** as a primary signal — empirically rejected by the residualized-axis audit (`project-residualized-axis-audit-2026-05-25`).
- **No ingestion of the KOGL-4 정비구역 polygon source** (`data.go.kr 15082965` / Seoul `OA-20957`) into derived dashboard features. The governing license forbids derivative use. See `docs/development_spatial_companion_status.md`.

## Parked blocks and unblock paths

Three evidence blocks are explicitly **parked** in the MVP and the dashboard renders them as `parked` / `missing_local_artifact` / `not_scoped` status badges accordingly:

- **Block 1 — Tenure pressure.** No StatNuri tenure-split candidate validated; data.go.kr `RTMSDataSvcAptRent` is code-ready but credential-blocked. Unblocks on: a portal-authenticated StatNuri catalog browse identifying a tenure-split form_id, **or** a decoded `RTMSDataSvcAptRent` key set as `MOLIT_SERVICE_KEY` in a fresh shell. See `docs/tenure_source_status.md`.
- **Block 3 — Vulnerability.** No source identified (KOSIS demographics / household income / age structure all out of scope). Unblocks on: source identification followed by a status doc analogous to `docs/tenure_source_status.md`.
- **Block 4c — Spatial development companion.** Resolved at gu-year grain via StatNuri 2300/2 (`molit_landuse_client.py`, `data/statnuri_landuse_panel.parquet`). Surface metrics: `landuse_built_share`, `landuse_vegetation_share`, `landuse_infrastructure_share`, `landuse_transport_share`. This is **gu-level broadcast / context**, not within-gu spatial variation — every dong in a gu receives the gu-level value. The dong-grain designation overlay (Track 1, 의제처리구역 SHP) remains parked under KOGL-4. Track 2 Seoul aggregate tables (235 / 10804 / 145) remain empty at file source. See `docs/molit_probe_2026-06-07.md` for the probe verdict and `docs/development_spatial_companion_status.md` for the parked tracks.

The dashboard's `development_pressure_spatial_variation` is `gu` when `data/statnuri_landuse_panel.parquet` is present, `none` otherwise. Live block-4 signals (`statnuri_unsold_*` at gu × year, `national_redevelopment_intensity_*` at national × year, plus the new `landuse_*` shares at gu × year) coexist; none claims dong-grain spatial variation.

## Data sources

- **AlphaEarth annual embeddings** (`GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`) via Earth Engine, 2017–2024. Requires a GCP project with the Earth Engine API enabled.
- **MOLIT 아파트 전월세 실거래가** (data.go.kr dataset 15126474), pulled per-(gu, month) by `molit_client.py`. Requires a service key in the `MOLIT_SERVICE_KEY` env var — use the **Decoded** key from your data.go.kr 마이페이지, not the Encoded one (the client passes it through `requests.params`, which URL-encodes once).
- **MOLIT 통계누리 연도별 재개발사업 현황** (form_id 6189, style_num 1), pulled annually by `molit_redev_client.py` and aggregated into `data/national_redevelopment_intensity.parquet`. National-aggregate only — no `시군구` field. Requires `MOLIT_STAT_NURI_KEY` plus either env vars (`MOLIT_REDEV_FORM_ID`/`_STYLE_NUM`) or `--form-id`/`--style-num` CLI flags. A companion 재건축 table (6193/1) is granted but currently returns empty rows; parked pending an alternative `style_num`.
- **MOLIT 통계누리 시·군·구별 미분양현황** (form_id 2082, style_num 128), pulled monthly by `molit_unsold_client.py` over 2017-01 – 2024-12 and aggregated to annual at gu grain into `data/statnuri_unsold_panel.parquet`. Seoul gus only at present; nationwide extension requires province-disambiguated gu-name → LAWD_CD mapping. API quirk: when monthly unsold = 0 the field `미분양현황` is omitted from the row; the builder treats omitted-field as zero per the empirical convention (verified against 2020 vs 2024 cache files). `MOLIT_UNSOLD_FORM_ID`/`_STYLE_NUM` env vars override defaults.
- **MOLIT 통계누리 행정구역별·지목별 국토이용현황_시군구** (form_id 2300, style_num 2), pulled annually by `molit_landuse_client.py` over 2017 – 2024 into `data/statnuri_landuse_panel.parquet` (25 Seoul gus × 8 years = 200 rows). Schema is `{date YYYY, 시도, 시군구, 28 land-use categories × {면적 m², 지번수 parcels}}`. The builder retains the raw 56 per-category columns for audit and computes four descriptive shares — `landuse_built_share`, `landuse_vegetation_share`, `landuse_infrastructure_share`, `landuse_transport_share` — which are deliberately **overlapping proxies**, not an orthogonal partition; see the module docstring for the exact formulas. Block 4c (the spatial development companion) consumes this panel as a **gu-level broadcast / context** layer; it is *not* a dong-grain designation overlay. `MOLIT_LANDUSE_FORM_ID`/`_STYLE_NUM` env vars override defaults.
- **Labeled cases** (`data/labeled_cases.csv`) — 12 Seoul dongs hand-labeled from the academic literature; citations in the CSV. `dong_code` is now aligned to the canonical 8-digit D001 EMD `A1` legal-dong code, and `lawd_cd` is retained as the explicit 5-digit gu code for gu-level joins. Three legacy lat/lon values remain proxy-box centers outside their matched legal-dong polygons; that is surfaced by `legal_dong_polygons.py` as non-fatal `[data-QA]` output and should not be silently repaired.

## Repository layout

```
prototype.py             active research scaffold (learned axis + LOO + plots + model panel)
molit_client.py          data.go.kr / MOLIT 전월세 client (pagination, retry, raw cache)
molit_stat_nuri_client.py 통계누리 OpenAPI probe client (transport + retry + scrubbing)
molit_redev_client.py    재개발/재건축 annual probe + national panel builder (on top of StatNuri)
molit_unsold_client.py   시·군·구별 미분양현황 monthly probe + Seoul gu-level panel builder
legal_dong_polygons.py   D001 AL EMD loader + 마포구/강남구 pilot polygon manifest builder
seoul_pilot_extract.py   resumable AlphaEarth extractor for the 마포구/강남구 pilot manifest
seoul_pilot_qa.py        QA report for pilot completeness, variance, 2022 artifact, and overlap checks
dashboard_pilot_contract.py descriptive dashboard handoff table for the completed AlphaEarth pilot
dashboard_app.py         localhost dashboard over the pilot contract
docs/                    methodology and scope specs (committed; see dashboard_mvp_spec, full_seoul_expansion_scope)
archive/                 superseded code retained for reference
data/labeled_cases.csv   hand-labeled cases (tracked)
data/                    raw pulls and parquet caches (gitignored)
outputs/                 generated plots (gitignored)
```

## Status of components

| Component | State |
|---|---|
| AlphaEarth axis learning + LOO | **rejected** by 2026-05-25 residualization audit; archived, not in MVP pipeline |
| MOLIT rent client | implemented with guardrails (pagination, retry, fail-loud, raw-chunk cache); awaiting first live pull |
| National redev intensity control | implemented; 8 years (2017–2024) validated against live API; additive invariant on 건립가구 categories holds; joined into `data/dong_year_model_panel.parquet` |
| Gu-level unsold-housing stress control | implemented; 96 monthly pulls (2017-01..2024-12) over Seoul's 25 gus; annual mean/max/Dec; joined into `data/dong_year_model_panel.parquet` by `lawd_cd × year` |
| Gu-level land-use context (Block 4c) | implemented as `molit_landuse_client.py`; 8 annual pulls (2017..2024) over Seoul's 25 gus = 200 rows in `data/statnuri_landuse_panel.parquet`. Four descriptive shares (built / vegetation / infrastructure / transport) plus raw 56-column per-category audit retention. Merges into the dashboard contract as **gu-level broadcast** (not within-gu spatial variation) and flips `development_pressure_spatial_variation` from `none` to `gu`. |
| Legal-dong polygon pilot manifest | implemented; loads NSDI D001 AL EMD snapshot (pinned `AL_D001_00_20260509(EMD)`), reprojects EPSG:5186→4326, filters to 마포구+강남구 (40 dongs), writes `data/pilot_legal_dong_manifest.parquet`. Canonical `dong_code` repair is complete (0/12 mismatches); 3 lat/lon-not-contained proxy-center cases remain as non-fatal `[data-QA]` warnings |
| AlphaEarth pilot extractor | implemented as `seoul_pilot_extract.py`; full 40-dong × 8-year pilot complete (320/320 rows, 0 missing) with resumable per-call cache under `data/seoul_pilot_alphaearth_cache/`; full run took 677.9s (~2.12s per polygon-year). `seoul_pilot_qa.py` passes completeness and within-gu variance, reproduces the 2021→2022 artifact, and compares the four overlap cases to the legacy 1km-proxy EE panel through the old-code map |
| Dashboard pilot contract | implemented as `dashboard_pilot_contract.py`; writes `data/dashboard_pilot_contract.parquet` (gitignored) with 320 rows, descriptive AlphaEarth physical-change metrics, 2022-artifact flags, within-gu anomaly ranks/z-scores, and explicit status columns. When local Block 4 artifacts are present, it merges live gu-year unsold controls and national-year redevelopment controls; tenure remains parked and vulnerability remains not scoped. No forecast, probability, or composite score is computed |
| 재건축 (recon) annual table | granted but empty at style_num=1; parked pending portal-listed alternatives |
| Data.go.kr 전월세 live pull | scaffolded; LAWD_CD extraction fixed for 8-digit codes; blocked on data.go.kr-decoded service key (StatNuri key returns 401 on `apis.data.go.kr`) |
| Synthetic mock pipeline | works end-to-end; perfect LOO is by construction, not by evidence |
| Hwagok / Mullae axis-specificity audit | open |
| 2022 AlphaEarth artifact diagnosis | reproduced in 마포구+강남구 pilot: 95% of dongs have 2021→2022 as the max angular YoY jump; MVP policy remains `flag_2022` |
| Four-block descriptive dashboard | implemented for 마포구+강남구 pilot; full-Seoul + Blocks 1/3/4c parked per `docs/mvp_state_2026.md` |
