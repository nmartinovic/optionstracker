let portfolioChart = null;
let portfolioRows = []; // from portfolio.csv
let historyRows = [];   // from history.csv
let currentFilter = null; // underlying or null

/* ---------- utils ---------- */
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
function midnight(d)     { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function addDays(d, n)   { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function fmtShort(d)     { return d.toLocaleDateString(undefined, { month:"short", day:"numeric", year:"numeric" }); }

/* ---------- last run ---------- */
async function loadLastRun() {
  const t = await fetchText("./data/last_run.txt");
  const el = document.getElementById("last-run");
  if (t) el.textContent = "Last update: " + new Date(t.trim()).toLocaleString();
  else el.textContent = "Last update: (not yet recorded)";
}

/* ---------- header stats (overall vs filtered) ---------- */
function setHeaderStats(totalValue, totalPnl, totalPct) {
  const vEl = document.getElementById("stat-total-value");
  const pEl = document.getElementById("stat-total-pnl");
  const pctEl = document.getElementById("stat-total-pct");

  if (vEl) vEl.textContent = fmtUsd(totalValue ?? 0);
  if (pEl) {
    pEl.textContent = fmtUsd(totalPnl ?? 0);
    pEl.classList.toggle("good", (totalPnl ?? 0) >= 0);
    pEl.classList.toggle("bad", (totalPnl ?? 0) < 0);
  }
  if (pctEl) {
    const txt = isFinite(totalPct) ? totalPct.toFixed(2) + "%" : "-";
    pctEl.textContent = txt;
    pctEl.classList.toggle("good", (totalPct ?? 0) >= 0);
    pctEl.classList.toggle("bad", (totalPct ?? 0) < 0);
  }
}

function updateHeaderStatsOverall() {
  if (!portfolioRows.length) return;
  const rows = [...portfolioRows].sort(byDateAsc);
  const latest = rows[rows.length - 1];
  const totalValue = parseFloat(latest.total_value);
  const totalCost  = parseFloat(latest.total_cost_basis);
  const totalPnl   = parseFloat(latest.total_pnl);
  const totalPct   = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;
  setHeaderStats(totalValue, totalPnl, totalPct);
}

function updateHeaderStatsUnderlying(underlying) {
  if (!historyRows.length) return;
  const dates = Array.from(new Set(historyRows.map(r => r.date))).sort();
  if (!dates.length) return;
  const latestDate = dates[dates.length - 1];

  const todays = historyRows.filter(r => r.date === latestDate && r.underlying === underlying);
  let totalValue = 0, totalCost = 0;
  for (const r of todays) {
    const value = parseFloat(r.value);
    const cpc   = parseFloat(r.cost_per_contract);
    const cons  = parseInt(r.contracts, 10);
    const cost  = (isFinite(cpc) && isFinite(cons)) ? cpc * cons * 100 : 0;
    totalValue += isFinite(value) ? value : 0;
    totalCost  += isFinite(cost) ? cost : 0;
  }
  const totalPnl = totalValue - totalCost;
  const totalPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;
  setHeaderStats(totalValue, totalPnl, totalPct);
}

function updateHeaderStatsByFilter() {
  if (currentFilter) updateHeaderStatsUnderlying(currentFilter);
  else updateHeaderStatsOverall();
}

/* ---------- countdown (Mar 20, 2026) ---------- */
function initCountdown() {
  const target = new Date(2026, 2, 20); // Mar = month index 2
  const start  = new Date(2025, 2, 20); // one-year window
  const today  = midnight(new Date());

  // days left (ceil so it ticks down at midnight local time)
  const MS = 24 * 60 * 60 * 1000;
  const daysLeft = Math.max(0, Math.ceil((midnight(target) - today) / MS));
  const daysLeftEl = document.getElementById("days-left");
  const statusEl   = document.getElementById("countdown-status");
  if (daysLeftEl) daysLeftEl.textContent = daysLeft.toString();

  const totalDays   = Math.max(1, Math.round((midnight(target) - midnight(start)) / MS));
  const elapsedDays = Math.min(totalDays, Math.max(0, Math.round((today - midnight(start)) / MS)));
  const pct = Math.min(100, Math.max(0, (elapsedDays / totalDays) * 100));
  const fill = document.getElementById("progress-fill");
  if (fill) fill.style.width = pct + "%";

  // titles under progress bar
  const sLab = document.getElementById("progress-start");
  const eLab = document.getElementById("progress-end");
  if (sLab) sLab.textContent = fmtShort(start);
  if (eLab) eLab.textContent = fmtShort(target);

  // build the grid (one square per day)
  const grid = document.getElementById("days-grid");
  if (grid) {
    grid.innerHTML = "";
    const frag = document.createDocumentFragment();
    for (let i = 0; i <= totalDays; i++) {
      const d = addDays(start, i);
      const div = document.createElement("div");
      div.className = "day" + (d < today ? " checked" : (d.getTime() === today.getTime() ? " today" : ""));
      div.title = fmtShort(d);
      frag.appendChild(div);
    }
    grid.appendChild(frag);
  }

  if (statusEl) {
    statusEl.textContent = daysLeft === 0
      ? "🎉 Long-term gains reached!"
      : `Progress: ${elapsedDays}/${totalDays} days (${pct.toFixed(1)}%)`;
  }
}

/* ---------- loaders ---------- */
async function loadStats() {
  const text = await fetchText("./data/portfolio.csv");
  const { rows } = parseCSV(text);
  portfolioRows = rows;
  updateHeaderStatsOverall();
}

async function loadHistory() {
  const text = await fetchText("./data/history.csv");
  const { rows } = parseCSV(text);
  historyRows = rows;
}

/* ---------- chart series ---------- */
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

/* ---------- chart ---------- */
function percentAxisBounds(pcts) {
  const finite = pcts.filter(v => Number.isFinite(v));
  if (!finite.length) return { suggestedMin: -10, suggestedMax: 10 };
  const minPct = Math.min(...finite);
  const maxPct = Math.max(...finite);
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

/* ---------- filtering ---------- */
function applyFilter(underlying) {
  currentFilter = underlying;
  const series = computeSeriesUnderlying(underlying);
  renderOrUpdateChart(series, underlying);
  updateHeaderStatsByFilter();
  const badge = document.getElementById("chart-filter-label");
  if (badge) badge.textContent = `${underlying} — double-click to reset`;
}

function resetFilter() {
  currentFilter = null;
  const series = computeSeriesAll();
  renderOrUpdateChart(series, "Total");
  updateHeaderStatsByFilter();
  const badge = document.getElementById("chart-filter-label");
  if (badge) badge.textContent = "";
}

/* ---------- table ---------- */
async function loadPortfolioChart() {
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
    tr.addEventListener("click", () => {
      const firstCell = tr.querySelector("td");
      const key = (firstCell?.textContent || "").trim();
      const underlying = key.split(" ")[0];
      if (underlying) applyFilter(underlying);
    });
    tbody.appendChild(tr);
  });

  const table = document.getElementById("positions-table");
  table.addEventListener("dblclick", resetFilter);
  const canvas = document.getElementById("portfolioChart");
  canvas.addEventListener("dblclick", resetFilter);
}

/* ---------- init ---------- */
async function init() {
  initCountdown();
  await Promise.all([loadLastRun(), loadStats(), loadHistory()]);
  await loadPortfolioChart();
  await loadPositionsTable();
}
init();
