from dataclasses import dataclass, asdict, field
from typing import Optional

STATUS_OPTIONS = ["Wishlist", "Reading", "Finished"]

@dataclass
class Book:
    id: int = 0
    isbn: str = ""
    title: str = ""
    author: str = ""
    rating: int = 0
    notes: str = ""
    thumbnail: str = ""
    status: str = "Reading"
    date: str = ""          # ISO YYYY-MM-DD (user-provided)
    added_at: str = ""      # ISO timestamp
    page_count: int = 0
    published_date: str = ""
    publisher: str = ""
    categories: str = ""
    language: str = ""
    description: str = ""
    source: str = ""        # google / openlibrary / isbndb / web(*) / manual / cover-ocr

    @classmethod
    def headers(cls) -> list[str]:
        return list(asdict(cls()).keys())

    def to_row(self) -> dict:
        return asdict(self)
