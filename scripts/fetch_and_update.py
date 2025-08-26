import os
import json
import math
import time
from datetime import datetime, timezone
import pandas as pd
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
    t = "C" if pos["type"].lower().startswith("c") else "P"
    return f'{pos["underlying"]} {pos["expiry"]} {t} {pos["strike"]}'

def mark_price(bid, ask, last_price):
    if bid is not None and ask is not None and not (is_nan(bid) or is_nan(ask)):
        px = (float(bid) + float(ask)) / 2.0 - 0.05
    else:
        if last_price is None or is_nan(last_price):
            return 0.0
        px = float(last_price) - 0.05
    return round(max(px, 0.0), 2)

def is_nan(x):
    try:
        return math.isnan(float(x))
    except Exception:
        return False

def pick_expiry(tk: yf.Ticker, requested_expiry: str) -> str:
    """Return the exact requested expiry if available; otherwise the nearest available expiry."""
    avail = tk.options or []
    if requested_expiry in avail:
        return requested_expiry
    try:
        req = datetime.strptime(requested_expiry, "%Y-%m-%d").date()
        if avail:
            best = min(
                avail,
                key=lambda s: abs((datetime.strptime(s, "%Y-%m-%d").date() - req).days)
            )
            return best
    except Exception:
        pass
    return requested_expiry  # fallback

def fetch_option_row(underlying: str, requested_expiry: str, typ: str, strike: float):
    """
    Fetches the option row using the nearest-matching expiry and closest strike.
    Returns dict with bid, ask, lastPrice, used_expiry, used_strike.
    """
    tk = yf.Ticker(underlying)
    used_expiry = pick_expiry(tk, requested_expiry)

    try:
        chain = tk.option_chain(used_expiry)
        df = chain.calls if typ.lower().startswith("c") else chain.puts
        if df is None or df.empty:
            print(f"[WARN] Empty chain for {underlying} {used_expiry} {typ}")
            return None

        df = df.copy()
        df["strike_diff"] = (df["strike"] - float(strike)).abs()
        row = df.loc[df["strike_diff"].idxmin()]
        used_strike = float(row["strike"])

        # small sanity tolerance (0.02 covers .01 tick and float issues)
        if abs(used_strike - float(strike)) > 0.02:
            print(f"[WARN] Closest strike for {underlying} {requested_expiry} {typ} {strike} "
                  f"is {used_strike} at expiry {used_expiry} (requested not found).")

        out = {
            "bid": float(row.get("bid", float("nan"))),
            "ask": float(row.get("ask", float("nan"))),
            "lastPrice": float(row.get("lastPrice", float("nan"))),
            "used_expiry": used_expiry,
            "used_strike": used_strike,
        }
        if used_expiry != requested_expiry:
            print(f"[INFO] Mapped expiry for {underlying}: requested {requested_expiry} -> used {used_expiry}")
        return out

    except Exception as e:
        print(f"[ERROR] Fetch {underlying} {requested_expiry} {typ} {strike}: {e}")
        return None

def ensure_csv_headers():
    if not os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, "w", encoding="utf-8") as f:
            f.write("date,symbolKey,underlying,expiry,type,strike,contracts,cost_per_contract,price,value,pnl,pnl_pct\n")
    if not os.path.exists(PORTFOLIO_CSV):
        with open(PORTFOLIO_CSV, "w", encoding="utf-8") as f:
            f.write("date,total_value,total_cost_basis,total_pnl\n")

def append_history(rows):
    with open(HISTORY_CSV, "a", encoding="utf-8") as f:
        for r in rows:
            f.write("{date},{symbolKey},{underlying},{expiry},{type},{strike},{contracts},{cost_per_contract},{price},{value},{pnl},{pnl_pct}\n".format(**r))

def append_portfolio(date_str, total_value, total_cost, total_pnl):
    with open(PORTFOLIO_CSV, "a", encoding="utf-8") as f:
        f.write(f"{date_str},{total_value:.2f},{total_cost:.2f},{total_pnl:.2f}\n")

def main():
    cfg = read_config(CONFIG_PATH)
    positions = cfg.get("positions", [])
    ensure_csv_headers()

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
            "symbolKey": infer_symbol_key(pos),  # keep the user-requested key
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

        time.sleep(0.2)  # be polite

    append_history(history_rows)
    total_pnl = total_value - total_cost
    append_portfolio(date_str, total_value, total_cost, total_pnl)

    with open(LAST_RUN, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())

if __name__ == "__main__":
    main()
