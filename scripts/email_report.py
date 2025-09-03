# scripts/email_report.py
# Weekly email for Options Tracker (Brevo): render chart -> commit -> upload to Brevo Gallery -> email HTML (no attachment)

import os, subprocess, datetime as dt, base64, json
from pathlib import Path
import pandas as pd
import requests

# chart libs (installed in the weekly workflow)
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
IMG_PATH = ROOT / "docs" / "images" / "weekly-report.png"

PORTFOLIO_CSV = DATA_DIR / "portfolio.csv"
HISTORY_CSV   = DATA_DIR / "history.csv"

# --- ENV ---
BREVO_API_KEY   = os.environ.get("BREVO_API_KEY", "")
FROM_EMAIL      = os.environ.get("REPORT_FROM_EMAIL", "")
TO_EMAILS_RAW   = os.environ.get("REPORT_TO_EMAILS", "").strip()
GITHUB_REPO     = os.environ.get("GITHUB_REPOSITORY", "")  # owner/repo
GITHUB_REF_NAME = os.environ.get("GITHUB_REF_NAME", "main")
GITHUB_SHA      = os.environ.get("GITHUB_SHA", "")[:7]

def parse_recipients(raw: str):
    parts = [p.strip() for p in raw.replace("\n", ",").replace(" ", ",").split(",") if p.strip()]
    seen, out = set(), []
    for p in parts:
        lp = p.lower()
        if lp not in seen:
            out.append({"email": p})
            seen.add(lp)
    return out

def money(n: float) -> str:
    try:
        n = float(n)
    except Exception:
        return "-"
    return f"${n:,.2f}"

def pct(n: float) -> str:
    try:
        return f"{float(n):.2f}%"
    except Exception:
        return "-"

def load_data():
    if not PORTFOLIO_CSV.exists() or not HISTORY_CSV.exists():
        raise SystemExit("Missing docs/data/*.csv files (portfolio.csv, history.csv). Run the daily job first.")
    pf = pd.read_csv(PORTFOLIO_CSV)
    hist = pd.read_csv(HISTORY_CSV)
    pf["date"]   = pd.to_datetime(pf["date"])
    hist["date"] = pd.to_datetime(hist["date"])
    for col in ["total_value","total_cost_basis","total_pnl"]:
        pf[col] = pd.to_numeric(pf[col], errors="coerce")
    for col in ["contracts","cost_per_contract","price","value","pnl","pnl_pct"]:
        if col in hist.columns:
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
    return pf.sort_values("date"), hist.sort_values("date")

def make_chart_png(pf: pd.DataFrame, save_path: Path):
    dates = pf["date"].dt.date.values
    values = pf["total_value"].values.astype(float)
    pnls   = pf["total_pnl"].values.astype(float)
    costs  = pf["total_cost_basis"].values.astype(float)
    pct_ret = np.where(costs > 0, (pnls / costs) * 100.0, 0.0)

    finite_pct = pct_ret[np.isfinite(pct_ret)]
    if finite_pct.size == 0:
        y2_min, y2_max = -10, 10
    else:
        y2_min = min(0.0, float(np.min(finite_pct)))
        y2_max = max(0.0, float(np.max(finite_pct)))
        pad = max(2.0, 0.05 * max(abs(y2_min), abs(y2_max)))
        y2_min -= pad; y2_max += pad

    fig, ax1 = plt.subplots(figsize=(11, 4))
    ax2 = ax1.twinx()

    ax1.plot(dates, values, linewidth=2, label="Total Value (USD)")
    ax1.plot(dates, pnls,   linewidth=2, label="Total P&L (USD)")
    ax2.plot(dates, pct_ret, linewidth=2, linestyle=(0, (6,4)), label="Return (%)")

    ax1.set_ylabel("USD"); ax2.set_ylabel("% Return")
    ax2.set_ylim(y2_min, y2_max)
    ax1.grid(True, axis="y", linestyle=":")
    for sp in ("top","right","left","bottom"): ax1.spines[sp].set_visible(False)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", frameon=False)
    fig.tight_layout()

    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, dpi=160)
    plt.close(fig)

def git_config():
    subprocess.run(["git","config","user.name","options-tracker-bot"], check=True)
    subprocess.run(["git","config","user.email","actions@users.noreply.github.com"], check=True)

def commit_chart():
    git_config()
    subprocess.run(["git","add", str(IMG_PATH)], check=True)
    # commit only if changed
    diff = subprocess.run(["git","diff","--cached","--quiet"])
    if diff.returncode != 0:
        subprocess.run(["git","commit","-m", f"chore(email): weekly chart {dt.date.today().isoformat()}"], check=True)
        subprocess.run(["git","push"], check=True)

def build_raw_github_url() -> str:
    # raw is instant—no Pages deploy needed
    # https://raw.githubusercontent.com/{owner}/{repo}/{branch}/docs/images/weekly-report.png
    if "/" not in GITHUB_REPO:
        return ""
    owner, repo = GITHUB_REPO.split("/", 1)
    cache_bust = GITHUB_SHA or str(int(dt.datetime.utcnow().timestamp()))
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{GITHUB_REF_NAME}/docs/images/{IMG_PATH.name}?v={cache_bust}"

def upload_to_brevo_gallery(image_url: str) -> str:
    """Upload external image URL to Brevo Content Library, return hosted CDN URL."""
    if not BREVO_API_KEY or not image_url:
        return ""
    # API: POST https://api.brevo.com/v3/emailCampaigns/images  body: {"imageUrl": "<absolute url>"}
    # (Documented "Upload an image to your account's image gallery") 
    r = requests.post(
        "https://api.brevo.com/v3/emailCampaigns/images",
        headers={"accept":"application/json","content-type":"application/json","api-key":BREVO_API_KEY},
        json={"imageUrl": image_url},
        timeout=45
    )
    if r.status_code not in (200, 201):
        print("WARN: Brevo gallery upload failed:", r.status_code, r.text[:300])
        return ""
    try:
        data = r.json()
        # common fields: "url" (public), sometimes wrapped in "images"—handle both
        if isinstance(data, dict):
            if "url" in data and isinstance(data["url"], str):
                return data["url"]
            if "images" in data and isinstance(data["images"], list) and data["images"]:
                u = data["images"][0].get("url")
                if isinstance(u, str): return u
    except Exception:
        pass
    return ""

def build_email_html(pf: pd.DataFrame, hist: pd.DataFrame, public_img_url: str) -> str:
    latest = pf.iloc[-1]
    total_value = float(latest["total_value"])
    total_cost  = float(latest["total_cost_basis"])
    total_pnl   = float(latest["total_pnl"])
    total_pct   = (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0

    latest_date = pd.to_datetime(latest["date"]).date()
    cutoff = latest_date - dt.timedelta(days=7)
    older = pf[pf["date"].dt.date <= cutoff]
    if not older.empty:
        prev = older.iloc[-1]
        prev_pct = (float(prev["total_pnl"]) / float(prev["total_cost_basis"]) * 100.0) if float(prev["total_cost_basis"]) > 0 else 0.0
        delta7 = total_pct - prev_pct
    else:
        delta7 = 0.0

    latest_hist_date = hist["date"].max()
    todays = hist[hist["date"] == latest_hist_date].copy().sort_values("pnl", ascending=False)

    img_tag = f"<img src='{public_img_url}' alt='Options Tracker chart' style='width:100%;max-width:1000px;border-radius:12px;display:block;margin:8px 0'/>" if public_img_url else ""

    def row(r):
        return (
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{r['symbolKey']}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{int(r['contracts'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{money(r['price'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{money(r['value'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{money(r['pnl'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{pct(r['pnl_pct'])}</td>"
            f"</tr>"
        )

    rows_html = "".join(row(r) for _, r in todays.head(12).iterrows())

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0b1221;background:#ffffff;margin:0;padding:16px;">
  <div style="max-width:720px;margin:0 auto;">
    <h2 style="margin:0 0 4px 0;">Options Tracker — Weekly Update</h2>
    <div style="color:#6b7280;margin-bottom:12px;">Marked value via midpoint − $0.05 (delayed quotes)</div>

    <div style="background:#f8fafc;border-radius:12px;padding:14px 16px;margin-bottom:12px;">
      <table role="presentation" style="width:100%;border-collapse:collapse">
        <tr>
          <td style="padding:6px 0;width:33%;">
            <div style="color:#6b7280;font-size:13px;">Total value</div>
            <div style="font-weight:700;font-size:22px;">{money(total_value)}</div>
          </td>
          <td style="padding:6px 0;width:33%;">
            <div style="color:#6b7280;font-size:13px;">Total return ($)</div>
            <div style="font-weight:700;font-size:22px;">{money(total_pnl)}</div>
          </td>
          <td style="padding:6px 0;width:33%;">
            <div style="color:#6b7280;font-size:13px;">Total return (%)</div>
            <div style="font-weight:700;font-size:22px;">{pct(total_pct)} <span style="color:#6b7280;font-size:12px">(Δ 7d: {pct(delta7)})</span></div>
          </td>
        </tr>
      </table>
    </div>

    {img_tag}

    <div style="background:#f8fafc;border-radius:12px;padding:14px 16px;margin-top:12px;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">
        <strong>Latest positions ({latest_hist_date.date()})</strong>
        <span style="color:#6b7280;font-size:12px;">Updated {latest_date}</span>
      </div>
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr>
            <th align="left"  style="padding:8px;border-bottom:1px solid #e5e7eb;">Symbol</th>
            <th align="right" style="padding:8px;border-bottom:1px solid #e5e7eb;">Contracts</th>
            <th align="right" style="padding:8px;border-bottom:1px solid #e5e7eb;">Price</th>
            <th align="right" style="padding:8px;border-bottom:1px solid #e5e7eb;">Value</th>
            <th align="right" style="padding:8px;border-bottom:1px solid #e5e7eb;">P&L</th>
            <th align="right" style="padding:8px;border-bottom:1px solid #e5e7eb;">P&L %</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>

    <div style="color:#6b7280;font-size:12px;margin-top:12px;">Sent automatically by GitHub Actions via Brevo.</div>
  </div>
</body></html>"""

def send_email_with_brevo(html: str):
    if not BREVO_API_KEY:
        raise RuntimeError("BREVO_API_KEY missing")
    to_list = parse_recipients(TO_EMAILS_RAW)
    if not to_list:
        raise RuntimeError("REPORT_TO_EMAILS is missing or empty")

    payload = {
        "sender": {"email": FROM_EMAIL or "no-reply@example.com", "name": "Options Tracker"},
        "to": to_list,
        "subject": f"Options Tracker — Weekly Update ({dt.date.today().isoformat()})",
        "htmlContent": html
    }
    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"accept":"application/json","content-type":"application/json","api-key":BREVO_API_KEY},
        json=payload, timeout=60
    )
    if r.status_code not in (200, 201, 202):
        print("Brevo error:", r.status_code, r.text[:500])
        r.raise_for_status()
    print("Brevo accepted:", r.text[:300])

def main():
    pf, hist = load_data()
    make_chart_png(pf, IMG_PATH)
    commit_chart()  # ensures the raw URL is live immediately

    # 1) use raw.githubusercontent.com (instant)
    raw_url = build_raw_github_url()

    # 2) upload that URL to Brevo Content Library (returns CDN URL)
    brevo_img_url = upload_to_brevo_gallery(raw_url) or raw_url

    html = build_email_html(pf, hist, brevo_img_url)
    send_email_with_brevo(html)

if __name__ == "__main__":
    main()
