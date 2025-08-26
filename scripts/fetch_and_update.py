
import os
import json
import math
import time
import pandas as pd
from datetime import datetime, timezone
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(__file__))
DOCS_DATA = os.path.join(ROOT, "docs", "data")
CONFIG_PATH = os.path.join(ROOT, "config", "options.json")

os.makedirs(DOCS_DATA, exist_ok=True)

HISTORY_CSV = os.path.join(DOCS_DATA, "history.csv")
PORTFOLIO_CSV = os.path.join(DOCS_DATA, "portfolio.csv")
LAST_RUN = os.path.join(DOCS_DATA, "last_run.txt")

def read_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def infer_symbol_key(pos):
    # e.g. "AAPL 2025-09-19 C 200"
    t = "C" if pos["type"].lower().startswith("c") else "P"
    return f'{pos["underlying"]} {pos["expiry"]} {t} {pos["strike"]}'

def mark_price(bid, ask, last_price):
    # midpoint minus 0.05, floored at 0.00, fallback to lastPrice if bid/ask missing
    if bid is not None and ask is not None and not (math.isnan(bid) or math.isnan(ask)):
        px = (float(bid) + float(ask)) / 2.0 - 0.05
    else:
        # fallback to last price
        if last_price is None or (isinstance(last_price, float) and math.isnan(last_price)):
            return 0.0
        px = float(last_price) - 0.05
    return round(max(px, 0.0), 2)

def fetch_option_row(underlying: str, expiry: str, typ: str, strike: float):
    """
    Returns dict with bid, ask, lastPrice for the specified contract using yfinance option_chain.
    """
    tk = yf.Ticker(underlying)
    try:
        chain = tk.option_chain(expiry)
        df = chain.calls if typ.lower().startswith("c") else chain.puts
        row = df.loc[df["strike"] == float(strike)]
        if row.empty:
            return None
        row = row.iloc[0]
        out = {
            "bid": float(row.get("bid", float("nan"))),
            "ask": float(row.get("ask", float("nan"))),
            "lastPrice": float(row.get("lastPrice", float("nan")))
        }
        return out
    except Exception as e:
        print(f"Error fetching {underlying} {expiry} {typ} {strike}: {e}")
        return None

def ensure_csv_headers():
    if not os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, "w", encoding="utf-8") as f:
            f.write("date,symbolKey,underlying,expiry,type,strike,contracts,cost_per_contract,price,value,pnl,pnl_pct\n")
    if not os.path.exists(PORTFOLIO_CSV):
        with open(PORTFOLIO_CSV, "w", encoding="utf-8") as f:
            f.write("date,total_value,total_cost_basis,total_pnl\n")

def append_history(rows):
    # rows: list of dicts
    with open(HISTORY_CSV, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(
                "{date},{symbolKey},{underlying},{expiry},{type},{strike},{contracts},{cost_per_contract},{price},{value},{pnl},{pnl_pct}\n".format(
                    **r
                )
            )

def append_portfolio(date_str, total_value, total_cost, total_pnl):
    with open(PORTFOLIO_CSV, "a", encoding="utf-8") as f:
        f.write(f"{date_str},{total_value:.2f},{total_cost:.2f},{total_pnl:.2f}\n")

def main():
    cfg = read_config(CONFIG_PATH)
    positions = cfg.get("positions", [])
    ensure_csv_headers()

    # Use UTC date for the daily record
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history_rows = []

    total_value = 0.0
    total_cost = 0.0

    for pos in positions:
        underlying = pos["underlying"]
        expiry = pos["expiry"]
        typ = pos["type"]
        strike = float(pos["strike"])
        contracts = int(pos["contracts"])
        cost_per_contract = float(pos["cost_per_contract"])

        info = fetch_option_row(underlying, expiry, typ, strike)
        if info is None:
            # write zeroes but keep the row so you can spot missing contracts
            price = 0.0
        else:
            price = mark_price(info.get("bid"), info.get("ask"), info.get("lastPrice"))

        value = price * contracts * 100.0
        cost_basis = cost_per_contract * contracts * 100.0
        pnl = value - cost_basis
        pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0

        total_value += value
        total_cost += cost_basis

        row = {
            "date": date_str,
            "symbolKey": (f"{underlying} {expiry} " + ("C" if typ.lower().startswith("c") else "P") + f" {strike}"),
            "underlying": underlying,
            "expiry": expiry,
            "type": typ.lower(),
            "strike": f"{strike:.2f}",
            "contracts": str(contracts),
            "cost_per_contract": f"{cost_per_contract:.2f}",
            "price": f"{price:.2f}",
            "value": f"{value:.2f}",
            "pnl": f"{pnl:.2f}",
            "pnl_pct": f"{pnl_pct:.2f}",
        }
        history_rows.append(row)

        # tiny delay
        time.sleep(0.2)

    # Append rows (no duplicate check; time series by date)
    append_history(history_rows)
    total_pnl = total_value - total_cost
    append_portfolio(date_str, total_value, total_cost, total_pnl)

    with open(LAST_RUN, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())

if __name__ == "__main__":
    main()
