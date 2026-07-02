"""
quiver_research.py
Pulls congressional trades, insider purchases, government contracts,
and lobbying data from Quiver Quantitative REST API.
Scores signals against swing strategy v2.1 rules.
Writes quiver_signals.json to Google Drive folder.

Runs via GitHub Actions at 6:45pm ET every weekday.
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# ── Config ────────────────────────────────────────────────────────────────────
QUIVER_TOKEN = os.environ["QUIVER_API_KEY"]
DRIVE_FOLDER_ID = "1OHkZnvWHr13aAF7tq5dLph4Gs1wK7RlY"
QUIVER_BASE = "https://api.quiverquant.com/beta"

ASCHENBRENNER_LONGS = ["NBIS","SNDK","BE","CRWV","CORZ","IREN","APLD","RIOT","CLSK","SEI","BTDR"]
VIP_POLITICIANS = ["nancy pelosi","scott bessent","lutnick","wright","gabbard"]
KEY_COMMITTEES = ["armed services","intelligence","finance","science","technology","energy"]
OPEN_POSITIONS = ["AMZN","UBER"]

HEADERS = {"Authorization": f"Bearer {QUIVER_TOKEN}"}

today = datetime.now(timezone.utc).date()
thirty_days_ago = (today - timedelta(days=30)).isoformat()
fourteen_days_ago = (today - timedelta(days=14)).isoformat()
seven_days_ago = (today - timedelta(days=7)).isoformat()

# ── Quiver API calls ──────────────────────────────────────────────────────────

def get_congress_trades():
    url = f"{QUIVER_BASE}/live/congresstrading"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def get_insider_trades():
    url = f"{QUIVER_BASE}/live/insiders"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def get_lobbying():
    url = f"{QUIVER_BASE}/live/lobbying"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def get_contracts():
    url = f"{QUIVER_BASE}/live/governmentcontracts"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def get_trump_trades():
    url = f"{QUIVER_BASE}/live/trumptrades"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

# ── Signal processing ─────────────────────────────────────────────────────────

def process_congress(raw):
    buys = []
    sells = []

    for t in raw:
        ticker = t.get("ticker") or t.get("Ticker")
        if not ticker or ticker == "N/A":
            continue

        txn = (t.get("transaction") or t.get("Transaction") or "").lower()
        filed = t.get("filed") or t.get("Filed") or ""
        traded = t.get("traded") or t.get("Traded") or ""
        amount = t.get("amount") or t.get("Amount") or ""
        politician = (t.get("representative") or t.get("Representative") or "").lower()

        if filed < thirty_days_ago:
            continue

        if "purchase" in txn or "buy" in txn:
            is_vip = any(vip in politician for vip in VIP_POLITICIANS)
            is_meaningful = (
                "50,001" in amount or "100,001" in amount or "250,001" in amount
                or "500,001" in amount or "1,000,001" in amount
                or (is_vip and "15,000" in amount)
                or (is_vip and "1,001" in amount)
            )
            if not is_meaningful and not is_vip:
                continue

            buys.append({
                "ticker": ticker.upper(),
                "politician": t.get("representative") or t.get("Representative"),
                "chamber": t.get("chamber") or t.get("Chamber"),
                "party": t.get("party") or t.get("Party"),
                "amount": amount,
                "traded": traded,
                "filed": filed,
                "days_old": (today - datetime.fromisoformat(filed).date()).days if filed else 99,
                "is_vip": is_vip,
                "is_aschenbrenner": ticker.upper() in ASCHENBRENNER_LONGS
            })

        elif "sale" in txn or "sell" in txn:
            if ticker.upper() in OPEN_POSITIONS:
                sells.append({
                    "ticker": ticker.upper(),
                    "politician": t.get("representative") or t.get("Representative"),
                    "amount": amount,
                    "filed": filed,
                    "action": "EXIT_SIGNAL — congressional SELL on open position"
                })

    from collections import defaultdict
    ticker_buys = defaultdict(list)
    for b in buys:
        ticker_buys[b["ticker"]].append(b)

    clusters = []
    for ticker, entries in ticker_buys.items():
        if len(entries) >= 2:
            dates = [datetime.fromisoformat(e["filed"]).date() for e in entries if e.get("filed")]
            if dates and (max(dates) - min(dates)).days <= 7:
                clusters.append(ticker)

    for b in buys:
        b["is_cluster"] = b["ticker"] in clusters
        b["cluster_count"] = len(ticker_buys[b["ticker"]]) if b["ticker"] in clusters else 1

    return buys, sells

def process_insiders(raw):
    results = []
    c_suite_titles =
