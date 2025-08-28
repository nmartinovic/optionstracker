let portfolioChart = null;
let portfolioRows = []; // from portfolio.csv
let historyRows = [];   // from history.csv
let currentFilter = null; // underlying or null

async function fetchText(path) {
  const res = await fetch(path, { cache: "no-cache" });
  if (!res.ok) return "";
  return res.text();
}

function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length <= 1) return { headers: [], rows: [] };
  const headers = lines[0].split(",");
  const rows = lines.slice(1).map(line => {
    const cols = line.split(",");
    const obj = {};
    headers.forEach((h, i) => obj[h] = cols[i]);
    return obj;
  });
  return { headers, rows };
}

function fmtUsd(n) {
  const num = typeof n === "string" ? parseFloat(n) : n;
  if (Number.isNaN(num)) return "-";
  return num.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

function byDateAsc(a, b) { return a.date.localeCompare(b.date); }

async function loadLastRun() {
  const t = await fetchText("./data/last_run.txt");
  const el = document.getElementById("last-run");
  if (t) el.textContent = "Last update: " + new Date(t.trim()).toLocaleString();
  else el.textContent = "Last update: (not yet recorded)";
}

async function loadStats() {
  const text = await fetchText("./data/portfolio.csv");
  const { rows } = parseCSV(text);
  portfolioRows = rows;
  if (!rows.length) return;
  rows.sort(byDateAsc);
  const latest = rows[rows.length - 1];

  const totalValue = parseFloat(latest.total_value);
  const totalCost  = parseFloat(latest.total_cost_basis);
  const totalPnl   = parseFloat(latest.total_pnl);
  const totalPct   = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;

  const vEl = document.getElementById("stat-total-value");
  const pEl = document.getElementById("stat-total-pnl");
  const pctEl = document.getElementById("stat-total-pct");

  if (vEl) vEl.textContent = fmtUsd(totalValue);
  if (pEl) {
    pEl.textContent = fmtUsd(totalPnl);
    pEl.classList.toggle("good", totalPnl >= 0);
    pEl.classList.toggle("bad", totalPnl < 0);
  }
  if (pctEl) {
    const txt = isFinite(totalPct) ? totalPct.toFixed(2) + "%" : "-";
    pctEl.textContent = txt;
    pctEl.classList.toggle("good", totalPct >= 0);
    pctEl.classList.toggle("bad", totalPct < 0);
  }
}

async function loadHistory() {
  const text = await fetchText("./data/history.csv");
  const { rows } = parseCSV(text);
  historyRows = rows;
}

function computeSeriesAll() {
  if (!portfolioRows.length) return { labels: [], value: [], pnl: [], pct: [] };
  const rows = [...portfolioRows].sort(byDateAsc);
  const labels = rows.map(r => r.date);
  const value  = rows.map(r => parseFloat(r.total_value));
  const pnl    = rows.map(r => parseFloat(r.total_pnl));
  const pct    = rows.map(r => {
    const c = parseFloat(r.total_cost_basis);
    const p = parseFloat(r.total_pnl);
    return c > 0 ? (p / c) * 100 : 0;
  });
  return { labels, value, pnl, pct };
}

function computeSeriesUnderlying(underlying) {
  if (!historyRows.length) return { labels: [], value: [], pnl: [], pct: [] };
  // aggregate per date for a given underlying
  const map = new Map(); // date -> {value, cost}
  for (const r of historyRows) {
    if (r.underlying !== underlying) continue;
    const d = r.date;
    const val = parseFloat(r.value);
    const cpc = parseFloat(r.cost_per_contract);
    const cons = parseInt(r.contracts, 10);
    const cost = (isFinite(cpc) && isFinite(cons)) ? cpc * cons * 100 : 0;
    const entry = map.get(d) || { value: 0, cost: 0 };
    entry.value += isFinite(val) ? val : 0;
    entry.cost  += isFinite(cost) ? cost : 0;
    map.set(d, entry);
  }
  const labels = Array.from(map.keys()).sort();
  const value  = labels.map(d => map.get(d).value);
  const cost   = labels.map(d => map.get(d).cost);
  const pnl    = labels.map((_, i) => value[i] - cost[i]);
  const pct    = labels.map((_, i) => cost[i] > 0 ? (pnl[i] / cost[i]) * 100 : 0);
  return { labels, value, pnl, pct };
}

function percentAxisBounds(pcts) {
  const finite = pcts.filter(v => Number.isFinite(v));
  if (!finite.length) return { suggestedMin: -10, suggestedMax: 10 }; // safe default
  const minPct = Math.min(...finite);
  const maxPct = Math.max(...finite);
  // ensure 0% is included in the visible range without forcing it to be the min
  return {
    suggestedMin: Math.min(0, minPct),
    suggestedMax: Math.max(0, maxPct)
  };
}

function renderOrUpdateChart(series, labelPrefix = "Total") {
  const ctx = document.getElementById("portfolioChart").getContext("2d");
  const yPctBounds = percentAxisBounds(series.pct);

  const data = {
    labels: series.labels,
    datasets: [
      { label: `${labelPrefix} Value`, data: series.value, yAxisID: "y" },
      { label: `${labelPrefix} P&L`,   data: series.pnl,   yAxisID: "y" },
      { label: `${labelPrefix} Return (%)`, data: series.pct, yAxisID: "yPct", borderDash: [6, 4], pointRadius: 2 }
    ]
  };

  const opts = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    elements: { line: { tension: 0.25 } },
    scales: {
      y: {
        title: { display: true, text: "USD" },
        beginAtZero: false
      },
      yPct: {
        position: "right",
        title: { display: true, text: "% Return" },
        grid: { drawOnChartArea: false },
        // include 0% in the scale but allow negative values
        suggestedMin: yPctBounds.suggestedMin,
        suggestedMax: yPctBounds.suggestedMax,
        ticks: { callback: v => `${v}%` }
      }
    },
    plugins: {
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const ds = ctx.dataset.label || "";
            const v = ctx.parsed.y;
            if (ctx.dataset.yAxisID === "yPct") return `${ds}: ${v.toFixed(2)}%`;
            return `${ds}: ${fmtUsd(v)}`;
          }
        }
      }
    }
  };

  if (portfolioChart) {
    portfolioChart.data = data;
    portfolioChart.options = opts;
    portfolioChart.update();
  } else {
    portfolioChart = new Chart(ctx, { type: "line", data, options: opts });
  }
}

function applyFilter(underlying) {
  currentFilter = underlying;
  const series = computeSeriesUnderlying(underlying);
  renderOrUpdateChart(series, underlying);
  const badge = document.getElementById("chart-filter-label");
  if (badge) badge.textContent = `${underlying} — double-click to reset`;
}

function resetFilter() {
  currentFilter = null;
  const series = computeSeriesAll();
  renderOrUpdateChart(series, "Total");
  const badge = document.getElementById("chart-filter-label");
  if (badge) badge.textContent = "";
}

async function loadPortfolioChart() {
  // default view: total portfolio
  const series = computeSeriesAll();
  renderOrUpdateChart(series, "Total");
}

async function loadPositionsTable() {
  const text = await fetchText("./data/history.csv");
  const { rows } = parseCSV(text);
  historyRows = rows;
  if (!rows.length) return;

  rows.sort(byDateAsc);
  const latestDate = rows[rows.length - 1].date;
  const todays = rows.filter(r => r.date === latestDate);

  const tbody = document.querySelector("#positions-table tbody");
  tbody.innerHTML = "";

  todays.forEach(r => {
    const tr = document.createElement("tr");
    const pnl = parseFloat(r.pnl);
    const pnlPct = parseFloat(r.pnl_pct);
    tr.innerHTML = `
      <td>${r.symbolKey}</td>
      <td>${r.contracts}</td>
      <td>${fmtUsd(r.cost_per_contract)}</td>
      <td>${fmtUsd(r.price)}</td>
      <td>${fmtUsd(r.value)}</td>
      <td class="${pnl >= 0 ? "good" : "bad"}">${fmtUsd(pnl)}</td>
      <td class="${pnlPct >= 0 ? "good" : "bad"}">${(isFinite(pnlPct) ? pnlPct.toFixed(2) : "-")}%</td>
    `;
    // click-to-filter by underlying (first token of symbolKey)
    tr.addEventListener("click", () => {
      const firstCell = tr.querySelector("td");
      const key = (firstCell?.textContent || "").trim();
      const underlying = key.split(" ")[0];
      if (underlying) applyFilter(underlying);
    });
    tbody.appendChild(tr);
  });

  // double-click to reset filter (chart and table)
  const table = document.getElementById("positions-table");
  table.addEventListener("dblclick", resetFilter);

  const canvas = document.getElementById("portfolioChart");
  canvas.addEventListener("dblclick", resetFilter);
}

async function init() {
  await Promise.all([
    loadLastRun(),
    loadStats(),
    loadHistory()
  ]);
  await loadPortfolioChart();
  await loadPositionsTable();
}
init();
