import os, json, math, time, random, re
from datetime import datetime, timezone, date
import requests

ROOT = os.path.dirname(os.path.dirname(__file__))
DOCS_DATA = os.path.join(ROOT, "docs", "data")
CONFIG_PATH = os.path.join(ROOT, "config", "options.json")
os.makedirs(DOCS_DATA, exist_ok=True)

HISTORY_CSV   = os.path.join(DOCS_DATA, "history.csv")
PORTFOLIO_CSV = os.path.join(DOCS_DATA, "portfolio.csv")
LAST_RUN      = os.path.join(DOCS_DATA, "last_run.txt")

FAST_MODE = os.getenv("FAST_MODE") == "1" or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
MAX_RETRIES        = 2 if FAST_MODE else 4
BASE_SLEEP         = 0.6 if FAST_MODE else 1.0
JITTER             = 0.3
BETWEEN_UNDERLYINGS= 0.15 if FAST_MODE else 0.5
INITIAL_STAGGER_MAX= 0.0 if FAST_MODE else 2.5

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
UA = {"User-Agent": "options-tracker/1.0", "Accept": "application/json"}

def read_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def symbol_key(pos):
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

def backoff_sleep(attempt: int):
    delay = (BASE_SLEEP * (2 ** attempt)) + random.uniform(-JITTER, JITTER)
    time.sleep(max(0.15, delay))

def fetch_cboe_chain(sym: str):
    # For equities/ETFs just {SYM}.json; indexes use leading underscore (not needed for your list)
    url = CBOE_URL.format(sym=sym.upper())
    last_err = None
    for a in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=12)
            if r.status_code in (429, 503):
                last_err = Exception(f"{r.status_code} from Cboe")
                backoff_sleep(a); continue
            r.raise_for_status()
            j = r.json()
            # Data shape is either {"options":[...]} or {"data":{"options":[...]}}
            opts = j.get("options")
            if opts is None and isinstance(j.get("data"), dict):
                opts = j["data"].get("options")
            if not opts: return []
            # normalize numeric fields and keep OPRA symbol
            out = []
            for o in opts:
                # keys vary; handle both "last" and "last_trade_price"
                last = o.get("last")
                if last is None: last = o.get("last_trade_price")
                out.append({
                    "opra":     o.get("option") or o.get("symbol") or "",
                    "bid":      _tofloat(o.get("bid")),
                    "ask":      _tofloat(o.get("ask")),
                    "last":     _tofloat(last),
                })
            return out
        except Exception as e:
            last_err = e
            backoff_sleep(a)
    print(f"[ERROR] Cboe fetch failed for {sym}: {last_err}")
    return []

def _tofloat(x):
    try:
        if x in (None, "", "NaN"): return float("nan")
        return float(x)
    except Exception:
        return float("nan")

# OPRA parser: <root><YY><MM><DD><C|P><strike*1000 8-digits>
OPRA_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$")

def parse_opra(opra: str):
    m = OPRA_RE.match(opra)
    if not m: return None
    yy = int(m.group("yy"))
    year = 2000 + yy  # good till 2099
    month = int(m.group("mm")); day = int(m.group("dd"))
    cp = "call" if m.group("cp") == "C" else "put"
    strike = int(m.group("strike")) / 1000.0
    try:
        exp = date(year, month, day).strftime("%Y-%m-%d")
    except Exception:
        return None
    return {"expiry": exp, "type": cp, "strike": strike}

def ensure_csv_headers():
    if not os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, "w", encoding="utf-8") as f:
            f.write("date,symbolKey,underlying,expiry,type,strike,contracts,cost_per_contract,price,value,pnl,pnl_pct\n")
    if not os.path.exists(PORTFOLIO_CSV):
        with open(PORTFOLIO_CSV, "w", encoding="utf-8") as f:
            f.write("date,total_value,total_cost_basis,total_pnl\n")

def replace_today(file_path: str, header_line: str, date_str: str):
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(header_line); return
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines: lines = [header_line.strip()]
    head = lines[0]
    body = [ln for ln in lines[1:] if not ln.startswith(date_str + ",")]
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(head + ("\n" if not head.endswith("\n") else ""))
        for ln in body: f.write(ln + "\n")

def append_history(rows):
    with open(HISTORY_CSV, "a", encoding="utf-8") as f:
        for r in rows:
            f.write("{date},{symbolKey},{underlying},{expiry},{type},{strike},{contracts},{cost_per_contract},{price},{value},{pnl},{pnl_pct}\n".format(**r))

def append_portfolio(date_str, total_value, total_cost, total_pnl):
    with open(PORTFOLIO_CSV, "a", encoding="utf-8") as f:
        f.write(f"{date_str},{total_value:.2f},{total_cost:.2f},{total_pnl:.2f}\n")

def nearest(items, key_fn, target):
    # find item minimizing abs(key(item) - target)
    return min(items, key=lambda it: abs(key_fn(it) - target))

def nearest_expiry(requested: str, expiries: list[str]) -> str:
    if requested in expiries: return requested
    try:
        req = datetime.strptime(requested, "%Y-%m-%d").date()
        dd = [datetime.strptime(e, "%Y-%m-%d").date() for e in expiries]
        best = min(dd, key=lambda d: abs((d - req).days))
        return best.strftime("%Y-%m-%d")
    except Exception:
        return requested

def main():
    if INITIAL_STAGGER_MAX > 0:
        time.sleep(random.uniform(0, INITIAL_STAGGER_MAX))

    cfg = read_config(CONFIG_PATH)
    positions = cfg.get("positions", [])
    ensure_csv_headers()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    replace_today(HISTORY_CSV, "date,symbolKey,underlying,expiry,type,strike,contracts,cost_per_contract,price,value,pnl,pnl_pct\n", date_str)
    replace_today(PORTFOLIO_CSV, "date,total_value,total_cost_basis,total_pnl\n", date_str)

    # group by underlying
    by_ul = {}
    for p in positions:
        by_ul.setdefault(p["underlying"].upper(), []).append(p)

    total_value = 0.0
    total_cost  = 0.0
    history_rows = []

    for underlying, plist in by_ul.items():
        raw = fetch_cboe_chain(underlying)
        if not raw:
            print(f"[WARN] No chain from Cboe for {underlying}")
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
                    "price": f"{0.00:.2f}",
                    "value": f"{0.00:.2f}",
                    "pnl": f"{(-cost_basis):.2f}",
                    "pnl_pct": f"{(-100.0):.2f}" if cost_basis > 0 else "0.00",
                })
                total_cost += cost_basis
            time.sleep(BETWEEN_UNDERLYINGS)
            continue

        # Parse OPRA, keep only well-formed rows
        parsed = []
        for r in raw:
            opra = (r.get("opra") or "").strip().upper()
            info = parse_opra(opra)
            if not info: continue
            parsed.append({
                "expiry": info["expiry"],
                "type": info["type"],  # "call"/"put"
                "strike": float(info["strike"]),
                "bid": r.get("bid"),
                "ask": r.get("ask"),
                "last": r.get("last"),
            })

        # precompute all expiries available by type
        expiries_by_type = {
            "call": sorted({x["expiry"] for x in parsed if x["type"] == "call"}),
            "put":  sorted({x["expiry"] for x in parsed if x["type"] == "put"}),
        }

        for pos in plist:
            typ = pos["type"].lower()
            strike = float(pos["strike"])
            contracts = int(pos["contracts"])
            cost_per_contract = float(pos["cost_per_contract"])
            req_exp = pos["expiry"]

            available = expiries_by_type.get(typ, [])
            use_exp = nearest_expiry(req_exp, available) if available else req_exp

            candidates = [x for x in parsed if x["type"] == typ and x["expiry"] == use_exp]
            if not candidates:
                price = 0.0
            else:
                # closest strike for that expiry
                best = nearest(candidates, key_fn=lambda x: x["strike"], target=strike)
                if abs(best["strike"] - strike) > 0.02:
                    print(f"[WARN] {underlying} {req_exp} {typ} {strike} -> {best['strike']} @ {use_exp}")
                price = mark_price(best.get("bid"), best.get("ask"), best.get("last"))

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
                "expiry": req_exp,
                "type": typ,
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
