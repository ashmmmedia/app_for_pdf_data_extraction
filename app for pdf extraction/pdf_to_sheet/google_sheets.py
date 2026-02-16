from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1


@dataclass
class SheetConfig:
    spreadsheet_id: str
    worksheet_name: str
    credentials_file: str | None = None
    credentials_info: dict[str, Any] | None = None


class GoogleSheetWriter:
    def __init__(self, config: SheetConfig) -> None:
        self.config = config
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = self._load_credentials(scopes)
        client = gspread.authorize(credentials)

        self.sheet = client.open_by_key(config.spreadsheet_id)
        self.worksheet = self.sheet.worksheet(config.worksheet_name)

    def _load_credentials(self, scopes: list[str]) -> Credentials:
        if self.config.credentials_info:
            return Credentials.from_service_account_info(
                self.config.credentials_info,
                scopes=scopes,
            )

        if not self.config.credentials_file:
            raise ValueError("Service account credentials are required.")

        creds_path = Path(self.config.credentials_file)
        if not creds_path.exists():
            raise FileNotFoundError(f"Service account JSON not found: {creds_path}")

        return Credentials.from_service_account_file(str(creds_path), scopes=scopes)

    def get_headers(self) -> list[str]:
        row = [h.strip() for h in self.worksheet.row_values(1)]
        # Trim trailing empty cells so we don't treat extra blanks as headers.
        while row and not row[-1]:
            row.pop()
        return row

    def upsert_headers(self, headers: list[str]) -> list[str]:
        existing = self.get_headers()
        if not existing:
            self.worksheet.update("A1", [headers])
            return headers

        # Append any missing headers to preserve existing columns and
        # allow newly added fields to be written.
        missing = [header for header in headers if header not in existing]
        if missing:
            updated = existing + missing
            self.worksheet.update("A1", [updated])
            return updated

        return existing

    def find_first_empty_data_row(self) -> int:
        values = self.worksheet.get_all_values()
        if len(values) < 2:
            return 2

        for row_index in range(2, len(values) + 1):
            row = values[row_index - 1]
            if not any(cell.strip() for cell in row):
                return row_index

        return len(values) + 1

    def write_row(self, headers: list[str], row_data: dict[str, str]) -> int:
        active_headers = self.upsert_headers(headers)
        target_row = self.find_first_empty_data_row()

        values = [row_data.get(header, "") for header in active_headers]
        end_a1 = rowcol_to_a1(target_row, len(active_headers))
        range_name = f"A{target_row}:{end_a1}"
        self.worksheet.update(range_name, [values], value_input_option="USER_ENTERED")

        return target_row

    def write_rows(self, headers: list[str], rows: list[dict[str, str]]) -> int:
        if not rows:
            raise ValueError("No rows provided to write.")

        active_headers = self.upsert_headers(headers)
        start_row = self.find_first_empty_data_row()

        values = [
            [row.get(header, "") for header in active_headers] for row in rows
        ]
        end_a1 = rowcol_to_a1(start_row + len(rows) - 1, len(active_headers))
        range_name = f"A{start_row}:{end_a1}"
        self.worksheet.update(range_name, values, value_input_option="USER_ENTERED")

        return start_row
