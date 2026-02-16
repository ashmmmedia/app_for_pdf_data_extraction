from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from dotenv import load_dotenv
from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

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
# Cloud Run HTTP request limit is 32 MiB; keep uploads safely under that.
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

app.secret_key = os.getenv("SECRET_KEY", "dev-insecure-change-me")

_USER_DB_DEFAULT = "/tmp/app.db"
_T = TypeVar("_T")


def _default_field_map_path() -> str:
    return os.getenv("FIELD_MAP_FILE", "field_map.yaml")


def _user_db_path() -> Path:
    return Path(os.getenv("USER_DB_PATH", _USER_DB_DEFAULT))


def _ensure_user_db() -> None:
    db_path = _user_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                google_sub TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _save_user(profile: dict[str, Any]) -> None:
    db_path = _user_db_path()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO users (google_sub, email, first_name, last_name, created_at, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(google_sub)
            DO UPDATE SET
                email=excluded.email,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                last_login_at=excluded.last_login_at
            """,
            (
                profile["sub"],
                profile["email"],
                profile.get("first_name"),
                profile.get("last_name"),
                now,
                now,
            ),
        )
        conn.commit()


def _firebase_web_config() -> dict[str, str]:
    config: dict[str, str] = {
        "apiKey": os.getenv("FIREBASE_API_KEY", ""),
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
        "appId": os.getenv("FIREBASE_APP_ID", ""),
    }
    measurement_id = os.getenv("FIREBASE_MEASUREMENT_ID", "")
    if measurement_id:
        config["measurementId"] = measurement_id
    return config


def _firebase_web_configured() -> bool:
    config = _firebase_web_config()
    return all([config.get("apiKey"), config.get("authDomain"), config.get("projectId"), config.get("appId")])


def _init_firebase() -> None:
    if firebase_admin._apps:
        return
    service_account = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE")
    if service_account:
        cred = credentials.Certificate(service_account)
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()


def _flash_error(message: str) -> None:
    session.setdefault("flash_errors", []).append(message)


def _consume_flash_errors() -> list[str]:
    return session.pop("flash_errors", [])


def _login_required(view: Callable[..., _T]) -> Callable[..., _T]:
    def wrapped(*args: Any, **kwargs: Any) -> _T:
        if not g.user:
            _flash_error("Please sign in with Google to continue.")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    return wrapped


@app.before_request
def load_user() -> None:
    g.user = session.get("user")


@app.context_processor
def inject_user() -> dict[str, Any]:
    return {"current_user": g.user, "firebase_config": _firebase_web_config()}


_ensure_user_db()


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        errors=_consume_flash_errors(),
        extracted=None,
        raw_text=None,
        raw_text_entries=None,
        row_index=None,
        worksheet_name=os.getenv("GOOGLE_WORKSHEET_NAME", "Sheet1"),
        field_map_path=_default_field_map_path(),
    )


@app.post("/auth/firebase")
def auth_firebase() -> tuple[dict[str, str], int]:
    if not _firebase_web_configured():
        return {"error": "Firebase web config missing."}, 400

    data = request.get_json(silent=True) or {}
    id_token = data.get("id_token")
    if not id_token:
        return {"error": "Missing id_token."}, 400

    try:
        _init_firebase()
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception:
        return {"error": "Invalid Firebase token."}, 401

    email = decoded.get("email")
    if not email:
        return {"error": "Firebase token missing email."}, 400

    full_name = decoded.get("name") or ""
    first_name = decoded.get("given_name") or (full_name.split(" ", 1)[0] if full_name else "")
    last_name = decoded.get("family_name") or (full_name.split(" ", 1)[1] if " " in full_name else "")

    profile = {
        "sub": decoded.get("uid"),
        "email": email,
        "first_name": first_name or None,
        "last_name": last_name or None,
    }

    if not profile["sub"]:
        return {"error": "Firebase token missing uid."}, 400

    _save_user(profile)
    session["user"] = {
        "sub": profile["sub"],
        "email": profile["email"],
        "first_name": profile["first_name"],
        "last_name": profile["last_name"],
    }
    return {"ok": "true"}, 200


@app.post("/logout")
def logout() -> str:
    session.pop("user", None)
    return redirect(url_for("index"))


@app.post("/submit")
@_login_required
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
