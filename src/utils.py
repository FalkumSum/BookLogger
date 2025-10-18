import re
from typing import Optional

def clean_isbn(s: str) -> str:
    return re.sub(r"[^0-9X]", "", (s or "").upper())

def validate_isbn13(isbn13: str) -> bool:
    if not (isbn13.isdigit() and len(isbn13) == 13):
        return False
    total = sum((int(d) * (1 if i % 2 == 0 else 3)) for i, d in enumerate(isbn13[:12]))
    check = (10 - (total % 10)) % 10
    return check == int(isbn13[-1])

def validate_isbn10(isbn10: str) -> bool:
    if len(isbn10) != 10:
        return False
    total = 0
    for i, ch in enumerate(isbn10[:9], start=1):
        if not ch.isdigit():
            return False
        total += i * int(ch)
    check = total % 11
    last = isbn10[-1]
    return (last == "X" and check == 10) or (last.isdigit() and check == int(last))

def isbn13_to_isbn10(isbn13: str) -> Optional[str]:
    if not (isbn13.startswith("978") and validate_isbn13(isbn13)):
        return None
    core = isbn13[3:12]
    total = sum((i + 1) * int(d) for i, d in enumerate(core))
    remainder = total % 11
    check = "X" if remainder == 10 else str(remainder)
    return core + check

def extract_isbn13_from_text(s: str) -> Optional[str]:
    if not s:
        return None
    candidates = re.findall(r"\d{13}", s)
    candidates = sorted(candidates, key=lambda x: (not x.startswith(("978", "979")),))
    for c in candidates:
        if validate_isbn13(c):
            return c
    return None

def safe_url(u: str | None) -> Optional[str]:
    if isinstance(u, str):
        u = u.strip()
        if u.lower().startswith(("http://", "https://")) and len(u) > 7:
            return u
    return None
