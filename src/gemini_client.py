"""Thin wrapper around the google-genai SDK: client init, native PDF upload
(with wait-for-ACTIVE), and chat-session creation with a fixed system prompt."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3-flash-preview"

# Hard ceiling per HTTP request. Without this, a stalled connection or a
# server-side hang blocks forever with zero CPU/network signal - which is
# exactly what happened during testing (a call sat "running" for 90+
# minutes with no progress). Better to fail loudly and let a retry happen.
REQUEST_TIMEOUT_MS = 10 * 60 * 1000


def make_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to your .env file "
            "(see .env.example) or export it in your shell."
        )
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )


def upload_pdf(client: genai.Client, pdf_path: Path, poll_seconds: float = 2.0, timeout_seconds: float = 300.0):
    """Uploads a PDF via the Files API and blocks until Gemini finishes
    processing it (state ACTIVE), so the very next generate call can use it."""
    uploaded = client.files.upload(file=str(pdf_path))

    waited = 0.0
    file_info = client.files.get(name=uploaded.name)
    while file_info.state.name == "PROCESSING":
        if waited >= timeout_seconds:
            raise TimeoutError(f"Timed out waiting for {pdf_path.name} to finish processing on Gemini.")
        time.sleep(poll_seconds)
        waited += poll_seconds
        file_info = client.files.get(name=uploaded.name)

    if file_info.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file upload for {pdf_path.name} ended in state {file_info.state.name}")

    return file_info


def start_chat(client: genai.Client, model: str, system_instruction: str, max_output_tokens: int = 8192):
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=max_output_tokens,
        temperature=0.3,
    )
    return client.chats.create(model=model, config=config)


def send(chat, parts: List) -> str:
    response = chat.send_message(parts)
    if not response.text:
        raise RuntimeError(
            f"Empty response from Gemini (finish_reason={getattr(response.candidates[0], 'finish_reason', '?') if response.candidates else '?'})"
        )
    return response.text
