"""
dashboard_app.py
================

Localhost dashboard for the Gong2026 pilot contract.

Serves a small, dependency-light dashboard over
`data/dashboard_pilot_contract.parquet`. The dashboard is descriptive only:
it exposes physical-change metrics, 2022 artifact flags, and block status
badges. It does not compute a forecast, probability, displacement-risk score,
or composite score.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
DEFAULT_CONTRACT = HERE / "data" / "dashboard_pilot_contract.parquet"

DISPLAY_COLS = [
    "emd_cd",
    "dong_name_kr",
    "lawd_cd",
    "gu_name",
    "year",
    "centroid_lat",
    "centroid_lon",
    "physical_embedding_norm",
    "physical_yoy_year_pair",
    "physical_yoy_angular",
    "physical_yoy_cosine_dist",
    "physical_yoy_euclid",
    "physical_yoy_angular_gu_z",
    "physical_yoy_angular_gu_rank_desc",
    "physical_yoy_angular_gu_percentile_desc",
    "physical_2022_artifact_flag",
    "physical_source",
    "physical_grain",
    "physical_status",
    "physical_artifact_policy",
    "tenure_status",
    "vulnerability_status",
    "housing_stress_status",
    "development_pressure_status",
    "development_pressure_spatial_variation",
    "dashboard_claim_scope",
    "composite_score_status",
    "statnuri_unsold_mean_units",
    "statnuri_unsold_max_units",
    "statnuri_unsold_dec_units",
    "national_redevelopment_intensity_zone_count",
    "national_redevelopment_intensity_area_m2",
    "national_redevelopment_intensity_demolition_targets",
    "national_redevelopment_intensity_units_total",
]


def load_payload(contract_path: Path) -> dict:
    if not contract_path.exists():
        raise FileNotFoundError(
            f"missing dashboard contract: {contract_path}. "
            "Run `python dashboard_pilot_contract.py` first.")
    df = pd.read_parquet(contract_path)
    cols = [c for c in DISPLAY_COLS if c in df.columns]
    view = df[cols].copy()
    for col in view.select_dtypes(include=["float", "float64", "float32"]).columns:
        view[col] = view[col].round(6)
    records = view.replace({np.nan: None}).to_dict("records")
    summary = {
        "rows": int(len(df)),
        "dongs": int(df["emd_cd"].nunique()),
        "years": sorted(df["year"].astype(int).unique().tolist()),
        "gus": (df.groupby(["lawd_cd", "gu_name"])["emd_cd"]
                .nunique()
                .reset_index(name="dongs")
                .to_dict("records")),
        "statuses": {
            c: sorted(df[c].dropna().astype(str).unique().tolist())
            for c in [
                "physical_status",
                "tenure_status",
                "vulnerability_status",
                "housing_stress_status",
                "development_pressure_status",
                "composite_score_status",
            ]
            if c in df.columns
        },
        "artifact_2022_flags": int(df["physical_2022_artifact_flag"].sum()),
        "contract_path": str(contract_path),
    }
    return {"summary": summary, "rows": records}


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Gong2026 Pilot Dashboard</title>
  <style>
    :root {
      --ink: #172126;
      --muted: #66767c;
      --line: #d9e0df;
      --paper: #f7f8f5;
      --panel: #ffffff;
      --green: #237a57;
      --teal: #1d6f86;
      --amber: #b77714;
      --red: #b4463a;
      --blue: #3d64a3;
      --shadow: 0 12px 30px rgba(31, 43, 49, .10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--paper);
      color: var(--ink);
      letter-spacing: 0;
    }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 290px 1fr;
    }
    aside {
      border-right: 1px solid var(--line);
      background: #fbfcfa;
      padding: 18px 16px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }
    main {
      padding: 18px;
      display: grid;
      gap: 14px;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 22px; line-height: 1.1; }
    h2 { font-size: 14px; text-transform: uppercase; color: var(--muted); }
    h3 { font-size: 16px; }
    .subtle { color: var(--muted); font-size: 12px; line-height: 1.45; }
    .stack { display: grid; gap: 10px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto auto;
      gap: 12px;
      align-items: end;
    }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
    select, input[type="range"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 9px 10px;
      color: var(--ink);
      font-size: 14px;
    }
    .segmented {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      border: 1px solid var(--line);
      border-radius: 7px;
      overflow: hidden;
      background: #fff;
    }
    button {
      border: 0;
      background: transparent;
      padding: 9px 12px;
      cursor: pointer;
      color: var(--ink);
      font-size: 14px;
    }
    button.active { background: #dfeee6; color: #124c34; font-weight: 700; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .grid-main { display: grid; grid-template-columns: minmax(460px, 1.4fr) minmax(360px, .9fr); gap: 14px; }
    .kpi { min-height: 92px; display: grid; align-content: space-between; }
    .kpi strong { display: block; font-size: 24px; margin-top: 8px; }
    .badge-row { display: flex; flex-wrap: wrap; gap: 7px; }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 5px 8px;
      font-size: 12px;
      background: #fff;
    }
    .badge.live { border-color: #b8dbc8; background: #eef8f1; color: var(--green); }
    .badge.warn { border-color: #ead39b; background: #fff8e6; color: var(--amber); }
    .badge.off { border-color: #d7dde1; background: #f1f4f4; color: var(--muted); }
    .badge.artifact { border-color: #e8b7ad; background: #fff0ee; color: var(--red); }
    svg { width: 100%; display: block; }
    #mapSvg { height: 500px; border: 1px solid var(--line); border-radius: 8px; background: #fdfefe; }
    .dot { stroke: #fff; stroke-width: 1.5; cursor: pointer; }
    .dot:hover { stroke: #172126; stroke-width: 2; }
    .dot.selected { stroke: #172126; stroke-width: 3; }
    .chart { min-height: 270px; }
    .bar-row {
      display: grid;
      grid-template-columns: 72px 1fr 64px;
      gap: 8px;
      align-items: center;
      font-size: 12px;
      margin: 8px 0;
    }
    .bar-track { height: 10px; background: #e9eeee; border-radius: 999px; overflow: hidden; }
    .bar { height: 100%; background: var(--teal); }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px 12px;
      font-size: 13px;
    }
    .detail-grid dt { color: var(--muted); }
    .detail-grid dd { margin: 2px 0 0; font-weight: 700; overflow-wrap: anywhere; }
    .table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .table th, .table td { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; }
    .table th { color: var(--muted); font-weight: 600; }
    @media (max-width: 980px) {
      .app { grid-template-columns: 1fr; }
      aside { position: static; height: auto; }
      .toolbar, .grid-main, .grid-3 { grid-template-columns: 1fr; }
      #mapSvg { height: 380px; }
    }
  </style>
</head>
<body>
<div class="app">
  <aside class="stack">
    <div class="stack">
      <h1>Gong2026 Pilot</h1>
      <p class="subtle">마포구 + 강남구 legal-dong panel, 2017–2024</p>
    </div>
    <section class="panel stack">
      <h2>Evidence Blocks</h2>
      <div id="statusBadges" class="badge-row"></div>
    </section>
    <section class="panel stack">
      <h2>Selected Dong</h2>
      <h3 id="selectedTitle">—</h3>
      <dl id="detailGrid" class="detail-grid"></dl>
    </section>
    <section class="panel stack">
      <h2>Contract</h2>
      <p id="contractPath" class="subtle"></p>
      <p class="subtle">Descriptive physical-change layer. No forecast, probability, or composite score.</p>
    </section>
  </aside>
  <main>
    <section class="panel toolbar">
      <label>Year
        <select id="yearSelect"></select>
      </label>
      <label>Metric
        <select id="metricSelect">
          <option value="physical_yoy_angular">YoY angular change</option>
          <option value="physical_yoy_euclid">YoY Euclidean change</option>
          <option value="physical_embedding_norm">Embedding norm</option>
          <option value="statnuri_unsold_mean_units">Unsold mean units</option>
          <option value="national_redevelopment_intensity_zone_count">Redevelopment zones</option>
        </select>
      </label>
      <label>Gu
        <div class="segmented">
          <button data-gu="all" class="active">All</button>
          <button data-gu="11440">마포구</button>
          <button data-gu="11680">강남구</button>
        </div>
      </label>
    </section>

    <section class="grid-3">
      <div class="panel kpi"><span class="subtle">Rows in view</span><strong id="kpiRows">—</strong></div>
      <div class="panel kpi"><span class="subtle">Median metric</span><strong id="kpiMedian">—</strong></div>
      <div class="panel kpi"><span class="subtle">2022 artifact rows</span><strong id="kpiArtifact">—</strong></div>
    </section>

    <section class="grid-main">
      <div class="panel stack">
        <div>
          <h2>Legal-Dong Map</h2>
          <p class="subtle" id="mapCaption">Centroids colored by selected metric</p>
        </div>
        <svg id="mapSvg" role="img"></svg>
      </div>
      <div class="stack">
        <div class="panel stack">
          <h2>Top Dongs</h2>
          <div id="barList"></div>
        </div>
        <div class="panel stack">
          <h2>Selected Timeline</h2>
          <svg id="lineSvg" class="chart"></svg>
        </div>
      </div>
    </section>

    <section class="panel stack">
      <h2>Current Year Table</h2>
      <table class="table">
        <thead>
          <tr>
            <th>Dong</th><th>Gu</th><th>YoY angular</th><th>Gu rank</th><th>Unsold mean</th><th>Artifact</th>
          </tr>
        </thead>
        <tbody id="rowTable"></tbody>
      </table>
    </section>
  </main>
</div>

<script>
let payload, rows, summary;
let state = { year: 2024, gu: "all", metric: "physical_yoy_angular", selected: null };

const fmt = (v, d=3) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(d);
const clean = v => (v === null || v === undefined || Number.isNaN(v)) ? null : Number(v);

function colorScale(v, min, max, artifact) {
  if (artifact) return "#b4463a";
  if (v === null) return "#aab4b8";
  const t = max > min ? Math.max(0, Math.min(1, (v - min) / (max - min))) : .5;
  const stops = [[35,122,87], [29,111,134], [61,100,163]];
  const a = t < .5 ? stops[0] : stops[1];
  const b = t < .5 ? stops[1] : stops[2];
  const u = t < .5 ? t * 2 : (t - .5) * 2;
  return `rgb(${Math.round(a[0]+(b[0]-a[0])*u)},${Math.round(a[1]+(b[1]-a[1])*u)},${Math.round(a[2]+(b[2]-a[2])*u)})`;
}

function rowsForYear() {
  return rows.filter(r => r.year === state.year && (state.gu === "all" || r.lawd_cd === state.gu));
}

function initControls() {
  const ys = summary.years;
  yearSelect.innerHTML = ys.map(y => `<option value="${y}">${y}</option>`).join("");
  yearSelect.value = String(state.year);
  yearSelect.onchange = () => { state.year = Number(yearSelect.value); render(); };
  metricSelect.onchange = () => { state.metric = metricSelect.value; render(); };
  document.querySelectorAll("[data-gu]").forEach(btn => {
    btn.onclick = () => {
      state.gu = btn.dataset.gu;
      document.querySelectorAll("[data-gu]").forEach(b => b.classList.toggle("active", b === btn));
      render();
    };
  });
}

function renderStatus() {
  const s = summary.statuses;
  const badges = [
    ["Physical", s.physical_status?.[0], "live"],
    ["Housing stress", s.housing_stress_status?.[0], s.housing_stress_status?.[0] === "live" ? "live" : "warn"],
    ["Development", s.development_pressure_status?.[0], s.development_pressure_status?.[0] === "live" ? "live" : "warn"],
    ["Tenure", s.tenure_status?.[0], "warn"],
    ["Vulnerability", s.vulnerability_status?.[0], "off"],
    ["Composite", s.composite_score_status?.[0], "off"],
  ];
  statusBadges.innerHTML = badges.map(([name, status, cls]) =>
    `<span class="badge ${cls}">${name}: ${status || "—"}</span>`).join("");
  contractPath.textContent = summary.contract_path;
}

function renderKpis(current) {
  const vals = current.map(r => clean(r[state.metric])).filter(v => v !== null).sort((a,b)=>a-b);
  const med = vals.length ? vals[Math.floor(vals.length/2)] : null;
  kpiRows.textContent = current.length;
  kpiMedian.textContent = fmt(med);
  kpiArtifact.textContent = current.filter(r => r.physical_2022_artifact_flag).length;
}

function renderMap(current) {
  const svg = mapSvg;
  svg.innerHTML = "";
  const w = svg.clientWidth || 700, h = svg.clientHeight || 500, pad = 34;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const lons = current.map(r => r.centroid_lon), lats = current.map(r => r.centroid_lat);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const vals = current.map(r => clean(r[state.metric])).filter(v => v !== null);
  const min = vals.length ? Math.min(...vals) : 0, max = vals.length ? Math.max(...vals) : 1;
  const scaleX = lon => pad + ((lon - minLon) / (maxLon - minLon || 1)) * (w - pad*2);
  const scaleY = lat => h - pad - ((lat - minLat) / (maxLat - minLat || 1)) * (h - pad*2);

  const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bg.setAttribute("x", 0); bg.setAttribute("y", 0); bg.setAttribute("width", w); bg.setAttribute("height", h);
  bg.setAttribute("fill", "#fdfefe"); svg.appendChild(bg);

  current.forEach(r => {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", scaleX(r.centroid_lon));
    c.setAttribute("cy", scaleY(r.centroid_lat));
    c.setAttribute("r", r.physical_2022_artifact_flag ? 8 : 7);
    c.setAttribute("fill", colorScale(clean(r[state.metric]), min, max, r.physical_2022_artifact_flag));
    c.setAttribute("class", `dot ${state.selected === r.emd_cd ? "selected" : ""}`);
    c.onclick = () => { state.selected = r.emd_cd; render(); };
    c.appendChild(document.createElementNS("http://www.w3.org/2000/svg", "title")).textContent =
      `${r.dong_name_kr} ${fmt(r[state.metric])}`;
    svg.appendChild(c);
  });
  mapCaption.textContent = `${state.year} · ${metricSelect.options[metricSelect.selectedIndex].text}`;
}

function renderBars(current) {
  const sortable = current.filter(r => clean(r[state.metric]) !== null)
    .sort((a,b) => clean(b[state.metric]) - clean(a[state.metric])).slice(0, 8);
  const max = sortable.length ? Math.max(...sortable.map(r => clean(r[state.metric]))) : 1;
  barList.innerHTML = sortable.map(r => {
    const pct = Math.max(4, (clean(r[state.metric]) / (max || 1)) * 100);
    return `<div class="bar-row" role="button" onclick="state.selected='${r.emd_cd}';render();">
      <span>${r.dong_name_kr}</span><span class="bar-track"><span class="bar" style="width:${pct}%"></span></span><strong>${fmt(r[state.metric])}</strong>
    </div>`;
  }).join("");
}

function renderLine() {
  const selected = state.selected || rowsForYear()[0]?.emd_cd;
  state.selected = selected;
  const series = rows.filter(r => r.emd_cd === selected).sort((a,b)=>a.year-b.year);
  const svg = lineSvg;
  svg.innerHTML = "";
  const w = svg.clientWidth || 360, h = 270, pad = 34;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const vals = series.map(r => clean(r.physical_yoy_angular));
  const valid = vals.filter(v => v !== null);
  const max = Math.max(...valid, .001);
  const x = i => pad + (i / (series.length - 1 || 1)) * (w - pad*2);
  const y = v => h - pad - ((v || 0) / max) * (h - pad*2);
  const path = series.map((r,i) => `${i === 0 ? "M" : "L"}${x(i)},${y(clean(r.physical_yoy_angular))}`).join(" ");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path); p.setAttribute("fill","none"); p.setAttribute("stroke","#1d6f86"); p.setAttribute("stroke-width","3");
  svg.appendChild(p);
  series.forEach((r,i) => {
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(clean(r.physical_yoy_angular)));
    dot.setAttribute("r", r.physical_2022_artifact_flag ? 6 : 4);
    dot.setAttribute("fill", r.physical_2022_artifact_flag ? "#b4463a" : "#1d6f86");
    svg.appendChild(dot);
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", x(i)); t.setAttribute("y", h - 9); t.setAttribute("text-anchor", "middle"); t.setAttribute("font-size", "10"); t.setAttribute("fill", "#66767c");
    t.textContent = r.year; svg.appendChild(t);
  });
}

function renderDetail() {
  const r = rows.find(x => x.emd_cd === state.selected && x.year === state.year) || rowsForYear()[0];
  if (!r) return;
  state.selected = r.emd_cd;
  selectedTitle.textContent = `${r.dong_name_kr} · ${r.gu_name}`;
  const items = [
    ["EMD", r.emd_cd],
    ["Year", r.year],
    ["YoY pair", r.physical_yoy_year_pair || "—"],
    ["YoY angular", fmt(r.physical_yoy_angular)],
    ["Gu rank", fmt(r.physical_yoy_angular_gu_rank_desc, 0)],
    ["Unsold mean", fmt(r.statnuri_unsold_mean_units, 0)],
    ["Redev zones", fmt(r.national_redevelopment_intensity_zone_count, 0)],
    ["Artifact", r.physical_2022_artifact_flag ? "2021→2022 flag" : "no"],
  ];
  detailGrid.innerHTML = items.map(([k,v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

function renderTable(current) {
  const sorted = [...current].sort((a,b) => (clean(b.physical_yoy_angular)||-1) - (clean(a.physical_yoy_angular)||-1));
  rowTable.innerHTML = sorted.map(r => `<tr>
    <td>${r.dong_name_kr}</td><td>${r.gu_name}</td><td>${fmt(r.physical_yoy_angular)}</td>
    <td>${fmt(r.physical_yoy_angular_gu_rank_desc, 0)}</td><td>${fmt(r.statnuri_unsold_mean_units, 0)}</td>
    <td>${r.physical_2022_artifact_flag ? '<span class="badge artifact">flag</span>' : ''}</td>
  </tr>`).join("");
}

function render() {
  const current = rowsForYear();
  renderStatus();
  renderKpis(current);
  renderMap(current);
  renderBars(current);
  renderLine();
  renderDetail();
  renderTable(current);
}

fetch("/api/contract")
  .then(r => r.json())
  .then(data => {
    payload = data; rows = data.rows; summary = data.summary;
    state.year = Math.max(...summary.years);
    initControls();
    render();
  })
  .catch(err => {
    document.body.innerHTML = `<main class="panel" style="margin:20px"><h1>Dashboard failed to load</h1><p>${err}</p></main>`;
  });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    payload: dict | None = None
    contract_path: Path = DEFAULT_CONTRACT

    def _send(self, body: bytes, content_type: str,
              status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/contract":
            try:
                Handler.payload = load_payload(Handler.contract_path)
                body = json.dumps(Handler.payload, ensure_ascii=False,
                                  allow_nan=False).encode("utf-8")
                self._send(body, "application/json; charset=utf-8")
            except Exception as exc:  # pragma: no cover - localhost diagnostics
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(body, "application/json; charset=utf-8", status=500)
            return
        self._send(b"not found", "text/plain; charset=utf-8", status=404)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[dashboard] {self.address_string()} - {fmt % args}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the Gong2026 pilot dashboard.")
    ap.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=8765, type=int)
    args = ap.parse_args()

    Handler.contract_path = Path(args.contract)
    load_payload(Handler.contract_path)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Gong2026 dashboard: {url}")
    print(f"Contract: {Handler.contract_path}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
