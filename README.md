# Gong2026

Seoul gentrification research prototype. Pairs Google AlphaEarth satellite embeddings with MOLIT (data.go.kr) rent-transaction records to test whether physical neighborhood change and tenure-pressure can be separately identified at the dong (administrative neighborhood) level, 2017–2024.

## Status

**Research-design repair phase — not a validated model.** A within-panel audit found that the learned embedding axis is not gentrification-specific: Mullae (an active_panel case) is the strongest outlier rather than the labeled control Hwagok, and all dongs share a suspicious 2022 year-over-year peak that points to an AlphaEarth pipeline artifact rather than to urban change. Until that is resolved, mock and live runs are scaffold checks, not empirical evidence.

## What this is — and isn't

- **Is:** a screening layer for *physical* neighborhood change (AlphaEarth) paired with a *tenure-pressure* layer (wolse ratio from MOLIT). Output is a per-(dong, year) projection slope on a learned drift axis plus a wolse slope — reported separately, not as a single score.
- **Isn't:** a displacement predictor. AlphaEarth measures morphology, not who is displaced. The defensible framing is a four-block layered model — physical / tenure / vulnerability / development pressure — kept distinct so social and commercial risk don't get collapsed.
- **Not a PF credit signal.** Real-estate project-finance underwriting requires variables this model does not carry (acquisition cost, debt structure, pre-sale rate, exit liquidity). The intended downstream use, if and when validated, is spatial due-diligence input, not credit scoring.

## Methodology

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

## Data sources

- **AlphaEarth annual embeddings** (`GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`) via Earth Engine, 2017–2024. Requires a GCP project with the Earth Engine API enabled.
- **MOLIT 아파트 전월세 실거래가** (data.go.kr dataset 15126474), pulled per-(gu, month) by `molit_client.py`. Requires a service key in the `MOLIT_SERVICE_KEY` env var — use the **Decoded** key from your data.go.kr 마이페이지, not the Encoded one (the client passes it through `requests.params`, which URL-encodes once).
- **MOLIT 통계누리 연도별 재개발사업 현황** (form_id 6189, style_num 1), pulled annually by `molit_redev_client.py` and aggregated into `data/national_redevelopment_intensity.parquet`. National-aggregate only — no `시군구` field. Requires `MOLIT_STAT_NURI_KEY` plus either env vars (`MOLIT_REDEV_FORM_ID`/`_STYLE_NUM`) or `--form-id`/`--style-num` CLI flags. A companion 재건축 table (6193/1) is granted but currently returns empty rows; parked pending an alternative `style_num`.
- **MOLIT 통계누리 시·군·구별 미분양현황** (form_id 2082, style_num 128), pulled monthly by `molit_unsold_client.py` over 2017-01 – 2024-12 and aggregated to annual at gu grain into `data/statnuri_unsold_panel.parquet`. Seoul gus only at present; nationwide extension requires province-disambiguated gu-name → LAWD_CD mapping. API quirk: when monthly unsold = 0 the field `미분양현황` is omitted from the row; the builder treats omitted-field as zero per the empirical convention (verified against 2020 vs 2024 cache files). `MOLIT_UNSOLD_FORM_ID`/`_STYLE_NUM` env vars override defaults.
- **Labeled cases** (`data/labeled_cases.csv`) — 12 Seoul dongs hand-labeled from the academic literature; citations in the CSV. Carries an explicit `lawd_cd` column (5-digit gu code) derived from the human-validated `gu` Korean name; the prototype uses this for gu-level joins rather than inferring from `dong_code`, because at least one case (Ikseon, `dong_code=11305680`) has a `dong_code` whose first-5-digits map to a different gu than the labeled `gu` value. The prototype prints a `[data-QA]` note every run listing such overrides so they stay visible.

## Repository layout

```
prototype.py             active research scaffold (learned axis + LOO + plots + model panel)
molit_client.py          data.go.kr / MOLIT 전월세 client (pagination, retry, raw cache)
molit_stat_nuri_client.py 통계누리 OpenAPI probe client (transport + retry + scrubbing)
molit_redev_client.py    재개발/재건축 annual probe + national panel builder (on top of StatNuri)
molit_unsold_client.py   시·군·구별 미분양현황 monthly probe + Seoul gu-level panel builder
archive/                 superseded code retained for reference
data/labeled_cases.csv   hand-labeled cases (tracked)
data/                    raw pulls and parquet caches (gitignored)
outputs/                 generated plots (gitignored)
```

## Status of components

| Component | State |
|---|---|
| AlphaEarth axis learning + LOO | implemented; **scientific validity under audit** (see Status above) |
| MOLIT rent client | implemented with guardrails (pagination, retry, fail-loud, raw-chunk cache); awaiting first live pull |
| National redev intensity control | implemented; 8 years (2017–2024) validated against live API; additive invariant on 건립가구 categories holds; joined into `data/dong_year_model_panel.parquet` |
| Gu-level unsold-housing stress control | implemented; 96 monthly pulls (2017-01..2024-12) over Seoul's 25 gus; annual mean/max/Dec; joined into `data/dong_year_model_panel.parquet` by `lawd_cd × year` |
| 재건축 (recon) annual table | granted but empty at style_num=1; parked pending portal-listed alternatives |
| Data.go.kr 전월세 live pull | scaffolded; LAWD_CD extraction fixed for 8-digit codes; blocked on data.go.kr-decoded service key (StatNuri key returns 401 on `apis.data.go.kr`) |
| Synthetic mock pipeline | works end-to-end; perfect LOO is by construction, not by evidence |
| Hwagok / Mullae axis-specificity audit | open |
| 2022 AlphaEarth artifact diagnosis | open |
| Composite four-block model | not yet implemented; deliberately deferred until axis is validated |
