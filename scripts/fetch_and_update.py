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

# ---------- knobs ----------
FAST_MODE = os.getenv("FAST_MODE") == "1" or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
MAX_RETRIES        = 3 if FAST_MODE else 5
BASE_SLEEP         = 0.8 if FAST_MODE else 1.2
JITTER             = 0.4
BETWEEN_UNDERLYINGS= 0.3 if FAST_MODE else 0.8
INITIAL_STAGGER_MAX= 0.0 if FAST_MODE else 6.0

def is_nan(x):
    try: return math.isnan(float(x))
    except Exception: return False

def mark_price(bid, ask, last_price):
    # midpoint minus 0.05, floored at 0.00; fallback to lastPrice
    if bid is not None and ask is not None and not (is_nan(bid) or is_nan(ask)):
        px = (float(bid) + float(ask)) / 2.0 - 0.05
    else:
        if last_price is None or is_nan(last_price): return 0.0
        px = float(last_price) - 0.05
    return round(max(px, 0.0), 2)

def read_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def symbol_key(pos):
    t = "C" if pos["type"].lower().startswith("c") else "P"
    return f'{pos["underlying"]} {pos["expiry"]} {t} {pos["strike"]}'

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

def nearest_expiry(requested: str, avail: list[str]) -> str:
    if requested in avail: return requested
    try:
        req = datetime.strptime(requested, "%Y-%m-%d").date()
        if avail:
            best = min(avail, key=lambda s: abs((datetime.strptime(s, "%Y-%m-%d").date() - req).days))
            if best != requested:
                print(f"[INFO] Mapped expiry: {requested} -> {best}")
            return best
    except Exception:
        pass
    return requested

def fetch_option_chain_both(tk: yf.Ticker, expiry: str):
    def _pull(): return tk.option_chain(expiry)
    oc = with_retries(_pull)
    if not oc: return None, None
    return oc.calls, oc.puts

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
    # small stagger (skipped in FAST_MODE)
    if INITIAL_STAGGER_MAX > 0:
        time.sleep(random.uniform(0, INITIAL_STAGGER_MAX))

    cfg = read_config(CONFIG_PATH)
    positions = cfg.get("positions", [])
    ensure_csv_headers()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # de-dupe today's rows before appending
    replace_today(HISTORY_CSV, "date,symbolKey,underlying,expiry,type,strike,contracts,cost_per_contract,price,value,pnl,pnl_pct\n", date_str)
    replace_today(PORTFOLIO_CSV, "date,total_value,total_cost_basis,total_pnl\n", date_str)

    # group positions by underlying
    by_ul = {}
    for p in positions:
        by_ul.setdefault(p["underlying"], []).append(p)

    total_value = 0.0
    total_cost  = 0.0
    history_rows = []

    for underlying, plist in by_ul.items():
        tk = yf.Ticker(underlying)

        # one call to list expiries for this underlying
        avail = list_expiries(tk)
        if not avail:
            print(f"[WARN] No expiries available for {underlying} (rate limit or no options).")
            # write zeros for this underlying’s positions but continue
            for pos in plist:
                contracts = int(pos["contracts"])
                cost_basis = float(pos["cost_per_contract"]) * contracts * 100.0
                history_rows.append({
                    "date": date_str,
                    "symbolKey": symbol_key(pos),
                    "underlying": pos["underlying"],
                    "expiry": pos["expiry"],
                    "type": pos["type"].lower(),
                    "strike": f"{float(pos['strike']):.2f}",
                    "contracts": str(contracts),
                    "cost_per_contract": f"{float(pos['cost_per_contract']):.2f}",
                    "price": f"{0.0:.2f}",
                    "value": f"{0.0:.2f}",
                    "pnl": f"{(-cost_basis):.2f}",
                    "pnl_pct": f"{(-100.0):.2f}" if cost_basis > 0 else "0.00",
                })
                total_cost += cost_basis
            time.sleep(BETWEEN_UNDERLYINGS)
            continue

        # map each requested expiry to nearest available
        requested_exps = sorted({p["expiry"] for p in plist})
        exp_map = {req: nearest_expiry(req, avail) for req in requested_exps}

        # fetch each USED expiry once (both calls & puts) and cache
        chains = {}  # used_expiry -> (calls_df, puts_df)
        for used_exp in sorted(set(exp_map.values())):
            calls_df, puts_df = fetch_option_chain_both(tk, used_exp)
            chains[used_exp] = (calls_df, puts_df)

        # now resolve each position from cached chains
        for pos in plist:
            typ = pos["type"]
            strike = float(pos["strike"])
            contracts = int(pos["contracts"])
            cost_per_contract = float(pos["cost_per_contract"])

            used_exp = exp_map[pos["expiry"]]
            calls_df, puts_df = chains.get(used_exp, (None, None))
            df = calls_df if typ.lower().startswith("c") else puts_df

            if df is None or df.empty:
                print(f"[WARN] Empty chain for {underlying} {used_exp} {typ}")
                price = 0.0
            else:
                # closest strike
                df = df.copy()
                df["strike_diff"] = (df["strike"] - strike).abs()
                row = df.loc[df["strike_diff"].idxmin()]
                used_strike = float(row["strike"])
                if abs(used_strike - strike) > 0.02:
                    print(f"[WARN] Closest strike for {underlying} {pos['expiry']} {typ} {strike} -> {used_strike} @ {used_exp}")
                price = mark_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))

            value = price * contracts * 100.0
            cost_basis = cost_per_contract * contracts * 100.0
            pnl = value - cost_basis
            pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0

            total_value += value
            total_cost  += cost_basis

            history_rows.append({
                "date": date_str,
                "symbolKey": symbol_key(pos),
                "underlying": pos["underlying"],
                "expiry": pos["expiry"],
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

    # write results
    append_history(history_rows)
    append_portfolio(date_str, total_value, total_cost, total_value - total_cost)

    with open(LAST_RUN, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())

if __name__ == "__main__":
    main()
