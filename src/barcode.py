from typing import Optional
from PIL import Image
import io
import streamlit as st
import zxingcpp
from .utils import extract_isbn13_from_text, validate_isbn13

def decode_isbn(image_bytes: bytes) -> Optional[str]:
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
