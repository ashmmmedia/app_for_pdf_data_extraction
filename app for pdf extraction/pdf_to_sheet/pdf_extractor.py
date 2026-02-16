from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .config import FieldRule


def extract_pdf_text(pdf_path: str | Path) -> str:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    page_text: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text.append(page.extract_text() or "")

    return normalize_text("\n".join(page_text))


def extract_campaigns(text: str) -> list[dict[str, str]]:
    section = _extract_campaign_section(text)
    pattern = re.compile(
        r"(?m)^\s*(?P<name>.+?)\s*\n"
        r"(?:\s*\n)*\s*(?P<cost>CA\$\d[\d,]*\.\d{2})\s*\n"
        r"(?:\s*\n)*\s*From\s*(?P<start>[^\n]+?)\s*to\s*(?P<end>[^\n]+?)\s*$"
    )

    campaigns: list[dict[str, str]] = []
    for match in pattern.finditer(section):
        name = clean_value(match.group("name"))
        campaigns.append(
            {
                "campaign_name": name,
                "campaign_cost": clean_value(match.group("cost")),
                "campaign_start_date": clean_value(match.group("start")),
                "campaign_end_date": clean_value(match.group("end")),
            }
        )

    return campaigns


def _extract_campaign_section(text: str) -> str:
    lower = text.lower()
    start_idx = lower.find("campaigns")
    section = text if start_idx < 0 else text[start_idx:]

    end_markers = [
        "meta platforms",
        "powered by",
        "invoice no.",
        "gst/hst",
        "ab ",
        "united states",
    ]

    end_idx = None
    section_lower = section.lower()
    for marker in end_markers:
        idx = section_lower.find(marker)
        if idx > 0:
            end_idx = idx if end_idx is None else min(end_idx, idx)

    if end_idx is not None:
        return section[:end_idx]
    return section


def normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_fields_from_text(text: str, rules: list[FieldRule]) -> tuple[dict[str, str], list[str]]:
    extracted: dict[str, str] = {}
    missing_required: list[str] = []

    for rule in rules:
        match = re.search(rule.pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            try:
                raw_value = match.group(rule.group)
            except (IndexError, KeyError) as exc:
                raise ValueError(
                    f"Invalid group `{rule.group}` for field `{rule.name}`."
                ) from exc
            extracted[rule.name] = clean_value(raw_value)
            continue

        if rule.required:
            missing_required.append(rule.name)
        extracted[rule.name] = rule.default

    return extracted, missing_required


def clean_value(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\s{2,}", " ", value)
    return value
