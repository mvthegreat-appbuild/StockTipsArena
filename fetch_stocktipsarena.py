import json
import re
import datetime
from playwright.sync_api import sync_playwright

SKIP = {"BUY", "SELL", "SWING", "TARGET", "ENTRY", "CMP", "SL", "HIT",
        "STOP", "LOSS", "ACTIVE", "NSE", "BSE", "BY", "ALL", "NEW"}


def main():
    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 2400})

        def handle_response(resp):
            try:
                ctype = resp.headers.get("content-type") or ""
                if "json" in ctype and resp.status == 200:
                    captured.append(resp.json())
            except Exception:
                pass

        page.on("response", handle_response)

        for url in ["https://stocktipsarena.com/discover", "https://stocktipsarena.com/"]:
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(2500)
                for label in ["Swing", "Swing (≤2wk)"]:
                    btn = page.get_by_text(label, exact=False).first
                    if btn.count():
                        btn.click(timeout=2000)
                        page.wait_for_timeout(2000)
                for _ in range(15):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(600)
            except Exception as e:
                print("Navigation note:", url, e)

        trades = []
        seen = set()

        def walk(obj):
            if isinstance(obj, list):
                for item in obj:
                    walk(item)
            elif isinstance(obj, dict):
                sym = obj.get("symbol") or obj.get("ticker") or obj.get("stock_symbol")
                entry = obj.get("entry") or obj.get("entry_price") or obj.get("buy_price")
                target = obj.get("target") or obj.get("target_price")
                horizon = str(obj.get("horizon") or obj.get("timeframe") or obj.get("category") or "")
                if sym and (entry or target) and ("swing" in horizon.lower() or "2wk" in horizon.lower() or not horizon):
                    key = f"{sym}-{obj.get('id', '')}-{entry}-{target}"
                    if key not in seen:
                        seen.add(key)
                        st_raw = str(obj.get("status") or obj.get("outcome") or "").lower()
                        if "target" in st_raw or "hit" in st_raw:
                            st = "target"
                        elif "sl" in st_raw or "stop" in st_raw:
                            st = "sl"
                        else:
                            st = "active"
                        sl_val = obj.get("sl") or obj.get("stop_loss")
                        cmp_val = obj.get("cmp") or obj.get("ltp")
                        try:
                            trades.append({
                                "id": f"sta-{len(trades) + 1}",
                                "symbol": str(sym).upper().replace(".NS", ""),
                                "name": str(obj.get("company_name") or obj.get("name") or sym),
                                "advisor": str(obj.get("advisor_name") or obj.get("advisor") or "SEBI Advisor"),
                                "source": str(obj.get("source") or obj.get("channel") or "StockTipsArena"),
                                "side": "SELL" if "sell" in str(obj.get("side") or obj.get("action") or "").lower() else "BUY",
                                "horizon": horizon or "Swing (≤2wk)",
                                "date": str(obj.get("date") or obj.get("created_at") or ""),
                                "entry": float(entry) if entry else None,
                                "sl": float(sl_val) if sl_val else None,
                                "target": float(target) if target else None,
                                "cmp": float(cmp_val) if cmp_val else None,
                                "status": st,
                                "note": str(obj.get("note") or obj.get("rationale") or ""),
                                "link": f"https://stocktipsarena.com/tip/{obj['slug']}" if obj.get("slug") else "https://stocktipsarena.com/discover"
                            })
                        except Exception:
                            pass
                for v in obj.values():
                    walk(v)

        walk(captured)

        if len(trades) < 10:
            cards = page.locator("a[href*='/tip/'], article, [class*='card']").all()
            for c in cards:
                try:
                    txt = c.inner_text().strip()
                except Exception:
                    continue
                if "swing" not in txt.lower():
                    continue
                href = c.get_attribute("href") or ""
                if not href:
                    sub_a = c.locator("a[href*='/tip/']").first
                    if sub_a.count():
                        href = sub_a.get_attribute("href") or ""
                sym = next((w for w in re.findall(r"\b[A-Z]{2,15}\b", txt) if w not in SKIP), None)
                m_ent = re.search(r"(?:Entry|Buy|Sell|CMP)[^\d]{0,12}₹?\s*([0-9,]+(?:\.[0-9]+)?)", txt, re.I)
                m_tgt = re.search(r"Target[^\d]{0,12}₹?\s*([0-9,]+(?:\.[0-9]+)?)", txt, re.I)
                m_sl = re.search(r"(?:SL|Stop\s*Loss)[^\d]{0,12}₹?\s*([0-9,]+(?:\.[0-9]+)?)", txt, re.I)
                if sym and (m_ent or m_tgt):
                    key = f"{sym}-{href or txt[:40]}"
                    if key not in seen:
                        seen.add(key)
                        lines = [l.strip() for l in txt.splitlines() if l.strip()]
                        date_match = None
                        for l in lines:
                            date_match = re.search(r"\d{1,2}\s+[A-Za-z]{3,9}\s+202\d", l)
                            if date_match:
                                break
                        if re.search(r"target\s*hit", txt, re.I):
                            st = "target"
                        elif re.search(r"stop\s*loss|closed\s*at", txt, re.I):
                            st = "sl"
                        else:
                            st = "active"
                        trades.append({
                            "id": f"sta-{len(trades) + 1}",
                            "symbol": sym,
                            "name": lines[0] if lines else sym,
                            "advisor": next((l for l in lines if "by " in l.lower()), "SEBI Advisor").replace("By ", ""),
                            "source": "StockTipsArena",
                            "side": "SELL" if re.search(r"\bSELL\b", txt) else "BUY",
                            "horizon": "Swing (≤2wk)",
                            "date": date_match.group(0) if date_match else "",
                            "entry": float(m_ent.group(1).replace(",", "")) if m_ent else None,
                            "sl": float(m_sl.group(1).replace(",", "")) if m_sl else None,
                            "target": float(m_tgt.group(1).replace(",", "")) if m_tgt else None,
                            "cmp": None,
                            "status": st,
                            "note": "",
                            "link": ("https://stocktipsarena.com" + href) if href.startswith("/") else (href or "https://stocktipsarena.com/discover")
                        })

        browser.close()

    print("Total swing trades extracted:", len(trades))
    with open("stocktipsarena_swing.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total": len(trades),
            "trades": trades
        }, f, indent=2)


if __name__ == "__main__":
    main()
