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
- **Labeled cases** (`data/labeled_cases.csv`) — 12 Seoul dongs hand-labeled from the academic literature; citations in the CSV.

## Repository layout

```
prototype.py            active research scaffold (learned axis + LOO + plots)
molit_client.py         data.go.kr / MOLIT 전월세 client (pagination, retry, raw cache)
archive/                superseded code retained for reference
data/labeled_cases.csv  hand-labeled cases (tracked)
data/                   raw pulls and parquet caches (gitignored)
outputs/                generated plots (gitignored)
```

## Status of components

| Component | State |
|---|---|
| AlphaEarth axis learning + LOO | implemented; **scientific validity under audit** (see Status above) |
| MOLIT rent client | implemented with guardrails (pagination, retry, fail-loud, raw-chunk cache); awaiting first live pull |
| Synthetic mock pipeline | works end-to-end; perfect LOO is by construction, not by evidence |
| Hwagok / Mullae axis-specificity audit | open |
| 2022 AlphaEarth artifact diagnosis | open |
| Composite four-block model | not yet implemented; deliberately deferred until axis is validated |
