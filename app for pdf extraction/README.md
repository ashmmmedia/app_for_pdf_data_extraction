# PDF to Google Sheets (First Empty Row)

This app:
1. Reads a PDF.
2. Extracts values using regex rules from `field_map.yaml`.
3. Writes one row to the first empty row in your Google Sheet.

It is designed for a fixed PDF layout (same template every time).

## Setup

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy environment template:

```bash
cp .env.local.example .env.local
```

3. Configure `.env.local`:
- `GOOGLE_SHEETS_ID`: spreadsheet ID from the URL.
- `GOOGLE_WORKSHEET_NAME`: tab name (example: `Sheet1`).
- `GOOGLE_SERVICE_ACCOUNT_FILE`: absolute path to your service account JSON key.
- `FIELD_MAP_FILE`: usually `field_map.yaml`.

4. Google setup:
- Enable Google Sheets API and Google Drive API.
- Create a service account and download JSON key.
- Share the target Google Sheet with the service account email.

## Configure extraction fields

Edit `field_map.yaml` so field names/patterns match your PDF labels.

Use inspect mode to see extracted text:

```bash
python -m pdf_to_sheet.main inspect --pdf /path/to/your.pdf --lines 300
```

## Run

Dry run (parse only):

```bash
python -m pdf_to_sheet.main sync --pdf /path/to/your.pdf --dry-run
```

Write to Google Sheets:

```bash
python -m pdf_to_sheet.main sync --pdf /path/to/your.pdf
```

## Web UI

Run the local web app (upload up to 10 PDFs, paste sheet URL). Use "Text only" to just extract raw PDF text. The app uses the service account JSON path from `.env`:

```bash
python web_app.py
```

Then open `http://127.0.0.1:5000`.

## Notes

- Header row is row `1`. Data writes from row `2`.
- If there is a blank row in the middle, the app fills that first blank row.
- If sheet headers do not match `field_map.yaml` field order, the app stops with an error to prevent wrong-column writes.
