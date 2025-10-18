from typing import Optional
import requests

def _query(q: str, api_key: Optional[str] = None) -> Optional[dict]:
    params = {"q": q, "maxResults": 1, "printType": "books"}
    if api_key:
        params["key"] = api_key
    r = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=10)
    if not r.ok: return None
    items = r.json().get("items", [])
    if not items: return None
    info = items[0].get("volumeInfo", {})
    return {
        "title": info.get("title", ""),
        "author": ", ".join(info.get("authors", []) or []),
        "thumbnail": (info.get("imageLinks", {}) or {}).get("thumbnail", ""),
        "page_count": info.get("pageCount") or 0,
        "published_date": info.get("publishedDate", ""),
        "publisher": info.get("publisher", ""),
        "categories": ", ".join(info.get("categories", []) or []),
        "language": info.get("language", ""),
        "description": info.get("description", ""),
        "source": "google",
    }

def by_isbn(isbn: str, api_key: Optional[str]) -> Optional[dict]:
    rec = _query(f"isbn:{isbn}", api_key)
    if rec: rec["isbn"] = isbn; return rec
    return None

def by_text(query: str, api_key: Optional[str], limit: int = 8) -> list[dict]:
    params = {"q": query, "maxResults": limit, "printType": "books"}
    if api_key:
        params["key"] = api_key
    r = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=10)
    r.raise_for_status()
    results = []
    for it in r.json().get("items", []):
        info = it.get("volumeInfo", {})
        ids = info.get("industryIdentifiers", []) or []
        isbn = ""
        for ident in ids:
            if ident.get("type") in ("ISBN_13", "ISBN_10"):
                isbn = ident.get("identifier", ""); break
        results.append({
            "id": it.get("id"),
            "title": info.get("title", ""),
            "author": ", ".join(info.get("authors", []) or []),
            "thumbnail": (info.get("imageLinks", {}) or {}).get("thumbnail", ""),
            "isbn": isbn,
            "page_count": info.get("pageCount") or 0,
            "published_date": info.get("publishedDate", ""),
            "publisher": info.get("publisher", ""),
            "categories": ", ".join(info.get("categories", []) or []),
            "language": info.get("language", ""),
            "description": info.get("description", ""),
            "source": "google-search",
        })
    return results
