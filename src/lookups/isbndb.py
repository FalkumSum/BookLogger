from typing import Optional
import requests

def by_isbn(isbn: str, api_key: str | None) -> Optional[dict]:
    if not api_key: return None
    try:
        r = requests.get(f"https://api2.isbndb.com/book/{isbn}",
                         headers={"X-API-Key": api_key}, timeout=10)
        if not r.ok: return None
        data = r.json().get("book", {})
        return {
            "isbn": isbn,
            "title": data.get("title", ""),
            "author": ", ".join(data.get("authors", []) or []),
            "publisher": data.get("publisher", ""),
            "language": data.get("language", ""),
            "page_count": data.get("pages", 0),
            "published_date": data.get("date_published", ""),
            "thumbnail": data.get("image", ""),
            "categories": ", ".join(data.get("subjects", []) or []),
            "description": data.get("overview", ""),
            "source": "isbndb",
        }
    except Exception:
        return None
