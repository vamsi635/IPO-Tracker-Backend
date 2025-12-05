import gspread
from screener_scraper import fetch_bulk_deals
from datetime import datetime
import pytz
import os
import requests

# Telegram setup
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message):
    if TELEGRAM_BOT_TOKEN is None:
        print("Telegram token missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})


def main():
    print("Starting Screener bulk deal check...")

    # Authorize Google Sheets
    gc = gspread.service_account(filename="service_account.json")

    sh = gc.open("IPO_Tracker")
    master = sh.worksheet("Master_Log")

    # Fetch deals from Screener
    deals = fetch_bulk_deals()

    if not deals:
        print("No deals found.")
        return

    today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")

    new_rows = []
    for d in deals:
        row = [
            today,
            d["stock"],
            d["type"],
            d["client"],
            d["qty"],
            d["price"]
        ]
        new_rows.append(row)

    # Append rows to Master_Log
    master.append_rows(new_rows)

    # Send Telegram summary
    msg = "📊 *Bulk Deals Update*\n\n"
    for d in deals[:5]:
        msg += f"{d['stock']} | {d['client']} | {d['type']} | {d['qty']} @ {d['price']}\n"

    send_telegram(msg)
    print("Job completed.")

if __name__ == "__main__":
    main()
