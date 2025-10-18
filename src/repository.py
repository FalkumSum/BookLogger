from datetime import datetime
from typing import List
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from gspread_dataframe import get_as_dataframe, set_with_dataframe

from .models import Book

SCOPE_SHEETS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def _ws():
    secrets = st.secrets
    creds = Credentials.from_service_account_info(dict(secrets["gcp_service_account"]), scopes=SCOPE_SHEETS)
    gc = gspread.authorize(creds)
    sh = gc.open(secrets["sheet"]["name"])
    try:
        ws = sh.worksheet(secrets["sheet"]["worksheet"])
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(secrets["sheet"]["worksheet"], rows=1000, cols=40)
        ws.append_row(Book.headers())
    return ws

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    headers = Book.headers()
    if df.empty:
        df = pd.DataFrame(columns=headers)
    for col in headers:
        if col not in df.columns:
            df[col] = "" if col not in ("id", "rating", "page_count") else 0
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(0).astype(int).clip(0, 5)
    df["page_count"] = pd.to_numeric(df["page_count"], errors="coerce").fillna(0).astype(int)
    for c in [c for c in headers if c not in ("id", "rating", "page_count")]:
        df[c] = df[c].astype("string").fillna("").replace("nan", "")
    df["status"] = df["status"].replace("", "Wishlist")
    return df[headers]

def read_all() -> pd.DataFrame:
    df = get_as_dataframe(_ws(), header=0, evaluate_formulas=True).dropna(how="all")
    return _normalize(df)

def write_all(df: pd.DataFrame):
    ws = _ws()
    ws.clear()
    set_with_dataframe(ws, df[Book.headers()])

def next_id(df: pd.DataFrame) -> int:
    return 1 if df.empty else int(pd.to_numeric(df["id"], errors="coerce").fillna(0).max()) + 1

def add_book(book: Book):
    df = read_all()
    if not book.id:
        book.id = next_id(df)
    if not book.added_at:
        book.added_at = datetime.now().isoformat(timespec="seconds")
    df = pd.concat([df, pd.DataFrame([book.to_row()])], ignore_index=True)
    write_all(_normalize(df))

def delete_ids(ids: List[int]):
    df = read_all()
    df = df[~df["id"].isin(ids)]
    write_all(_normalize(df))
