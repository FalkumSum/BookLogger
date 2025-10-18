from typing import Optional
import requests

def by_isbn(isbn: str) -> Optional[dict]:
    try:
        r = requests.get(f"https://openlibrary.org/isbn/{isbn}.json", timeout=10)
        if not r.ok: return None
        data = r.json()
        title = data.get("title", "")
        authors = []
        for a in data.get("authors", []):
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
