# Block 4c spatial development companion — source status

Source-discovery scout for **Block 4c (spatial development pressure)**,
the per-dong/per-gu spatial signal that complements the existing
national-year `national_redevelopment_intensity_*` Block 4a control. The
current Block 4 has zero spatial variation; this scout identifies
candidate sources that would give it gu/dong granularity.

Recorded 2026-06-04. No integration, no probing, no dashboard changes.
This document is the deliverable; the next code milestone happens only
after a candidate is explicitly green-lit.

## 1. Acceptance criteria

Any candidate must clear the following before being green-lit for
integration:

1. Seoul geography — gu or dong, preferably polygon or point/address
   that can be intersected with the existing pilot manifest.
2. Time coverage overlapping 2017–2024.
3. Development-relevant signal — permits, completions, demolition,
   redevelopment/reconstruction zones, building age, or 정비구역
   designation.
4. Reproducible source path — direct download URL or a documented
   OpenAPI endpoint, not a portal browse buried behind login.
5. License usable for repo/dashboard with attribution.

## 2. Priority A — 정비구역 GIS (redevelopment zone polygons)

The strongest match for "spatial development pressure" at dong scale is
the official 정비구역 polygon overlay. A dong either intersects a
designated zone or it does not; type (재개발 / 재건축 / 도시환경정비)
and designation status (designated / project-implementation /
management-disposal / completed) give graduated intensity.

| Source | URL | Format / grain | Coverage | Verdict |
|---|---|---|---|---|
| **서울특별시_의제처리구역 위치정보_20231004** | `data.go.kr/data/15082965/fileData.do` | SHP, UTF-8; polygon per zone; zone types include 정비구역, 재정비촉진지구, 도시개발구역, 의제처리구역 | Seoul, snapshot dated 2023-10-04 | **Primary candidate.** Native polygons over Seoul. Single snapshot — no time series — but a static "is this dong inside any designated zone?" overlay is exactly the spatial signal Block 4c is missing. Coverage is a current-state snapshot, not 2017–2024 time series; consumers must therefore treat it as a *cross-sectional designation map*, not a panel feature. |
| 서울 의제처리구역 위치정보 (Seoul Open Data mirror) | `data.seoul.go.kr/dataList/OA-20957/F/1/datasetView.do` | Same SHP via Seoul Open Data Plaza | Same | Mirror of above; use whichever has less access friction. data.go.kr historically wins on download mechanics; data.seoul.go.kr may require login. |
| **서울특별시_재개발 재건축 정비사업 현황_20210505** | `data.go.kr/data/15051920/fileData.do` | CSV / Excel; project-level records (project ID, type, gu, current status, dates) | Seoul, snapshot dated 2021-05-05 | **Secondary candidate.** Project-level attributes (status, key dates) that complement the polygon overlay — joinable by project name / 사업장 name to the polygon dataset. Vintage is stale (2021); a fresher snapshot would be needed for any current-state claim. |
| 정비사업 정보몽땅 (cleanup.seoul.go.kr) | `cleanup.seoul.go.kr/cleanup/bsnssttus/lscrMainIndx.do` | Web portal; gu-filterable search | Live | Authoritative for project status but **not a reproducible source** — interactive UI, no API or stable download. Useful as a cross-reference / validation only. |
| 서울도시공간포털 (도시계획조회 > 정비사업구역계) | `urban.seoul.go.kr/view/html/PMNU4030600001` | GIS viewer; layer may expose WFS/WMS | Live | Authoritative current-state view of designated zones. WFS/WMS endpoint not confirmed from web search alone; user-side portal browse needed to confirm. |
| 재재맵 (jjmap.co.kr) | `jjmap.co.kr` | Third-party private viewer | Live | Third-party; license unsuitable for repo use. Reference only. |

**Recommendation for Priority A**: pursue
`data.go.kr/15082965` (the SHP) as the cross-sectional designation
overlay, augmented by `data.go.kr/15051920` (the project-level CSV)
for attributes / dates. Treat the combination as a "designation
snapshot" feature, not a time series. The existing pilot manifest is
already `EPSG:4326`; the 의제처리구역 SHP needs the same projection
treatment as the D001 EMD pull (likely `EPSG:5179` source).

## 3. Priority B — 건축 인허가 (building permits) and 준공 (completions)

If a time-series signal of construction activity per gu/dong is wanted,
the cleanest gu × month / dong × year cuts live in MOLIT's 건축HUB
OpenAPI and in Seoul's per-gu aggregate tables.

| Source | URL | Format / grain | Coverage | Verdict |
|---|---|---|---|---|
| **국토교통부_건축HUB_건축인허가정보 서비스** | `data.go.kr/data/15136267/openapi.do` | OpenAPI; per-building permit records with permit date, building type, GFA, location | National; coverage extends to 2017 and earlier | **Primary candidate for time-series permits.** Per-record, joinable to gu/dong via the existing LAWD_CD plumbing. Likely needs approval-flow on data.go.kr same as the rent endpoint — not on the user's existing granted set, so an approval request would precede integration. |
| **국토교통부_건축HUB_건축물대장정보 서비스** | `data.go.kr/data/15134735/openapi.do` | OpenAPI; per-building register (총괄표제부 / 표제부 / 층별개요 / 부속지번 / 전유공통면적 / 오수처리시설 / 주택가격 / 지역지구구역) | National; comprehensive | **Primary candidate for building age / 노후도.** Per-building 사용승인일 (use-approval date) → age. Bulk-volume API; per-dong batching pattern would mirror `molit_client.py`. |
| 서울시 건축허가 통계 | `data.seoul.go.kr/dataList/235/S/2/datasetView.do` | Annual; gu × use-type counts and GFA | Seoul, annual updates | **Aggregate alternate.** No spatial geometry beyond gu, but trivially joinable. Useful as a low-friction permit-volume proxy without going through OpenAPI approval. |
| 서울시 건축물 현황 통계 | `data.seoul.go.kr/dataList/10804/S/2/datasetView.do` | Annual; gu × use-type / structure / floors building counts | Seoul, annual | **Aggregate alternate** for completed-stock counts. Same caveats. |
| 서울시 건축물 연면적 통계 | `data.seoul.go.kr/dataList/145/S/2/datasetView.do` | Annual; gu × use-type total GFA | Seoul, annual | Aggregate GFA companion to 10804. |
| 건축허가·착공·준공 현황 (index.go.kr) | `index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1224` | National index; no API exposed | National time series | Reference-only index page. Useful for national-level sanity checks. |
| **세움터 건축데이터 민간개방** | `open.eais.go.kr` | Bulk file downloads of 건축물대장; per-시군구 부속지번 search | National | **Alternate bulk path.** If OpenAPI approval is blocked, the 세움터 bulk-download path provides the same data as a per-region ZIP. Higher friction but no approval needed. |
| 건축물 생애이력정보 API | `blcm.go.kr/api/ser/selectApiRefer.do?referGb=basic` | OpenAPI; per-building lifecycle (permit → 착공 → 사용승인 → demolition) | National | **Strong candidate** for explicit demolition / lifecycle events. Less commonly used than 건축HUB; worth confirming approval requirements. |

**Recommendation for Priority B**: if approval-flow appetite is low,
start with the **Seoul aggregate tables** (`235`, `10804`, `145`) for
gu × year permit / completion / stock counts — they are no-approval,
file-download, exactly the four-block grain (gu × year) that Block 4
already uses for `statnuri_unsold_*`. The 건축HUB OpenAPI is the
higher-resolution upgrade if Seoul aggregates prove insufficient.

## 4. Priority C — building age / 노후도

| Source | URL | Format / grain | Coverage | Verdict |
|---|---|---|---|---|
| **서울시 건축연도별 주택현황 통계** | `data.seoul.go.kr/dataList/231/S/2/datasetView.do` | Annual; gu × build-year-bucket housing counts | Seoul, annual | **Cheapest "median building age per gu" proxy.** No spatial geometry per building but trivially joinable. Good first-cut age signal. |
| 국토교통부 건축HUB 건축물대장정보 서비스 | (see §3) | per-building 사용승인일 | National | Authoritative per-dong age, but pricier (OpenAPI approval + volume). |
| 국토교통부_GIS건물통합정보_20240709 | `data.go.kr/data/15083092/fileData.do` | National building-footprint GIS dataset with attributes (build year, GFA, structure) | National, snapshot 2024-07-09 | **Strongest spatial age signal.** Building polygons + 사용승인일. File-download, likely large. Worth pursuing if a true building-level age choropleth is wanted; otherwise the Seoul gu-aggregate table covers the gu-level proxy. |

## 5. Out of scope / rejected

- **재재맵 / private viewers** — third-party, no usable license.
- **NSDI 4077 OpenAPI list** (`nsdi.go.kr/lxportal/?menuno=4077`) — meta-index of national spatial OpenAPIs; surfaced multiple times but no single canonical candidate emerged that wasn't already covered by the entries above.

## 6. Verdict

Block 4c can be unparked with two-track integration:

- **Track 1 — designation overlay**: download
  `data.go.kr/15082965` (서울 의제처리구역 SHP) once, reproject to
  EPSG:4326, store as a versioned snapshot under `data/raw/` (gitignored).
  A small loader produces per-dong boolean / zone-type-string columns
  that the dashboard contract can carry as the `development_pressure_spatial_variation` upgrade from `none` to `dong`.
- **Track 2 — gu × year time series**: pull the three Seoul aggregate
  tables (`235`, `10804`, `145`) for permit volume, building stock, and
  GFA per gu × year. Aggregate-level, no OpenAPI approval, joinable on
  the existing `lawd_cd × year` Block 4 key.

These are complementary: Track 1 gives a spatial cross-section (where
zones are designated *now*); Track 2 gives a temporal signal (how much
permit / completion activity each gu has *over time*). Together they
fill the `development_pressure_status: missing_local_artifact` field
into something the dashboard map can render as a meaningful Block 4
choropleth.

**Open user-side actions before any code:**

1. Confirm the **`data.go.kr 15082965` SHP downloads without login or
   approval** and that the KOGL-style license permits repo/dashboard
   use with attribution (the same check pattern as the polygon-source
   decision in `docs/full_seoul_expansion_scope.md` §4).
2. Confirm the three Seoul aggregate tables (`data.seoul.go.kr` IDs
   `235`, `10804`, `145`) are file-downloadable without login and
   under a usable license.
3. Decide whether to also pursue the **건축HUB OpenAPI** (`15136267`,
   `15134735`) on the data.go.kr approval path, or whether the
   designation overlay + Seoul aggregate tables are sufficient for
   the MVP Block 4c claim.

Until items (1) and (2) are confirmed, no client code is written.
Until item (3) is decided, the choice between aggregate-only and
per-building-resolution remains open.

## Sources

- [서울특별시_의제처리구역 위치정보_20231004 (data.go.kr 15082965)](https://www.data.go.kr/data/15082965/fileData.do) — primary 정비구역 SHP
- [서울시 의제처리구역 위치정보 (data.seoul.go.kr OA-20957)](https://data.seoul.go.kr/dataList/OA-20957/F/1/datasetView.do) — Seoul Open Data mirror
- [서울특별시_재개발 재건축 정비사업 현황_20210505 (data.go.kr 15051920)](https://www.data.go.kr/data/15051920/fileData.do) — project-level attributes
- [서울시 재개발 재건축 정비사업 현황 (data.seoul.go.kr OA-2253)](https://data.seoul.go.kr/dataList/OA-2253/S/1/datasetView.do) — Seoul mirror
- [정비사업 정보몽땅](https://cleanup.seoul.go.kr/) — Seoul portal for 정비사업 lookup (reference only)
- [서울도시공간포털 도시관리계획 > 정비사업구역계](https://urban.seoul.go.kr/view/html/PMNU4030600001) — Seoul GIS viewer
- [국토교통부_건축HUB_건축인허가정보 서비스 (data.go.kr 15136267)](https://www.data.go.kr/data/15136267/openapi.do) — building permit OpenAPI
- [국토교통부_건축HUB_건축물대장정보 서비스 (data.go.kr 15134735)](https://www.data.go.kr/data/15134735/openapi.do) — building register OpenAPI
- [서울시 건축허가 통계 (data.seoul.go.kr 235)](https://data.seoul.go.kr/dataList/235/S/2/datasetView.do) — aggregate permit counts
- [서울시 건축물 현황 통계 (data.seoul.go.kr 10804)](https://data.seoul.go.kr/dataList/10804/S/2/datasetView.do) — aggregate building stock
- [서울시 건축물 연면적 통계 (data.seoul.go.kr 145)](https://data.seoul.go.kr/dataList/145/S/2/datasetView.do) — aggregate GFA
- [서울시 건축연도별 주택현황 통계 (data.seoul.go.kr 231)](https://data.seoul.go.kr/dataList/231/S/2/datasetView.do) — gu × build year housing counts
- [건축데이터 민간개방 시스템 세움터](https://open.eais.go.kr/) — bulk 건축물대장 downloads
- [건축물 생애이력정보 API](https://blcm.go.kr/api/ser/selectApiRefer.do?referGb=basic) — building lifecycle / demolition events
- [국토교통부_GIS건물통합정보_20240709 (data.go.kr 15083092)](https://www.data.go.kr/data/15083092/fileData.do) — national building-footprint GIS
- [건축허가·착공·준공 현황 (index.go.kr)](https://www.index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1224) — national time-series index
