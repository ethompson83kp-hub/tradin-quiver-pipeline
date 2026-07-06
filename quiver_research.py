"""
quiver_research.py — Swing Strategy v2.1
Field names confirmed from live API response.
Open positions loaded dynamically from open_positions.json in repo.
"""

import os, json, requests, urllib.request, base64
from datetime import datetime, timedelta, timezone

QUIVER_TOKEN = os.environ["QUIVER_API_KEY"]
GH_PAT = os.environ.get("GH_PAT", "")
QUIVER_BASE = "https://api.quiverquant.com/beta"
REPO = "ethompson83kp-hub/tradin-quiver-pipeline"
ASCHENBRENNER_LONGS = ["NBIS","SNDK","BE","CRWV","CORZ","IREN","APLD","RIOT","CLSK","SEI","BTDR"]
VIP_POLITICIANS = ["nancy pelosi","scott bessent","lutnick","wright","gabbard"]
HEADERS = {"Authorization": f"Bearer {QUIVER_TOKEN}"}
GH_HEADERS = {"Authorization": f"token {GH_PAT}", "Content-Type": "application/json"}

today = datetime.now(timezone.utc).date()
thirty_days_ago = (today - timedelta(days=30)).isoformat()

# Load open positions dynamically
try:
    with urllib.request.urlopen(
        f"https://raw.githubusercontent.com/{REPO}/main/open_positions.json"
    ) as f:
        OPEN_POSITIONS = json.load(f)
    print(f"  Open positions loaded: {OPEN_POSITIONS}")
except Exception as e:
    print(f"  Could not load open_positions.json: {e} — using empty list")
    OPEN_POSITIONS = []

def get(endpoint, params=None):
    r = requests.get(f"{QUIVER_BASE}/{endpoint}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def safe_get(endpoint, label, params=None):
    try:
        data = get(endpoint, params)
        print(f"  {label}: {len(data)} records")
        return data
    except Exception as e:
        print(f"  {label} unavailable: {e}")
        return []

def push_open_positions(positions):
    """Push current open positions list to GitHub repo."""
    if not GH_PAT:
        print("  GH_PAT not set — skipping open_positions.json push")
        return
    try:
        url = f"https://api.github.com/repos/{REPO}/contents/open_positions.json"
        # Get current SHA if file exists
        sha = None
        try:
            r = requests.get(url, headers=GH_HEADERS, timeout=10)
            if r.status_code == 200:
                sha = r.json().get("sha")
        except Exception:
            pass
        content = base64.b64encode(json.dumps(positions).encode()).decode()
        body = {
            "message": f"Update open positions {today.isoformat()}",
            "content": content
        }
        if sha:
            body["sha"] = sha
        r = requests.put(url, headers=GH_HEADERS, json=body, timeout=10)
        if r.status_code in (200, 201):
            print(f"  open_positions.json pushed: {positions}")
        else:
            print(f"  open_positions.json push failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"  open_positions.json push error: {e}")

def process_congress(raw):
    buys, sells = [], []
    print(f"  Raw congressional records: {len(raw)}")
    for t in raw:
        ticker = (t.get("Ticker") or "").strip().upper()
        if not ticker or ticker == "N/A":
            continue
        txn = (t.get("Transaction") or "").lower()
        report_date = (t.get("ReportDate") or "").strip()
        traded_date = (t.get("TransactionDate") or "").strip()
        amount_raw = t.get("Amount") or 0
        politician = (t.get("Representative") or "").lower()
        party = t.get("Party") or ""
        house = t.get("House") or ""
        if not report_date:
            continue
        try:
            report_dt = datetime.fromisoformat(report_date).date()
        except ValueError:
            continue
        if report_dt < datetime.fromisoformat(thirty_days_ago).date():
            continue
        is_vip = any(v in politician for v in VIP_POLITICIANS)
        try:
            amount_float = float(amount_raw)
        except (TypeError, ValueError):
            amount_float = 0
        if "purchase" in txn or "buy" in txn:
            buys.append({
                "ticker": ticker,
                "politician": t.get("Representative") or "",
                "chamber": house, "party": party,
                "amount": amount_raw, "amount_float": amount_float,
                "traded": traded_date, "filed": report_date,
                "days_old": (today - report_dt).days,
                "is_vip": is_vip,
                "is_aschenbrenner": ticker in ASCHENBRENNER_LONGS,
                "excess_return": t.get("ExcessReturn") or 0
            })
        elif ("sale" in txn or "sell" in txn) and ticker in OPEN_POSITIONS:
            sells.append({
                "ticker": ticker,
                "politician": t.get("Representative") or "",
                "amount": amount_raw, "filed": report_date,
                "action": "EXIT_SIGNAL — congressional SELL on open position"
            })
    print(f"  Congressional buys: {len(buys)}, exit signals: {len(sells)}")
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

def process_trump(raw):
    results = []
    for t in raw:
        ticker = (t.get("Ticker") or "").strip().upper()
        if not ticker or ticker == "N/A":
            continue
        traded = (t.get("Traded") or "").strip()
        filed = (t.get("Filed") or "").strip()
        if not traded:
            continue
        try:
            traded_date = datetime.fromisoformat(traded.split(" ")[0]).date()
        except ValueError:
            continue
        if traded_date < datetime.fromisoformat(thirty_days_ago).date():
            continue
        txn = (t.get("Transaction") or "").lower()
        if "purchase" in txn or "buy" in txn:
            results.append({
                "ticker": ticker, "traded": traded, "filed": filed,
                "amount": t.get("Amount") or "",
                "company": t.get("Company") or "",
                "excess_return": t.get("ExcessReturn") or 0
            })
    return results

def process_contracts(raw):
    results = []
    for t in raw:
        ticker = (t.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            amount = float(t.get("Amount") or 0)
        except (TypeError, ValueError):
            continue
        date = (t.get("Date") or t.get("date") or "").strip()
        if not date or date < thirty_days_ago:
            continue
        if amount >= 50000000:
            results.append({
                "ticker": ticker, "amount": amount,
                "amount_formatted": f"${amount/1e6:.1f}M",
                "date": date, "description": t.get("Description") or ""
            })
    return results

def process_lobbying(raw):
    results = []
    for t in raw:
        ticker = (t.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            amount = float(t.get("Amount") or 0)
        except (TypeError, ValueError):
            continue
        date = (t.get("Date") or t.get("date") or "").strip()
        if not date or date < thirty_days_ago:
            continue
        if amount >= 500000:
            results.append({
                "ticker": ticker, "amount": amount,
                "date": date, "client": t.get("Client") or ""
            })
    return results

def process_dark_pool(raw):
    results = []
    for t in raw:
        ticker = (t.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            dpi = float(t.get("DPI") or t.get("dpi") or 0)
            dp_total = float(t.get("dp_total") or 0)
        except (TypeError, ValueError):
            continue
        date = (t.get("Date") or t.get("date") or "").strip()
        if dpi >= 50 and dp_total >= 100000:
            results.append({"ticker": ticker, "dpi": dpi, "dp_total": dp_total, "date": date})
    return results

def build_signal_map(congress_buys, trump_trades, contracts, lobbying, dark_pool):
    from collections import defaultdict
    td = defaultdict(lambda: {
        "congress": [], "trump": [], "contracts": [],
        "lobbying": [], "dark_pool": [], "signals": []
    })
    for b in congress_buys:
        td[b["ticker"]]["congress"].append(b)
        td[b["ticker"]]["signals"].append(
            f"Congressional buy: {b['politician']} ${b['amount_float']:,.0f} (filed {b['filed']}, {b['days_old']}d ago)")
    for tr in trump_trades:
        td[tr["ticker"]]["trump"].append(tr)
        td[tr["ticker"]]["signals"].append(f"Trump trade: {tr['company']} {tr['amount']} (traded {tr['traded']})")
    for c in contracts:
        td[c["ticker"]]["contracts"].append(c)
        td[c["ticker"]]["signals"].append(f"Gov contract: {c['amount_formatted']} ({c['date']})")
    for l in lobbying:
        td[l["ticker"]]["lobbying"].append(l)
        td[l["ticker"]]["signals"].append(f"Lobbying: ${l['amount']/1e6:.1f}M ({l['date']})")
    for d in dark_pool:
        td[d["ticker"]]["dark_pool"].append(d)
        td[d["ticker"]]["signals"].append(f"Dark pool DPI: {d['dpi']:.1f}% ({d['date']})")
    results = []
    for ticker, data in td.items():
        datasets = sum([
            1 if data["congress"] else 0, 1 if data["trump"] else 0,
            1 if data["contracts"] else 0, 1 if data["lobbying"] else 0,
            1 if data["dark_pool"] else 0
        ])
        ce = data["congress"]
        is_cluster = any(b.get("is_cluster") for b in ce)
        is_vip = any(b.get("is_vip") for b in ce)
        is_asch = ticker in ASCHENBRENNER_LONGS
        has_trump = bool(data["trump"])
        if datasets >= 3:
            tier, label = 1, "TOP SIGNAL"
        elif datasets == 2:
            tier, label = 2, "DOUBLE SIGNAL"
        elif is_cluster:
            tier, label = 3, "CONGRESSIONAL CLUSTER"
        elif (is_vip or has_trump) and ce:
            tier, label = 4, "VIP CONGRESSIONAL BUY"
        elif ce:
            tier, label = 4, "CONGRESSIONAL BUY"
        elif data["trump"]:
            tier, label = 4, "TRUMP TRADE"
        elif data["contracts"] or data["lobbying"]:
            tier, label = 6, "GOV/LOBBYING ONLY"
        elif data["dark_pool"]:
            tier, label = 6, "DARK POOL ONLY"
        else:
            tier, label = 7, "WEAK SIGNAL"
        if is_asch and tier > 2:
            tier = 2
            label = f"ASCHENBRENNER + {label}"
        tier_score = {1: 30, 2: 25, 3: 20, 4: 15, 6: 5, 7: 2}.get(tier, 2)
        dataset_bonus = min(datasets * 5, 15)
        freshness = 5 if any(b.get("days_old", 99) <= 14 for b in ce) else 2 if ce else 0
        trump_bonus = 3 if has_trump else 0
        results.append({
            "ticker": ticker, "tier": tier, "tier_label": label,
            "datasets": datasets,
            "quiver_score": tier_score + dataset_bonus + freshness + trump_bonus,
            "is_cluster": is_cluster, "is_vip": is_vip,
            "is_aschenbrenner": is_asch, "has_trump_signal": has_trump,
            "signals": data["signals"], "congress_entries": ce,
            "trump_entries": data["trump"], "contract_entries": data["contracts"],
            "lobbying_entries": data["lobbying"], "dark_pool_entries": data["dark_pool"]
        })
    results.sort(key=lambda x: (x["tier"], -x["quiver_score"]))
    return results

def main():
    print(f"Running Quiver pipeline — {today}")
    print(f"Watching for SELL signals on: {OPEN_POSITIONS}")

    print("Fetching congressional trades...")
    congress_raw = get("live/congresstrading")
    print(f"  Got {len(congress_raw)} raw records")

    trump_raw = safe_get("bulk/trumpstocktrades", "Trump trades")
    contracts_raw = safe_get("live/govcontractsall", "Gov contracts")
    lobbying_raw = safe_get("live/lobbying", "Lobbying")
    dark_pool_raw = safe_get("live/offexchange", "Dark pool")

    congress_buys, exit_signals = process_congress(congress_raw)
    trump_trades = process_trump(trump_raw)
    contracts = process_contracts(contracts_raw)
    lobbying = process_lobbying(lobbying_raw)
    dark_pool = process_dark_pool(dark_pool_raw)
    signals = build_signal_map(congress_buys, trump_trades, contracts, lobbying, dark_pool)

    output = {
        "generated": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "open_positions_watched": OPEN_POSITIONS,
        "exit_signals": exit_signals,
        "top_signals": [s for s in signals if s["tier"] == 1],
        "double_signals": [s for s in signals if s["tier"] == 2],
        "cluster_signals": [s for s in signals if s["tier"] == 3],
        "vip_congressional": [s for s in signals if s["tier"] == 4],
        "all_signals_ranked": signals,
        "summary": {
            "total_tickers_flagged": len(signals),
            "top_signals": len([s for s in signals if s["tier"] == 1]),
            "double_signals": len([s for s in signals if s["tier"] == 2]),
            "clusters": len([s for s in signals if s["is_cluster"]]),
            "vip_buys": len([s for s in signals if s["is_vip"]]),
            "trump_signals": len([s for s in signals if s["has_trump_signal"]]),
            "exit_signals": len(exit_signals),
            "congressional_buys": len(congress_buys),
            "trump_trades": len(trump_trades),
            "contracts": len(contracts),
            "lobbying": len(lobbying),
            "dark_pool_flags": len(dark_pool)
        }
    }

    print(f"Summary: {output['summary']}")
    with open("quiver_signals.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    print("Done — quiver_signals.json written.")

if __name__ == "__main__":
    main()
