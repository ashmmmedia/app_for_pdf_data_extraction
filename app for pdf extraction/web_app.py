from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request

from pdf_to_sheet.config import load_field_rules
from pdf_to_sheet.google_sheets import GoogleSheetWriter, SheetConfig
from pdf_to_sheet.pdf_extractor import (
    extract_campaigns,
    extract_fields_from_text,
    extract_pdf_text,
)
from pdf_to_sheet.utils import extract_sheet_id

# Prefer .env.local for secrets, fall back to .env
load_dotenv(".env.local")
load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


def _default_field_map_path() -> str:
    return os.getenv("FIELD_MAP_FILE", "field_map.yaml")


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        errors=[],
        extracted=None,
        raw_text=None,
        raw_text_entries=None,
        row_index=None,
        worksheet_name=os.getenv("GOOGLE_WORKSHEET_NAME", "Sheet1"),
        field_map_path=_default_field_map_path(),
    )


@app.post("/submit")
def submit() -> str:
    errors: list[str] = []
    extracted: dict[str, str] | None = None
    raw_text: str | None = None
    raw_text_entries: list[dict[str, str]] = []
    row_index: int | None = None
    row_count: int | None = None

    pdf_files = request.files.getlist("pdf_files")
    if not pdf_files:
        single = request.files.get("pdf_file")
        if single:
            pdf_files = [single]
    sheet_value = (request.form.get("sheet_url") or "").strip()
    worksheet_name = (request.form.get("worksheet_name") or "").strip() or "Sheet1"
    text_only = bool(request.form.get("text_only"))
    field_map_path = _default_field_map_path()

    if not pdf_files or not any(f.filename for f in pdf_files):
        errors.append("Please upload at least one PDF file.")
    elif len([f for f in pdf_files if f.filename]) > 10:
        errors.append("Please upload 10 PDFs or fewer at a time.")

    sheet_id = ""
    if not text_only:
        if not sheet_value:
            errors.append("Please provide a Google Sheet URL or ID.")
        else:
            try:
                sheet_id = extract_sheet_id(sheet_value)
            except ValueError as exc:
                errors.append(str(exc))

    service_account_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not text_only:
        if not service_account_path:
            errors.append("Missing GOOGLE_SERVICE_ACCOUNT_FILE in .env.")

    if errors:
        return render_template(
            "index.html",
            errors=errors,
            extracted=extracted,
            raw_text=raw_text,
            raw_text_entries=raw_text_entries,
            row_index=row_index,
            worksheet_name=worksheet_name,
            field_map_path=field_map_path,
        )

    all_rows: list[dict[str, str]] = []
    text = ""
    for pdf_file in pdf_files:
        if not pdf_file.filename:
            continue
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_pdf:
                pdf_file.save(temp_pdf.name)
                text = extract_pdf_text(temp_pdf.name)
        except Exception as exc:
            errors.append(f"{pdf_file.filename}: PDF could not be read ({exc})")
            continue
        finally:
            if "temp_pdf" in locals():
                try:
                    Path(temp_pdf.name).unlink(missing_ok=True)
                except OSError:
                    pass

        raw_text = text
        raw_text_entries.append({"filename": pdf_file.filename, "text": text})
        if text_only:
            continue

        try:
            rules = load_field_rules(field_map_path)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            return render_template(
                "index.html",
                errors=errors,
                extracted=extracted,
                raw_text=raw_text,
                raw_text_entries=raw_text_entries,
                row_index=row_index,
                row_count=row_count,
                worksheet_name=worksheet_name,
                field_map_path=field_map_path,
            )

        extracted, missing_required = extract_fields_from_text(text, rules)
        if missing_required:
            errors.append(
                f"{pdf_file.filename}: Required fields not found: "
                + ", ".join(missing_required)
            )
            continue

        campaigns = extract_campaigns(text)
        if campaigns:
            for campaign in campaigns:
                row = dict(extracted)
                row.update(campaign)
                all_rows.append(row)
        else:
            all_rows.append(dict(extracted))

    if text_only:
        return render_template(
            "index.html",
            errors=errors,
            extracted=extracted,
            raw_text=raw_text,
            raw_text_entries=raw_text_entries,
            row_index=row_index,
            row_count=row_count,
            worksheet_name=worksheet_name,
            field_map_path=field_map_path,
            text_only=True,
        )

    try:
        writer = GoogleSheetWriter(
            SheetConfig(
                spreadsheet_id=sheet_id,
                worksheet_name=worksheet_name,
                credentials_file=service_account_path,
                credentials_info=None,
            )
        )
        rules = load_field_rules(field_map_path)
        headers = [rule.name for rule in rules]
        if all_rows:
            row_index = writer.write_rows(headers, all_rows)
            row_count = len(all_rows)
        else:
            errors.append("No rows were generated from the uploaded PDFs.")
    except Exception as exc:
        errors.append(f"Google Sheets update failed: {exc}")

    return render_template(
        "index.html",
        errors=errors,
        extracted=extracted,
        raw_text=raw_text,
        raw_text_entries=raw_text_entries,
        row_index=row_index,
        row_count=row_count,
        worksheet_name=worksheet_name,
        field_map_path=field_map_path,
    )


if __name__ == "__main__":
    app.run(debug=True)
