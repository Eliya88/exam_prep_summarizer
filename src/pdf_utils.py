"""Cheap local PDF helpers (text extraction + page counts). No API calls here."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


def extract_text(pdf_path: Path) -> str:
    """Best-effort plain-text extraction, used only for the lightweight
    full-course context digest (not for the detailed slide-by-slide pass,
    which sends the native PDF to Gemini instead)."""
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        return f"[Could not read {pdf_path.name}: {e}]"

    parts = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(f"--- page {i} ---\n{text.strip()}")
    return "\n\n".join(parts)


def page_count(pdf_path: Path) -> int:
    try:
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0
