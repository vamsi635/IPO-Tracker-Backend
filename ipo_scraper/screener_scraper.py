import requests
from bs4 import BeautifulSoup
from datetime import datetime

def parse_amount(amount_text):
    """Convert '12 Cr', '25.4 Cr' etc into float crores."""
    try:
        amount = amount_text.replace("Cr", "").strip()
        return float(amount)
    except:
        return None

def scrape_screener_bulk(symbol):
    """
    Scrapes latest bulk deals from Screener for a given NSE symbol.
    Returns a list of dicts.
    """
    url = f"https://www.screener.in/company/{symbol}/"
    headers = {"User-Agent": "Mozilla/5.0"}

    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    bulk_section = soup.find("table", {"class": "data-table"})
    if bulk_section is None:
        return []

    rows = bulk_section.find_all("tr")[1:]  # skip header
    deals = []

    for row in rows:
        cols = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cols) < 5:
            continue

        deal_date = datetime.strptime(cols[0], "%d-%b-%Y").date()
        investor = cols[1]
        deal_type = cols[2]   # BUY or SELL
        quantity = cols[3]
        value_text = cols[4]  # e.g. '12.30 Cr'

        value_cr = parse_amount(value_text)

        deals.append({
            "date": str(deal_date),
            "type": deal_type,
            "investor": investor,
            "value_cr": value_cr,
            "raw_value": value_text
        })

    return deals


def filter_new_deals(all_deals, last_logged_date):
    """
    Filters and returns deals *after* the last logged date.
    """
    last_date_obj = datetime.strptime(last_logged_date, "%Y-%m-%d").date()

    new_deals = [
        d for d in all_deals
        if datetime.strptime(d["date"], "%Y-%m-%d").date() > last_date_obj
    ]

    return new_deals
