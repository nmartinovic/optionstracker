
# Options Tracker (GitHub Pages + GitHub Actions)

A simple static site that tracks the *marked* value of your options portfolio daily and charts progress over time.
- Hosted on GitHub Pages (`/docs` folder).
- Automatically updates daily via GitHub Actions.
- Price = midpoint between bid/ask minus $0.05, floored at $0.00.


## Quick Start

1. **Create a new GitHub repo** and upload this folder's contents.
2. Edit `config/options.json` with your options positions (examples included).
3. Commit + push to `main` (or your default branch).
4. **Enable GitHub Pages** in repo settings:
   - Source: `Deploy from a branch`
   - Branch: `main`
   - Folder: `/docs`
5. The site will be available at: `https://<your-username>.github.io/<repo-name>/`
6. The scheduled workflow runs daily and writes data to `docs/data/`. You can also run it manually in the Actions tab.

> Note: This uses free, public Yahoo Finance data via `yfinance`. If you prefer a commercial API (Polygon, Finnhub, Tradier, etc.), swap out the fetch logic in `scripts/fetch_and_update.py`.

## Configure Your Positions

Edit `config/options.json`:
```json
{
  "base_currency": "USD",
  "positions": [
    {
      "underlying": "AAPL",
      "expiry": "2025-09-19",
      "type": "call",
      "strike": 200,
      "contracts": 2,
      "cost_per_contract": 5.50
    },
    {
      "underlying": "MSFT",
      "expiry": "2025-12-19",
      "type": "put",
      "strike": 300,
      "contracts": 1,
      "cost_per_contract": 12.40
    }
  ]
}
```
- `type`: `"call"` or `"put"`
- `contracts`: number of contracts (1 contract = 100 shares)
- `cost_per_contract`: entry price in USD **per contract** (not per 100 shares)

## What gets recorded?

Daily marks (UTC date):
- Per-option price = `max(((bid + ask)/2 - 0.05), 0.00)`
- Value = `price * contracts * 100`
- P&L = `value - (cost_per_contract * contracts * 100)`
- Portfolio totals are also computed and stored.

Data files saved to `docs/data/`:
- `history.csv`: per-position snapshot for each run
- `portfolio.csv`: aggregated portfolio value and P&L by date
- `last_run.txt`: timestamp of the last successful update

## Local Testing

You can run the script locally:
```bash
pip install -r requirements.txt
python scripts/fetch_and_update.py
```
Then open `docs/index.html` in your browser.

## Notes & Limitations
- Yahoo Finance data may occasionally omit a bid/ask for certain contracts. In that case, we fallback to `lastPrice - 0.05` (floored at $0).
- Schedule uses UTC. Default is once daily at `23:10 UTC`. Adjust in `.github/workflows/daily.yaml` if you prefer.
- This is for **personal use**; no guarantees for accuracy or completeness.
