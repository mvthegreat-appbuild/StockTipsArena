import json
import os
import re
import html
import datetime
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse
from playwright.sync_api import sync_playwright

OUT_FILE = "stocktipsarena_swing.json"
MAX_AGE_DAYS = 21
MAX_ARTICLES = 60

FEEDS = [
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://www.livemint.com/rss/markets",
]

GOOGLE_QUERIES = [
    "Motilal Oswal stocks to buy target stop loss",
    "Anand Rathi stocks to buy target stop loss",
    "Kotak Securities stocks to buy target stop loss",
    "Religare Broking stocks to buy target stop loss",
    "Angel One stocks to buy target stop loss",
    "HDFC Securities stocks to buy target stop loss",
    "ICICI Securities stocks to buy target stop loss",
    "Sharekhan stocks to buy target stop loss",
    "brokerage stocks to buy today target stop loss",
]

FIRMS = [
    "Motilal Oswal", "Anand Rathi", "Religare", "Kotak Securities",
    "ICICI Securities", "ICICI Direct", "HDFC Securities", "Axis Securities",
    "Sharekhan", "Angel One", "Choice Broking", "Geojit", "Nirmal Bang",
    "SBI Securities", "Prabhudas Lilladher", "Emkay", "Nuvama", "JM Financial",
    "Mirae Asset", "Tradebulls", "Bonanza", "LKP Securities", "Systematix",
    "Morgan Stanley", "Jefferies", "Goldman Sachs", "Nomura", "CLSA",
    "Ventura", "Arihant", "Centrum", "Elara", "Lakshmishree",
]

KEYWORDS = ["stocks to buy", "stock to buy", "buy", "target", "recommend",
            "stop-loss", "stop loss", "picks", "trade ideas", "trading ideas"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NUM = r"([0-9][0-9,]*(?:\.\d+)?)"
BS_RE = re.compile(r"\b(Buy|Sell)\b", re.I)
TGT_RE = re.compile(r"target(?:\s*price)?(?:\s*of)?[^0-9₹]{0,20}?(?:₹|Rs\.?)?\s*" + NUM, re.I)
SL_RE = re.compile(r"stop[\s\-]*loss(?:\s*(?:of|at|below))?[^0-9₹]{0,20}?(?:₹|Rs\.?)?\s*" + NUM, re.I)
ENT_RE = re.compile(
    r"\b(?:CMP|entry|buy(?:ing)?|sell|at|above|near|around)\b\s*[:\-]?\s*"
    r"(?:at|above|near|around)?\s*(?:₹|Rs\.?)\s*" + NUM, re.I)

BAD = {"entry", "target", "stop", "cmp", "at", "above", "near", "around", "the",
       "this", "these", "that", "stock", "stocks", "shares", "share", "now",
       "today", "on", "in", "with", "and", "for", "a", "an", "if", "as", "it",
       "its", "buy", "sell", "stoploss", "price", "support", "levels", "only",
       "from", "to", "of", "or", "but", "also", "can", "one"}


def num(s):
    return float(s.replace(",", ""))


def name_after(body, pos):
    seg = body[pos:pos + 90]
    seg = re.sub(r"^[ \t:,\-–]+", "", seg)
    if not seg or seg[0] in "\r\n":
        return None
    seg = re.sub(r"^(?:the\s+)?(?:shares?|stocks?)\s+of\s+", "", seg, flags=re.I)
    mm = re.match(
        r"([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Za-z0-9&.\-()]+){0,4}?)\s*"
        r"(?=[,:|(@\n]|\bat\b|\bAt\b|\babove\b|\bnear\b|\baround\b|\bCMP\b|₹|Rs)", seg)
    if not mm:
        return None
    nm = mm.group(1).strip()
    if nm.split()[0].lower() in BAD:
        return None
    return nm


def name_before(body, pos):
    seg = body[max(0, pos - 80):pos]
    lines = [l for l in seg.split("\n") if l.strip()]
    if not lines:
        return None
    line = lines[-1]
    line = re.sub(r"^\s*(?:\d+[\.\)]|[-•*])\s*", "", line)
    line = re.sub(r"[\s\-–—:|,(]+$", "", line).strip()
    if not line or len(line) > 45 or len(line.split()) > 6:
        return None
    if not (line[0].isupper() or line[0].isdigit()):
        return None
    if line.split()[0].lower() in BAD:
        return None
    return line


def extract_calls(body):
    out = []
    ms = list(BS_RE.finditer(body))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(body)
        end = min(end, m.end() + 320)
        win = body[m.end():end]
        t = TGT_RE.search(win)
        s = SL_RE.search(win)
        if not t or not s:
            continue
        entry = None
        for em in ENT_RE.finditer(win):
            if t.start() <= em.start() < t.end() or s.start() <= em.start() < s.end():
                continue
            entry = num(em.group(1))
            break
        if entry is None or entry <= 0:
            continue
        tgt, sl = num(t.group(1)), num(s.group(1))
        if abs(tgt - entry) / entry > 0.5 or abs(sl - entry) / entry > 0.5:
            continue
        name = name_after(body, m.end()) or name_before(body, m.start())
        if not name:
            continue
        side = "SELL" if m.group(1).lower() == "sell" else "BUY"
        out.append((side, name, entry, sl, tgt))
    return out


def find_firm(title, body):
    hay = (title + " " + body[:2500]).lower()
    for f in FIRMS:
        if f.lower() in hay:
            return f
    return "Brokerage"


def parse_feed(ctx, url, today):
    items = []
    try:
        r = ctx.request.get(url, headers={"User-Agent": UA}, timeout=30000)
        txt = r.text()
        print("FEED", r.status, len(txt), url[:90])
        if r.status != 200:
            return items
        root = ET.fromstring(txt)
        for it in root.iter("item"):
            title = html.unescape((it.findtext("title") or "").strip())
            link = (it.findtext("link") or "").strip()
            pub = it.findtext("pubDate") or ""
            try:
                d = parsedate_to_datetime(pub).date()
            except Exception:
                d = today
            if link:
                items.append((title, link, d))
    except Exception as e:
        print("FEED problem:", url[:90], e)
    return items


def main():
    today = datetime.date.today()
    trades = []
    seen = set()
    all_items = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 2000})
        page = ctx.new_page()

        for f in FEEDS:
            for title, link, d in parse_feed(ctx, f, today)[:60]:
                all_items[link] = (title, d)

        for q in GOOGLE_QUERIES:
            gurl = ("https://news.google.com/rss/search?q=" + quote_plus(q + " when:14d")
                    + "&hl=en-IN&gl=IN&ceid=IN:en")
            got = parse_feed(ctx, gurl, today)
            got.sort(key=lambda x: x[2], reverse=True)
            for title, link, d in got[:10]:
                all_items[link] = (title, d)

        print("Items collected:", len(all_items))

        cands = []
        for link, (title, d) in all_items.items():
            tl = title.lower()
            if (today - d).days > MAX_AGE_DAYS:
                continue
            if not any(k in tl for k in KEYWORDS):
                continue
            score = 0
            if any(fm.lower() in tl for fm in FIRMS):
                score += 2
            if "stocks to buy" in tl or "stock to buy" in tl:
                score += 1
            cands.append((score, d, link, title))
        cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        cands = cands[:MAX_ARTICLES]
        print("Articles to read:", len(cands))

        shown = 0
        denied = 0
        for score, d, link, title in cands:
            try:
                page.goto(link, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000 if "news.google.com" in link else 1500)
                body = page.inner_text("body")
                host = urlparse(page.url).netloc.replace("www.", "")
                if "access denied" in body[:200].lower():
                    denied += 1
                    continue
                firm = find_firm(title, body)
                found = 0
                for side, name, entry, sl, tgt in extract_calls(body):
                    key = (firm, name.upper(), d.isoformat(), tgt)
                    if key in seen:
                        continue
                    seen.add(key)
                    found += 1
                    trades.append({
                        "id": f"bp-{len(trades) + 1}",
                        "symbol": name.upper(),
                        "name": name,
                        "advisor": firm,
                        "source": host,
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
                        "link": page.url,
                    })
                if found == 0 and shown < 4:
                    shown += 1
                    flat = re.sub(r"\s+", " ", body)
                    i = flat.lower().find("target")
                    print("NO CALLS in:", host, "|", title[:70])
                    print("   around target:", flat[max(0, i - 150):i + 200] if i >= 0 else "(word target not found)")
            except Exception as e:
                print("Article problem:", link[:80], str(e)[:100])

        print("Blocked pages (access denied):", denied)
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
