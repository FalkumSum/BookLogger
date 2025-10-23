# src/repository.py
from datetime import datetime, date
from typing import List
import time
import math

import pandas as pd
import streamlit as st
import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials
from gspread_dataframe import get_as_dataframe

from .models import Book

# --- NEW: optional NumPy import (for scalar detection) ---
try:
    import numpy as np
except Exception:
    np = None

SCOPE_SHEETS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# --- NEW: normalization helpers for JSON/Sheets ---
def _to_native(value):
    """Convert NumPy/Pandas scalars & datetimes into JSON-serializable Python types."""
    # Empty / None stays empty
    if value is None:
        return None

    # Convert NaN to empty
    if isinstance(value, float) and math.isnan(value):
        return None

    # NumPy scalars (int64, float64, bool_, etc.)
    if np is not None and isinstance(value, np.generic):
        return value.item()

    # Pandas Timestamp / NaT (avoid importing pandas just for types)
    cls = type(value).__name__
    if cls == "Timestamp":
        # to_pydatetime() works for valid Timestamps; NaT will raise or behave oddly
        try:
            return value.to_pydatetime().isoformat()
        except Exception:
            return None

    # Python datetime/date -> ISO8601 (USER_ENTERED lets Sheets parse it)
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    # Lists/tuples (keep it flat if you use them)
    if isinstance(value, (list, tuple)):
        return [_to_native(v) for v in value]

    # Strings / ints / floats / bools are already fine
    return value

def _to_native_row(values):
    return [_to_native(v) for v in values]

def _to_native_2d(values_2d):
    return [[_to_native(v) for v in row] for row in values_2d]
# --- END helpers ---

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
        # write headers once via single update
        _safe_update(ws, 'A1', [Book.headers()])
    return ws

def _safe_update(ws, range_name, values, retries: int = 5):
    """
    Single values.update with exponential backoff on 429.
    """
    # --- ensure JSON-safe before sending ---
    values = _to_native_2d(values)
    for attempt in range(retries):
        try:
            return ws.update(range_name, values, value_input_option="USER_ENTERED")
        except APIError as e:
            msg = str(e)
            if "Quota exceeded" in msg and attempt < retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s, 8s, ...
                continue
            raise

def _safe_append_row(ws, values, retries: int = 5):
    # --- ensure JSON-safe before sending ---
    values = _to_native_row(values)
    for attempt in range(retries):
        try:
            return ws.append_row(values, value_input_option="USER_ENTERED")
        except APIError as e:
            msg = str(e)
            if "Quota exceeded" in msg and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    headers = Book.headers()
    if df.empty:
        df = pd.DataFrame(columns=headers)
    # Ensure columns exist
    for col in headers:
        if col not in df.columns:
            df[col] = "" if col not in ("id", "rating", "page_count") else 0
    # Types
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

def _to_values(df: pd.DataFrame) -> list[list]:
    """
    Convert dataframe to a 2D list including header row.
    """
    headers = Book.headers()
    df = _normalize(df.copy())
    # Make sure NaNs are blanks to avoid 'nan' strings
    df = df.where(pd.notnull(df), "")
    values = [headers] + df[headers].values.tolist()
    # --- ensure JSON-safe before returning ---
    return _to_native_2d(values)

def write_all(df: pd.DataFrame):
    """
    Overwrite the sheet in a single update call (no clear). Also shrinks/grows the sheet rows exactly once if needed.
    """
    ws = _ws()
    values = _to_values(df)
    # Resize to exactly fit values (optional; counts as another write but keeps sheet clean)
    try:
        ws.resize(rows=len(values), cols=len(values[0]) if values else len(Book.headers()))
    except APIError:
        # If resize hits quota, we can still just write the data; old trailing rows may remain visible.
        pass
    _safe_update(ws, 'A1', values)

def next_id(df: pd.DataFrame) -> int:
    return 1 if df.empty else int(pd.to_numeric(df["id"], errors="coerce").fillna(0).max()) + 1

def add_book(book: Book):
    """
    Append a single row (1 API write) instead of read-modify-overwrite of the entire sheet.
    """
    ws = _ws()
    df = read_all()
    if not book.id:
        book.id = next_id(df)
    if not book.added_at:
        book.added_at = datetime.now().isoformat(timespec="seconds")

    # If the sheet is fresh and only contains headers, ensure headers exist in row 1
    if ws.row_count == 0:
        _safe_update(ws, 'A1', [Book.headers()])

    row = _normalize(pd.DataFrame([book.to_row()])).iloc[0].tolist()
    _safe_append_row(ws, row)

def delete_ids(ids: List[int]):
    """
    Deleting arbitrary rows requires rewriting the sheet (OK; happens infrequently).
    """
    df = read_all()
    df = df[~df["id"].isin(ids)]
    write_all(df)
