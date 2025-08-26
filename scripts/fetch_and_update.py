import os
import json
import math
import time
import random
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
import requests

ROOT = os.path.dirname(os.path.dirname(__file__))
DOCS_DATA = os.path.join(ROOT, "docs", "data")
CONFIG_PATH = os.path.join(ROOT, "config", "options.json")

os.makedirs(DOCS_DATA, exist_ok=True)

HISTORY_CSV = os.path.join(DOCS_DATA, "history.csv")
PORTFOLIO_CSV = os.path.join(DOCS_DATA, "portfolio.csv")
LAST_RUN = os.path.join(DOCS_DATA, "last_run.txt")

# --- Tuning knobs ---
MAX_RETRIES = 6                 # total attempts for each Yahoo call
BASE_SLEEP = 1.5                # base backoff seconds
JITTER = 0.75                   # +/- jitter added to sleep
BETWEEN_UNDERLYINGS = 1.0       # pause between different tickers
INITIAL_STAGGER_MAX = 8.0       # random stagger at job start to avoid dogpiles

def read_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def infer_symbol_key(pos):
    t = "C" if pos["type"].lower().startswith("c") else "P"
    return f'{pos["underlying"]} {pos["expiry"]} {t} {pos["strike"]}'

def is_nan(x):
    try:
        return math.isnan(float(x))
    except Exception:
        return False

def mark_price(bid, ask, last_price):
    # midpoint minus 0.05, floored at 0.00, fallback to lastPrice if bid/ask missing
    if bid is not None and ask is not None and not (is_nan(bid) or is_nan(ask)):
        px = (float(bid) + float(ask)) / 2.0 - 0.05
    else:
        if last_price is None or is_nan(last_price):
            return 0.0
        px = float(last_price) - 0.05
    return round(max(px, 0.0), 2)

def sleep_backoff(attempt: int):
    # exponential backoff with jitter
    delay = (BASE_SLEEP * (2 ** attempt)) + random.uniform(-JITTER, JITTER)
    time.sleep(max(0.25, delay))

def with_retries(fn, *args, **kwargs):
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except (YFRateLimitError,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError) as e:
            last_err = e
            print(f"[RATE-LIMIT/NET] {e.__class__.__name__} on {fn.__name__}, attempt {attempt+1}/{MAX_RETRIES}. Backing off…")
            sleep_backoff(attempt)
        except Exception as e:
            # other transient issues: retry a couple times, then give up
            last_err = e
            print(f"[WARN] {e.__class__.__name__} on {fn.__name__}, attempt {attempt+1}/{MAX_RETRIES}.")
            sleep_backoff(attempt)
    print(f"[ERROR] Exhausted retries for {fn.__name__}: {last_err}")
    return None

def list_expiries(tk: yf.Ticker):
    # tk.options triggers a request; wrap it
    return with_retries(lambda: tk.options) or []

def pick_expiry(tk: yf.Ticker, requested_expiry: str) -> str:
    """Return the exact requested expiry if available; otherwise nearest available expiry."""
    avail = list_expiries(tk)
    if requested_expiry in avail:
        return requested_expiry
    try:
        req = datetime.strptime(requested_expiry, "%Y-%m-%d").date()
        if avail:
            best = min(
                avail,
                key=lambda s: abs((datetime.strptime(s, "%Y-%m-%d").date() - req).days)
            )
            if best != requested_expiry:
                print(f"[INFO] Mapped expiry: requested {requested_expiry} -> used {best}")
            return best
    except Exception:
        pass
    return requested_expiry

def fetch_chain(tk: yf.Ticker, expiry: str, call_or_put: str):
    def _pull():
        chain = tk.option_chain(expiry)
        return chain.calls if call_or_put.lower().startswith("c") else chain.puts
    return with_retries(_pull)

def fetch_option_row(underlying: str, requested_expiry: str, typ: str, strike: float):
    """
    Fetch the option row using nearest-matching expiry and closest strike.
    Returns dict with bid, ask, lastPrice, used_expiry, used_strike.
    """
    tk = yf.Ticker(underlying)
    used_expiry = pick_expiry(tk, requested_expiry)
    df = fetch_chain(tk, used_expiry, typ)
    if df is None or df.empty:
        print(f"[WARN] Empty chain for {underlying} {used_expiry} {typ}")
        return None

    df = df.copy()
    df["strike_diff"] = (df["strike"] - float(strike)).abs()
    row = df.loc[df["strike_diff"].idxmin()]
    used_strike = float(row["strike"])

    if abs(used_strike - float(strike)) > 0.02:
        print(f"[WARN] Closest strike for {underlying} {requested_expiry} {typ} {strike} "
              f"is {used_strike} at expiry {used_expiry} (requested not found).")

    return {
        "bid": float(row.get("bid", float("nan"))),
        "ask": float(row.get("ask", float("nan"))),
        "lastPrice": float(row.get("lastPrice", float("nan"))),
        "used_expiry": used_expiry,
        "used_strike": used_strike,
    }

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
    # small stagger so we don't hit Yahoo at the exact top of the minute
    time.sleep(random.uniform(0, INITIAL_STAGGER_MAX))

    cfg = read_config(CONFIG_PATH)
    positions = cfg.get("positions", [])
    ensure_csv_headers()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history_rows = []

    total_value = 0.0
    total_cost = 0.0

    # group by underlying to pace calls
    by_underlying = {}
    for pos in positions:
        by_underlying.setdefault(pos["underlying"], []).append(pos)

    for underlying, plist in by_underlying.items():
        for pos in plist:
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
                "symbolKey": infer_symbol_key(pos),  # keep your requested key
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

        # pause between different underlyings
        time.sleep(BETWEEN_UNDERLYINGS)

    append_history(history_rows)
    total_pnl = total_value - total_cost
    append_portfolio(date_str, total_value, total_cost, total_pnl)

    with open(LAST_RUN, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())

if __name__ == "__main__":
    main()
