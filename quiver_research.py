"""
quiver_research.py — Swing Strategy v2.1
"""
import os, json, requests
from datetime import datetime, timedelta, timezone

QUIVER_TOKEN = os.environ["QUIVER_API_KEY"]
QUIVER_BASE = "https://api.quiverquant.com/beta"
ASCHENBRENNER_LONGS = ["NBIS","SNDK","BE","CRWV","CORZ","IREN","APLD","RIOT","CLSK","SEI","BTDR"]
VIP_POLITICIANS = ["nancy pelosi","scott bessent","lutnick","wright","gabbard"]
OPEN_POSITIONS = ["AMZN","UBER"]
HEADERS = {"Authorization": f"Bearer {QUIVER_TOKEN}"}
today = datetime.now(timezone.utc).date()
thirty_days_ago = (today - timedelta(days=30)).isoformat()

def get(endpoint):
    r = requests.get(f"{QUIVER_BASE}/{endpoint}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def process_congress(raw):
    buys, sells = [], []
    print(f"  Raw records: {len(raw)}")
    for t in raw:
        ticker = (t.get("Ticker") or t.get("ticker") or "").strip().upper()
        if not ticker or ticker == "N/A":
            continue
        txn = (t.get("Transaction") or t.get("transaction") or "").lower()
        filed = (t.get("Filed") or t.get("filed") or "").strip()
        amount = (t.get("Amount") or t.get("amount") or "").strip()
        traded = (t.get("Traded") or t.get("traded") or "").strip()
        politician = (t.get("Representative") or t.get("representative") or "").lower()
        if not filed:
            continue
        try:
            filed_date = datetime.fromisoformat(filed).date()
        except ValueError:
            continue
        if filed_date < datetime.fromisoformat(thirty_days_ago).date():
            continue
        is_vip = any(v in politician for v in VIP_POLITICIANS)
        if "purchase" in txn or "buy" in txn:
            buys.append({
                "ticker": ticker,
                "politician": t.get("Representative") or t.get("representative") or "",
                "chamber": t.get("Chamber") or t.get("chamber") or "",
                "party": t.get("Party") or t.get("party") or "",
                "amount": amount, "traded": traded, "filed": filed,
                "days_old": (today - filed_date).days,
                "is_vip": is_vip,
                "is_aschenbrenner": ticker in ASCHENBRENNER_LONGS
            })
        elif ("sale" in txn or "sell" in txn) and ticker in OPEN_POSITIONS:
            sells.append({
                "ticker": ticker,
                "politician": t.get("Representative") or t.get("representative") or "",
                "amount": amount, "filed": filed,
                "action": "EXIT_SIGNAL — congressional SELL on open position"
            })
    print(f"  Buys: {len(buys)}, Sells: {len(sells)}")
    from collections import defaultdict
    tb = defaultdict(list)
    for b in buys:
        tb[b["ticker"]].append(b)
    clusters = []
    for k, v in tb.items():
        if len(v) >= 2:
            try:
                dates = [datetime.fromisoformat(e["filed"]).date() for e in v]
                if (max(dates) - min(dates)).days <= 7:
                    clusters.append(k)
            except Exception:
                pass
    for b in buys:
        b["is_cluster"] = b["ticker"] in clusters
        b["cluster_count"] = len(tb[b["ticker"]]) if b["ticker"] in clusters else 1
    return buys, sells

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
            results.append({"ticker": ticker, "amount": amount, "date": date,
                           "client": t.get("Client") or t.get("client") or ""})
    return results

def build_signal_map(congress_buys, lobbying):
    from collections import defaultdict
    td = defaultdict(lambda: {"congress": [], "lobbying": [], "signals": []})
    for b in congress_buys:
        td[b["ticker"]]["congress"].append(b)
        td[b["ticker"]]["signals"].append(
            f"Congressional buy: {b['politician']} {b['amount']} (filed {b['filed']}, {b['days_old']}d ago)")
    for l in lobbying:
        td[l["ticker"]]["lobbying"].append(l)
        td[l["ticker"]]["signals"].append(f"Lobbying: ${l['amount']/1e6:.1f}M ({l['date']})")
    results = []
    for ticker, data in td.items():
        datasets = sum([1 if data["congress"] else 0, 1 if data["lobbying"] else 0])
        ce = data["congress"]
        is_cluster = any(b.get("is_cluster") for b in ce)
        is_vip = any(b.get("is_vip") for b in ce)
        is_asch = ticker in ASCHENBRENNER_LONGS
        if datasets >= 2:
            tier, label = 2, "DOUBLE SIGNAL"
        elif is_cluster:
            tier, label = 3, "CONGRESSIONAL CLUSTER"
        elif is_vip and ce:
            tier, label = 4, "VIP CONGRESSIONAL BUY"
        elif ce:
            tier, label = 4, "CONGRESSIONAL BUY"
        else:
            tier, label = 6, "LOBBYING ONLY"
        if is_asch and tier > 2:
            tier, label = 2, f"ASCHENBRENNER + {label}"
        tier_score = {2: 25, 3: 20, 4: 15, 6: 5}.get(tier, 5)
        dataset_bonus = min(datasets * 5, 15)
        freshness = 5 if any(b.get("days_old", 99) <= 14 for b in ce) else 2 if ce else 0
        results.append({
            "ticker": ticker, "tier": tier, "tier_label": label,
            "datasets": datasets, "quiver_score": tier_score + dataset_bonus + freshness,
            "is_cluster": is_cluster, "is_vip": is_vip, "is_aschenbrenner": is_asch,
            "signals": data["signals"], "congress_entries": ce,
            "lobbying_entries": data["lobbying"]
        })
    results.sort(key=lambda x: (x["tier"], -x["quiver_score"]))
    return results

def main():
    print(f"Running Quiver pipeline — {today}")
    print("Fetching congressional trades...")
    congress_raw = get("live/congresstrading")
    print(f"  Got {len(congress_raw)} records")
    lobbying_raw = []
    try:
        print("Fetching lobbying...")
        lobbying_raw = get("live/lobbying")
        print(f"  Got {len(lobbying_raw)} records")
    except Exception as e:
        print(f"  Unavailable: {e}")
    congress_buys, exit_signals = process_congress(congress_raw)
    lobbying = process_lobbying(lobbying_raw)
    signals = build_signal_map(congress_buys, lobbying)
    output = {
        "generated": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exit_signals": exit_signals,
        "double_signals": [s for s in signals if s["tier"] == 2],
        "cluster_signals": [s for s in signals if s["tier"] == 3],
        "vip_congressional": [s for s in signals if s["tier"] == 4],
        "all_signals_ranked": signals,
        "summary": {
            "total_tickers_flagged": len(signals),
            "double_signals": len([s for s in signals if s["tier"] == 2]),
            "clusters": len([s for s in signals if s["is_cluster"]]),
            "vip_buys": len([s for s in signals if s["is_vip"]]),
            "exit_signals": len(exit_signals),
            "congressional_buys_processed": len(congress_buys),
            "lobbying_processed": len(lobbying)
        }
    }
    print(f"Summary: {output['summary']}")
    with open("quiver_signals.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    print("Written: quiver_signals.json")

if __name__ == "__main__":
    main()
