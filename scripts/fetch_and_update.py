import os, json, math, time, random
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
import requests

ROOT = os.path.dirname(os.path.dirname(__file__))
DOCS_DATA = os.path.join(ROOT, "docs", "data")
CONFIG_PATH = os.path.join(ROOT, "config", "options.json")

os.makedirs(DOCS_DATA, exist_ok=True)

HISTORY_CSV   = os.path.join(DOCS_DATA, "history.csv")
PORTFOLIO_CSV = os.path.join(DOCS_DATA, "portfolio.csv")
LAST_RUN      = os.path.join(DOCS_DATA, "last_run.txt")

# ---------- knobs (faster on manual runs) ----------
FAST_MODE = os.getenv("FAST_MODE") == "1" or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
MAX_RETRIES        = 3 if FAST_MODE else 5
BASE_SLEEP         = 0.8 if FAST_MODE else 1.2
JITTER             = 0.4
BETWEEN_UNDERLYINGS= 0.3 if FAST_MODE else 0.8
INITIAL_STAGGER_MAX= 0.0 if FAST_MODE else 6.0

def read_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def infer_symbol_key(pos):
    t = "C" if pos["type"].lower().startswith("c") else "P"
    return f'{pos["underlying"]} {pos["expiry"]} {t} {pos["strike"]}'

def is_nan(x):
    try: return math.isnan(float(x))
    except Exception: return False

def mark_price(bid, ask, last_price):
    if bid is not None and ask is not None and not (is_nan(bid) or is_nan(ask)):
        px = (float(bid) + float(ask)) / 2.0 - 0.05
    else:
        if last_price is None or is_nan(last_price): return 0.0
        px = float(last_price) - 0.05
    return round(max(px, 0.0), 2)

def sleep_backoff(attempt: int):
    delay = (BASE_SLEEP * (2 ** attempt)) + random.uniform(-JITTER, JITTER)
    time.sleep(max(0.2, delay))

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
            print(f"[RATE-LIMIT/NET] {e.__class__.__name__} on {fn.__name__} (try {attempt+1}/{MAX_RETRIES})")
            sleep_backoff(attempt)
        except Exception as e:
            last_err = e
            print(f"[WARN] {e.__class__.__name__} on {fn.__name__} (try {attempt+1}/{MAX_RETRIES})")
            sleep_backoff(attempt)
    print(f"[ERROR] Exhausted retries for {fn.__name__}: {last_err}")
    return None

def list_expiries(tk: yf.Ticker):
    return with_retries(lambda: tk.options) or []

def pick_expiry(tk: yf.Ticker, requested_expiry: str) -> str:
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
                print(f"[INFO] Mapped expiry: {requested_expiry} -> {best}")
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
        print(f"[WARN] Closest strike for {underlying} {requested_expiry} {typ} {strike} -> {used_strike} @ {used_expiry}")
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

def replace_today(file_path: str, header_line: str, date_str: str):
    """Rewrite CSV removing rows for date_str, preserving header."""
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(header_line)
        return
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines:
        lines = [header_line.strip()]
    head = lines[0]
    body = [ln for ln in lines[1:] if not ln.startswith(date_str + ",")]
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(head + ("\n" if not head.endswith("\n") else ""))
        for ln in body:
            f.write(ln + "\n")

def append_history(rows):
    with open(HISTORY_CSV, "a", encoding="utf-8") as f:
        for r in rows:
            f.write("{date},{symbolKey},{underlying},{expiry},{type},{strike},{contracts},{cost_per_contract},{price},{value},{pnl},{pnl_pct}\n".format(**r))

def append_portfolio(date_str, total_value, total_cost, total_pnl):
    with open(PORTFOLIO_CSV, "a", encoding="utf-8") as f:
        f.write(f"{date_str},{total_value:.2f},{total_cost:.2f},{total_pnl:.2f}\n")

def main():
    # small random stagger (skipped in FAST_MODE)
    if INITIAL_STAGGER_MAX > 0:
        time.sleep(random.uniform(0, INITIAL_STAGGER_MAX))

    cfg = read_config(CONFIG_PATH)
    positions = cfg.get("positions", [])
    ensure_csv_headers()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- de-dupe today's rows before appending ---
    replace_today(HISTORY_CSV, "date,symbolKey,underlying,expiry,type,strike,contracts,cost_per_contract,price,value,pnl,pnl_pct\n", date_str)
    replace_today(PORTFOLIO_CSV, "date,total_value,total_cost_basis,total_pnl\n", date_str)

    history_rows = []
    total_value = 0.0
    total_cost  = 0.0

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
            price = 0.0 if info is None else mark_price(info.get("bid"), info.get("ask"), info.get("lastPrice"))

            value = price * contracts * 100.0
            cost_basis = cost_per_contract * contracts * 100.0
            pnl = value - cost_basis
            pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0

            total_value += value
            total_cost  += cost_basis

            history_rows.append({
                "date": date_str,
                "symbolKey": infer_symbol_key(pos),
                "underlying": pos["underlying"],
                "expiry": expiry,
                "type": typ.lower(),
                "strike": f"{strike:.2f}",
                "contracts": str(contracts),
                "cost_per_contract": f"{cost_per_contract:.2f}",
                "price": f"{price:.2f}",
                "value": f"{value:.2f}",
                "pnl": f"{pnl:.2f}",
                "pnl_pct": f"{pnl_pct:.2f}",
            })

        time.sleep(BETWEEN_UNDERLYINGS)

    append_history(history_rows)
    append_portfolio(date_str, total_value, total_cost, total_value - total_cost)

    with open(LAST_RUN, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())

if __name__ == "__main__":
    main()
