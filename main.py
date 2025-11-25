import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import pytz
import schedule
import time

# -------------------------------
# 1. TELEGRAM SETTINGS
# -------------------------------
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

# -------------------------------
# 2. FIREBASE SETTINGS
# -------------------------------
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# -------------------------------
# 3. SCRAPER FUNCTION
# -------------------------------
def scrape_bulk_deals():
    url = "https://www.screener.in/screens/1/bulk-deals/"
    html = requests.get(url).text
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table")
    rows = table.find_all("tr")[1:]  # skip header

    deals = []
    for r in rows:
        cols = [c.text.strip() for c in r.find_all("td")]
        if len(cols) < 6:
            continue

        deal = {
            "date": cols[0],
            "client": cols[1],
            "type": cols[2],
            "qty": cols[3],
            "price": cols[4],
            "stock": cols[5],
            "timestamp": datetime.now(pytz.timezone("Asia/Kolkata"))
        }

        deals.append(deal)

    return deals

# -------------------------------
# 4. SAVE TO FIREBASE
# -------------------------------
def save_to_firebase(deals):
    ref = db.collection("bulk_deals")
    for d in deals:
        ref.add(d)

# -------------------------------
# 5. MAIN JOB
# -------------------------------
def job():
    print("Scraping started...")
    deals = scrape_bulk_deals()

    if not deals:
        send_telegram("No bulk deals found today.")
        return

    save_to_firebase(deals)

    # alert summary
    msg = "🔔 Bulk Deals Update:\n\n"
    for d in deals[:5]:
        msg += f"{d['stock']} | {d['client']} | {d['type']} | {d['qty']} @ {d['price']}\n"

    send_telegram(msg)
    print("Job completed.")

# -------------------------------
# 6. RUN DAILY AT 8 PM
# -------------------------------
schedule.every().day.at("20:00").do(job)

if __name__ == "__main__":
    print("Bulk Deals Tracker Running...")
    while True:
        schedule.run_pending()
        time.sleep(1)
