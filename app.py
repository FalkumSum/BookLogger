import streamlit as st
from datetime import date, datetime
import pandas as pd

from src.models import Book, STATUS_OPTIONS
from src.utils import safe_url
from src.repository import read_all, write_all, add_book, delete_ids
from src.services import BookLookupService
from src import ocr as ocrmod

from src.barcode import decode_isbn

st.set_page_config(page_title="Book Logger", page_icon="📚", layout="wide")

# Secrets guard
def require_secret(section: str, key: str, example: str = ""):
    if section not in st.secrets:
        st.error(f"Missing secrets section: [{section}]"); 
        if example: st.code(example, language="toml"); st.stop()
    if key not in st.secrets[section]:
        st.error(f"Missing secret: [{section}].{key}"); 
        if example: st.code(example, language="toml"); st.stop()
    return st.secrets[section][key]

require_secret("sheet", "name", example='[sheet]\nname = "book_logger"\nworksheet = "books"')
require_secret("sheet", "worksheet")

GOOGLE_KEY = st.secrets.get("google_books", {}).get("api_key", "")
ISBNDB_KEY = st.secrets.get("isbndb", {}).get("api_key", "")

svc = BookLookupService(google_api_key=GOOGLE_KEY, isbndb_key=ISBNDB_KEY)

st.title("📚 Simple Book Logger (modular)")

with st.expander("➕ Add a book", expanded=True):
    tab_scan, tab_cover, tab_search, tab_manual = st.tabs(
        ["📷 Scan ISBN", "📸 Cover OCR", "🔎 Search", "✍️ Manual"]
    )

    # ---- Scan ISBN ----
    with tab_scan:
        img = st.camera_input("Take a photo of the BARCODE", help="Fill the frame; good light.")
        if img is not None:
            isbn = decode_isbn(img.getvalue())
            if isbn:
                st.success(f"Scanned ISBN: {isbn}")
                with st.spinner("Looking up metadata..."):
                    meta = svc.by_isbn(isbn)
                if meta:
                    colL, colR = st.columns([1,2])
                    with colL:
                        if safe_url(meta.get("thumbnail")):
                            st.image(meta["thumbnail"])
                        else:
                            st.write("No cover")
                    with colR:
                        st.markdown(f"**{meta.get('title','')}**")
                        st.caption(meta.get("author",""))
                        rating = st.slider("Rating", 0, 5, 0, key="scan_rating")
                        status = st.selectbox("Status", STATUS_OPTIONS, index=0, key="scan_status")
                        d = st.date_input("Date (optional)", value=None, format="YYYY-MM-DD", key="scan_date")
                        # editable meta
                        m_pages = st.number_input("Pages", 0, 5000, int(meta.get("page_count", 0)))
                        m_pubdate = st.text_input("Published date", value=meta.get("published_date",""))
                        m_publisher = st.text_input("Publisher", value=meta.get("publisher",""))
                        m_categories = st.text_input("Categories", value=meta.get("categories",""))
                        m_lang = st.text_input("Language", value=meta.get("language",""))
                        m_desc = st.text_area("Description", value=meta.get("description",""))
                        notes = st.text_area("Your Notes", key="scan_notes")
                        if st.button("Add to library", type="primary"):
                            add_book(Book(
                                isbn=meta.get("isbn", isbn),
                                title=meta.get("title",""),
                                author=meta.get("author",""),
                                rating=int(rating),
                                notes=notes.strip(),
                                thumbnail=meta.get("thumbnail",""),
                                status=status,
                                date=d.isoformat() if isinstance(d, date) else "",
                                page_count=int(m_pages or 0),
                                published_date=m_pubdate.strip(),
                                publisher=m_publisher.strip(),
                                categories=m_categories.strip(),
                                language=m_lang.strip(),
                                description=m_desc.strip(),
                                source=meta.get("source","unknown"),
                            ))
                            st.success("Added!")
                            st.rerun()
                else:
                    st.warning("No match. Try Cover OCR or Manual.")
            else:
                st.info("Barcode not detected—try again closer and well lit.")

    # ---- Cover OCR ----
    with tab_cover:
        # If OCR (Vision) isn’t available, warn but keep the UI working.
        if not getattr(ocrmod, "AVAILABLE", False):
            st.warning(
                "Cover OCR is unavailable (Vision SDK not installed or credentials missing). "
                "Install `google-cloud-vision` and set `[gcp_service_account]` in secrets to enable this."
            )
    
        img = st.camera_input(
            "Take a photo of the FRONT COVER",
            help="Avoid glare; center the title area."
        )
    
        if img is not None:
            # Use the safe OCR module wrapper
            with st.spinner("Reading cover text..."):
                text = ocrmod.extract_text(img.getvalue())
    
            if not text:
                st.info("No text detected. Try retaking the photo.")
            else:
                with st.expander("📝 Extracted text"):
                    st.code(text)
    
                # Heuristic guess for title/author
                guess_t, guess_a = ocrmod.guess_title_author(text)
                st.markdown("**Best guess**")
                c1, c2 = st.columns(2)
                with c1:
                    title_guess = st.text_input("Title (guess)", value=guess_t, key="ocr_guess_title")
                with c2:
                    author_guess = st.text_input("Author (guess)", value=guess_a, key="ocr_guess_author")
    
                # Search candidates on Google Books using extracted text (or title guess)
                q_default = (guess_t or text.replace("\n", " "))[:120]
                q = st.text_input("Refine search query", value=q_default, key="ocr_search_q")
    
                if st.button("Find candidates", key="ocr_find_candidates"):
                    with st.spinner("Searching..."):
                        results = svc.search_text(q, limit=8)
    
                    if not results:
                        st.info("No candidates. You can still add manually below.")
                    else:
                        cols = st.columns(2)
                        for i, r in enumerate(results):
                            with cols[i % 2]:
                                thumb = safe_url(r.get("thumbnail"))
                                if thumb:
                                    st.image(thumb)
                                st.markdown(f"**{r.get('title','')}**")
                                st.caption(r.get("author", ""))
                                st.caption(f"ISBN: {r.get('isbn') or '—'} • Pages: {r.get('page_count', 0)}")
    
                                rating = st.slider("Rating", 0, 5, 0, key=f"ocr_rate_{r['id']}")
                                status = st.selectbox("Status", STATUS_OPTIONS, index=0, key=f"ocr_status_{r['id']}")
                                d = st.date_input("Date", value=None, format="YYYY-MM-DD", key=f"ocr_date_{r['id']}")
                                notes = st.text_input("Notes", key=f"ocr_notes_{r['id']}")
    
                                if st.button("Add this", key=f"ocr_add_{r['id']}"):
                                    add_book(Book(
                                        isbn=r.get("isbn",""),
                                        title=r.get("title",""),
                                        author=r.get("author",""),
                                        rating=int(rating),
                                        notes=notes.strip(),
                                        thumbnail=r.get("thumbnail",""),
                                        status=status,
                                        date=d.isoformat() if isinstance(d, date) else "",
                                        page_count=int(r.get("page_count") or 0),
                                        published_date=r.get("published_date",""),
                                        publisher=r.get("publisher",""),
                                        categories=r.get("categories",""),
                                        language=r.get("language",""),
                                        description=r.get("description",""),
                                        source=r.get("source","google-search"),
                                    ))
                                    st.success(f"Added “{r.get('title','(untitled)')}”")
    
                # --- Manual add from the guesses ---
                st.markdown("**Or add manually from the guess**")
                m1, m2 = st.columns(2)
                with m1:
                    m_title = st.text_input("Title *", value=title_guess, key="ocr_m_title")
                    m_author = st.text_input("Author", value=author_guess, key="ocr_m_author")
                    m_publisher = st.text_input("Publisher", key="ocr_m_publisher")
                    m_pages = st.number_input("Pages", min_value=0, step=1, value=0, key="ocr_m_pages")
                    m_pubdate = st.text_input("Published date", key="ocr_m_pubdate")
                with m2:
                    m_categories = st.text_input("Categories", key="ocr_m_categories")
                    m_lang = st.text_input("Language", key="ocr_m_lang")
                    m_desc = st.text_area("Description", height=120, key="ocr_m_desc")
                    m_thumb = st.text_input("Cover URL (optional)", key="ocr_m_thumb")
                    m_rating = st.slider("Rating", 0, 5, 0, key="ocr_m_rating")
                    m_status = st.selectbox("Status", STATUS_OPTIONS, index=0, key="ocr_m_status")
                    m_date = st.date_input("Date (optional)", value=None, format="YYYY-MM-DD", key="ocr_m_date")
    
                m_notes = st.text_area("Your Notes", key="ocr_m_notes")
    
                if st.button("Add manual (from OCR)", type="primary", key="ocr_m_add"):
                    if not m_title.strip():
                        st.warning("Title is required.")
                    else:
                        add_book(Book(
                            isbn="",
                            title=m_title.strip(),
                            author=m_author.strip(),
                            rating=int(m_rating),
                            notes=m_notes.strip(),
                            thumbnail=m_thumb.strip(),
                            status=m_status,
                            date=m_date.isoformat() if isinstance(m_date, date) else "",
                            page_count=int(m_pages or 0),
                            published_date=m_pubdate.strip(),
                            publisher=m_publisher.strip(),
                            categories=m_categories.strip(),
                            language=m_lang.strip(),
                            description=m_desc.strip(),
                            source="cover-ocr",
                        ))
                        st.success(f"Added “{m_title}”")
                        st.rerun()


    # ---- Search by text ----
    with tab_search:
        q = st.text_input("Title / Author", placeholder="e.g., The Hobbit")
        if st.button("Search", type="primary"):
            with st.spinner("Searching..."):
                results = svc.search_text(q.strip(), limit=12)
            if not results:
                st.info("No results.")
            else:
                grid_cols = st.slider("Grid columns", 3, 5, 4, key="search_cols")
                for i in range(0, len(results), grid_cols):
                    row = results[i:i+grid_cols]
                    cols = st.columns(len(row))
                    for col, r in zip(cols, row):
                        with col:
                            if safe_url(r["thumbnail"]): st.image(r["thumbnail"])
                            st.markdown(f"**{r['title']}**")
                            st.caption(r["author"])
                            st.text(f"ISBN: {r['isbn'] or '—'}")
                            st.caption(f"Pages: {r.get('page_count',0)}  ·  Pub: {r.get('published_date','')}")
                            rating = st.slider("Rating", 0, 5, 0, key=f"rate_{r['id']}")
                            status = st.selectbox("Status", STATUS_OPTIONS, index=0, key=f"status_{r['id']}")
                            d = st.date_input("Date", value=None, format="YYYY-MM-DD", key=f"date_{r['id']}")
                            notes = st.text_input("Notes", key=f"notes_{r['id']}")
                            if st.button("Add", key=f"add_{r['id']}", type="primary"):
                                add_book(Book(
                                    isbn=r["isbn"],
                                    title=r["title"],
                                    author=r["author"],
                                    rating=int(rating),
                                    notes=notes.strip(),
                                    thumbnail=r["thumbnail"],
                                    status=status,
                                    date=d.isoformat() if isinstance(d, date) else "",
                                    page_count=int(r.get("page_count") or 0),
                                    published_date=r.get("published_date",""),
                                    publisher=r.get("publisher",""),
                                    categories=r.get("categories",""),
                                    language=r.get("language",""),
                                    description=r.get("description",""),
                                    source=r.get("source","google-search"),
                                ))
                                st.success(f"Added “{r['title']}”")

    # ---- Manual ----
    with tab_manual:
        st.markdown("Use ISBN lookup in the 'Scan' tab, or add everything by hand here.")
        c1, c2 = st.columns(2)
        with c1:
            m_title = st.text_input("Title *")
            m_author = st.text_input("Author")
            m_isbn = st.text_input("ISBN")
            m_publisher = st.text_input("Publisher")
            m_pages = st.number_input("Pages", min_value=0, step=1, value=0)
            m_pubdate = st.text_input("Published date")
        with c2:
            m_categories = st.text_input("Categories")
            m_lang = st.text_input("Language")
            m_desc = st.text_area("Description", height=120)
            m_thumb = st.text_input("Cover URL (optional)")
            m_rating = st.slider("Rating", 0, 5, 0)
            m_status = st.selectbox("Status", STATUS_OPTIONS, index=0)
            m_date = st.date_input("Date (optional)", value=None, format="YYYY-MM-DD")
        m_notes = st.text_area("Your Notes")
        if st.button("Add manual", type="primary"):
            if not m_title.strip():
                st.warning("Title is required.")
            else:
                add_book(Book(
                    isbn=m_isbn.strip(),
                    title=m_title.strip(),
                    author=m_author.strip(),
                    rating=int(m_rating),
                    notes=m_notes.strip(),
                    thumbnail=m_thumb.strip(),
                    status=m_status,
                    date=m_date.isoformat() if isinstance(m_date, date) else "",
                    page_count=int(m_pages or 0),
                    published_date=m_pubdate.strip(),
                    publisher=m_publisher.strip(),
                    categories=m_categories.strip(),
                    language=m_lang.strip(),
                    description=m_desc.strip(),
                    source="manual",
                ))
                st.success(f"Added “{m_title}”")
                st.rerun()

st.divider()
st.subheader("📖 Your library")

df = read_all()

# Filters
left, right = st.columns([3,2])
with left:
    f_query = st.text_input("Filter", placeholder="Search title/author/ISBN/notes/categories/publisher")
with right:
    c1, c2, c3 = st.columns(3)
    with c1: f_min = st.selectbox("Min rating", [0,1,2,3,4,5], index=0)
    with c2: f_status = st.selectbox("Status", ["All"] + STATUS_OPTIONS, index=0)
    with c3: view = st.radio("View", ["List","Grid"], index=0, horizontal=True)

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

todelete = []

if view == "Grid":
    cols_count = st.slider("Grid columns", 3, 6, 4, key="library_cols")
    items = list(fdf.to_dict(orient="records"))
    for i in range(0, len(items), cols_count):
        row = items[i:i+cols_count]
        cols = st.columns(len(row))
        for col, it in zip(cols, row):
            with col:
                card = st.container(border=True)
                with card:
                    if safe_url(it.get("thumbnail")): st.image(it["thumbnail"])
                    else: st.write("No cover")
                    st.markdown(f"**{it['title']}**")
                    st.caption(it.get("author",""))
                    st.write(f"⭐ {int(it.get('rating',0))} • {it.get('status','Wishlist')} • Src: {it.get('source','')}")
                    st.write(f"ISBN: {it.get('isbn','—')}")
                    extra = []
                    if it.get("page_count"): extra.append(f"{int(it['page_count'])} pages")
                    if it.get("published_date"): extra.append(it["published_date"])
                    if extra: st.caption(" · ".join(extra))
                    if it.get("publisher"): st.caption(f"Publisher: {it['publisher']}")
                    if it.get("categories"): st.caption(f"Categories: {it['categories']}")
                    if it.get("language"): st.caption(f"Lang: {it['language']}")
                    if it.get("notes"): st.write(it["notes"])
                    if st.button("Delete", key=f"del_{it['id']}"):
                        todelete.append(int(it["id"]))
else:
    for _, row in fdf.iterrows():
        box = st.container(border=True)
        cols = box.columns([1,5,2])
        with cols[0]:
            if safe_url(row.get("thumbnail")): st.image(row["thumbnail"])
            else: st.write("No cover")
        with cols[1]:
            st.markdown(f"**{row['title']}**")
            st.caption(row.get("author",""))
            st.write(f"⭐ {int(row.get('rating',0))} • {row.get('status','Wishlist')} • Src: {row.get('source','')}")
            st.write(f"ISBN: {row.get('isbn','—')}")
            extra = []
            if row.get("page_count"): extra.append(f"{int(row['page_count'])} pages")
            if row.get("published_date"): extra.append(row["published_date"])
            if extra: st.caption(" · ".join(extra))
            if row.get("publisher"): st.caption(f"Publisher: {row['publisher']}")
            if row.get("categories"): st.caption(f"Categories: {row['categories']}")
            if row.get("language"): st.caption(f"Lang: {row['language']}")
            if row.get("description"):
                with st.expander("Description"): st.write(row["description"])
            if row.get("notes"): st.write(row["notes"])
            st.caption(f"Added: {row.get('added_at','')}")
        with cols[2]:
            if st.button("Delete", key=f"del_{row['id']}"):
                todelete.append(int(row["id"]))

if todelete:
    delete_ids(todelete)
    st.success("Deleted.")
    st.rerun()

st.download_button("⬇️ Export CSV", data=fdf.to_csv(index=False), file_name="books.csv", mime="text/csv")

st.divider()
st.subheader("✏️ Edit all entries (sheet view)")

with st.expander("Current entries (read-only preview)"):
    st.dataframe(df.sort_values("added_at", ascending=False), width="stretch", hide_index=True)

st.caption("You can edit the table below and click **Save changes** to update the Google Sheet.")

editable = df.copy()

# Convert 'date' strings → datetime for the editor
if "date" in editable.columns:
    editable["date"] = pd.to_datetime(editable["date"], errors="coerce")

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
        "source": st.column_config.TextColumn("source"),
    },
    hide_index=True,
    key="editor",
)

ed = edited.copy()

# Convert datetime/date → ISO string for saving
if "date" in ed.columns:
    def _to_iso(x):
        if pd.isna(x):
            return ""
        if isinstance(x, pd.Timestamp):
            return x.date().isoformat()
        if isinstance(x, ddate):
            return x.isoformat()
        # If something slipped through as string already:
        s = str(x).strip()
        try:
            return pd.to_datetime(s, errors="coerce").date().isoformat()
        except Exception:
            return s
    ed["date"] = ed["date"].apply(_to_iso)

# Now continue with your duplicate-id check and write_all(ed)
if ed["id"].duplicated().any():
    st.error("Duplicate ids found. Make sure each row has a unique 'id'.")
else:
    write_all(ed)
    st.success("Sheet updated.")
    st.rerun()

c1, c2 = st.columns([1,3])
with c1:
    if st.button("Save changes", type="primary"):
        # validate IDs
        if edited["id"].duplicated().any():
            st.error("Duplicate ids found. Make sure each row has a unique 'id'.")
        else:
            write_all(edited)
            st.success("Sheet updated.")
            st.rerun()
with c2:
    st.caption("Tip: Add a row at the bottom to insert new entries here.")
