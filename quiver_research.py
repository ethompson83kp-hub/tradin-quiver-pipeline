"""
quiver_research.py — Swing Strategy v2.1
Pulls Quiver data, scores signals, writes quiver_signals.json to Google Drive.
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
OPEN_POSITIONS = ["AMZN","UBER"]

HEADERS = {"Authorization": f"Bearer {QUIVER_TOKEN}"}

today = datetime.now(timezone.utc).date()
thirty_days_ago = (today - timedelta(days=30)).isoformat()
fourteen_days_ago = (today - timedelta(days=14)).isoformat()

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
    print(f"  Raw congressional records: {len(raw)}")

    for t in raw:
        ticker = t.get("Ticker") or t.get("ticker") or ""
        ticker = ticker.strip().upper()
        if not ticker or ticker in ("N/A", ""):
            continue

        txn = (t.get("Transaction") or t.get("transaction") or "").lower().strip()
        filed = (t.get("Filed") or t.get("filed") or "").strip()
        traded = (t.get("Traded") or t.get("traded") or "").strip()
        amount = (t.get("Amount") or t.get("amount") or "").strip()
        politician = (t.get("Representative") or t.get("representative") or "").lower().strip()

        if not filed:
            continue
        try:
            filed_date = datetime.fromisoformat(filed).date()
        except ValueError:
            continue
        if filed_date < datetime.fromisoformat(thirty_days_ago).date():
            continue

        is_vip = any(vip in politician for vip in VIP_POLITICIANS)

        if "purchase" in txn or "buy" in txn:
            buys.append({
                "ticker": ticker,
                "politician": t.get("Representative") or t.get("representative") or "",
                "chamber": t.get("Chamber") or t.get("chamber") or "",
                "party": t.get("Party") or t.get("party") or "",
                "amount": amount,
                "traded": traded,
                "filed": filed,
                "days_old": (today - filed_date).days,
                "is_vip": is_vip,
                "is_aschenbrenner": ticker in ASCHENBRENNER_LONGS
            })

        elif "sale" in txn or "sell" in txn:
            if ticker in OPEN_POSITIONS:
                sells.append({
                    "ticker": ticker,
                    "politician": t.get("Representative") or t.get("representative") or "",
                    "amount": amount,
                    "filed": filed,
                    "action": "EXIT_SIGNAL — congressional SELL on open position"
                })

    print(f"  Congressional buys after filter: {len(buys)}, sells: {len(sells)}")

    from collections import defaultdict
    ticker_buys = defaultdict(list)
    for b in buys:
        ticker_buys[b["ticker"]].append(b)

    clusters = []
    for ticker, entries in ticker_buys.items():
        if len(entries) >= 2:
            try:
                dates = [datetime.fromisoformat(e["filed"]).date() for e in entries if e.get("filed")]
                if dates and (max(dates) - min(dates)).days <= 7:
                    clusters.append(ticker)
            except Exception:
                pass

    for b in buys:
        b["is_cluster"] = b["ticker"] in clusters
        b["cluster_count"] = len(ticker_buys[b["ticker"]]) if b["ticker"] in clusters else 1

    return buys, sells

def process_insiders(raw):
    results = []
    c_suite_titles = ["chief executive","chief financial","chief operating","president","ceo","cfo","coo"]

    for t in raw:
        ticker = (t.get("ticker") or "").strip().upper()
        if not ticker or ticker == "N/A":
            continue

        txn_code = t.get("transaction_type") or t.get("TransactionCode") or ""
        if txn_code != "P":
            continue

        date = (t.get("date") or t.get("Date") or "").strip()
        if not date or date < fourteen_days_ago:
            continue

        try:
            shares = float(t.get("shares") or 0)
            price = float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue

        dollar_value = shares * price
        title = (t.get("officer_title") or "").lower()
        is_director = bool(t.get("is_director"))
        is_csuite = any(c in title for c in c_suite_titles)
        qualifies = (is_csuite and dollar_value >= 25000) or (is_director and dollar_value >= 100000)

        if not qualifies:
            continue

        results.append({
            "ticker": ticker,
            "owner": t.get("owner") or "",
            "title": t.get("officer_title") or "",
            "shares": shares,
            "price": price,
            "dollar_value": round(dollar_value, 2),
            "date": date,
            "is_csuite": is_csuite,
            "is_director": is_director
        })

    return results

def process_lobbying(raw):
    results = []
    for t in raw:
        ticker = (t.get("Ticker") or t.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            amount = float(t.get("Amount") or t.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        date = (t.get("Date") or t.get("date") or "").strip()
        if not date or date < thirty_days_ago:
            continue
        if amount >= 500000:
            results.append({
                "ticker": ticker,
                "amount": amount,
                "date": date,
                "client": t.get("Client") or t.get("client") or ""
            })
    return results

def process_contracts(raw):
    results = []
    for t in raw:
        ticker = (t.get("Ticker") or t.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            amount = float(t.get("Amount") or t.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        date = (t.get("Date") or t.get("date") or "").strip()
        if not date or date < thirty_days_ago:
            continue
        if amount >= 50000000:
            results.append({
                "ticker": ticker,
                "amount": amount,
                "amount_formatted": f"${amount/1e6:.1f}M",
                "date": date,
                "description": t.get("Description") or t.get("description") or ""
            })
    return results

def process_trump_trades(raw):
    results = []
    for t in raw:
        ticker = (t.get("Ticker") or t.get("ticker") or "").strip().upper()
        if not ticker or ticker == "N/A":
            continue
        date = (t.get("Date") or t.get("date") or "").strip()
        if not date or date < thirty_days_ago:
            continue
        txn = (t.get("Transaction") or t.get("transaction") or "").lower()
        if "purchase" in txn or "buy" in txn:
            results.append({
                "ticker": ticker,
                "date": date,
                "amount": t.get("Amount") or t.get("amount") or "",
                "entity": t.get("Entity") or t.get("entity") or ""
            })
    return results

# ── Signal map ────────────────────────────────────────────────────────────────

def build_signal_map(congress_buys, insiders, lobbying, contracts, trump_trades):
    from collections import defaultdict
    ticker_data = defaultdict(lambda: {
        "congress": [], "insider": [], "lobbying": [],
        "contracts": [], "trump": [], "signals": []
    })

    for b in congress_buys:
        t = b["ticker"]
        ticker_data[t]["congress"].append(b)
        ticker_data[t]["signals"].append(
            f"Congressional buy: {b['politician']} {b['amount']} (filed {b['filed']}, {b['days_old']}d ago)"
        )

    for i in insiders:
        t = i["ticker"]
        ticker_data[t]["insider"].append(i)
        ticker_data[t]["signals"].append(
            f"Insider buy: {i['owner']} ({i['title']}) ${i['dollar_value']:,.0f}"
        )

    for l in lobbying:
        t = l["ticker"]
        ticker_data[t]["lobbying"].append(l)
        ticker_data[t]["signals"].append(f"Lobbying: ${l['amount']/1e6:.1f}M ({l['date']})")

    for c in contracts:
        t = c["ticker"]
        ticker_data[t]["contracts"].append(c)
        ticker_data[t]["signals"].append(f"Gov contract: {c['amount_formatted']} ({c['date']})")

    for tr in trump_trades:
        t = tr["ticker"]
        ticker_data[t]["trump"].append(tr)
        ticker_data[t]["signals"].append(f"Trump trade: {tr['entity']} {tr['amount']} ({tr['date']})")

    results = []
    for ticker, data in ticker_data.items():
        datasets = sum([
            1 if data["congress"] else 0,
            1 if data["insider"] else 0,
            1 if data["lobbying"] else 0,
            1 if data["contracts"] else 0,
            1 if data["trump"] else 0
        ])

        congress_entries = data["congress"]
        is_cluster = any(b.get("is_cluster") for b in congress_entries)
        is_vip = any(b.get("is_vip") for b in congress_entries)
        is_aschenbrenner = ticker in ASCHENBRENNER_LONGS
        has_trump = bool(data["trump"])

        if datasets >= 3:
            tier, tier_label = 1, "TOP SIGNAL"
        elif datasets == 2:
            tier, tier_label = 2, "DOUBLE SIGNAL"
        elif is_cluster:
            tier, tier_label = 3, "CONGRESSIONAL CLUSTER"
        elif (is_vip or has_trump) and congress_entries:
            tier, tier_label = 4, "VIP CONGRESSIONAL BUY"
        elif congress_entries:
            tier, tier_label = 4, "CONGRESSIONAL BUY"
        elif data["insider"]:
            tier, tier_label = 5, "INSIDER BUY"
        else:
            tier, tier_label = 6, "LOBBYING/CONTRACT ONLY"

        if is_aschenbrenner and tier > 2:
            tier = 2
            tier_label = f"ASCHENBRENNER + {tier_label}"

        tier_score = {1: 30, 2: 25, 3: 20, 4: 15, 5: 10, 6: 5}.get(tier, 5)
        dataset_bonus = min(datasets * 5, 15)
        freshness = 5 if any(b.get("days_old", 99) <= 14 for b in congress_entries) else 2 if congress_entries else 0
        trump_bonus = 5 if has_trump else 0
        quiver_score = tier_score + dataset_bonus + freshness + trump_bonus

        results.append({
            "ticker": ticker,
            "tier": tier,
            "tier_label": tier_label,
            "datasets": datasets,
            "quiver_score": quiver_score,
            "is_cluster": is_cluster,
            "is_vip": is_vip,
            "is_aschenbrenner": is_aschenbrenner,
            "has_trump_signal": has_trump,
            "signals": data["signals"],
            "congress_entries": congress_entries,
            "insider_entries": data["insider"],
            "lobbying_entries": data["lobbying"],
            "contract_entries": data["contracts"],
            "trump_entries": data["trump"]
        })

    results.sort(key=lambda x: (x["tier"], -x["quiver_score"]))
    return results

# ── Google Drive write ────────────────────────────────────────────────────────

def write_to_drive(data: dict):
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds_dict = json.loads(creds_json)
    delegated_email = os.environ.get("GOOGLE_DELEGATED_EMAIL", "")

    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )

    if delegated_email:
        creds = creds.with_subject(delegated_email)

    service = build("drive", "v3", credentials=creds)

    try:
        existing = service.files().list(
            q=f"name='quiver_signals.json' and '{DRIVE_FOLDER_ID}' in parents",
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        for f in existing.get("files", []):
            service.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
            print(f"  Deleted old file: {f['id']}")
    except Exception as e:
        print(f"  Warning: could not delete old files: {e}")

    content = json.dumps(data, indent=2, default=str).encode("utf-8")
    media = MediaInMemoryUpload(content, mimetype="application/json")
    file_metadata = {
        "name": "quiver_signals.json",
        "parents": [DRIVE_FOLDER_ID],
        "mimeType": "application/json"
    }
    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,createdTime",
        supportsAllDrives=True
    ).execute()

    return result

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Running Quiver research pipeline — {today}")

    print("Fetching congressional trades...")
    congress_raw = get_congress_trades()
    print(f"  Got {len(congress_raw)} raw records")

    print("Fetching insider trades...")
    try:
        insider_raw = get_insider_trades()
        print(f"  Got {len(insider_raw)} insider records")
    except Exception as e:
        print(f"  Insider trades unavailable: {e}")
        insider_raw = []

    print("Fetching lobbying data...")
    try:
        lobbying_raw = get_lobbying()
        print(f"  Got {len(lobbying_raw)} lobbying records")
    except Exception as e:
        print(f"  Lobbying unavailable: {e}")
        lobbying_raw = []

    print("Fetching government contracts...")
    try:
        contracts_raw = get_contracts()
        print(f"  Got {len(contracts_raw)} contract records")
    except Exception as e:
        print(f"  Contracts unavailable: {e}")
        contracts_raw = []

    print("Fetching Trump trades...")
    try:
        trump_raw = get_trump_trades()
        print(f"  Got {len(trump_raw)} Trump trade records")
    except Exception as e:
        print(f"  Trump trades unavailable: {e}")
        trump_raw = []

    print("Processing signals...")
    congress_buys, exit_signals = process_congress(congress_raw)
    insiders = process_insiders(insider_raw)
    lobbying = process_lobbying(lobbying_raw)
    contracts = process_contracts(contracts_raw)
    trump_trades_processed = process_trump_trades(trump_raw)

    print("Building signal map...")
    signals = build_signal_map(congress_buys, insiders, lobbying, contracts, trump_trades_processed)

    endpoints_available = {
        "congressional_trades": True,
        "insider_trades": len(insider_raw) > 0,
        "lobbying": len(lobbying_raw) > 0,
        "government_contracts": len(contracts_raw) > 0,
        "trump_trades": len(trump_raw) > 0
    }

    output = {
        "generated": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoints_available": endpoints_available,
        "exit_signals": exit_signals,
        "top_signals": [s for s in signals if s["tier"] == 1],
        "double_signals": [s for s in signals if s["tier"] == 2],
        "cluster_signals": [s for s in signals if s["tier"] == 3],
        "vip_congressional": [s for s in signals if s["tier"] == 4],
        "insider_only": [s for s in signals if s["tier"] == 5],
        "all_signals_ranked": signals,
        "summary": {
            "total_tickers_flagged": len(signals),
            "top_signals": len([s for s in signals if s["tier"] == 1]),
            "double_signals": len([s for s in signals if s["tier"] == 2]),
            "clusters": len([s for s in signals if s["is_cluster"]]),
            "vip_buys": len([s for s in signals if s["is_vip"]]),
            "exit_signals": len(exit_signals),
            "congressional_buys_processed": len(congress_buys),
            "insider_buys_processed": len(insiders),
            "lobbying_processed": len(lobbying),
            "trump_trades_processed": len(trump_trades_processed)
        }
    }

    print(f"Signals: {output['summary']}")
    print(f"Endpoints: {endpoints_available}")
    print("Writing to Google Drive...")
    result = write_to_drive(output)
    print(f"Written: {result['name']} ({result['id']})")
    print("Done.")

if __name__ == "__main__":
    main()
