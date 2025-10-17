import io
from datetime import datetime, date
from typing import List

import pandas as pd
import requests
import streamlit as st
from PIL import Image

import gspread
from google.oauth2.service_account import Credentials
from gspread_dataframe import get_as_dataframe, set_with_dataframe

# Try zxing-cpp for decoding (works on Streamlit Cloud)
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
SHEET_NAME = SECRETS["sheet"]["name"]
WORKSHEET_NAME = SECRETS["sheet"]["worksheet"]
API_KEY = SECRETS.get("google_books", {}).get("api_key", "")

HEADERS = [
    "id","isbn","title","author","rating","notes","thumbnail",
    "status","date","added_at"
]
STATUS_OPTIONS = ["Wishlist", "Reading", "Finished"]

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
        ws = sh.add_worksheet(WORKSHEET_NAME, rows=400, cols=20)
        ws.append_row(HEADERS)
    return ws

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df = pd.DataFrame(columns=HEADERS)
    # Ensure all expected columns exist
    for col in HEADERS:
        if col not in df.columns:
            df[col] = "" if col not in ("rating",) else 0
    # Types & defaults
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(0).astype(int).clip(0, 5)
    df["status"] = df["status"].replace("", "Wishlist")
    # Keep simple ISO strings for "date" and "added_at"
    df["date"] = df["date"].astype(str).where(df["date"].notna(), "")
    df["added_at"] = df["added_at"].astype(str).where(df["added_at"].notna(), "")
    # Column order
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
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    write_df(normalize_columns(df))

def delete_rows(ids: List[int]):
    df = read_df()
    df = df[~df["id"].isin(ids)]
    write_df(normalize_columns(df))

# -----------------------------
# Google Books
# -----------------------------
def google_books_lookup_by_isbn(isbn: str):
    if not isbn:
        return None
    params = {"q": f"isbn:{isbn}", "maxResults": 1}
    if API_KEY:
        params["key"] = API_KEY
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
        "isbn": isbn,
    }

def google_books_search(query: str, limit=12):
    params = {"q": query, "maxResults": limit}
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
            if ident.get("type") in ("ISBN_13","ISBN_10"):
                isbn = ident.get("identifier","")
                break
        out.append({
            "id": it.get("id"),
            "title": info.get("title",""),
            "author": ", ".join(info.get("authors", []) or []),
            "thumbnail": (info.get("imageLinks", {}) or {}).get("thumbnail",""),
            "isbn": isbn,
        })
    return out

# -----------------------------
# Barcode from camera photo
# -----------------------------
def decode_isbn_from_image(image_bytes: bytes) -> str | None:
    if not ZXING_READY:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        results = zxingcpp.read_barcodes(img)
        for res in results:
            txt = (res.text or "").strip()
            if txt.isdigit() and len(txt) in (10, 13):
                return txt
        return None
    except Exception:
        return None

# -----------------------------
# UI — Add books
# -----------------------------
st.title("📚 Simple Book Logger")

with st.expander("➕ Add a book", expanded=True):
    tab_scan, tab_search, tab_manual = st.tabs(["📷 Scan ISBN", "🔎 Search", "✍️ Manual"])

    # --- Scan ---
    with tab_scan:
        img = st.camera_input("Take a photo of the barcode", help="Good light, fill the frame with the barcode.")
        if img is not None:
            isbn = decode_isbn_from_image(img.getvalue()) if ZXING_READY else None
            if isbn:
                st.success(f"Scanned ISBN: {isbn}")
                with st.spinner("Looking up Google Books..."):
                    meta = google_books_lookup_by_isbn(isbn)
                if meta:
                    c1, c2 = st.columns([1,2])
                    with c1:
                        if meta.get("thumbnail"): st.image(meta["thumbnail"])
                    with c2:
                        st.markdown(f"**{meta['title']}**")
                        st.caption(meta.get("author",""))
                        rating = st.slider("Rating", 0, 5, 0, key="scan_rating")
                        status = st.selectbox("Status", STATUS_OPTIONS, index=0, key="scan_status")
                        d = st.date_input("Date (optional)", value=None, format="YYYY-MM-DD", key="scan_date")
                        notes = st.text_area("Notes", key="scan_notes")
                        if st.button("Add to library", type="primary"):
                            add_row({
                                "isbn": meta["isbn"],
                                "title": meta["title"],
                                "author": meta["author"],
                                "rating": int(rating),
                                "notes": notes.strip(),
                                "thumbnail": meta.get("thumbnail",""),
                                "status": status,
                                "date": d.isoformat() if isinstance(d, date) else "",
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
                    row_items = results[i:i+grid_cols]
                    cols = st.columns(len(row_items))
                    for col, r in zip(cols, row_items):
                        with col:
                            if r["thumbnail"]: st.image(r["thumbnail"])
                            st.markdown(f"**{r['title']}**")
                            st.caption(r["author"])
                            st.text(f"ISBN: {r['isbn'] or '—'}")
                            rating = st.slider("Rating", 0, 5, 0, key=f"rate_{r['id']}")
                            status = st.selectbox("Status", STATUS_OPTIONS, index=0, key=f"status_{r['id']}")
                            d = st.date_input("Date", value=None, format="YYYY-MM-DD", key=f"date_{r['id']}")
                            notes = st.text_input("Notes", key=f"notes_{r['id']}")
                            if st.button("Add", key=f"add_{r['id']}", use_container_width=True):
                                add_row({
                                    "isbn": r["isbn"],
                                    "title": r["title"],
                                    "author": r["author"],
                                    "rating": int(rating),
                                    "notes": notes.strip(),
                                    "thumbnail": r["thumbnail"],
                                    "status": status,
                                    "date": d.isoformat() if isinstance(d, date) else "",
                                })
                                st.success(f"Added “{r['title']}”")

    # --- Manual ---
    with tab_manual:
        c1, c2 = st.columns(2)
        with c1:
            m_title = st.text_input("Title *")
            m_author = st.text_input("Author")
            m_isbn = st.text_input("ISBN")
            m_notes = st.text_area("Notes")
        with c2:
            m_rating = st.slider("Rating", 0, 5, 0)
            m_status = st.selectbox("Status", STATUS_OPTIONS, index=0)
            m_date = st.date_input("Date (optional)", value=None, format="YYYY-MM-DD")
            m_thumb = st.text_input("Cover URL (optional)")
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
                })
                st.success(f"Added “{m_title}”")
                st.rerun()

st.divider()
st.subheader("📖 Your library")

# -----------------------------
# Load & list current entries
# -----------------------------
df = read_df()

# Filters & view
left, right = st.columns([3,2])
with left:
    f_query = st.text_input("Filter", placeholder="Search title/author/ISBN/notes")
with right:
    c1, c2, c3 = st.columns(3)
    with c1:
        f_min = st.selectbox("Min rating", options=[0,1,2,3,4,5], index=0)
    with c2:
        f_status = st.selectbox("Status", options=["All"] + STATUS_OPTIONS, index=0)
    with c3:
        view = st.radio("View", options=["List","Grid"], index=0, horizontal=True)

fdf = df.copy()
if f_query:
    ql = f_query.lower()
    mask = (
        fdf["title"].fillna("").str.lower().str.contains(ql) |
        fdf["author"].fillna("").str.lower().str.contains(ql) |
        fdf["isbn"].fillna("").str.lower().str.contains(ql) |
        fdf["notes"].fillna("").str.lower().str.contains(ql)
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
        row_items = items[i:i+cols_count]
        cols = st.columns(len(row_items))
        for col, it in zip(cols, row_items):
            with col:
                card = st.container(border=True)
                with card:
                    if it.get("thumbnail"):
                        st.image(it["thumbnail"])
                    st.markdown(f"**{it['title']}**")
                    st.caption(it.get("author",""))
                    st.write(f"⭐ {int(it.get('rating',0))} • {it.get('status','Wishlist')}")
                    st.write(f"ISBN: {it.get('isbn','—')}")
                    if it.get("date"):
                        st.caption(f"Date: {it['date']}")
                    if it.get("notes"):
                        st.write(it["notes"])
                    if st.button("Delete", key=f"del_{it['id']}"):
                        todelete.append(int(it["id"]))
else:
    for _, row in fdf.iterrows():
        box = st.container(border=True)
        cols = box.columns([1,5,2])
        with cols[0]:
            if row.get("thumbnail"):
                st.image(row["thumbnail"])
            else:
                st.write("No cover")
        with cols[1]:
            st.markdown(f"**{row['title']}**")
            st.caption(row.get("author",""))
            st.write(f"⭐ {int(row.get('rating',0))} • {row.get('status','Wishlist')} • ISBN: {row.get('isbn','—')}")
            if row.get("date"):
                st.caption(f"Date: {row['date']}")
            if row.get("notes"):
                st.write(row["notes"])
            st.caption(f"Added: {row.get('added_at','')}")
        with cols[2]:
            if st.button("Delete", key=f"del_{row['id']}"):
                todelete.append(int(row["id"]))

if todelete:
    delete_rows(todelete)
    st.success("Deleted.")
    st.rerun()

st.download_button("⬇️ Export CSV", data=fdf.to_csv(index=False), file_name="books.csv", mime="text/csv")

st.divider()

# -----------------------------
# ADMIN: Edit all entries (optional manual edit of the Sheet)
# -----------------------------
st.subheader("✏️ Edit all entries (sheet view)")

# Show the current entire table first (read-only preview)
with st.expander("Current entries (read-only preview)"):
    st.dataframe(df.sort_values("added_at", ascending=False), use_container_width=True, hide_index=True)

st.caption("You can edit the table below and click **Save changes** to update the Google Sheet.")
editable = df.copy()
# Make id & added_at visible but discourage editing:
# We'll allow editing but validate; Streamlit doesn't have per-column disable in data_editor.
edited = st.data_editor(
    editable,
    use_container_width=True,
    num_rows="dynamic",
    column_config={
        "status": st.column_config.SelectboxColumn("status", options=STATUS_OPTIONS),
        "rating": st.column_config.NumberColumn("rating", min_value=0, max_value=5, step=1),
        "date": st.column_config.DateColumn("date", format="YYYY-MM-DD"),
        "thumbnail": st.column_config.TextColumn("thumbnail", help="Cover URL"),
        "notes": st.column_config.TextColumn("notes"),
        "isbn": st.column_config.TextColumn("isbn"),
        "title": st.column_config.TextColumn("title"),
        "author": st.column_config.TextColumn("author"),
        "id": st.column_config.NumberColumn("id", help="Row id (keep unique integers)"),
        "added_at": st.column_config.TextColumn("added_at", help="Auto timestamp (ISO)."),
    },
    hide_index=True,
    key="editor",
)

c1, c2 = st.columns([1,3])
with c1:
    if st.button("Save changes", type="primary"):
        # Validate & normalize
        ed = edited.copy()
        ed = normalize_columns(ed)

        # Check ids unique & nonzero
        if ed["id"].duplicated().any():
            st.error("Duplicate ids found. Make sure each row has a unique 'id'.")
        else:
            # Ensure added_at present
            ed.loc[ed["added_at"].eq(""), "added_at"] = datetime.now().isoformat(timespec="seconds")
            write_df(ed)
            st.success("Sheet updated.")
            st.rerun()
with c2:
    st.caption("Tip: To add a new row, scroll to the bottom of the editor and click “Add row”. Fill at least a title and a unique id.")

