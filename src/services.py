from typing import Optional
from datetime import date
from .utils import clean_isbn, validate_isbn13, extract_isbn13_from_text, isbn13_to_isbn10
from .lookups import google_books, openlibrary, isbndb, web_fallback

class BookLookupService:
    def __init__(self, google_api_key: str | None = None, isbndb_key: str | None = None):
        self.google_api_key = google_api_key or None
        self.isbndb_key = isbndb_key or None

    def by_isbn(self, raw: str) -> Optional[dict]:
        if not raw: return None
        s = raw.strip()
        if not (s.isdigit() and len(s) in (10, 13)):
            found = extract_isbn13_from_text(s)
            if found: s = found
        s = clean_isbn(s)
        if len(s) == 13 and not validate_isbn13(s): return None
        if len(s) not in (10, 13): return None

        rec = google_books.by_isbn(s, self.google_api_key)
        if rec: return rec

        rec = isbndb.by_isbn(s, self.isbndb_key)
        if rec: return rec

        rec = openlibrary.by_isbn(s)
        if rec: return rec

        rec = web_fallback.web_fallback(s)
        if rec:
            # optional enrichment
            enrich = google_books.by_isbn(s, self.google_api_key) or openlibrary.by_isbn(s)
            if enrich:
                for k, v in enrich.items():
                    if not rec.get(k) and v:
                        rec[k] = v
        return rec

    def search_text(self, query: str, limit: int = 8) -> list[dict]:
        return google_books.by_text(query, self.google_api_key, limit=limit)
