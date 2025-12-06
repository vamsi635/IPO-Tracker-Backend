# main.py
import os, json, time, requests, hashlib, re
from datetime import datetime, date
from dateutil import parser as dateparser
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup
import pandas as pd

# -------- CONFIG (no secrets here; workflow injects env vars) ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GSPREAD_B64 = os.getenv("GSPREAD_SA_JSON_B64")

# GOOGLE SHEET name and tabs
SHEET_NAME = "IPO_Tracker"   # change if your sheet uses a different name
TAB_MAIN = "Mainboard_IPO_List"
TAB_SME = "SME_List"
TAB_MASTER = "Master_Log"

# Screener base (we'll try symbol first)
SCREENER_COMPANY_URL = "https://www.screener.in/company/{}/consolidated/"

# ---------------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured; would send:", msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

def parse_flexible_date(s):
    if s is None: return None
    if isinstance(s, date): return s
    s = str(s).strip()
    if not s: return None
    s = re.sub(r"\s+", " ", s)
    try:
        dt = dateparser.parse(s, dayfirst=False)
        return dt.date()
    except Exception:
        return None

def row_hash(symbol, trade_date, qty, price, value):
    s = f"{symbol}|{trade_date}|{qty}|{price}|{value}"
    return hashlib.sha1(s.encode()).hexdigest()

# ---------------------------------------------------------------------------------------------------------------------
# Google Sheets auth using our GSPREAD_SA_JSON_B64 secret
# ---------------------------------------------------------------------------------------------------------------------
def init_gspread():
    if not GSPREAD_B64:
        raise Exception("GSPREAD_SA_JSON_B64 is missing in environment.")
    # decode and write file
    sa_path = "service_account.json"
    with open(sa_path, "wb") as f:
        f.write(__import__("base64").b64decode(GSPREAD_B64))
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(sa_path, scope)
    client = gspread.authorize(creds)
    return client

# ---------------------------------------------------------------------------------------------------------------------
# Screener scraping: try company page symbol-> consolidated -> look for tables with 'bulk' or 'deals'
# ---------------------------------------------------------------------------------------------------------------------
def fetch_screener_deals_for_symbol(symbol):
    url = SCREENER_COMPANY_URL.format(symbol)
    headers = {"User-Agent":"Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
    except Exception as e:
        print("Request error:", e)
        return []
    if r.status_code != 200:
        print("Screener returned", r.status_code, "for", symbol, url)
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    deals = []
    # find tables that include words like 'bulk' or 'deal'
    for table in soup.find_all("table"):
        ths = " ".join([th.get_text(" ", strip=True).lower() for th in table.find_all("th")])
        if any(k in ths for k in ["bulk", "block", "deal", "deals", "off-market"]):
            for tr in table.find_all("tr"):
                cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cols) < 3: continue
                # heuristic: first col is date, others contain buy/sell/qty/price
                dt = parse_flexible_date(cols[0])
                text = " | ".join(cols)
                # attempt extract buy/sell and numeric value
                bs = None
                for c in cols:
                    if re.search(r"\b(buy|sell|b/s)\b", c, re.I):
                        bs = c
                        break
                nums = re.findall(r"[\d,]+(?:\.\d+)?", text.replace("₹",""))
                value = None
                qty = None
                price = None
                if nums:
                    # heuristics: last numeric -> price or value: we can't be perfect for all companies
                    try:
                        price = float(nums[-1].replace(",",""))
                    except:
                        price = None
                    if len(nums) >= 2:
                        try:
                            qty = float(nums[0].replace(",",""))
                        except:
                            qty = None
                deals.append({
                    "trade_date": dt.isoformat() if dt else None,
                    "symbol": symbol,
                    "raw": text,
                    "buy_sell": bs,
                    "qty": qty,
                    "price": price,
                    "value": None
                })
    return deals

# ---------------------------------------------------------------------------------------------------------------------
# Main: read tracked symbols from two tabs, for each symbol fetch deals since listing_date and append to Master_Log
# ---------------------------------------------------------------------------------------------------------------------
def main():
    client = init_gspread()
    sh = client.open(SHEET_NAME)
    # read both tabs
    def read_tab(tabname):
        try:
            w = sh.worksheet(tabname)
        except Exception as e:
            print("Missing tab:", tabname, e)
            return []
        data = w.get_all_records()
        rows = []
        for r in data:
            symbol = r.get("Symbol") or r.get("symbol") or r.get("NSE Symbol")
            name = r.get("Stock Name") or r.get("Stock") or r.get("stock name")
            ld = parse_flexible_date(r.get("Listing Date") or r.get("ListingDate") or r.get("Date"))
            if symbol:
                rows.append({"symbol":str(symbol).strip().upper(), "stock_name":name, "listing_date":ld})
        return rows

    tracked = read_tab(TAB_MAIN) + read_tab(TAB_SME)
    if not tracked:
        print("No tracked symbols found in sheets.")
        return

    # open master log worksheet (create if missing)
    try:
        master = sh.worksheet(TAB_MASTER)
    except:
        master = sh.add_worksheet(title=TAB_MASTER, rows=1000, cols=20)
        # write header
        header = ["Inserted_AtUTC","Date","Stock Name","Symbol","Buy (₹)","Sell (₹)","Net (₹)","Source","Raw Info","Notes"]
        master.append_row(header)

    inserted_any = False
    run_date = date.today().isoformat()
    for t in tracked:
        sym = t["symbol"]
        list_date = t["listing_date"]
        print("Fetching", sym)
        deals = fetch_screener_deals_for_symbol(sym)
        # filter deals on or after listing_date (if listing_date present)
        filtered = []
        for d in deals:
            if not d.get("trade_date"):
                continue
            td = dateparser.parse(d["trade_date"]).date() if isinstance(d["trade_date"], str) else None
            if list_date and td and td < list_date:
                continue
            filtered.append(d)
        if not filtered:
            continue
        # compute per-date totals (buy vs sell by presence of "buy"/"sell" in buy_sell or raw)
        # we'll compute day-level net for the run_date OR for all filtered dates and append rows for those dates
        summary_by_date = {}
        for d in filtered:
            td = d["trade_date"]
            if td not in summary_by_date:
                summary_by_date[td] = {"buy":0.0,"sell":0.0,"raw":[]}
            text = d.get("raw","")
            # crude detection
            if d.get("buy_sell") and re.search(r"buy", d["buy_sell"], re.I):
                summary_by_date[td]["buy"] += (d.get("qty") or 0)
            elif d.get("buy_sell") and re.search(r"sell", d["buy_sell"], re.I):
                summary_by_date[td]["sell"] += (d.get("qty") or 0)
            else:
                # try raw
                if re.search(r"\bbuy\b", text, re.I):
                    summary_by_date[td]["buy"] += (d.get("qty") or 0)
                elif re.search(r"\bsell\b", text, re.I):
                    summary_by_date[td]["sell"] += (d.get("qty") or 0)
            summary_by_date[td]["raw"].append(text)
        # append to master
        for td, vals in summary_by_date.items():
            buy = vals["buy"]
            sell = vals["sell"]
            net = buy - sell
            inserted_at = datetime.utcnow().isoformat()
            row = [inserted_at, td, t.get("stock_name") or sym, sym, buy, sell, net, SCREENER_COMPANY_URL.format(sym), "; ".join(vals["raw"])[:200], ""]
            master.append_row(row)
            inserted_any = True
            # If this date is today (or you can change to notify for any newly inserted row), send telegram
            if td == run_date:
                send_telegram(f"📈 {sym} bulk deals on {td} — Buy: {buy:,} Sell: {sell:,} Net: {net:,}")
    if not inserted_any:
        print("No new deals for tracked symbols.")
    else:
        print("Inserted rows into Master_Log.")

if __name__ == "__main__":
    main()
