import io
import re
from datetime import datetime, date
from typing import List, Optional

import pandas as pd
import requests
import streamlit as st
from PIL import Image

import gspread
from google.oauth2.service_account import Credentials
from gspread_dataframe import get_as_dataframe, set_with_dataframe

# Try zxing-cpp for barcode decoding (works on Streamlit Cloud)
ZXING_READY = True
try:
    import zxingcpp
except Exception:
    ZXING_READY = False

st.set_page_config(page_title="Book Logger", page_icon="📚", layout="wide")

# -----------------------------
# Config / Secrets
# -----------------------------
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SECRETS = st.secrets

def require_secret(section: str, key: str, example: str = ""):
    if section not in SECRETS:
        st.error(f"Missing secrets section: [{section}] in secrets.toml")
        if example:
            st.code(example, language="toml")
        st.stop()
    if key not in SECRETS[section]:
        st.error(f"Missing secret: [{section}].{key} in secrets.toml")
        if example:
            st.code(example, language="toml")
        st.stop()
    return SECRETS[section][key]

SHEET_NAME = require_secret("sheet", "name", example='[sheet]\nname = "book_logger"\nworksheet = "books"')
WORKSHEET_NAME = require_secret("sheet", "worksheet")
API_KEY = SECRETS.get("google_books", {}).get("api_key", "")

# Base schema (will be created/extended automatically)
HEADERS = [
    "id", "isbn", "title", "author", "rating", "notes", "thumbnail",
    "status", "date", "added_at",
    # New metadata:
    "page_count", "published_date", "publisher", "categories", "language", "description"
]
STATUS_OPTIONS = ["Wishlist", "Reading", "Finished"]

# -----------------------------
# Small helpers
# -----------------------------
def safe_url(u):
    """Return a clean http(s) URL or None (avoids NaN/float issues)."""
    if isinstance(u, str):
        u = u.strip()
        if u.lower().startswith(("http://", "https://")) and len(u) > 7:
            return u
    return None

def coerce_string(df: pd.DataFrame, cols: List[str]):
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype("string").fillna("")
            df[c] = df[c].replace("nan", "")

# -----------------------------
# Google Sheets helpers
# -----------------------------
@st.cache_resource
def get_ws():
    creds = Credentials.from_service_account_info(dict(SECRETS["gcp_service_account"]), scopes=SCOPE)
    gc = gspread.authorize(creds)
    sh = gc.open(SHEET_NAME)
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(WORKSHEET_NAME, rows=800, cols=30)
        ws.append_row(HEADERS)
    return ws

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df = pd.DataFrame(columns=HEADERS)

    # Ensure all expected columns exist
    for col in HEADERS:
        if col not in df.columns:
            df[col] = "" if col not in ("rating", "id", "page_count") else 0

    # Types / defaults
    df["id"] = pd.to_numeric(df["id"], errors="ignore")
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)

    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(0).astype(int).clip(0, 5)
    df["page_count"] = pd.to_numeric(df["page_count"], errors="coerce").fillna(0).astype(int)

    str_cols = [
        "isbn", "title", "author", "notes", "thumbnail",
        "status", "date", "added_at",
        "published_date", "publisher", "categories", "language", "description"
    ]
    coerce_string(df, str_cols)

    # Default status
    df["status"] = df["status"].replace("", "Wishlist")

    # Keep column order stable
    return df[HEADERS]

def read_df() -> pd.DataFrame:
    df = get_as_dataframe(get_ws(), header=0, evaluate_formulas=True).dropna(how="all")
    return normalize_columns(df)

def write_df(df: pd.DataFrame):
    ws = get_ws()
    ws.clear()
    set_with_dataframe(ws, df[HEADERS])

def next_id(df: pd.DataFrame) -> int:
    return 1 if df.empty else int(pd.to_numeric(df["id"], errors="coerce").fillna(0).max()) + 1

def add_row(row: dict):
    df = read_df()
    row.setdefault("id", next_id(df))
    row.setdefault("added_at", datetime.now().isoformat(timespec="seconds"))
    row.setdefault("status", "Wishlist")
    row.setdefault("date", "")
    row.setdefault("rating", 0)
    # fill new metadata defaults
    row.setdefault("page_count", 0)
    row.setdefault("published_date", "")
    row.setdefault("publisher", "")
    row.setdefault("categories", "")
    row.setdefault("language", "")
    row.setdefault("description", "")
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    write_df(normalize_columns(df))

def delete_rows(ids: List[int]):
    df = read_df()
    df = df[~df["id"].isin(ids)]
    write_df(normalize_columns(df))

# -----------------------------
# ISBN utils
# -----------------------------
def clean_isbn(s: str) -> str:
    """Uppercase, keep digits and X only."""
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
    """Convert 978* ISBN-13 to ISBN-10 (returns None if not convertible)."""
    if not (isbn13.startswith("978") and validate_isbn13(isbn13)):
        return None
    core = isbn13[3:12]  # 9 digits
    total = sum((i + 1) * int(d) for i, d in enumerate(core))
    remainder = total % 11
    check = "X" if remainder == 10 else str(remainder)
    return core + check

def extract_isbn13_from_text(s: str) -> Optional[str]:
    """
    Find the first valid ISBN-13 sequence in a noisy scan string.
    Handles '978... 51299' (price add-on), '978...+51299', etc.
    """
    if not s:
        return None
    candidates = re.findall(r"\d{13}", s)
    # Prioritize 978/979
    candidates = sorted(candidates, key=lambda x: (not x.startswith(("978", "979")),))
    for c in candidates:
        if validate_isbn13(c):
            return c
    return None

# -----------------------------
# Barcode decoding
# -----------------------------
def decode_isbn_from_image(image_bytes: bytes) -> Optional[str]:
    if not ZXING_READY:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        results = zxingcpp.read_barcodes(img)
        for res in results:
            raw = (res.text or "").strip()
            if raw.isdigit() and len(raw) == 13 and validate_isbn13(raw):
                return raw
            found = extract_isbn13_from_text(raw)
            if found:
                return found
        return None
    except Exception:
        return None

# -----------------------------
# Lookups
# -----------------------------
def _google_books_query(q: str, api_key: Optional[str] = None):
    params = {"q": q, "maxResults": 1, "printType": "books"}
    if api_key:
        params["key"] = api_key
    r = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=10)
    if not r.ok:
        return None
    items = r.json().get("items", [])
    if not items:
        return None
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
    }

def google_books_lookup_by_isbn_any(isbn: str, api_key: Optional[str] = None):
    rec = _google_books_query(f"isbn:{isbn}", api_key)
    if rec:
        rec["isbn"] = isbn
        return rec
    if isbn.isdigit() and len(isbn) == 13 and isbn.startswith("978"):
        alt10 = isbn13_to_isbn10(isbn)
        if alt10:
            rec = _google_books_query(f"isbn:{alt10}", api_key)
            if rec:
                rec["isbn"] = isbn
                return rec
    return None

def openlibrary_lookup_by_isbn(isbn: str):
    try:
        r = requests.get(f"https://openlibrary.org/isbn/{isbn}.json", timeout=10)
        if not r.ok:
            return None
        data = r.json()
        title = data.get("title", "")
        authors = []
        for a in data.get("authors", []):
            key = a.get("key")
            if key:
                ar = requests.get(f"https://openlibrary.org{key}.json", timeout=10)
                if ar.ok:
                    authors.append(ar.json().get("name", ""))
        # try extra fields
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
        }
    except Exception:
        return None

def lookup_book_by_isbn_robust(isbn_raw: str):
    """Clean, validate & try Google Books (13/10) then OpenLibrary."""
    if not isbn_raw:
        return None
    s = isbn_raw.strip()
    if not (s.isdigit() and len(s) in (10, 13)):
        found = extract_isbn13_from_text(s)
        if found:
            s = found
    s = clean_isbn(s)
    if len(s) == 13 and not validate_isbn13(s):
        return None
    if len(s) not in (10, 13):
        return None

    rec = google_books_lookup_by_isbn_any(s, API_KEY or None)
    if rec:
        if "isbn" not in rec:
            rec["isbn"] = s
        # Ensure all keys exist
        for k in ["page_count", "published_date", "publisher", "categories", "language", "description"]:
            rec.setdefault(k, "" if k != "page_count" else 0)
        return rec

    rec = openlibrary_lookup_by_isbn(s)
    if rec:
        return rec
    if len(s) == 13:
        alt10 = isbn13_to_isbn10(s)
        if alt10 and validate_isbn10(alt10):
            rec = openlibrary_lookup_by_isbn(alt10)
            if rec:
                rec["isbn"] = s
                return rec
    return None

def google_books_search(query: str, limit=12):
    params = {"q": query, "maxResults": limit, "printType": "books"}
    if API_KEY:
        params["key"] = API_KEY
    r = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=10)
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        info = it.get("volumeInfo", {})
        ids = info.get("industryIdentifiers", []) or []
        isbn = ""
        for ident in ids:
            if ident.get("type") in ("ISBN_13", "ISBN_10"):
                isbn = ident.get("identifier", "")
                break
        out.append({
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
        })
    return out

# -----------------------------
# UI — Add books
# -----------------------------
st.title("📚 Karlas Book Logger")

with st.expander("➕ Add a book", expanded=True):
    tab_scan, tab_search, tab_manual = st.tabs(["📷 Scan ISBN", "🔎 Search", "✍️ Manual"])

    # --- Scan ---
    with tab_scan:
        img = st.camera_input("Take a photo of the barcode", help="Good light, fill the frame with the barcode.")
        if img is not None:
            isbn = decode_isbn_from_image(img.getvalue()) if ZXING_READY else None
            if isbn:
                st.success(f"Scanned ISBN: {isbn}")
                with st.spinner("Looking up book details..."):
                    meta = lookup_book_by_isbn_robust(isbn)
                if meta:
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        thumb = safe_url(meta.get("thumbnail"))
                        if thumb:
                            st.image(thumb)
                        else:
                            st.write("No cover")
                    with c2:
                        st.markdown(f"**{meta.get('title','')}**")
                        st.caption(meta.get("author", ""))
                        rating = st.slider("Rating", 0, 5, 0, key="scan_rating")
                        status = st.selectbox("Status", STATUS_OPTIONS, index=0, key="scan_status")
                        d = st.date_input("Date (optional)", value=None, format="YYYY-MM-DD", key="scan_date")
                        # show extra metadata (editable)
                        m_pages = st.number_input("Pages", min_value=0, step=1, value=int(meta.get("page_count") or 0))
                        m_pubdate = st.text_input("Published date", value=meta.get("published_date", ""))
                        m_publisher = st.text_input("Publisher", value=meta.get("publisher", ""))
                        m_categories = st.text_input("Categories", value=meta.get("categories", ""))
                        m_lang = st.text_input("Language", value=meta.get("language", ""))
                        m_desc = st.text_area("Description", value=meta.get("description", ""), height=120)
                        notes = st.text_area("Your Notes", key="scan_notes")
                        if st.button("Add to library", type="primary"):
                            add_row({
                                "isbn": meta.get("isbn", ""),
                                "title": meta.get("title", ""),
                                "author": meta.get("author", ""),
                                "rating": int(rating),
                                "notes": notes.strip(),
                                "thumbnail": meta.get("thumbnail", ""),
                                "status": status,
                                "date": d.isoformat() if isinstance(d, date) else "",
                                "page_count": int(m_pages or 0),
                                "published_date": m_pubdate.strip(),
                                "publisher": m_publisher.strip(),
                                "categories": m_categories.strip(),
                                "language": m_lang.strip(),
                                "description": m_desc.strip(),
                            })
                            st.success("Added!")
                            st.rerun()
                else:
                    st.error("No book found for that ISBN. Try Search or Manual.")
            else:
                st.info("Couldn’t detect the barcode—try again closer and well lit.")

    # --- Search ---
    with tab_search:
        q = st.text_input("Title / Author", placeholder="e.g., The Hobbit")
        if st.button("Search Google Books", type="primary"):
            with st.spinner("Searching..."):
                results = google_books_search(q.strip(), limit=12)
            if not results:
                st.info("No results.")
            else:
                grid_cols = st.slider("Grid columns", 3, 5, 4, key="search_cols")
                for i in range(0, len(results), grid_cols):
                    row_items = results[i:i + grid_cols]
                    cols = st.columns(len(row_items))
                    for col, r in zip(cols, row_items):
                        with col:
                            url = safe_url(r["thumbnail"])
                            if url:
                                st.image(url)
                            st.markdown(f"**{r['title']}**")
                            st.caption(r["author"])
                            st.text(f"ISBN: {r['isbn'] or '—'}")
                            st.caption(f"Pages: {r.get('page_count',0)}  ·  Pub: {r.get('published_date','')}")
                            rating = st.slider("Rating", 0, 5, 0, key=f"rate_{r['id']}")
                            status = st.selectbox("Status", STATUS_OPTIONS, index=0, key=f"status_{r['id']}")
                            d = st.date_input("Date", value=None, format="YYYY-MM-DD", key=f"date_{r['id']}")
                            notes = st.text_input("Notes", key=f"notes_{r['id']}")
                            if st.button("Add", key=f"add_{r['id']}", width="stretch"):
                                add_row({
                                    "isbn": r["isbn"],
                                    "title": r["title"],
                                    "author": r["author"],
                                    "rating": int(rating),
                                    "notes": notes.strip(),
                                    "thumbnail": r["thumbnail"],
                                    "status": status,
                                    "date": d.isoformat() if isinstance(d, date) else "",
                                    "page_count": int(r.get("page_count") or 0),
                                    "published_date": r.get("published_date",""),
                                    "publisher": r.get("publisher",""),
                                    "categories": r.get("categories",""),
                                    "language": r.get("language",""),
                                    "description": r.get("description",""),
                                })
                                st.success(f"Added “{r['title']}”")

    # --- Manual ---
    with tab_manual:
        st.markdown("#### A) Manual ISBN lookup")
        isbn_input = st.text_input("Enter ISBN (10 or 13)")
        col_l, col_r = st.columns([1,3])
        with col_l:
            if st.button("Lookup ISBN"):
                with st.spinner("Looking up..."):
                    meta = lookup_book_by_isbn_robust(isbn_input)
                if meta:
                    st.session_state["manual_prefill"] = meta
                    st.success("Found book! Prefilled below.")
                else:
                    st.warning("No book found for that ISBN.")
        with col_r:
            st.caption("Tip: Works with 13-digit ISBN best. If it fails, try removing spaces/dashes.")

        st.markdown("#### B) Manual add / edit fields")
        pre = st.session_state.get("manual_prefill", {})
        c1, c2 = st.columns(2)
        with c1:
            m_title = st.text_input("Title *", value=pre.get("title", ""))
            m_author = st.text_input("Author", value=pre.get("author", ""))
            m_isbn = st.text_input("ISBN", value=pre.get("isbn", ""))
            m_publisher = st.text_input("Publisher", value=pre.get("publisher", ""))
            m_pages = st.number_input("Pages", min_value=0, step=1, value=int(pre.get("page_count") or 0))
            m_pubdate = st.text_input("Published date", value=pre.get("published_date", ""))
        with c2:
            m_categories = st.text_input("Categories", value=pre.get("categories", ""))
            m_lang = st.text_input("Language", value=pre.get("language", ""))
            m_desc = st.text_area("Description", value=pre.get("description", ""), height=120)
            m_thumb = st.text_input("Cover URL (optional)", value=pre.get("thumbnail", ""))
            m_rating = st.slider("Rating", 0, 5, 0)
            m_status = st.selectbox("Status", STATUS_OPTIONS, index=0)
            m_date = st.date_input("Date (optional)", value=None, format="YYYY-MM-DD")
        m_notes = st.text_area("Your Notes")

        if st.button("Add manual", type="primary"):
            if not m_title.strip():
                st.warning("Title is required.")
            else:
                add_row({
                    "isbn": m_isbn.strip(),
                    "title": m_title.strip(),
                    "author": m_author.strip(),
                    "rating": int(m_rating),
                    "notes": m_notes.strip(),
                    "thumbnail": m_thumb.strip(),
                    "status": m_status,
                    "date": m_date.isoformat() if isinstance(m_date, date) else "",
                    "page_count": int(m_pages or 0),
                    "published_date": m_pubdate.strip(),
                    "publisher": m_publisher.strip(),
                    "categories": m_categories.strip(),
                    "language": m_lang.strip(),
                    "description": m_desc.strip(),
                })
                st.success(f"Added “{m_title}”")
                st.session_state.pop("manual_prefill", None)
                st.rerun()

st.divider()
st.subheader("📖 Your library")

# -----------------------------
# Load & list current entries
# -----------------------------
df = read_df()

# Filters & view
left, right = st.columns([3, 2])
with left:
    f_query = st.text_input("Filter", placeholder="Search title/author/ISBN/notes/categories/publisher")
with right:
    c1, c2, c3 = st.columns(3)
    with c1:
        f_min = st.selectbox("Min rating", options=[0, 1, 2, 3, 4, 5], index=0)
    with c2:
        f_status = st.selectbox("Status", options=["All"] + STATUS_OPTIONS, index=0)
    with c3:
        view = st.radio("View", options=["List", "Grid"], index=0, horizontal=True)

fdf = df.copy()
if f_query:
    ql = f_query.lower()
    mask = (
        fdf["title"].fillna("").str.lower().str.contains(ql) |
        fdf["author"].fillna("").str.lower().str.contains(ql) |
        fdf["isbn"].fillna("").str.lower().str.contains(ql) |
        fdf["notes"].fillna("").str.lower().str.contains(ql) |
        fdf["categories"].fillna("").str.lower().str.contains(ql) |
        fdf["publisher"].fillna("").str.lower().str.contains(ql)
    )
    fdf = fdf[mask]
if f_status != "All":
    fdf = fdf[fdf["status"] == f_status]
fdf = fdf[fdf["rating"] >= f_min].sort_values("added_at", ascending=False)

# Render Grid/List
todelete = []

if view == "Grid":
    cols_count = st.slider("Grid columns", 3, 6, 4, key="library_cols")
    items = list(fdf.to_dict(orient="records"))
    for i in range(0, len(items), cols_count):
        row_items = items[i:i + cols_count]
        cols = st.columns(len(row_items))
        for col, it in zip(cols, row_items):
            with col:
                card = st.container(border=True)
                with card:
                    url = safe_url(it.get("thumbnail"))
                    if url:
                        st.image(url)
                    else:
                        st.write("No cover")
                    st.markdown(f"**{it['title']}**")
                    st.caption(it.get("author", ""))
                    st.write(f"⭐ {int(it.get('rating', 0))} • {it.get('status', 'Wishlist')}")
                    st.write(f"ISBN: {it.get('isbn', '—')}")
                    extra = []
                    if it.get("page_count"):
                        extra.append(f"{int(it['page_count'])} pages")
                    if it.get("published_date"):
                        extra.append(it["published_date"])
                    if extra:
                        st.caption(" · ".join(extra))
                    if it.get("publisher"):
                        st.caption(f"Publisher: {it['publisher']}")
                    if it.get("categories"):
                        st.caption(f"Categories: {it['categories']}")
                    if it.get("language"):
                        st.caption(f"Lang: {it['language']}")
                    if it.get("notes"):
                        st.write(it["notes"])
                    if st.button("Delete", key=f"del_{it['id']}"):
                        todelete.append(int(it["id"]))
else:
    for _, row in fdf.iterrows():
        box = st.container(border=True)
        cols = box.columns([1, 5, 2])
        with cols[0]:
            thumb = safe_url(row.get("thumbnail"))
            if thumb:
                st.image(thumb)
            else:
                st.write("No cover")
        with cols[1]:
            st.markdown(f"**{row['title']}**")
            st.caption(row.get("author", ""))
            st.write(f"⭐ {int(row.get('rating', 0))} • {row.get('status', 'Wishlist')} • ISBN: {row.get('isbn', '—')}")
            extra = []
            if row.get("page_count"):
                extra.append(f"{int(row['page_count'])} pages")
            if row.get("published_date"):
                extra.append(row["published_date"])
            if extra:
                st.caption(" · ".join(extra))
            if row.get("publisher"):
                st.caption(f"Publisher: {row['publisher']}")
            if row.get("categories"):
                st.caption(f"Categories: {row['categories']}")
            if row.get("language"):
                st.caption(f"Lang: {row['language']}")
            if row.get("description"):
                with st.expander("Description"):
                    st.write(row["description"])
            if row.get("notes"):
                st.write(row["notes"])
            st.caption(f"Added: {row.get('added_at', '')}")
        with cols[2]:
            if st.button("Delete", key=f"del_{row['id']}"):
                todelete.append(int(row["id"]))

if todelete:
    delete_rows(todelete)
    st.success("Deleted.")
    st.rerun()

st.download_button(
    "⬇️ Export CSV",
    data=fdf.to_csv(index=False),
    file_name="books.csv",
    mime="text/csv",
)

st.divider()

# -----------------------------
# ADMIN: Edit all entries (optional manual edit of the Sheet)
# -----------------------------
st.subheader("✏️ Edit all entries (sheet view)")

with st.expander("Current entries (read-only preview)"):
    st.dataframe(df.sort_values("added_at", ascending=False), width="stretch", hide_index=True)

st.caption("You can edit the table below and click **Save changes** to update the Google Sheet.")
editable = df.copy()

# Use TextArea for long description
try:
    desc_col = st.column_config.TextAreaColumn("description")
except Exception:
    desc_col = st.column_config.TextColumn("description")

edited = st.data_editor(
    editable,
    width="stretch",
    num_rows="dynamic",
    column_config={
        "status": st.column_config.SelectboxColumn("status", options=STATUS_OPTIONS),
        "rating": st.column_config.NumberColumn("rating", min_value=0, max_value=5, step=1),
        "page_count": st.column_config.NumberColumn("page_count", min_value=0, step=1),
        "date": st.column_config.DateColumn("date", format="YYYY-MM-DD"),
        "published_date": st.column_config.TextColumn("published_date"),
        "publisher": st.column_config.TextColumn("publisher"),
        "categories": st.column_config.TextColumn("categories"),
        "language": st.column_config.TextColumn("language"),
        "thumbnail": st.column_config.TextColumn("thumbnail", help="Cover URL"),
        "notes": st.column_config.TextColumn("notes"),
        "description": desc_col,
        "isbn": st.column_config.TextColumn("isbn"),
        "title": st.column_config.TextColumn("title"),
        "author": st.column_config.TextColumn("author"),
        "id": st.column_config.NumberColumn("id", help="Row id (unique integer)"),
        "added_at": st.column_config.TextColumn("added_at", help="Auto timestamp (ISO)."),
    },
    hide_index=True,
    key="editor",
)

c1, c2 = st.columns([1, 3])
with c1:
    if st.button("Save changes", type="primary"):
        ed = normalize_columns(edited.copy())

        # Check ids unique & nonzero
        if ed["id"].duplicated().any():
            st.error("Duplicate ids found. Make sure each row has a unique 'id'.")
        else:
            ed.loc[ed["added_at"].eq(""), "added_at"] = datetime.now().isoformat(timespec="seconds")
            write_df(ed)
            st.success("Sheet updated.")
            st.rerun()
with c2:
    st.caption("Tip: To add a new row, scroll to the bottom of the editor and click “Add row”. Fill at least a title and a unique id.")
