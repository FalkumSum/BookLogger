# src/ocr.py
from typing import Optional, Tuple
import re
import streamlit as st

# Try to import Vision; if missing, we stay disabled.
try:
    from google.cloud import vision as gcv
    from google.oauth2.service_account import Credentials
    VISION_IMPORT_OK = True
except Exception:
    gcv = None
    Credentials = None
    VISION_IMPORT_OK = False

SCOPE_VISION = ["https://www.googleapis.com/auth/cloud-platform"]

@st.cache_resource
def _client():
    if not VISION_IMPORT_OK:
        return None
    try:
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=SCOPE_VISION
        )
        return gcv.ImageAnnotatorClient(credentials=creds)
    except Exception:
        return None

# Public flag you can check from app.py
AVAILABLE: bool = _client() is not None

def extract_text(image_bytes: bytes) -> str:
    """
    Return full OCR text or "" when OCR is unavailable or fails.
    """
    client = _client()
    if not client:
        return ""
    try:
        img = gcv.Image(content=image_bytes)
        resp = client.text_detection(image=img)
        texts = resp.text_annotations
        return texts[0].description if texts else ""
    except Exception:
        return ""

def guess_title_author(text: str) -> Tuple[str, str]:
    """
    Lightweight heuristic to guess title and author from OCR text.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return "", ""

    candidates = [l for l in lines if len(l) >= 3]
    no_digits = [l for l in candidates if sum(ch.isdigit() for ch in l) <= 2]
    title = max(no_digits, key=len) if no_digits else max(candidates or lines, key=len)

    author = ""
    for l in candidates:
        if re.search(r"\bby\b", l, re.IGNORECASE):
            author = re.sub(r".*\bby\b", "", l, flags=re.IGNORECASE).strip()
            break
    if not author:
        short = [l for l in candidates if 1 <= len(l.split()) <= 4 and not any(ch.isdigit() for ch in l)]
        if short and short[0] != title:
            author = short[0]

    return title, author
