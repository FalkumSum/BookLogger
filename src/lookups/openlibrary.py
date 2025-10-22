from typing import Optional
import requests

def by_isbn(isbn: str) -> Optional[dict]:
    try:
        r = requests.get(f"https://openlibrary.org/isbn/{isbn}.json", timeout=10)
        if not r.ok:
            return None
        data = r.json()
        title = data.get("title", "")
        authors = []
        for a in data.get("authors", []) or []:
            key = a.get("key")
            if key:
                ar = requests.get(f"https://openlibrary.org{key}.json", timeout=10)
                if ar.ok:
                    authors.append(ar.json().get("name", ""))
        page_count = data.get("number_of_pages") or 0
        published_date = data.get("publish_date", "")
        publishers = data.get("publishers", [])
        publisher = ", ".join(publishers) if isinstance(publishers, list) else (publishers or "")
        return {
            "isbn": isbn,
            "title": title,
            "author": ", ".join([a for a in authors if a]),
            "thumbnail": "",
            "page_count": page_count,
            "published_date": published_date,
            "publisher": publisher,
            "categories": "",
            "language": "",
            "description": "",
            "source": "openlibrary",
        }
    except Exception:
        return None

def search_text(query: str, limit: int = 8) -> list[dict]:
    """
    Lightweight OpenLibrary text search fallback.
    Maps results to the same shape used in the app.
    """
    try:
        r = requests.get("https://openlibrary.org/search.json", params={"q": query, "limit": limit}, timeout=10)
        if not r.ok:
            return []
        data = r.json()
    except requests.RequestException:
        return []

    out = []
    for doc in data.get("docs", []) or []:
        # pick an ISBN if present
        isbn = ""
        for key in ("isbn13", "isbn"):
            if key in doc and doc[key]:
                # prefer the first 13-digit if available
                for maybe in doc[key]:
                    if isinstance(maybe, str) and len(maybe) == 13:
                        isbn = maybe
                        break
                if not isbn:
                    isbn = str(doc[key][0])
                break

        # cover
        cover_id = doc.get("cover_i")
        thumb = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""

        out.append({
            "id": f"ol_{doc.get('key','')}",
            "title": doc.get("title", "") or "",
            "author": ", ".join(doc.get("author_name", []) or []),
            "thumbnail": thumb,
            "isbn": isbn,
            "page_count": int(doc.get("number_of_pages_median") or 0),
            "published_date": str(doc.get("first_publish_year") or ""),
            "publisher": ", ".join(doc.get("publisher", [])[:2]) if doc.get("publisher") else "",
            "categories": ", ".join(doc.get("subject", [])[:3]) if doc.get("subject") else "",
            "language": (doc.get("language", []) or [""])[0] if doc.get("language") else "",
            "description": "",
            "source": "openlibrary-search",
        })
    return out
