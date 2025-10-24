# app.py
# Book Logger — Lite (Custom Columns) with Google Books + Saxo title/author search
# Uses only free sources (Google Books public API without key is fine).
# Requires: streamlit, pandas, gspread, google-auth, gspread_dataframe, requests, beautifulsoup4
# Also add a service account to .streamlit/secrets.toml (see comments below).

import re
from datetime import date
from typing import List, Optional
import html, unicodedata
import math

import pandas as pd
import requests
import streamlit as st

import gspread
from google.oauth2.service_account import Credentials
from gspread_dataframe import get_as_dataframe, set_with_dataframe

# Local scraper helpers (make sure scraper.py is in the same folder)
from scraper import (
    search_saxo_by_title,
    search_saxo_by_author,
)

# ==========================================
# App config
# ==========================================
st.set_page_config(page_title="Karlas Book Logger", page_icon="📚", layout="wide")

# Required secrets
# In .streamlit/secrets.toml, add:
# [sheet]
# name = "book_logger"
# worksheet = "books"
# [google_books]
# api_key = "YOUR_OPTIONAL_API_KEY"  # optional
#
# [gcp_service_account]
# type = "service_account"
# project_id = "..."
# private_key_id = "..."
# private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
# client_email = "..."
# client_id = "..."

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


SHEET_NAME = require_secret(
    "sheet",
    "name",
    example="""[sheet]\nname = 'book_logger'\nworksheet = 'books'""",
)
WORKSHEET_NAME = require_secret("sheet", "worksheet")
API_KEY = SECRETS.get("google_books", {}).get("api_key", "")

# ==========================================
# Target Google Sheet columns (order + names EXACTLY as requested)
# ==========================================
HEADERS = [
    "index",
    "Title",
    "Author",
    "Page count",
    "ISBN-13",
    "Published date",
    "ISBN-10",
    "Read date",
    "Rating",
    "Notes",
    "Thumbnail",
]

# ==========================================
# Utilities
# ==========================================

def safe_url(u: Optional[str]):
    if isinstance(u, str):
        u = u.strip()
        if u.lower().startswith(("http://", "https://")) and len(u) > 7:
            return u.replace("http://", "https://")
    return None

def clean_isbn(s: str) -> str:
    return re.sub(r"[^0-9X]", "", (s or "").upper())

def validate_isbn13(isbn13: str) -> bool:
    if not (isbn13.isdigit() and len(isbn13) == 13):
        return False
    total = sum((int(d) * (1 if i % 2 == 0 else 3)) for i, d in enumerate(isbn13[:12]))
    check = (10 - (total % 10)) % 10
    return check == int(isbn13[-1])

def isbn13_to_isbn10(isbn13: str) -> Optional[str]:
    if not (isbn13.startswith("978") and validate_isbn13(isbn13)):
        return None
    core = isbn13[3:12]
    total = sum((i + 1) * int(d) for i, d in enumerate(core))
    remainder = total % 11
    check = "X" if remainder == 10 else str(remainder)
    return core + check

def normalize_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = html.unescape(str(s))
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def extract_year(pubdate: str) -> str:
    m = re.search(r"(19|20)\d{2}", pubdate or "")
    return m.group(0) if m else ""

def best_cover_link(image_links: dict) -> str:
    links = image_links or {}
    for key in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
        url = links.get(key)
        if url:
            return url.strip().replace("http://", "https://")
    return ""

def _extract_isbns(info: dict) -> tuple[str, str]:
    isbn13, isbn10 = "", ""
    for ident in info.get("industryIdentifiers", []) or []:
        t = ident.get("type")
        v = (ident.get("identifier") or "").strip()
        if t == "ISBN_13" and not isbn13:
            isbn13 = v
        if t == "ISBN_10" and not isbn10:
            isbn10 = v
    if not isbn10 and isbn13 and isbn13.startswith("978") and validate_isbn13(isbn13):
        maybe10 = isbn13_to_isbn10(isbn13)
        if maybe10:
            isbn10 = maybe10
    return isbn13, isbn10

def normalize_lang_pref(raw: str | None) -> Optional[str]:
    """
    Accepts: 'da', 'danish', 'dansk', 'dk' (any case/whitespace) -> 'da'
    Empty/None or 'auto' -> None (no restriction)
    Other 2-letter codes (e.g., 'en', 'de') pass through.
    """
    if not raw:
        return None
    s = raw.strip().lower()
    if s in ("", "auto", "any", "all", "alle"):
        return None
    if s in ("da", "danish", "dansk", "dk"):
        return "da"
    m = re.fullmatch(r"[a-z]{2}", s)
    return m.group(0) if m else None


# ==========================================
# Google Sheets
# ==========================================

SCOPE_SHEETS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def get_ws():
    creds = Credentials.from_service_account_info(dict(SECRETS["gcp_service_account"]), scopes=SCOPE_SHEETS)
    gc = gspread.authorize(creds)
    sh = gc.open(SHEET_NAME)
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(WORKSHEET_NAME, rows=1000, cols=40)
        ws.append_row(HEADERS)
    return ws

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df = pd.DataFrame(columns=HEADERS)
    for col in HEADERS:
        if col not in df.columns:
            df[col] = "" if col not in ("index", "Rating", "Page count") else 0
    df["index"] = pd.to_numeric(df["index"], errors="coerce").fillna(0).astype(int)
    df["Rating"] = pd.to_numeric(df["Rating"], errors="coerce").fillna(0).astype(int).clip(0, 5)
    df["Page count"] = pd.to_numeric(df["Page count"], errors="coerce").fillna(0).astype(int)
    str_cols = [c for c in HEADERS if c not in ("index", "Rating", "Page count")]
    for c in str_cols:
        df[c] = df[c].astype("string").fillna("")
        df[c] = df[c].replace("nan", "")
    return df[HEADERS]

def read_df() -> pd.DataFrame:
    df = get_as_dataframe(get_ws(), header=0, evaluate_formulas=True).dropna(how="all")
    return normalize_columns(df)

def write_df(df: pd.DataFrame):
    ws = get_ws()
    ws.clear()
    set_with_dataframe(ws, df[HEADERS])

def next_index(df: pd.DataFrame) -> int:
    return 1 if df.empty else int(pd.to_numeric(df["index"], errors="coerce").fillna(0).max()) + 1

def add_row(row: dict):
    df = read_df()
    row.setdefault("index", next_index(df))
    for k, default in {
        "Title": "",
        "Author": "",
        "Page count": 0,
        "ISBN-13": "",
        "Published date": "",
        "ISBN-10": "",
        "Read date": "",
        "Rating": 0,
        "Notes": "",
        "Thumbnail": "",
    }.items():
        row.setdefault(k, default)
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    write_df(normalize_columns(df))

def delete_rows(indices: List[int]):
    df = read_df()
    df = df[~df["index"].isin(indices)]
    write_df(normalize_columns(df))

# ==========================================
# Google Books search (Title/Author/Keywords/ISBN) with language priority
# ==========================================

GB_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

def _looks_like_isbn(q: str) -> Optional[str]:
    cand = clean_isbn(q)
    if len(cand) == 13 and cand.isdigit():
        return cand
    if len(cand) == 10 and re.fullmatch(r"\d{9}[\dX]", cand):
        return cand
    return None

def _looks_like_author(q: str) -> bool:
    # Heuristic: 2–4 words, letters, no digits, not already using operators
    ql = q.lower()
    if any(tok in ql for tok in (":", " isbn", " intitle", " inauthor")):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ'\-]+", q)
    return (2 <= len(words) <= 4) and (not re.search(r"\d", q))

def _search_gbooks(params):
    r = requests.get(GB_ENDPOINT, params=params, timeout=10)
    if not r.ok:
        return []
    return r.json().get("items", []) or []

def google_books_search(query: str, limit=250, prefer_lang: Optional[str] = "da"):
    """
    Smart search + language priority:
      - If looks like ISBN -> isbn:<query>
      - If looks like author -> inauthor:"q" (+ fallbacks)
      - Else -> q (+ inauthor/intitle fallbacks)
    If prefer_lang is provided (e.g., "da"), we first fetch with langRestrict,
    then fetch globally and de-dup (preferred language first).
    """
    if not query or not query.strip():
        return []

    q = normalize_text(query)
    base_params = {"maxResults": min(limit, 40), "printType": "books"}
    if API_KEY:
        base_params["key"] = API_KEY

    def _attempts_for(qs: str):
        isbn = _looks_like_isbn(qs)
        if isbn:
            return [f"isbn:{isbn}"]
        elif _looks_like_author(qs):
            return [f'inauthor:"{qs}"', f'intitle:"{qs}"', qs]
        else:
            return [qs, f'inauthor:"{qs}"', f'intitle:"{qs}"']

    attempts = _attempts_for(q)

    def _run_attempts(lang: Optional[str]) -> list:
        params = base_params.copy()
        if lang:
            params["langRestrict"] = lang
        results = []
        for at in attempts:
            params["q"] = at
            results.extend(_search_gbooks(params))
            if len(results) >= limit:
                break
        return results

    items: list = []
    if prefer_lang:
        items.extend(_run_attempts(prefer_lang))
    if len(items) < limit:
        items.extend(_run_attempts(None))

    seen, uniq = set(), []
    for it in items:
        vid = it.get("id")
        if vid and vid not in seen:
            seen.add(vid)
            uniq.append(it)
        if len(uniq) >= limit:
            break

    out = []
    for it in uniq:
        info = it.get("volumeInfo", {}) or {}
        isbn13, isbn10 = _extract_isbns(info)
        title = normalize_text(info.get("title", ""))
        author = normalize_text(", ".join(info.get("authors", []) or []))
        thumb = best_cover_link(info.get("imageLinks", {}) or {})
        pubdate = normalize_text(info.get("publishedDate", ""))
        out.append({
            "id": it.get("id"),
            "Title": title,
            "Author": author,
            "Thumbnail": thumb,
            "ISBN-13": isbn13,
            "ISBN-10": isbn10,
            "Page count": info.get("pageCount") or 0,
            "Published date": pubdate,
            "language": info.get("language", ""),
        })
    return out

# ==========================================
# UI
# ==========================================

st.title("📚 Karlas Book Logger ")

# Keep a single, reusable Entry form state
if "add_form" not in st.session_state:
    st.session_state.add_form = {
        "Title": "",
        "Author": "",
        "Page count": 0,
        "ISBN-13": "",
        "Published date": "",
        "ISBN-10": "",
        "Read date": None,
        "Rating": 0,
        "Notes": "",
        "Thumbnail": "",
    }

# Search session state slots
st.session_state.setdefault("search_results_text", [])
st.session_state.setdefault("page_text", 0)

st.session_state.setdefault("saxo_results", [])
st.session_state.setdefault("saxo_page", 0)

st.session_state.setdefault("saxo_author_results", [])
st.session_state.setdefault("saxo_author_page", 0)

# -----------------------------
# SECTION 1 — Entries
# -----------------------------
with st.expander("📝 Entry", expanded=True):
    form = st.session_state.add_form
    c1, c2 = st.columns(2)
    with c1:
        form["Title"] = st.text_input("Title *", value=form.get("Title", ""))
        form["Author"] = st.text_input("Author", value=form.get("Author", ""))
        form["ISBN-13"] = st.text_input("ISBN-13", value=form.get("ISBN-13", ""))
        form["ISBN-10"] = st.text_input("ISBN-10", value=form.get("ISBN-10", ""))
        form["Page count"] = st.number_input("Page count", min_value=0, step=1, value=int(form.get("Page count", 0)))
        form["Published date"] = st.text_input("Published date", value=form.get("Published date", ""))
    with c2:
        rd = form.get("Read date")
        if isinstance(rd, str) and rd:
            try:
                rd = pd.to_datetime(rd, errors="coerce").date()
            except Exception:
                rd = None
        form["Read date"] = st.date_input("Read date (optional)", value=rd, format="YYYY-MM-DD")
        stars = ["0" ,"⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
        current_rating = int(st.session_state.add_form.get("Rating", 3))
        
        selected = st.radio(
            "Rating",
            options=range(6),
            format_func=lambda i: stars[i],
            index=current_rating,
            horizontal=True,
            key="rating_radio",
        )
        
        # Immediately sync session state
        if selected != current_rating:
            st.session_state.add_form["Rating"] = selected
            
        form["Thumbnail"] = st.text_input("Thumbnail (URL)", value=form.get("Thumbnail", ""))
        form["Notes"] = st.text_area("Notes", value=form.get("Notes", ""))

    if st.button("Add to library", type="primary", key="entry_add"):
        if not form.get("Title", "").strip():
            st.warning("Title is required.")
        else:
            # Derive ISBN-10 if missing but 13 present
            if not form.get("ISBN-10") and form.get("ISBN-13", "").startswith("978") and validate_isbn13(clean_isbn(form["ISBN-13"])):
                maybe10 = isbn13_to_isbn10(clean_isbn(form["ISBN-13"]))
                if maybe10:
                    form["ISBN-10"] = maybe10
            add_row({
                "Title": form["Title"].strip(),
                "Author": form["Author"].strip(),
                "Page count": int(form.get("Page count") or 0),
                "ISBN-13": clean_isbn(form.get("ISBN-13", "")),
                "Published date": form.get("Published date", "").strip(),
                "ISBN-10": clean_isbn(form.get("ISBN-10", "")),
                "Read date": form["Read date"].isoformat() if isinstance(form.get("Read date"), date) else "",
                "Rating": int(form.get("Rating", 0)),
                "Notes": form.get("Notes", "").strip(),
                "Thumbnail": safe_url(form.get("Thumbnail", "")) or "",
            })
            st.success(f"Added “{form.get('Title','')}”")
            st.session_state.add_form.update({
                "Title": "", "Author": "", "Page count": 0, "ISBN-13": "", "Published date": "",
                "ISBN-10": "", "Read date": None, "Notes": "", "Thumbnail": ""
            })
            st.rerun()

# -----------------------------
# SECTION 2 — Search (collapsed)
# -----------------------------
with st.expander("🔎 Search", expanded=False):
    st.markdown("### Google Books (free) — Title / Author / Keywords / ISBN")

    q = st.text_input(
        "Title / Author / Keywords / ISBN",
        placeholder="e.g., The Hobbit • Dan Brown • 9780385504201",
        key="q_text",
    )

    lang_in = st.text_input("Preferred language (leave empty for any)", value="da")
    prefer_lang = normalize_lang_pref(lang_in)

    cset_a, cset_b, cset_c = st.columns([1,1,2])
    with cset_a:
        cols_count = st.number_input("Columns", min_value=2, max_value=8, value=4, step=1)
    with cset_b:
        per_page = st.number_input("Cards per page", min_value=10, max_value=60, value=24, step=2)
    with cset_c:
        sort_opt = st.selectbox("Sort by", ["Relevance (API order)", "Title A→Z", "Author A→Z", "Year desc"], index=0)

    top_a, top_b = st.columns([1, 3])
    with top_a:
        if st.button("Search", key="btn_search_text"):
            st.session_state.search_results_text = google_books_search(
                q, limit=250, prefer_lang=prefer_lang
            )
            st.session_state.page_text = 0

    with top_b:
        results = st.session_state.search_results_text
        if results:
            res = results.copy()
            if sort_opt == "Title A→Z":
                res.sort(key=lambda r: (r.get("Title") or "").lower())
            elif sort_opt == "Author A→Z":
                res.sort(key=lambda r: (r.get("Author") or "").lower())
            elif sort_opt == "Year desc":
                def _yr(r):
                    y = extract_year(r.get("Published date",""))
                    return int(y) if y.isdigit() else -1
                res.sort(key=_yr, reverse=True)

            total = len(res)
            page = st.session_state.page_text
            pages = max(1, math.ceil(total / per_page))
            page = max(0, min(page, pages - 1))
            st.session_state.page_text = page

            start = page * per_page
            end = min(start + per_page, total)
            st.caption(f"Showing {start+1}–{end} of {total}  •  Page {page+1}/{pages}")

            items = res[start:end]
            for row_offset in range(0, len(items), cols_count):
                row_items = items[row_offset:row_offset + cols_count]
                cols = st.columns(len(row_items))
                for j, (col, r) in enumerate(zip(cols, row_items)):
                    global_idx = start + row_offset + j  # unique across the whole result set
                    with col:
                        url = safe_url(r.get("Thumbnail"))
                        if url:
                            st.image(url)
                        else:
                            st.caption("No cover")

                        st.markdown(f"**{r['Title']}**")
                        sub = [p for p in [r.get("Author"), extract_year(r.get("Published date",""))] if p]
                        if sub:
                            st.caption(" · ".join(sub))

                        meta_bits = []
                        if r.get("ISBN-13"): meta_bits.append(f"ISBN-13: {r['ISBN-13']}")
                        if r.get("ISBN-10"): meta_bits.append(f"ISBN-10: {r['ISBN-10']}")
                        if r.get("Page count"): meta_bits.append(f"{int(r['Page count'])} pages")
                        if r.get("language"): meta_bits.append(r["language"])
                        if meta_bits:
                            st.caption(" • ".join(meta_bits))

                        if st.button("Use this", key=f"use_text_{page}_{global_idx}"):
                            for k in st.session_state.add_form.keys():
                                if k == "Read date":
                                    continue
                                st.session_state.add_form[k] = r.get(k, st.session_state.add_form.get(k))
                            st.rerun()

            nav_l, nav_m, nav_r = st.columns([1,2,1])
            with nav_l:
                if st.button("◀ Prev", disabled=page == 0, key="btn_prev_text"):
                    st.session_state.page_text = max(0, page - 1)
            with nav_r:
                if st.button("Next ▶", disabled=end >= total, key="btn_next_text"):
                    st.session_state.page_text = min(pages - 1, page + 1)

    st.markdown("---")
    st.markdown("### Saxo — search by Title")

    q_saxo = st.text_input("Title (Saxo search)", placeholder="fx. Den lille Prins", key="q_saxo")

    ca, cb, cc = st.columns([1,1,2])
    with ca:
        cols_saxo = st.number_input("Columns", min_value=2, max_value=8, value=4, step=1, key="sx_cols")
    with cb:
        pp_saxo = st.number_input("Cards per page", min_value=8, max_value=48, value=16, step=2, key="sx_pp")
    with cc:
        st.caption("Tip: If Saxo returns empty, we harvest ISBN'er via Google Books (gratis) and try again on Saxo.")

    top_sx_a, top_sx_b = st.columns([1,3])
    with top_sx_a:
        if st.button("Search Saxo (Title)", key="btn_search_saxo"):
            with st.spinner("Søger på Saxo..."):
                st.session_state.saxo_results = search_saxo_by_title(q_saxo, max_results=60)
                st.session_state.saxo_page = 0

    with top_sx_b:
        results = st.session_state.saxo_results
        if results:
            total = len(results)
            page = st.session_state.saxo_page
            pages = max(1, math.ceil(total / pp_saxo))
            page = max(0, min(page, pages - 1))
            st.session_state.saxo_page = page
            start = page * pp_saxo
            end = min(start + pp_saxo, total)
            st.caption(f"Viser {start+1}–{end} af {total}  •  Side {page+1}/{pages}")

            items = results[start:end]
            for row_offset in range(0, len(items), cols_saxo):
                row_items = items[row_offset:row_offset + cols_saxo]
                cols = st.columns(len(row_items))
                for j, (col, r) in enumerate(zip(cols, row_items)):
                    global_idx = start + row_offset + j
                    with col:
                        url = safe_url(r.get("Thumbnail"))
                        if url:
                            st.image(url)
                        else:
                            st.caption("No cover")
                        st.markdown(f"**{r.get('Title','')}**")
                        sub = []
                        if r.get("Author"): sub.append(r["Author"])
                        yr = extract_year(r.get("Published date",""))
                        if yr: sub.append(yr)
                        if sub: st.caption(" · ".join(sub))
                        meta_bits = []
                        if r.get("ISBN-13"): meta_bits.append(f"ISBN-13: {r['ISBN-13']}")
                        if r.get("ISBN-10"): meta_bits.append(f"ISBN-10: {r['ISBN-10']}")
                        if r.get("Page count"): meta_bits.append(f"{int(r['Page count'])} pages")
                        if r.get("source"): meta_bits.append(r["source"])
                        if meta_bits: st.caption(" • ".join(meta_bits))
                        if st.button("Use this", key=f"use_sx_{page}_{global_idx}"):
                            for fld in ("Title","Author","Page count","ISBN-13","ISBN-10","Published date","Thumbnail"):
                                st.session_state.add_form[fld] = r.get(fld, st.session_state.add_form.get(fld))
                            st.rerun()

            nav_l, _, nav_r = st.columns([1,2,1])
            with nav_l:
                if st.button("◀ Prev", disabled=page == 0, key="btn_prev_sx"):
                    st.session_state.saxo_page = max(0, page - 1)
            with nav_r:
                if st.button("Next ▶", disabled=end >= total, key="btn_next_sx"):
                    st.session_state.saxo_page = min(pages - 1, page + 1)
        elif q_saxo.strip():
            st.info("Wait a minute or use Google Books search.")

    st.markdown("---")
    st.markdown("### Saxo — search by Author")

    q_saxo_author = st.text_input("Author (Saxo search)", placeholder="fx. Helle Helle", key="q_saxo_author")

    ca2, cb2, cc2 = st.columns([1, 1, 2])
    with ca2:
        cols_saxo_auth = st.number_input("Columns", min_value=2, max_value=8, value=4, step=1, key="sxauth_cols")
    with cb2:
        pp_saxo_auth = st.number_input("Cards per page", min_value=8, max_value=48, value=16, step=2, key="sxauth_pp")
    with cc2:
        st.caption("Vi søger direkte på Saxo med forfatternavn og filtrerer kun resultater hvor forfatter matcher.")

    top_sxauth_a, top_sxauth_b = st.columns([1, 3])
    with top_sxauth_a:
        if st.button("Search Saxo (Author)", key="btn_search_saxo_author"):
            with st.spinner("Søger på Saxo (forfatter)..."):
                st.session_state.saxo_author_results = search_saxo_by_author(q_saxo_author, max_results=60)
                st.session_state.saxo_author_page = 0

    with top_sxauth_b:
        results = st.session_state.saxo_author_results
        if results:
            total = len(results)
            page = st.session_state.saxo_author_page
            pages = max(1, math.ceil(total / pp_saxo_auth))
            page = max(0, min(page, pages - 1))
            st.session_state.saxo_author_page = page

            start = page * pp_saxo_auth
            end = min(start + pp_saxo_auth, total)
            st.caption(f"Viser {start+1}–{end} af {total}  •  Side {page+1}/{pages}")

            items = results[start:end]
            for row_offset in range(0, len(items), cols_saxo_auth):
                row_items = items[row_offset:row_offset + cols_saxo_auth]
                cols = st.columns(len(row_items))
                for j, (col, r) in enumerate(zip(cols, row_items)):
                    global_idx = start + row_offset + j
                    with col:
                        url = safe_url(r.get("Thumbnail"))
                        if url:
                            st.image(url)
                        else:
                            st.caption("No cover")

                        st.markdown(f"**{r.get('Title','')}**")
                        sub = []
                        if r.get("Author"): sub.append(r["Author"])
                        yr = extract_year(r.get("Published date",""))
                        if yr: sub.append(yr)
                        if sub: st.caption(" · ".join(sub))

                        meta_bits = []
                        if r.get("ISBN-13"): meta_bits.append(f"ISBN-13: {r['ISBN-13']}")
                        if r.get("ISBN-10"): meta_bits.append(f"ISBN-10: {r['ISBN-10']}")
                        if r.get("Page count"): meta_bits.append(f"{int(r['Page count'])} pages")
                        if r.get("source"): meta_bits.append(r["source"])
                        if meta_bits: st.caption(" • ".join(meta_bits))

                        if st.button("Use this", key=f"use_sxauth_{page}_{global_idx}"):
                            for fld in ("Title","Author","Page count","ISBN-13","ISBN-10","Published date","Thumbnail"):
                                st.session_state.add_form[fld] = r.get(fld, st.session_state.add_form.get(fld))
                            st.rerun()

            nav_l, _, nav_r = st.columns([1, 2, 1])
            with nav_l:
                if st.button("◀ Prev", disabled=page == 0, key="btn_prev_sxauth"):
                    st.session_state.saxo_author_page = max(0, page - 1)
            with nav_r:
                if st.button("Next ▶", disabled=end >= total, key="btn_next_sxauth"):
                    st.session_state.saxo_author_page = min(pages - 1, page + 1)
        elif q_saxo_author.strip():
            st.info("Ingen direkte forfatterresultater. Prøv at justere navnet (fx uden mellemnavn), eller søg via Google Books ovenfor.")

# ==========================================
# Library view
# ==========================================

st.divider()
st.subheader("📖 Your library")

df = read_df()

left, right = st.columns([3, 2])
with left:
    f_query = st.text_input("Filter", placeholder="Search title/author/isbn/notes")
with right:
    c1, c2 = st.columns(2)
    with c1:
        f_min = st.selectbox("Min rating", options=[0, 1, 2, 3, 4, 5], index=0)
    with c2:
        view = st.radio("View", options=["List", "Grid"], index=0, horizontal=True)

fdf = df.copy()
if f_query:
    ql = f_query.lower()
    mask = (
        fdf["Title"].fillna("").str.lower().str.contains(ql) |
        fdf["Author"].fillna("").str.lower().str.contains(ql) |
        fdf["ISBN-13"].fillna("").str.lower().str.contains(ql) |
        fdf["ISBN-10"].fillna("").str.lower().str.contains(ql) |
        fdf["Notes"].fillna("").str.lower().str.contains(ql)
    )
    fdf = fdf[mask]

fdf = fdf[fdf["Rating"] >= f_min].sort_values("index", ascending=False)

# Delete handling
_todelete: List[int] = []

if view == "Grid":
    cols_count_lib = st.slider("Grid columns", 3, 8, 5, key="library_cols")
    items = list(fdf.to_dict(orient="records"))
    for i in range(0, len(items), cols_count_lib):
        row_items = items[i:i + cols_count_lib]
        cols = st.columns(len(row_items))
        for col, it in zip(cols, row_items):
            with col:
                card = st.container(border=True)
                with card:
                    url = safe_url(it.get("Thumbnail"))
                    if url:
                        st.image(url)
                    else:
                        st.caption("No cover")
                    st.markdown(f"**{it['Title']}**")
                    subtitle_bits = [p for p in [it.get("Author"), extract_year(it.get("Published date","")), it.get("Read date")] if p]
                    if subtitle_bits:
                        st.caption(" · ".join(subtitle_bits))
                    meta_bits = []
                    if it.get("ISBN-13"): meta_bits.append(f"ISBN-13: {it['ISBN-13']}")
                    if it.get("ISBN-10"): meta_bits.append(f"ISBN-10: {it['ISBN-10']}")
                    if it.get("Page count"): meta_bits.append(f"{int(it['Page count'])} pages")
                    if meta_bits:
                        st.caption(" • ".join(meta_bits))
                    if it.get("Notes"):
                        st.write(it["Notes"])
                    if st.button("Delete", key=f"del_{it['index']}"):
                        _todelete.append(int(it["index"]))
else:
    for _, row in fdf.iterrows():
        box = st.container(border=True)
        cols = box.columns([1, 5, 2])
        with cols[0]:
            url = safe_url(row.get("Thumbnail"))
            if url:
                st.image(url)
            else:
                st.caption("No cover")
        with cols[1]:
            st.markdown(f"**{row['Title']}**")
            subtitle_bits = [p for p in [row.get("Author"), extract_year(row.get("Published date","")), row.get("Read date")] if p]
            if subtitle_bits:
                st.caption(" · ".join(subtitle_bits))
            meta_bits = []
            if row.get("ISBN-13"): meta_bits.append(f"ISBN-13: {row['ISBN-13']}")
            if row.get("ISBN-10"): meta_bits.append(f"ISBN-10: {row['ISBN-10']}")
            if row.get("Page count"): meta_bits.append(f"{int(row['Page count'])} pages")
            if meta_bits:
                st.caption(" • ".join(meta_bits))
            if row.get("Notes"):
                st.write(row["Notes"])
        with cols[2]:
            if st.button("Delete", key=f"del_{row['index']}"):
                _todelete.append(int(row["index"]))

if _todelete:
    delete_rows(_todelete)
    st.success("Deleted.")
    st.rerun()

st.download_button(
    "⬇️ Export CSV",
    data=fdf.to_csv(index=False),
    file_name="books.csv",
    mime="text/csv",
)

# ==========================================
# Quick editor (type-safe dates)
# ==========================================

st.divider()
st.subheader("✏️ Quick edit")

_df_edit = df.copy()
_df_edit["Read date"] = pd.to_datetime(_df_edit["Read date"], errors="coerce").dt.date

edited = st.data_editor(
    _df_edit,
    width="stretch",
    num_rows="dynamic",
    column_config={
        "index": st.column_config.NumberColumn("index", help="Row id (unique integer)"),
        "Page count": st.column_config.NumberColumn("Page count", min_value=0, step=1),
        "Rating": st.column_config.NumberColumn("Rating", min_value=0, max_value=5, step=1),
        "Read date": st.column_config.DateColumn("Read date", format="YYYY-MM-DD"),
        "Thumbnail": st.column_config.TextColumn("Thumbnail", help="Cover URL"),
    },
    hide_index=True,
    key="editor_lite",
)

c1, c2 = st.columns([1, 3])
with c1:
    if st.button("Save changes", type="primary"):
        ed = edited.copy()
        if "Read date" in ed.columns:
            ed["Read date"] = pd.to_datetime(ed["Read date"], errors="coerce").dt.date.astype("string").fillna("")
        ed = normalize_columns(ed)
        if ed["index"].duplicated().any():
            st.error("Duplicate index values found. Make sure each row has a unique 'index'.")
        else:
            write_df(ed)
            st.success("Sheet updated.")
            st.rerun()
with c2:
    st.caption("Tip: Add new rows at the bottom. Title + unique index required.")
