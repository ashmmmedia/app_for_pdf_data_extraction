from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import load_field_rules
from .google_sheets import GoogleSheetWriter, SheetConfig
from .pdf_extractor import extract_fields_from_text, extract_pdf_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract structured data from a fixed-format PDF and write it to Google Sheets."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Print extracted text from the PDF to help you build regex patterns.",
    )
    inspect_parser.add_argument("--pdf", required=True, help="Path to the PDF file.")
    inspect_parser.add_argument(
        "--lines",
        type=int,
        default=200,
        help="Maximum number of text lines to print.",
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Extract fields and write one row to the first empty row in Google Sheets.",
    )
    sync_parser.add_argument("--pdf", required=True, help="Path to the PDF file.")
    sync_parser.add_argument(
        "--field-map",
        default=os.getenv("FIELD_MAP_FILE", "field_map.yaml"),
        help="Path to field map YAML file.",
    )
    sync_parser.add_argument(
        "--sheet-id",
        default=os.getenv("GOOGLE_SHEETS_ID"),
        help="Google Spreadsheet ID (or set GOOGLE_SHEETS_ID).",
    )
    sync_parser.add_argument(
        "--worksheet",
        default=os.getenv("GOOGLE_WORKSHEET_NAME", "Sheet1"),
        help="Worksheet/tab name (or set GOOGLE_WORKSHEET_NAME).",
    )
    sync_parser.add_argument(
        "--credentials",
        default=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"),
        help="Path to Google service-account JSON (or set GOOGLE_SERVICE_ACCOUNT_FILE).",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and print data only; do not write to Google Sheets.",
    )

    return parser


def run_inspect(pdf_path: str, max_lines: int) -> int:
    text = extract_pdf_text(pdf_path)
    lines = text.splitlines()
    for line_number, line in enumerate(lines[:max_lines], start=1):
        print(f"{line_number:04d}: {line}")

    if len(lines) > max_lines:
        print(f"\n... truncated output ({len(lines)} total lines).")
    return 0


def run_sync(
    pdf_path: str,
    field_map_path: str,
    sheet_id: str | None,
    worksheet_name: str,
    credentials_file: str | None,
    dry_run: bool,
) -> int:
    if not sheet_id and not dry_run:
        print("Missing sheet id. Use --sheet-id or set GOOGLE_SHEETS_ID.", file=sys.stderr)
        return 2

    if not credentials_file and not dry_run:
        print(
            "Missing service-account file. Use --credentials or set GOOGLE_SERVICE_ACCOUNT_FILE.",
            file=sys.stderr,
        )
        return 2

    rules = load_field_rules(field_map_path)
    text = extract_pdf_text(pdf_path)
    extracted, missing_required = extract_fields_from_text(text, rules)

    if missing_required:
        print(
            "Required fields not found in PDF: " + ", ".join(missing_required),
            file=sys.stderr,
        )
        return 2

    headers = [rule.name for rule in rules]
    print(json.dumps(extracted, indent=2))

    if dry_run:
        print("\nDry run enabled: no Google Sheets update performed.")
        return 0

    writer = GoogleSheetWriter(
        SheetConfig(
            spreadsheet_id=sheet_id or "",
            worksheet_name=worksheet_name,
            credentials_file=credentials_file or "",
        )
    )
    row_index = writer.write_row(headers, extracted)
    print(f"\nWrote data to row {row_index} in worksheet '{worksheet_name}'.")
    return 0


def main() -> int:
    load_dotenv(".env.local")
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "inspect":
        return run_inspect(args.pdf, args.lines)

    if args.command == "sync":
        return run_sync(
            pdf_path=args.pdf,
            field_map_path=args.field_map,
            sheet_id=args.sheet_id,
            worksheet_name=args.worksheet,
            credentials_file=args.credentials,
            dry_run=args.dry_run,
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
