
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

async function loadPortfolioChart() {
  const text = await fetchText("./data/portfolio.csv");
  const { rows } = parseCSV(text);
  rows.sort(byDateAsc);

  const labels = rows.map(r => r.date);
  const totalValue = rows.map(r => parseFloat(r.total_value));
  const totalPnl = rows.map(r => parseFloat(r.total_pnl));

  const ctx = document.getElementById("portfolioChart").getContext("2d");
  const chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Total Value", data: totalValue },
        { label: "Total P&L", data: totalPnl }
      ]
    },
    options: {
      responsive: true,
      scales: {
        y: {
          beginAtZero: false
        }
      }
    }
  });
}

async function loadPositionsTable() {
  const text = await fetchText("./data/history.csv");
  const { rows } = parseCSV(text);
  if (!rows.length) return;

  // pick the latest date's rows
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
  await Promise.all([loadLastRun(), loadPortfolioChart(), loadPositionsTable()]);
}
init();
