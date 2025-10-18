from typing import Optional
import re
import requests

try:
    from bs4 import BeautifulSoup
    BS4_READY = True
except Exception:
    BS4_READY = False

UA = {"User-Agent": "Mozilla/5.0 (compatible; BookLogger/1.0)"}

def _soup(url: str):
    try:
        r = requests.get(url, headers=UA, timeout=10)
        if not r.ok: return None
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None

def _number(text: str) -> int:
    m = re.search(r"(\d{2,4})\s+(sider|sidor|pages)", text, re.IGNORECASE)
    if m:
        try: return int(m.group(1))
        except Exception: pass
    return 0

def _publisher(text: str) -> str:
    m = re.search(r"(förlag|forlag|publisher|Forlag)\s*[:\-]?\s*([A-Za-z0-9 .,&\-’'ÆØÅæøåÉé]+)", text, re.IGNORECASE)
    return m.group(2).strip(" .,-") if m else ""

def _lang(text: str) -> str:
    m = re.search(r"(språk|sprog|language)\s*[:\-]?\s*([A-Za-zæøåÄÖÅÉÍÓÚáéíóúñ\-]+)", text, re.IGNORECASE)
    return m.group(2) if m else ""

def adlibris(isbn: str) -> Optional[dict]:
    if not BS4_READY: return None
    urls = [
        f"https://www.adlibris.com/dk/sog?q={isbn}",
        f"https://www.adlibris.com/se/sok?q={isbn}",
        f"https://www.adlibris.com/no/sok?q={isbn}",
        f"https://www.adlibris.com/fi/haku?q={isbn}",
    ]
    for u in urls:
        soup = _soup(u)
        if not soup: continue
        a = soup.select_one('a[href*="/product/"], a[href*="/bog/"], a[href*="/bok/"]')
        if a and a.get("href"):
            href = a["href"]
            base = u.split("/")[0] + "//" + u.split("/")[2]
            if href.startswith("/"): u2 = base + href
            else: u2 = href
            soup = _soup(u2) or soup
        title = (soup.select_one('meta[property="og:title"]') or {}).get("content", "")
        image = (soup.select_one('meta[property="og:image"]') or {}).get("content", "")
        text = soup.get_text(" ", strip=True)
        return {
            "isbn": isbn, "title": title, "author": "",
            "thumbnail": image, "page_count": _number(text),
            "published_date": "", "publisher": _publisher(text),
            "categories": "", "language": _lang(text),
            "description": "", "source": "web(adlibris)"
        }
    return None

def saxo(isbn: str) -> Optional[dict]:
    if not BS4_READY: return None
    soup = _soup(f"https://www.saxo.com/dk/s?q={isbn}")
    if not soup: return None
    a = soup.select_one('a[href*="/bog/"], a[href*="/bog-p"]:not([href*="s?q="])')
    if a and a.get("href"):
        href = a["href"]
        if href.startswith("/"): soup = _soup("https://www.saxo.com" + href) or soup
        else: soup = _soup(href) or soup
    title = (soup.select_one('meta[property="og:title"]') or {}).get("content", "")
    image = (soup.select_one('meta[property="og:image"]') or {}).get("content", "")
    text = soup.get_text(" ", strip=True)
    author = ""
    h1 = soup.select_one("h1")
    if h1:
        m = re.search(r"[-–]\s*(?:af|forfatter)\s*:\s*(.+)$", h1.get_text(strip=True), re.IGNORECASE)
        if m: author = m.group(1)
    return {
        "isbn": isbn, "title": title, "author": author,
        "thumbnail": image, "page_count": _number(text),
        "published_date": "", "publisher": _publisher(text),
        "categories": "", "language": "", "description": "",
        "source": "web(saxo)"
    }

def imusic(isbn: str) -> Optional[dict]:
    if not BS4_READY: return None
    soup = _soup(f"https://www.imusic.dk/search?type=book&q={isbn}")
    if not soup: return None
    a = soup.select_one('a[href*="/books/"], a[href*="/bog/"], a[href*="/book/"]')
    if a and a.get("href"):
        href = a["href"]
        if href.startswith("/"): soup = _soup("https://www.imusic.dk" + href) or soup
        else: soup = _soup(href) or soup
    title = (soup.select_one('meta[property="og:title"]') or {}).get("content", "")
    image = (soup.select_one('meta[property="og:image"]') or {}).get("content", "")
    text = soup.get_text(" ", strip=True)
    return {
        "isbn": isbn, "title": title, "author": "",
        "thumbnail": image, "page_count": _number(text),
        "published_date": "", "publisher": _publisher(text),
        "categories": "", "language": "", "description": "",
        "source": "web(imusic)"
    }

def web_fallback(isbn: str) -> Optional[dict]:
    for fn in (adlibris, saxo, imusic):
        rec = fn(isbn)
        if rec and (rec.get("title") or rec.get("thumbnail") or rec.get("publisher")):
            return rec
    return None
