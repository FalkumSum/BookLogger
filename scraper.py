"""
Lightweight book scrapers for Danish / Nordic retailers with robust de-dup.

Functions:
- scrape_saxo(url)
- scrape_adlibris(url)
- scrape_imusic(url)
- scrape_url(url)
- search_saxo_by_title(query, max_results=20)

Notes:
- Uses only free sources (plain HTTP + Google Books public API w/out key).
"""

from __future__ import annotations
import re
import json
from typing import Optional, List
from urllib.parse import urlparse, urlunparse

import requests

try:
    from bs4 import BeautifulSoup  # pip install beautifulsoup4
    BS4_READY = True
except Exception:
    BS4_READY = False


# -----------------------------
# HTTP
# -----------------------------
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 BookLogger/1.1"
    ),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Referer": "https://www.saxo.com/dk/",
}

def _get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        if r.ok:
            return r
        return None
    except Exception:
        return None

def _soup(url: str) -> tuple[Optional["BeautifulSoup"], str]:
    r = _get(url)
    if not r:
        return None, ""
    html = r.text
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None, html
    return soup, html


# -----------------------------
# Text / ISBN helpers
# -----------------------------
def normalize_text(s: Optional[str]) -> str:
    import html as ihtml, unicodedata
    if not s:
        return ""
    s = ihtml.unescape(str(s))
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def safe_url(u: Optional[str]) -> Optional[str]:
    if isinstance(u, str):
        u = u.strip()
        if u.lower().startswith(("http://", "https://")) and len(u) > 7:
            return u.replace("http://", "https://")
    return None

def clean_isbn(s: str) -> str:
    return re.sub(r"[^0-9X]", "", (s or "").upper())

def validate_isbn13(isbn13: str) -> bool:
    if not (isbn13.isdigit() and len(isbn13) == 13):
        return False
    total = sum((int(d) * (1 if i % 2 == 0 else 3)) for i, d in enumerate(isbn13[:12]))
    check = (10 - (total % 10)) % 10
    return check == int(isbn13[-1])

def isbn13_to_isbn10(isbn13: str) -> Optional[str]:
    if not (isbn13.startswith("978") and validate_isbn13(isbn13)):
        return None
    core = isbn13[3:12]
    total = sum((i + 1) * int(d) for i, d in enumerate(core))
    remainder = total % 11
    check = "X" if remainder == 10 else str(remainder)
    return core + check

def extract_year(pubdate: str) -> str:
    m = re.search(r"(19|20)\d{2}", pubdate or "")
    return m.group(0) if m else ""


# -----------------------------
# URL normalization / keys
# -----------------------------
_SAXO_HOSTS = {"saxo.com", "www.saxo.com", "saxo.dk", "www.saxo.dk"}

def _normalize_url(u: str) -> str:
    """Strip query/fragment, force https, normalize host; keep path/casing."""
    try:
        p = urlparse(u.strip())
        host = p.netloc.lower()
        if host in _SAXO_HOSTS:
            host = "www.saxo.com"
        scheme = "https"
        # drop query & fragment
        norm = urlunparse((scheme, host, p.path, "", "", ""))
        # remove trailing slash except root
        if norm.endswith("/") and len(p.path) > 1:
            norm = norm[:-1]
        return norm
    except Exception:
        return u

def _isbn_from_saxo_url(u: str) -> Optional[str]:
    """
    Try to extract ISBN-13 directly from path:
      .../bog_<isbn13> or .../_bog_<isbn13> or any _bog_97XXXXXXXXXXX
    """
    try:
        path = urlparse(u).path.lower()
    except Exception:
        path = u.lower()
    m = re.search(r"(?:^|/)_?bog_(97[89]\d{10})(?:$|/)", path)
    if m and validate_isbn13(m.group(1)):
        return m.group(1)
    return None


# -----------------------------
# Title cleaning
# -----------------------------
SITE_SUFFIX_PATTERNS = [
    r"\s*\|\s*saxo(?:\.com)?\s*$",
    r"\s*-\s*saxo(?:\.com)?\s*$",
    r"\s*•\s*saxo(?:\.com)?\s*$",
    r"\s*\|\s*adlibris(?:\.com)?\s*$",
    r"\s*-\s*adlibris(?:\.com)?\s*$",
    r"\s*\|\s*imusic(?:\.dk|\.com)?\s*$",
    r"\s*-\s*imusic(?:\.dk|\.com)?\s*$",
]

def clean_product_title(raw: str, author_hint: str = "") -> str:
    t = normalize_text(raw)
    if not t:
        return ""
    for pat in SITE_SUFFIX_PATTERNS:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    if author_hint:
        ah = re.escape(normalize_text(author_hint))
        t = re.sub(rf"\s*[–\-]\s*{ah}\b.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*[–\-]\s*(bog|paperback|hardback|hardcover|indbundet)\b.*$", "", t, flags=re.IGNORECASE)
    for sep in (" – ", " — ", " - ", " | "):
        if sep in t:
            left = t.split(sep, 1)[0].strip()
            if len(left) >= 2:
                t = left
                break
    return t.strip()


# -----------------------------
# Parsers
# -----------------------------
def parse_jsonld_book(soup: "BeautifulSoup") -> dict:
    out = {}
    if not soup:
        return out
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            t = (obj.get("@type") or "").lower()
            if t in ("book", "product"):
                name = obj.get("name") or obj.get("headline") or ""
                if name and "Title" not in out:
                    out["Title"] = normalize_text(name)
                # author
                a = obj.get("author")
                if isinstance(a, dict):
                    out["Author"] = normalize_text(a.get("name", ""))
                elif isinstance(a, list) and a:
                    names = []
                    for el in a:
                        if isinstance(el, dict):
                            names.append(el.get("name", ""))
                        elif isinstance(el, str):
                            names.append(el)
                    out["Author"] = normalize_text(", ".join([n for n in names if n]))
                elif isinstance(a, str):
                    out["Author"] = normalize_text(a)
                # isbn
                isbn = obj.get("isbn") or obj.get("gtin13") or obj.get("sku") or ""
                isbn = clean_isbn(isbn)
                if len(isbn) == 13 and validate_isbn13(isbn):
                    out["ISBN-13"] = isbn
                # image
                img = obj.get("image")
                if isinstance(img, list) and img:
                    out["Thumbnail"] = safe_url(img[0])
                elif isinstance(img, str):
                    out["Thumbnail"] = safe_url(img)
    return out

def _extract_og_meta(soup: "BeautifulSoup") -> dict:
    out = {}
    if not soup:
        return out
    def _get(prop):
        tag = soup.select_one(f'meta[property="{prop}"], meta[name="{prop}"]')
        return (tag.get("content") or "").strip() if tag and tag.get("content") else ""
    og_title = _get("og:title") or _get("twitter:title")
    og_image = _get("og:image") or _get("twitter:image")
    if og_image:
        og_image = og_image.replace("http://", "https://")
    out["Title"] = clean_product_title(og_title)
    out["Thumbnail"] = og_image
    return out

def _extract_isbn13_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    for c in re.findall(r"\b(97[89]\d{10})\b", text):
        if validate_isbn13(c):
            return c
    for c in re.findall(r"\b(\d{13})\b", text):
        if validate_isbn13(c):
            return c
    return None


# -----------------------------
# Site scrapers
# -----------------------------
def scrape_saxo(url: str) -> dict:
    if not BS4_READY:
        return {"error": "BeautifulSoup not installed (pip install beautifulsoup4)."}
    soup, text = _soup(url)
    if not soup:
        return {}

    jld = parse_jsonld_book(soup)
    h1 = soup.select_one("h1")
    h1_title = normalize_text(h1.get_text(" ", strip=True)) if h1 else ""
    og = _extract_og_meta(soup)

    author = jld.get("Author", "")
    if not author:
        maybe = soup.find(string=re.compile(r"\b(af|forfatter)\b", re.IGNORECASE))
        if maybe:
            m = re.search(r"(?:af|forfatter)\s*[:\-]?\s*(.+)", normalize_text(str(maybe)), flags=re.IGNORECASE)
            if m:
                author = m.group(1).strip()

    isbn = jld.get("ISBN-13") or _extract_isbn13_from_text(text) or _isbn_from_saxo_url(url) or ""

    pages = 0
    pub = ""
    lang = ""
    m = re.search(r"(\d{2,4})\s+(sider|pages)", text, re.IGNORECASE)
    if m:
        try: pages = int(m.group(1))
        except Exception: pass
    m = re.search(r"(forlag|publisher)\s*[:\-]?\s*([A-Za-z0-9 .,&\-’'ÆØÅæøåÉé]+)", text, re.IGNORECASE)
    if m:
        pub = m.group(2).strip(" .,-")
    m = re.search(r"(sprog|language)\s*[:\-]?\s*([A-Za-zæøåÄÖÅÉÍÓÚáéíóúñ\-]+)", text, re.IGNORECASE)
    if m:
        lang = m.group(2)

    raw_title = jld.get("Title") or h1_title or og.get("Title") or ""
    title = clean_product_title(raw_title, author_hint=author)
    thumb = jld.get("Thumbnail") or safe_url(og.get("Thumbnail") or "") or ""

    out = {
        "Title": title,
        "Author": author,
        "Thumbnail": thumb or "",
        "ISBN-13": isbn,
        "ISBN-10": "",
        "Page count": pages or 0,
        "Published date": "",
        "Publisher": normalize_text(pub),
        "Language": normalize_text(lang),
        "source": "web(saxo)",
        "url": _normalize_url(url),
    }
    if out["ISBN-13"].startswith("978") and validate_isbn13(out["ISBN-13"]):
        maybe10 = isbn13_to_isbn10(out["ISBN-13"])
        if maybe10:
            out["ISBN-10"] = maybe10
    return out

def scrape_adlibris(url: str) -> dict:
    if not BS4_READY:
        return {"error": "BeautifulSoup not installed (pip install beautifulsoup4)."}
    soup, text = _soup(url)
    if not soup:
        return {}
    og = _extract_og_meta(soup)
    isbn = _extract_isbn13_from_text(text) or ""
    pub = ""
    pages = 0
    lang = ""
    m = re.search(r"(\d{2,4})\s+(sider|sidor|pages)", text, re.IGNORECASE)
    if m:
        try: pages = int(m.group(1))
        except Exception: pass
    m = re.search(r"(förlag|forlag|publisher)\s*[:\-]?\s*([A-Za-z0-9 .,&\-’'ÆØÅæøåÉé]+)", text, re.IGNORECASE)
    if m:
        pub = m.group(2).strip(" .,-")
    m = re.search(r"(språk|sprog|language)\s*[:\-]?\s*([A-Za-zæøåÄÖÅÉÍÓÚáéíóúñ\-]+)", text, re.IGNORECASE)
    if m:
        lang = m.group(2)
    out = {
        "Title": og.get("Title", ""),
        "Author": "",
        "Thumbnail": safe_url(og.get("Thumbnail") or "") or "",
        "ISBN-13": isbn,
        "ISBN-10": "",
        "Page count": pages or 0,
        "Published date": "",
        "Publisher": normalize_text(pub),
        "Language": normalize_text(lang),
        "source": "web(adlibris)",
        "url": _normalize_url(url),
    }
    if out["ISBN-13"].startswith("978") and validate_isbn13(out["ISBN-13"]):
        maybe10 = isbn13_to_isbn10(out["ISBN-13"])
        if maybe10:
            out["ISBN-10"] = maybe10
    return out

def scrape_imusic(url: str) -> dict:
    if not BS4_READY:
        return {"error": "BeautifulSoup not installed (pip install beautifulsoup4)."}
    soup, text = _soup(url)
    if not soup:
        return {}
    og = _extract_og_meta(soup)
    isbn = _extract_isbn13_from_text(text) or ""
    pub = ""
    pages = 0
    lang = ""
    m = re.search(r"(\d{2,4})\s+(sider|pages)", text, re.IGNORECASE)
    if m:
        try: pages = int(m.group(1))
        except Exception: pass
    m = re.search(r"(Forlag|Publisher)\s*[:\-]?\s*([A-Za-z0-9 .,&\-’'ÆØÅæøåÉé]+)", text, re.IGNORECASE)
    if m:
        pub = m.group(2).strip(" .,-")
    out = {
        "Title": og.get("Title", ""),
        "Author": "",
        "Thumbnail": safe_url(og.get("Thumbnail") or "") or "",
        "ISBN-13": isbn,
        "ISBN-10": "",
        "Page count": pages or 0,
        "Published date": "",
        "Publisher": normalize_text(pub),
        "Language": normalize_text(lang),
        "source": "web(imusic)",
        "url": _normalize_url(url),
    }
    if out["ISBN-13"].startswith("978") and validate_isbn13(out["ISBN-13"]):
        maybe10 = isbn13_to_isbn10(out["ISBN-13"])
        if maybe10:
            out["ISBN-10"] = maybe10
    return out

def scrape_url(url: str) -> dict:
    u = (url or "").strip()
    if "saxo.com" in u or "saxo.dk" in u:
        return scrape_saxo(u)
    if "adlibris.com" in u:
        return scrape_adlibris(u)
    if "imusic.dk" in u:
        return scrape_imusic(u)
    # Generic OG fallback
    if not BS4_READY:
        return {"error": "BeautifulSoup not installed (pip install beautifulsoup4)."}
    soup, text = _soup(u)
    og = _extract_og_meta(soup)
    isbn = _extract_isbn13_from_text(text) or ""
    out = {
        "Title": og.get("Title", ""),
        "Author": "",
        "Thumbnail": safe_url(og.get("Thumbnail") or "") or "",
        "ISBN-13": isbn,
        "ISBN-10": "",
        "Page count": 0,
        "Published date": "",
        "Publisher": "",
        "Language": "",
        "source": "web(generic)",
        "url": _normalize_url(u),
    }
    if out["ISBN-13"].startswith("978") and validate_isbn13(out["ISBN-13"]):
        maybe10 = isbn13_to_isbn10(out["ISBN-13"])
        if maybe10:
            out["ISBN-10"] = maybe10
    return out


# -----------------------------
# Google Books (free) helper for ISBN harvest
# -----------------------------
GB_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

def _gbooks_fetch_isbns(query: str, want: int = 12) -> List[str]:
    """Harvest likely ISBN-13 values for a title/author query."""
    q = normalize_text(query)
    if not q:
        return []
    isbns: List[str] = []
    seen = set()
    start = 0
    while start < 80 and len(isbns) < want:
        params = {"q": q, "printType": "books", "maxResults": 40, "startIndex": start, "langRestrict": "da"}
        r = requests.get(GB_ENDPOINT, params=params, timeout=10)
        if not r.ok:
            break
        items = r.json().get("items", []) or []
        if not items:
            break
        for it in items:
            info = it.get("volumeInfo", {}) or {}
            for ident in info.get("industryIdentifiers", []) or []:
                if ident.get("type") == "ISBN_13":
                    v = clean_isbn(ident.get("identifier", ""))
                    if len(v) == 13 and v not in seen:
                        seen.add(v)
                        isbns.append(v)
                        if len(isbns) >= want:
                            break
            if len(isbns) >= want:
                break
        if len(items) < 40:
            break
        start += 40
    return isbns


# -----------------------------
# Saxo search by title (robust + de-dup)
# -----------------------------
def _collect_saxo_links_from_html(soup: "BeautifulSoup") -> List[str]:
    """Find product links on a Saxo search page and return normalized URLs (no dupes)."""
    if not soup:
        return []
    links, seen = [], set()
    for a in soup.select('a[href]'):
        href = a.get("href", "")
        if not href or "s?q=" in href:
            continue
        # product URL patterns
        if re.search(r"/bog(?:-p)?/", href) or "_bog_" in href or href.endswith(".aspx"):
            if href.startswith("/"):
                href = "https://www.saxo.com" + href
            norm = _normalize_url(href)
            if norm not in seen:
                seen.add(norm)
                links.append(norm)
    return links

def _try_saxo_search_pages(query: str) -> List[str]:
    """Try multiple Saxo search URL shapes and collect normalized product links."""
    q = requests.utils.quote(normalize_text(query))
    candidates = [
        f"https://www.saxo.com/dk/s?q={q}",
        f"https://www.saxo.com/dk/search?q={q}",
        f"https://www.saxo.com/dk/soeg?q={q}",
    ]
    links: List[str] = []
    seen = set()
    for url in candidates:
        soup, _ = _soup(url)
        if not soup:
            continue
        for l in _collect_saxo_links_from_html(soup):
            if l not in seen:
                seen.add(l)
                links.append(l)
    return links

def _try_saxo_by_isbn(isbn13: str) -> List[str]:
    """Search Saxo for an ISBN and try direct product URL patterns; return normalized links."""
    out: List[str] = []
    seen = set()
    q = requests.utils.quote(isbn13)
    for url in (
        f"https://www.saxo.com/dk/s?q={q}",
        f"https://www.saxo.com/dk/search?q={q}",
    ):
        soup, _ = _soup(url)
        if soup:
            for l in _collect_saxo_links_from_html(soup):
                if l not in seen:
                    seen.add(l)
                    out.append(l)

    # Try direct product paths (often redirect); normalize final URL
    for url in (
        f"https://www.saxo.com/dk/bog_{isbn13}",
        f"https://www.saxo.com/dk/_bog_{isbn13}",
    ):
        r = _get(url)
        if r:
            final = _normalize_url(r.url)
            if "bog_" in final and final not in seen:
                seen.add(final)
                out.append(final)

    return out

def search_saxo_by_title(query: str, max_results: int = 20) -> List[dict]:
    """
    Robust Saxo search with strong de-dup:
      1) try Saxo search pages with the text
      2) if empty, harvest ISBN-13s via Google Books (lang=da) and search Saxo by ISBN
      3) de-dup by normalized URL before scraping
      4) de-dup scraped records by ISBN-13, else by normalized Title|Author
    """
    if not BS4_READY:
        return [{"error": "BeautifulSoup not installed (pip install beautifulsoup4)."}]
    q = normalize_text(query)
    if not q:
        return []

    # Pass 1: direct search
    links = _try_saxo_search_pages(q)

    # Pass 2: ISBN harvest if needed
    if not links:
        for isbn in _gbooks_fetch_isbns(q, want=10):
            new_links = _try_saxo_by_isbn(isbn)
            for l in new_links:
                if l not in links:
                    links.append(l)
            if len(links) >= max_results:
                break

    # Scrape with link-level de-dup
    out: List[dict] = []
    seen_urls = set()
    seen_isbn = set()
    for href in links:
        norm = _normalize_url(href)
        if norm in seen_urls:
            continue
        seen_urls.add(norm)

        # Pre-dedupe by ISBN present in URL
        url_isbn = _isbn_from_saxo_url(norm)
        if url_isbn:
            if url_isbn in seen_isbn:
                continue

        try:
            rec = scrape_saxo(norm)
        except Exception:
            continue

        if not rec or not (rec.get("Title") or rec.get("ISBN-13")):
            continue

        # Record-level de-dup
        isbn = rec.get("ISBN-13", "")
        if isbn and validate_isbn13(isbn):
            if isbn in seen_isbn:
                continue
            seen_isbn.add(isbn)
        else:
            # fallback key based on title+author
            key = (normalize_text(rec.get("Title","")).lower() + "|" +
                   normalize_text(rec.get("Author","")).lower())
            # ensure only one per key
            if any(
                (normalize_text(r.get("Title","")).lower() + "|" +
                 normalize_text(r.get("Author","")).lower()) == key
                for r in out
            ):
                continue

        out.append(rec)
        if len(out) >= max_results:
            break

    return out

def _author_matches(candidate: str, query: str) -> bool:
    """
    Loose, accent-friendly match:
    - case-insensitive
    - all query tokens must appear in the candidate author field
    """
    cand = normalize_text(candidate).lower()
    q = normalize_text(query).lower()
    if not q:
        return True
    tokens = [t for t in re.split(r"\s+", q) if t]
    return all(t in cand for t in tokens)

def search_saxo_by_author(author_query: str, max_results: int = 20) -> List[dict]:
    """
    Search Saxo by author name:
      1) Run the same Saxo search flow as title (since Saxo's q= handles both).
      2) Filter scraped results where Author loosely matches the query.
      3) Keep de-dup semantics from the underlying implementation.
    """
    # Reuse the existing robust search (q=author name)
    base = search_saxo_by_title(author_query, max_results=max_results * 2)  # get extra, we'll filter
    if not base:
        return []
    out = [r for r in base if _author_matches(r.get("Author", ""), author_query)]
    # Keep at most max_results
    return out[:max_results]
