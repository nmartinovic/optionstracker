# scripts/email_report_smtp.py
# Weekly email via Brevo SMTP with an inline CID image (works reliably in Gmail iOS).

import os, smtplib, subprocess, datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
IMG_PATH = ROOT / "docs" / "images" / "weekly-report.png"

PORTFOLIO_CSV = DATA_DIR / "portfolio.csv"
HISTORY_CSV   = DATA_DIR / "history.csv"

FROM_EMAIL = os.environ.get("REPORT_FROM_EMAIL", "")
TO_EMAILS  = [e.strip() for e in os.environ.get("REPORT_TO_EMAILS","").replace("\n",",").split(",") if e.strip()]
SMTP_USER  = os.environ.get("BREVO_SMTP_LOGIN", "")
SMTP_PASS  = os.environ.get("BREVO_SMTP_KEY", "")
SMTP_HOST  = "smtp-relay.brevo.com"
SMTP_PORT  = 587

def money(x):
    try: return f"${float(x):,.2f}"
    except: return "-"

def pct(x):
    try: return f"{float(x):.2f}%"
    except: return "-"

def load_data():
    if not PORTFOLIO_CSV.exists() or not HISTORY_CSV.exists():
        raise SystemExit("Missing docs/data/*.csv (run the daily job first).")
    pf = pd.read_csv(PORTFOLIO_CSV); pf["date"] = pd.to_datetime(pf["date"])
    hist = pd.read_csv(HISTORY_CSV); hist["date"] = pd.to_datetime(hist["date"])
    for c in ["total_value","total_cost_basis","total_pnl"]:
        pf[c] = pd.to_numeric(pf[c], errors="coerce")
    for c in ["contracts","cost_per_contract","price","value","pnl","pnl_pct"]:
        if c in hist.columns: hist[c] = pd.to_numeric(hist[c], errors="coerce")
    return pf.sort_values("date"), hist.sort_values("date")

def make_chart_png(pf: pd.DataFrame, path: Path):
    dates = pf["date"].dt.date.values
    values = pf["total_value"].values.astype(float)
    pnls   = pf["total_pnl"].values.astype(float)
    costs  = pf["total_cost_basis"].values.astype(float)
    pct_ret = np.where(costs > 0, (pnls / costs) * 100.0, 0.0)

    finite = pct_ret[np.isfinite(pct_ret)]
    if finite.size == 0:
        y2min, y2max = -10, 10
    else:
        y2min = min(0.0, float(np.min(finite)))
        y2max = max(0.0, float(np.max(finite)))
        pad = max(2.0, 0.05 * max(abs(y2min), abs(y2max)))
        y2min -= pad; y2max += pad

    fig, ax1 = plt.subplots(figsize=(11, 4))
    ax2 = ax1.twinx()
    ax1.plot(dates, values, linewidth=2, label="Total Value (USD)")
    ax1.plot(dates, pnls,   linewidth=2, label="Total P&L (USD)")
    ax2.plot(dates, pct_ret, linewidth=2, linestyle=(0,(6,4)), label="Return (%)")
    ax1.set_ylabel("USD"); ax2.set_ylabel("% Return"); ax2.set_ylim(y2min, y2max)
    ax1.grid(True, axis="y", linestyle=":")
    for sp in ("top","right","left","bottom"): ax1.spines[sp].set_visible(False)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", frameon=False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)

def git_commit_chart():
    subprocess.run(["git","config","user.name","options-tracker-bot"], check=True)
    subprocess.run(["git","config","user.email","actions@users.noreply.github.com"], check=True)
    subprocess.run(["git","add", str(IMG_PATH)], check=True)
    if subprocess.run(["git","diff","--cached","--quiet"]).returncode != 0:
        subprocess.run(["git","commit","-m", f"chore(email): weekly chart {dt.date.today().isoformat()}"], check=True)
        subprocess.run(["git","push"], check=True)

def build_html(pf: pd.DataFrame, hist: pd.DataFrame):
    latest = pf.iloc[-1]
    total_value = float(latest["total_value"])
    total_cost  = float(latest["total_cost_basis"])
    total_pnl   = float(latest["total_pnl"])
    total_pct   = (total_pnl/total_cost*100.0) if total_cost>0 else 0.0

    latest_date = pd.to_datetime(latest["date"]).date()
    older = pf[pf["date"].dt.date <= latest_date - dt.timedelta(days=7)]
    prev_pct = (float(older.iloc[-1]["total_pnl"]) / float(older.iloc[-1]["total_cost_basis"]) * 100.0) if not older.empty and float(older.iloc[-1]["total_cost_basis"])>0 else 0.0
    delta7 = total_pct - prev_pct

    latest_hist_date = hist["date"].max()
    todays = hist[hist["date"] == latest_hist_date].copy().sort_values("pnl", ascending=False)

    # IMPORTANT: width/height attributes help some mobile clients reserve space and render
    img_html = "<img src='cid:weekly_chart' width='1000' height='360' style='max-width:100%;height:auto;display:block;border:0;outline:none;text-decoration:none' alt='Options Tracker chart'/>"

    rows = []
    for _, r in todays.head(12).iterrows():
        rows.append(
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{r['symbolKey']}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{int(r['contracts'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{money(r['price'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{money(r['value'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{money(r['pnl'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right'>{pct(r['pnl_pct'])}</td>"
            f"</tr>"
        )
    rows_html = "".join(rows)

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

    {img_html}

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

    <div style="color:#6b7280;font-size:12px;margin-top:12px;">Sent automatically by GitHub Actions via Brevo SMTP.</div>
  </div>
</body></html>"""

def send_mail_with_cid(html: str, image_path: Path):
    if not FROM_EMAIL or not TO_EMAILS:
        raise RuntimeError("Missing REPORT_FROM_EMAIL or REPORT_TO_EMAILS")
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("Missing BREVO_SMTP_LOGIN or BREVO_SMTP_KEY")

    msg = MIMEMultipart("related")
    msg["Subject"] = f"Options Tracker — Weekly Update ({dt.date.today().isoformat()})"
    msg["From"] = FROM_EMAIL
    msg["To"] = ", ".join(TO_EMAILS)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html, "html"))
    msg.attach(alt)

    with open(image_path, "rb") as f:
        img = MIMEImage(f.read(), _subtype="png")
    img.add_header("Content-ID", "<weekly_chart>")
    img.add_header("Content-Disposition", "inline", filename=image_path.name)
    msg.attach(img)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(FROM_EMAIL, TO_EMAILS, msg.as_string())

def main():
    pf, hist = load_data()
    make_chart_png(pf, IMG_PATH)
    # optional: keep the PNG in the repo so it’s visible on the site, too
    git_commit_chart()
    html = build_html(pf, hist)
    send_mail_with_cid(html, IMG_PATH)

if __name__ == "__main__":
    main()
