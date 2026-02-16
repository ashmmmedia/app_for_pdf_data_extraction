from __future__ import annotations

import re


SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def extract_sheet_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Sheet URL/ID is required.")

    match = SHEET_ID_RE.search(value)
    if match:
        return match.group(1)

    # If it doesn't look like a URL, assume it's already the ID.
    return value

