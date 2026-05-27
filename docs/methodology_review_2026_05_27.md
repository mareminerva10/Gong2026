# Methodology Review — 2026-05-27

This note records a critical review of the current Gong2026 pilot state against external academic and public-methodology references. It is not a literature review for publication; it is a guardrail for what the dashboard may claim and what the next code should emit.

## Current Project State

What is now solid:

- Official legal-dong polygon source selected and loaded: NSDI D001 AL EMD, pinned to `AL_D001_00_20260509(EMD)`.
- Canonical legal-dong code repair complete: `labeled_cases.csv.dong_code` now matches D001 `A1`.
- Full 마포구 + 강남구 AlphaEarth pilot complete: 40 dongs × 8 years = 320 rows, 0 missing.
- Pilot QA passes completeness and within-gu variance.
- The 2021→2022 AlphaEarth artifact reproduces strongly in the pilot: 95% of pilot dongs have `2021-2022` as their maximum angular YoY jump.

What remains scientifically fragile:

- The old one-dimensional "gentrification axis" remains rejected for product use.
- The pilot proves physical-change extraction, not displacement or rent pressure.
- Block 1 tenure pressure is still parked without a live data.go.kr `RTMSDataSvcAptRent` pull.
- Block 3 vulnerability and Block 4c spatial development companion are not scoped.
- Legacy EE overlap comparison is diagnostic only, because the legacy panel used 1km proxy boxes and old pre-repair codes while the pilot uses official legal-dong polygons.

## External Anchors

### 1. Treat remote sensing as physical-change evidence, not displacement truth

Remote-sensing gentrification research is moving toward citywide, time-series urban-change screening. A recent Landsat ARD study reports strong redevelopment-identification performance and frames satellite time series as early-warning flags, but also notes only moderate agreement with socioeconomic reference maps and points to future integration of socioeconomic/environmental indicators. That matches our stance: AlphaEarth is a Block 2 physical-change layer, not a complete gentrification label.

Source: [Early detection of gentrification risk using Landsat ARD and machine learning](https://www.sciencedirect.com/science/article/pii/S2352938525003106).

### 2. Training-label scarcity and interpretation are real blockers

A review of remote sensing for urban poverty and gentrification emphasizes the promise of time-series satellite data but flags limited training samples, hard-to-label temporal patterns, and the risk that numerical correlations miss causal interpretation. This directly supports the Gong2026 decision not to train a supervised forecast on the current 12 labeled cases.

Source: [Remote Sensing of Urban Poverty and Gentrification](https://www.mdpi.com/2072-4292/13/20/4022).

### 3. Typology maps are a better near-term product metaphor than forecasts

The Urban Displacement Project's public typology-map repo explicitly summarizes housing-market dynamics and displacement/gentrification risk into categories, documents code/data, and warns that typologies should not be treated as predetermined trajectories. That is close to the product pattern Gong2026 should follow: transparent evidence blocks and badges, not a black-box probability.

Source: [urban-displacement/displacement-typologies](https://github.com/urban-displacement/displacement-typologies).

### 4. Earth Engine scale-up should eventually move beyond per-call `getInfo`

Google Earth Engine best practices recommend filtering/selecting early, using exports for expensive long-running computations, and being careful with large reductions. The pilot's per-call cache is acceptable at 320 rows because it is auditable and resumable. For all-Seoul production, `reduceRegions` or `Export` should be reconsidered if runtime or quota becomes painful.

Sources: [Earth Engine coding best practices](https://developers.google.com/earth-engine/guides/best_practices), [ee.Image.reduceRegions reference](https://developers.google.com/earth-engine/apidocs/ee-image-reduceregions).

## Implications For Gong2026

1. The next artifact should be a **dashboard data contract**, not another model. It should make provenance, native grain, data status, and artifact flags impossible to miss.
2. Block 2 should expose descriptive physical metrics:
   - embedding norm
   - YoY angular change
   - YoY cosine distance
   - YoY Euclidean distance
   - within-gu anomaly rank/z-score
   - `physical_2022_artifact_flag`
3. Parked or missing blocks should be represented as status columns, not zero-filled values.
4. No UI or export should contain "forecast", "prediction", "probability", or "displacement risk score" until live tenure/vulnerability/development layers and a defensible validation design exist.
5. Full-Seoul AlphaEarth extraction is technically authorized by pilot QA, but product value is higher if the dashboard contract lands first. Scaling without the contract would produce data faster than we can explain it.

## Follow-through Implemented

The recommended next artifact now exists as `dashboard_pilot_contract.py`. It writes a gitignored pilot table at `data/dashboard_pilot_contract.parquet` with 320 legal-dong-year rows, descriptive AlphaEarth physical-change metrics, 2022 artifact flags, within-gu anomaly ranks/z-scores, and explicit status columns for every block. When local Block 4 artifacts are present, the contract merges live gu-year unsold controls and national-year redevelopment controls. It deliberately computes no forecast, probability, displacement-risk score, or composite score.

This means the best next project move is no longer "prove extraction works"; that is done for the pilot. The next high-leverage choices are:

1. Build a thin dashboard/UI prototype over `dashboard_pilot_contract.parquet` that renders status badges and physical-change diagnostics before any full-Seoul expansion.
2. Decide whether Block 1 tenure can be unparked via a valid live source or should remain visibly parked in the MVP.
3. Only after the contract-driven UI is clear, authorize full-Seoul Block 2 extraction.
