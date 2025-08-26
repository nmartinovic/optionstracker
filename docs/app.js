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

function byDateAsc(a, b) {
  return a.date.localeCompare(b.date);
}

async function loadLastRun() {
  const t = await fetchText("./data/last_run.txt");
  const el = document.getElementById("last-run");
  if (t) {
    const when = new Date(t.trim());
    el.textContent = "Last update: " + when.toLocaleString();
  } else {
    el.textContent = "Last update: (not yet recorded)";
  }
}

// Top-level stats
async function loadStats() {
  const text = await fetchText("./data/portfolio.csv");
  const { rows } = parseCSV(text);
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

async function loadPortfolioChart() {
  const text = await fetchText("./data/portfolio.csv");
  const { rows } = parseCSV(text);
  if (!rows.length) return;

  rows.sort(byDateAsc);

  const labels     = rows.map(r => r.date);
  const totalValue = rows.map(r => parseFloat(r.total_value));
  const totalPnl   = rows.map(r => parseFloat(r.total_pnl));
  const totalPct   = rows.map(r => {
    const c = parseFloat(r.total_cost_basis);
    const p = parseFloat(r.total_pnl);
    return c > 0 ? (p / c) * 100 : 0;
  });

  const ctx = document.getElementById("portfolioChart").getContext("2d");
  const chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Total Value", data: totalValue, yAxisID: "y" },
        { label: "Total P&L",   data: totalPnl,   yAxisID: "y" },
        { label: "Total Return (%)", data: totalPct, yAxisID: "yPct", borderDash: [6, 4], pointRadius: 2 }
      ]
    },
    options: {
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
    }
  });
}

async function loadPositionsTable() {
  const text = await fetchText("./data/history.csv");
  const { rows } = parseCSV(text);
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
    tbody.appendChild(tr);
  });
}

async function init() {
  await Promise.all([
    loadLastRun(),
    loadStats(),
    loadPortfolioChart(),
    loadPositionsTable()
  ]);
}
init();
