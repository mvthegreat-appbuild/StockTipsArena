import json
import os
import re
import datetime
from playwright.sync_api import sync_playwright

OUT_FILE = "stocktipsarena_swing.json"
MAX_AGE_DAYS = 21
MAX_ARTICLES = 40

LIST_PAGES = [
    "https://www.business-standard.com/topic/stocks-to-buy-today",
    "https://www.business-standard.com/topic/stocks-to-buy-today/page-2",
]

FIRMS = [
    "Motilal Oswal", "Anand Rathi", "Religare", "Kotak Securities",
    "ICICI Securities", "ICICI Direct", "HDFC Securities", "Axis Securities",
    "Sharekhan", "Angel One", "Choice Broking", "Geojit", "Nirmal Bang",
    "SBI Securities", "Prabhudas Lilladher", "Emkay", "Nuvama", "JM Financial",
    "Mirae Asset", "Tradebulls", "Bonanza", "LKP Securities", "Systematix",
    "Morgan Stanley", "Jefferies", "Goldman Sachs", "Nomura", "CLSA",
    "Kotak Institutional", "Ventura", "Arihant", "Centrum", "Elara",
]

KEYWORDS = ["stock to buy", "stocks to buy", "recommend", "buy", "target"]

CALL_RE = re.compile(
    r"(Buy|Sell)\s+([A-Za-z0-9&.\- ]{2,40}?)\s*[,|]?\s*(?:at\s+)?CMP\s*[:\-]?\s*(?:Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)"
    r"\s*[,|]?\s*Stop[\s\-]*loss\s*[:\-]?\s*(?:Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)"
    r"\s*[,|]?\s*(?:Share price\s*)?Target(?:\s*price)?\s*[:\-]?\s*(?:Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)",
    re.I,
)


def num(s):
    return float(s.replace(",", ""))


def date_from_url(url):
    m = re.search(r"-1(\d{2})(\d{2})(\d{2})\d{5}_1", url)
    if not m:
        return None
    try:
        return datetime.date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def find_firm(title, body):
    hay = (title + " " + body[:2500]).lower()
    for f in FIRMS:
        if f.lower() in hay:
            return f
    return "Brokerage"


def main():
    today = datetime.date.today()
    trades = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            viewport={"width": 1280, "height": 2000},
        )
        page = ctx.new_page()

        links = {}
        for lp in LIST_PAGES:
            try:
                page.goto(lp, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                for _ in range(4):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(500)
                items = page.eval_on_selector_all(
                    "a[href]",
                    "els=>els.map(e=>({h:e.href,t:(e.innerText||'').trim()}))",
                )
                for it in items:
                    h = it["h"].split("?")[0].split("#")[0]
                    t = it["t"]
                    if "business-standard.com" in h and "/markets/news/" in h and h.endswith(".html"):
                        if any(k in t.lower() for k in KEYWORDS) or any(k in h for k in ["stocks-to-buy", "recommend"]):
                            links[h.replace("/amp/", "/")] = t
            except Exception as e:
                print("List page problem:", lp, e)

        print("Candidate articles found:", len(links))

        picked = []
        for h, t in links.items():
            d = date_from_url(h)
            if d and (today - d).days > MAX_AGE_DAYS:
                continue
            picked.append((d or today, h, t))
        picked.sort(key=lambda x: x[0], reverse=True)
        picked = picked[:MAX_ARTICLES]
        print("Recent articles to read:", len(picked))

        for d, h, t in picked:
            try:
                page.goto(h, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                body = page.inner_text("body")
                title = t or page.title()
                firm = find_firm(title, body)
                for m in CALL_RE.finditer(body):
                    side = "SELL" if m.group(1).lower() == "sell" else "BUY"
                    name = re.sub(r"\s+", " ", m.group(2)).strip(" ,|-")
                    if len(name) < 2:
                        continue
                    entry, sl, tgt = num(m.group(3)), num(m.group(4)), num(m.group(5))
                    key = (firm, name.upper(), d.isoformat(), tgt)
                    if key in seen:
                        continue
                    seen.add(key)
                    trades.append({
                        "id": f"bp-{len(trades) + 1}",
                        "symbol": name.upper(),
                        "name": name,
                        "advisor": firm,
                        "source": "Business Standard",
                        "side": side,
                        "horizon": "Short term",
                        "date": d.strftime("%d %b %Y"),
                        "sortdate": d.isoformat(),
                        "entry": entry,
                        "sl": sl,
                        "target": tgt,
                        "cmp": None,
                        "status": "active",
                        "note": "",
                        "link": h,
                    })
            except Exception as e:
                print("Article problem:", h, e)

        browser.close()

    trades.sort(key=lambda x: x["sortdate"], reverse=True)
    print("Total picks extracted:", len(trades))

    if not trades and os.path.exists(OUT_FILE):
        print("Nothing found; keeping the previous file.")
        return

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total": len(trades),
            "trades": trades,
        }, f, indent=2)


if __name__ == "__main__":
    main()
