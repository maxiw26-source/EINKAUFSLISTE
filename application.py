from flask import Flask, request, redirect, url_for, session, render_template_string, flash, Response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import os
import secrets
import string
import hashlib
import psycopg
import requests
import json
from urllib.parse import urlparse
from decimal import Decimal, InvalidOperation

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    WebPushException = Exception
from psycopg.rows import dict_row

app = Flask(__name__)

# PostgreSQL-Verbindung aus Render Environment.
DATABASE_URL = os.environ.get("DATABASE_URL")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get(
    "VAPID_SUBJECT",
    "mailto:admin@example.com"
).strip()

MAX_RECEIPT_FILE_SIZE = 8 * 1024 * 1024
ALLOWED_RECEIPT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL fehlt. Bitte die Internal Database URL in Render "
        "als Environment Variable DATABASE_URL speichern."
    )

app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    (DATABASE_URL + "|einkaufsliste-session").encode("utf-8")
).hexdigest()


def db_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def create_tables():
    with db_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS shopping_lists (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                invite_code TEXT NOT NULL UNIQUE
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                list_id BIGINT NOT NULL REFERENCES shopping_lists(id)
            )
        """)

        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_unique
            ON users (LOWER(username))
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id BIGSERIAL PRIMARY KEY,
                list_id BIGINT NOT NULL REFERENCES shopping_lists(id),
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                created_by BIGINT REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            ALTER TABLE items
            ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'Sonstiges'
        """)

        db.execute("""
            ALTER TABLE items
            ADD COLUMN IF NOT EXISTS barcode TEXT
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id BIGSERIAL PRIMARY KEY,
                list_id BIGINT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                category TEXT NOT NULL DEFAULT 'Sonstiges',
                barcode TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS favorites_list_name_unique
            ON favorites (list_id, LOWER(name))
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS purchase_history (
                id BIGSERIAL PRIMARY KEY,
                list_id BIGINT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                category TEXT NOT NULL DEFAULT 'Sonstiges',
                barcode TEXT,
                purchased_by BIGINT REFERENCES users(id),
                purchased_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                list_id BIGINT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
                endpoint TEXT NOT NULL UNIQUE,
                subscription_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            ALTER TABLE shopping_lists
            ADD COLUMN IF NOT EXISTS monthly_budget NUMERIC(10, 2)
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id BIGSERIAL PRIMARY KEY,
                list_id BIGINT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                quantity NUMERIC(10, 2) NOT NULL DEFAULT 0,
                minimum_quantity NUMERIC(10, 2) NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT 'Stück',
                category TEXT NOT NULL DEFAULT 'Sonstiges',
                barcode TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS inventory_list_name_unique
            ON inventory (list_id, LOWER(name))
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS push_subscriptions_list_idx
            ON push_subscriptions (list_id)
        """)

        db.execute("""
            ALTER TABLE shopping_lists
            ADD COLUMN IF NOT EXISTS store TEXT NOT NULL DEFAULT 'Kein Geschäft'
        """)

        db.execute("""
            ALTER TABLE items
            ADD COLUMN IF NOT EXISTS price NUMERIC(10, 2)
        """)

        db.execute("""
            ALTER TABLE items
            ADD COLUMN IF NOT EXISTS image_url TEXT
        """)

        db.execute("""
            ALTER TABLE favorites
            ADD COLUMN IF NOT EXISTS price NUMERIC(10, 2)
        """)

        db.execute("""
            ALTER TABLE favorites
            ADD COLUMN IF NOT EXISTS image_url TEXT
        """)

        db.execute("""
            ALTER TABLE purchase_history
            ADD COLUMN IF NOT EXISTS price NUMERIC(10, 2)
        """)

        db.execute("""
            ALTER TABLE purchase_history
            ADD COLUMN IF NOT EXISTS image_url TEXT
        """)


def generate_invite_code(length=8):
    alphabet = string.ascii_uppercase + string.digits

    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))

        with db_connection() as db:
            exists = db.execute(
                "SELECT id FROM shopping_lists WHERE invite_code = %s",
                (code,)
            ).fetchone()

        if not exists:
            return code


def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    with db_connection() as db:
        return db.execute("""
            SELECT users.id, users.username, users.list_id,
                   shopping_lists.name AS list_name,
                   shopping_lists.invite_code,
                   shopping_lists.store,
                   shopping_lists.monthly_budget
            FROM users
            JOIN shopping_lists ON shopping_lists.id = users.list_id
            WHERE users.id = %s
        """, (user_id,)).fetchone()


def login_required(view_function):
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Bitte zuerst anmelden.", "error")
            return redirect(url_for("login"))

        return view_function(*args, **kwargs)

    wrapped.__name__ = view_function.__name__
    return wrapped


BASE_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <meta name="theme-color" content="#198754">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="Einkauf+">
    <link rel="manifest" href="/manifest.json">
    <link rel="apple-touch-icon" href="/icon.svg">
    <link rel="icon" href="/icon.svg" type="image/svg+xml">

    <style>
        * { box-sizing: border-box; }

        :root {
            color-scheme: light;
            --background: #eef2f5;
            --card: #ffffff;
            --text: #202124;
            --muted: #6c757d;
            --border: #e1e5e9;
            --input: #ffffff;
            --soft: #fafbfc;
            --shadow: rgba(0, 0, 0, 0.10);
        }

        html[data-theme="dark"] {
            color-scheme: dark;
            --background: #111418;
            --card: #1c2127;
            --text: #f1f3f5;
            --muted: #adb5bd;
            --border: #343a40;
            --input: #252b32;
            --soft: #22282f;
            --shadow: rgba(0, 0, 0, 0.35);
        }

        body {
            margin: 0;
            padding: 18px;
            font-family: Arial, sans-serif;
            background: var(--background);
            color: var(--text);
            transition: background .2s ease, color .2s ease;
        }

        .container {
            width: 100%;
            max-width: 680px;
            margin: 20px auto;
        }

        .card {
            background: var(--card);
            border-radius: 18px;
            padding: 22px;
            margin-bottom: 16px;
            box-shadow: 0 5px 22px rgba(0, 0, 0, 0.10);
        }

        h1, h2 { margin-top: 0; }

        input, select {
            width: 100%;
            padding: 14px;
            margin: 7px 0 13px;
            border: 1px solid #cfd4da;
            border-radius: 10px;
            font-size: 17px;
        }

        button, .button {
            display: inline-block;
            border: none;
            border-radius: 10px;
            padding: 12px 16px;
            font-size: 16px;
            cursor: pointer;
            text-decoration: none;
            text-align: center;
        }

        .primary { background: #198754; color: white; }
        .blue { background: #0d6efd; color: white; }
        .red { background: #dc3545; color: white; }
        .gray { background: #6c757d; color: white; }

        .full { width: 100%; }

        .navigation {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            margin-bottom: 14px;
        }

        .flash {
            padding: 12px;
            border-radius: 10px;
            margin-bottom: 12px;
        }

        .flash.success { background: #d1e7dd; color: #0f5132; }
        .flash.error { background: #f8d7da; color: #842029; }

        .form-row {
            display: grid;
            grid-template-columns: 1fr 90px 105px 150px auto;
            gap: 9px;
            align-items: end;
        }

        .scanner-row {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 9px;
            margin-bottom: 14px;
        }

        .scanner-row input { margin: 0; }

        .list-tools {
            position: sticky;
            top: 8px;
            z-index: 30;
            margin-bottom: 16px;
            padding: 14px;
            border: 1px solid var(--border);
            border-radius: 16px;
            background: var(--card);
            box-shadow: 0 8px 26px var(--shadow);
        }

        .search-row {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 9px;
            align-items: center;
        }

        .search-row input,
        .search-row select {
            margin: 0;
        }

        .filter-chips {
            display: flex;
            gap: 8px;
            margin-top: 10px;
            overflow-x: auto;
            padding-bottom: 2px;
            scrollbar-width: none;
        }

        .filter-chips::-webkit-scrollbar {
            display: none;
        }

        .filter-chip {
            flex: 0 0 auto;
            border: 1px solid var(--border);
            background: var(--soft);
            color: var(--text);
            padding: 9px 13px;
            border-radius: 999px;
            font-size: 14px;
        }

        .filter-chip.active {
            background: #198754;
            color: white;
            border-color: #198754;
        }

        .filter-result {
            margin-top: 9px;
            color: var(--muted);
            font-size: 14px;
        }

        .shopping-item {
            transition: opacity .18s ease, transform .18s ease;
        }

        .shopping-item.hidden-by-filter {
            display: none;
        }

        .empty-filter-result {
            display: none;
            padding: 22px 10px;
            text-align: center;
            color: var(--muted);
        }

        .empty-filter-result.visible {
            display: block;
        }

        @media (max-width: 560px) {
            .search-row {
                grid-template-columns: 1fr;
            }

            .list-tools {
                top: 4px;
                padding: 11px;
            }
        }

        .category-badge {
            display: inline-block;
            margin-top: 5px;
            padding: 4px 8px;
            border-radius: 999px;
            background: #e9f5ee;
            color: #146c43;
            font-size: 13px;
            font-weight: bold;
        }

        .barcode-text {
            margin-top: 4px;
            color: var(--muted);
            font-family: monospace;
            font-size: 12px;
        }

        .product-status {
            min-height: 20px;
            margin: -4px 0 12px;
        }

        .product-status.success {
            color: #146c43;
            font-weight: bold;
        }

        .product-status.warning {
            color: #997404;
        }

        .product-status.error {
            color: #b02a37;
        }

        .install-note {
            background: #e7f5ec;
            border: 1px solid #b7dfc5;
        }

        .quick-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 10px;
        }

        .quick-card {
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 12px;
            background: var(--soft);
        }

        .quick-card strong {
            display: block;
            margin-bottom: 4px;
        }

        .quick-actions {
            display: flex;
            gap: 8px;
            margin-top: 10px;
        }

        .quick-actions form { flex: 1; }
        .quick-actions button { width: 100%; padding: 9px 10px; }

        .favorite-button {
            background: #ffc107;
            color: #212529;
        }

        .finish-button {
            width: 100%;
            margin-top: 18px;
            background: #6f42c1;
            color: white;
        }

        .history-date {
            color: var(--muted);
            font-size: 13px;
            margin-top: 4px;
        }

        .top-actions {
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
        }

        .theme-button, .voice-button {
            background: #343a40;
            color: white;
        }

        .voice-box {
            margin-bottom: 16px;
            padding: 14px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: var(--soft);
        }

        .notification-box {
            margin-bottom: 16px;
            padding: 14px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: var(--soft);
        }

        .notification-status {
            margin-top: 8px;
            min-height: 18px;
        }

        .offline-badge {
            display: none;
            position: fixed;
            left: 14px;
            right: 14px;
            bottom: 14px;
            z-index: 9999;
            padding: 12px;
            border-radius: 12px;
            background: #842029;
            color: white;
            text-align: center;
            box-shadow: 0 6px 24px rgba(0,0,0,.25);
        }

        body.is-offline .offline-badge {
            display: block;
        }

        .voice-box p {
            margin: 8px 0 0;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
        }

        .dashboard-hero {
            padding: 24px;
            border-radius: 18px;
            background: linear-gradient(135deg, #198754, #36a269);
            color: white;
            margin-bottom: 16px;
            box-shadow: 0 8px 26px rgba(0,0,0,.16);
        }

        .dashboard-hero h1 {
            margin-bottom: 8px;
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }

        .dashboard-tile {
            display: block;
            padding: 18px;
            border-radius: 16px;
            background: var(--card);
            border: 1px solid var(--border);
            color: var(--text);
            text-decoration: none;
            box-shadow: 0 5px 18px var(--shadow);
        }

        .dashboard-tile:hover {
            transform: translateY(-1px);
        }

        .dashboard-number {
            display: block;
            font-size: 30px;
            font-weight: bold;
            margin: 8px 0 4px;
        }

        .dashboard-overview {
            display: grid;
            grid-template-columns: 220px 1fr;
            gap: 16px;
            margin-bottom: 16px;
        }

        .budget-ring-card {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 250px;
            padding: 18px;
            border-radius: 18px;
            background: var(--card);
            border: 1px solid var(--border);
            box-shadow: 0 5px 18px var(--shadow);
        }

        .budget-ring {
            --progress: 0;
            width: 170px;
            height: 170px;
            display: grid;
            place-items: center;
            border-radius: 50%;
            background:
                conic-gradient(
                    #198754 calc(var(--progress) * 1%),
                    var(--soft) 0
                );
            position: relative;
        }

        .budget-ring.warning {
            background:
                conic-gradient(
                    #ffc107 calc(var(--progress) * 1%),
                    var(--soft) 0
                );
        }

        .budget-ring.danger {
            background:
                conic-gradient(
                    #dc3545 calc(var(--progress) * 1%),
                    var(--soft) 0
                );
        }

        .budget-ring::before {
            content: "";
            width: 128px;
            height: 128px;
            border-radius: 50%;
            background: var(--card);
            position: absolute;
        }

        .budget-ring-content {
            position: relative;
            z-index: 1;
            text-align: center;
        }

        .budget-ring-value {
            display: block;
            font-size: 24px;
            font-weight: bold;
        }

        .budget-ring-label {
            font-size: 13px;
            color: var(--muted);
        }

        .dashboard-side {
            display: grid;
            gap: 12px;
        }

        .spending-bars {
            display: grid;
            gap: 14px;
        }

        .spending-row {
            display: grid;
            gap: 6px;
        }

        .spending-head {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            font-size: 14px;
        }

        .spending-track {
            height: 12px;
            overflow: hidden;
            border-radius: 999px;
            background: var(--soft);
            border: 1px solid var(--border);
        }

        .spending-fill {
            height: 100%;
            min-width: 2px;
            border-radius: 999px;
            background: #0d6efd;
        }

        .spending-fill.month {
            background: #6f42c1;
        }

        .status-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
        }

        .status-card {
            padding: 14px;
            border: 1px solid var(--border);
            border-radius: 14px;
            background: var(--soft);
            text-align: center;
        }

        .status-icon {
            display: block;
            font-size: 24px;
            margin-bottom: 6px;
        }

        .status-value {
            display: block;
            font-size: 22px;
            font-weight: bold;
        }

        .quick-links.modern {
            grid-template-columns: repeat(4, 1fr);
        }

        .quick-link {
            transition: transform .18s ease, box-shadow .18s ease;
        }

        .quick-link:active {
            transform: scale(.98);
        }

        .receipt-upload {
            display: grid;
            gap: 14px;
        }

        .receipt-dropzone {
            position: relative;
            min-height: 190px;
            display: grid;
            place-items: center;
            padding: 22px;
            border: 2px dashed var(--border);
            border-radius: 16px;
            background: var(--soft);
            text-align: center;
            cursor: pointer;
        }

        .receipt-dropzone input {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            opacity: 0;
            cursor: pointer;
        }

        .receipt-dropzone strong {
            display: block;
            margin-bottom: 6px;
            font-size: 18px;
        }

        .receipt-preview {
            display: none;
            gap: 12px;
        }

        .receipt-preview.visible {
            display: grid;
        }

        .receipt-preview img {
            width: 100%;
            max-height: 560px;
            object-fit: contain;
            border-radius: 14px;
            background: #111;
            border: 1px solid var(--border);
        }

        .receipt-actions {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }

        .ocr-progress { display:none; gap:8px; padding:14px; border-radius:14px; background:var(--soft); border:1px solid var(--border); }
        .ocr-progress.visible { display:grid; }
        .ocr-progress-track { height:14px; overflow:hidden; border-radius:999px; background:var(--card); border:1px solid var(--border); }
        .ocr-progress-fill { height:100%; width:0%; border-radius:999px; background:#198754; transition:width .2s ease; }
        .receipt-review { display:none; gap:14px; }
        .receipt-review.visible { display:grid; }
        .receipt-summary { display:grid; grid-template-columns:1fr 1fr 1fr; gap:9px; }
        .receipt-table-wrap { overflow-x:auto; }
        .receipt-table { width:100%; border-collapse:collapse; min-width:560px; }
        .receipt-table th, .receipt-table td { padding:9px; border-bottom:1px solid var(--border); text-align:left; vertical-align:middle; }
        .receipt-table input { margin:0; min-width:90px; }
        .receipt-table .name-input { min-width:230px; }
        .raw-ocr { width:100%; min-height:150px; resize:vertical; font-family:monospace; font-size:13px; }
        @media (max-width:560px) { .receipt-summary { grid-template-columns:1fr; } }

        .scan-help {
            display: grid;
            gap: 8px;
            padding: 14px;
            border-radius: 14px;
            background: var(--soft);
            border: 1px solid var(--border);
        }

        .scan-help div {
            display: flex;
            gap: 9px;
            align-items: flex-start;
        }

        @media (max-width: 560px) {
            .receipt-actions {
                grid-template-columns: 1fr;
            }
        }

        @media (max-width: 760px) {
            .dashboard-overview {
                grid-template-columns: 1fr;
            }

            .budget-ring-card {
                min-height: 220px;
            }

            .status-grid,
            .quick-links.modern {
                grid-template-columns: 1fr 1fr;
            }
        }

        @media (max-width: 480px) {
            .status-grid,
            .quick-links.modern {
                grid-template-columns: 1fr;
            }
        }

        .dashboard-label {
            color: var(--muted);
            font-size: 14px;
        }

        .budget-progress {
            width: 100%;
            height: 18px;
            margin-top: 10px;
            overflow: hidden;
            border-radius: 999px;
            background: var(--soft);
            border: 1px solid var(--border);
        }

        .budget-progress-fill {
            height: 100%;
            background: #198754;
            transition: width .25s ease;
        }

        .budget-progress-fill.warning {
            background: #ffc107;
        }

        .budget-progress-fill.danger {
            background: #dc3545;
        }

        .budget-form {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 10px;
            margin-top: 14px;
        }

        .inventory-grid {
            display: grid;
            gap: 12px;
        }

        .inventory-card {
            padding: 14px;
            border: 1px solid var(--border);
            border-radius: 14px;
            background: var(--soft);
        }

        .inventory-header {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: flex-start;
        }

        .inventory-quantity {
            font-size: 22px;
            font-weight: bold;
        }

        .inventory-actions {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 8px;
            margin-top: 12px;
        }

        .inventory-actions form {
            margin: 0;
        }

        .inventory-actions button {
            width: 100%;
            padding: 9px;
        }

        .low-stock {
            color: #dc3545;
            font-weight: bold;
        }

        .inventory-form {
            display: grid;
            grid-template-columns: 1.4fr .7fr .7fr 1fr 1fr auto;
            gap: 9px;
            align-items: end;
        }

        @media (max-width: 700px) {
            .budget-form,
            .inventory-form {
                grid-template-columns: 1fr;
            }

            .inventory-actions {
                grid-template-columns: repeat(2, 1fr);
            }
        }

        .quick-links {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
        }

        .quick-link {
            display: block;
            padding: 16px 10px;
            border-radius: 14px;
            background: var(--soft);
            border: 1px solid var(--border);
            color: var(--text);
            text-decoration: none;
            text-align: center;
            font-weight: bold;
        }

        .recent-list {
            display: grid;
            gap: 8px;
        }

        .recent-entry {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            padding: 12px;
            border-radius: 12px;
            background: var(--soft);
            border: 1px solid var(--border);
        }

        @media (max-width: 560px) {
            .dashboard-grid,
            .quick-links {
                grid-template-columns: 1fr;
            }
        }

        .stat-card {
            padding: 14px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: var(--soft);
            text-align: center;
        }

        .stat-value {
            display: block;
            font-size: 22px;
            font-weight: bold;
            margin-bottom: 4px;
        }

        @media (max-width: 560px) {
            .stats-grid {
                grid-template-columns: 1fr;
            }
        }

        .store-form {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 10px;
            margin-bottom: 16px;
        }

        .store-form select {
            margin: 0;
        }

        .product-image {
            width: 58px;
            height: 58px;
            object-fit: contain;
            border-radius: 10px;
            background: var(--soft);
            border: 1px solid var(--border);
            float: left;
            margin-right: 10px;
        }

        .price-line {
            margin-top: 5px;
            font-weight: bold;
        }

        .total-box {
            margin-top: 18px;
            padding: 16px;
            border-radius: 12px;
            background: #eef7f1;
            font-size: 22px;
            font-weight: bold;
            text-align: right;
        }

        .suggestions {
            position: relative;
        }

        .suggestion-box {
            display: none;
            position: absolute;
            left: 0;
            right: 0;
            top: calc(100% - 12px);
            z-index: 20;
            background: var(--card);
            border: 1px solid #d6d9dc;
            border-radius: 10px;
            box-shadow: 0 8px 24px rgba(0,0,0,.14);
            max-height: 230px;
            overflow-y: auto;
        }

        .suggestion-entry {
            width: 100%;
            border-radius: 0;
            background: var(--card);
            color: var(--text);
            text-align: left;
            border-bottom: 1px solid #eeeeee;
        }

        .suggestion-entry:last-child {
            border-bottom: none;
        }

        .category-heading {
            margin: 18px 0 6px;
            padding-bottom: 6px;
            border-bottom: 2px solid #198754;
        }

        @media (max-width: 560px) {
            .store-form {
                grid-template-columns: 1fr;
            }
        }

        dialog {
            width: min(92vw, 520px);
            border: none;
            border-radius: 18px;
            padding: 20px;
            box-shadow: 0 10px 40px rgba(0,0,0,.25);
        }

        dialog::backdrop { background: rgba(0,0,0,.55); }

        video {
            width: 100%;
            min-height: 240px;
            border-radius: 12px;
            background: #111;
            object-fit: cover;
        }

        .scanner-video-wrap {
            position: relative;
        }

        .scan-guide {
            position: absolute;
            left: 8%;
            right: 8%;
            top: 38%;
            height: 24%;
            border: 3px solid white;
            border-radius: 12px;
            box-shadow: 0 0 0 9999px rgba(0,0,0,.22);
            pointer-events: none;
        }

        .dialog-buttons {
            display: flex;
            gap: 9px;
            margin-top: 12px;
        }

        .dialog-buttons button { flex: 1; }

        .item {
            display: grid;
            grid-template-columns: 1fr auto auto;
            gap: 9px;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #eeeeee;
        }

        .item-name {
            font-size: 18px;
            overflow-wrap: anywhere;
        }

        .completed {
            text-decoration: line-through;
            color: #888888;
        }

        .small {
            color: var(--muted);
            font-size: 14px;
        }

        .code {
            font-family: monospace;
            font-size: 22px;
            font-weight: bold;
            letter-spacing: 2px;
            background: #f1f3f5;
            padding: 10px;
            border-radius: 10px;
            text-align: center;
        }

        .tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
        }

        .tabs a { flex: 1; }

        @media (max-width: 560px) {
            .form-row {
                grid-template-columns: 1fr;
            }

            .scanner-row {
                grid-template-columns: 1fr;
            }

            .item {
                grid-template-columns: 1fr auto;
            }

            .item form:last-child {
                grid-column: 2;
            }

            .navigation {
                align-items: flex-start;
            }
        }
    </style>
    <script src="https://unpkg.com/@zxing/browser@0.2.1"></script>
    <script src="https://cdn.jsdelivr.net/npm/tesseract.js@6/dist/tesseract.min.js"></script>
</head>
<body>
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% for category, message in messages %}
                <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
        {% endwith %}

        {{ content|safe }}
    </div>

    <div class="offline-badge">
        📵 Offline – Änderungen können erst bei aktiver Internetverbindung gespeichert werden.
    </div>

    <script>
        function updateOnlineStatus() {
            document.body.classList.toggle("is-offline", !navigator.onLine);
        }

        window.addEventListener("online", updateOnlineStatus);
        window.addEventListener("offline", updateOnlineStatus);
        updateOnlineStatus();

        if ("serviceWorker" in navigator) {
            window.addEventListener("load", function () {
                navigator.serviceWorker.register("/service-worker.js")
                    .catch(function (error) {
                        console.log("Service Worker:", error);
                    });
            });
        }
    </script>
</body>
</html>
"""


def page(title, content, **values):
    inner = render_template_string(content, **values)
    return render_template_string(BASE_HTML, title=title, content=inner)


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


@app.route("/registrieren", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        mode = request.form.get("mode", "create")
        list_name = request.form.get("list_name", "").strip()
        invite_code = request.form.get("invite_code", "").strip().upper()

        if len(username) < 3:
            flash("Der Benutzername muss mindestens 3 Zeichen haben.", "error")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("Das Passwort muss mindestens 6 Zeichen haben.", "error")
            return redirect(url_for("register"))

        try:
            with db_connection() as db:
                existing_user = db.execute(
                    "SELECT id FROM users WHERE LOWER(username) = LOWER(%s)",
                    (username,)
                ).fetchone()

                if existing_user:
                    flash("Dieser Benutzername ist bereits vergeben.", "error")
                    return redirect(url_for("register"))

                if mode == "join":
                    shopping_list = db.execute(
                        "SELECT id FROM shopping_lists WHERE invite_code = %s",
                        (invite_code,)
                    ).fetchone()

                    if not shopping_list:
                        flash("Der Einladungscode ist ungültig.", "error")
                        return redirect(url_for("register"))

                    list_id = shopping_list["id"]

                else:
                    if not list_name:
                        list_name = "Gemeinsame Einkaufsliste"

                    new_code = generate_invite_code()

                    list_row = db.execute(
                        """
                        INSERT INTO shopping_lists (name, invite_code)
                        VALUES (%s, %s)
                        RETURNING id
                        """,
                        (list_name, new_code)
                    ).fetchone()
                    list_id = list_row["id"]

                user_row = db.execute("""
                    INSERT INTO users (username, password_hash, list_id)
                    VALUES (%s, %s, %s)
                    RETURNING id
                """, (
                    username,
                    generate_password_hash(password),
                    list_id
                )).fetchone()

                user_id = user_row["id"]

            session.clear()
            session["user_id"] = user_id
            flash("Registrierung erfolgreich.", "success")
            return redirect(url_for("dashboard"))

        except psycopg.Error as error:
            app.logger.exception("Datenbankfehler bei Registrierung: %s", error)
            flash("Beim Speichern ist ein Fehler aufgetreten.", "error")

    content = """
    <div class="card">
        <h1>🛒 Registrieren</h1>

        <div class="tabs">
            <a class="button gray" href="{{ url_for('login') }}">Anmelden</a>
            <a class="button blue" href="{{ url_for('register') }}">Registrieren</a>
        </div>

        <form method="POST">
            <label>Benutzername</label>
            <input name="username" minlength="3" required>

            <label>Passwort</label>
            <input type="password" name="password" minlength="6" required>

            <label>Was möchtest du machen?</label>
            <select name="mode" id="mode" onchange="changeMode()">
                <option value="create">Neue gemeinsame Liste erstellen</option>
                <option value="join">Mit Einladungscode beitreten</option>
            </select>

            <div id="create-fields">
                <label>Name der Liste</label>
                <input name="list_name" placeholder="Zum Beispiel: Maxi & Freundin">
            </div>

            <div id="join-fields" style="display:none;">
                <label>Einladungscode</label>
                <input name="invite_code" placeholder="Zum Beispiel: A1B2C3D4">
            </div>

            <button class="primary full" type="submit">Konto erstellen</button>
        </form>
    </div>

    <script>
        function changeMode() {
            const mode = document.getElementById("mode").value;
            document.getElementById("create-fields").style.display =
                mode === "create" ? "block" : "none";
            document.getElementById("join-fields").style.display =
                mode === "join" ? "block" : "none";
        }
    </script>
    """
    return page("Registrieren", content)


@app.route("/anmelden", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with db_connection() as db:
            user = db.execute(
                "SELECT id, password_hash FROM users WHERE LOWER(username) = LOWER(%s)",
                (username,)
            ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Benutzername oder Passwort ist falsch.", "error")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]
        flash("Du bist jetzt angemeldet.", "success")
        return redirect(url_for("dashboard"))

    content = """
    <div class="card">
        <h1>🔐 Anmelden</h1>

        <div class="tabs">
            <a class="button blue" href="{{ url_for('login') }}">Anmelden</a>
            <a class="button gray" href="{{ url_for('register') }}">Registrieren</a>
        </div>

        <form method="POST">
            <label>Benutzername</label>
            <input name="username" required autofocus>

            <label>Passwort</label>
            <input type="password" name="password" required>

            <button class="primary full" type="submit">Anmelden</button>
        </form>
    </div>
    """
    return page("Anmelden", content)


@app.route("/abmelden")
def logout():
    session.clear()
    flash("Du wurdest abgemeldet.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    if not user:
        session.clear()
        return redirect(url_for("login"))

    with db_connection() as db:
        summary = db.execute("""
            SELECT
                (
                    SELECT COUNT(*)
                    FROM items
                    WHERE list_id = %s AND completed = FALSE
                ) AS open_items,
                (
                    SELECT COUNT(*)
                    FROM favorites
                    WHERE list_id = %s
                ) AS favorite_count,
                (
                    SELECT COALESCE(SUM(COALESCE(price, 0)), 0)
                    FROM purchase_history
                    WHERE list_id = %s
                      AND purchased_at >= DATE_TRUNC('month', CURRENT_TIMESTAMP)
                ) AS month_spending,
                (
                    SELECT COALESCE(SUM(COALESCE(price, 0)), 0)
                    FROM purchase_history
                    WHERE list_id = %s
                      AND purchased_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
                ) AS week_spending
        """, (
            user["list_id"],
            user["list_id"],
            user["list_id"],
            user["list_id"]
        )).fetchone()

        last_purchase = db.execute("""
            SELECT
                purchased_at,
                COUNT(*) AS item_count,
                COALESCE(SUM(COALESCE(price, 0)), 0) AS total
            FROM purchase_history
            WHERE list_id = %s
            GROUP BY purchased_at
            ORDER BY purchased_at DESC
            LIMIT 1
        """, (user["list_id"],)).fetchone()

        recent_items = db.execute("""
            SELECT name, quantity, category, created_at
            FROM items
            WHERE list_id = %s
            ORDER BY created_at DESC
            LIMIT 5
        """, (user["list_id"],)).fetchall()

        inventory_summary = db.execute("""
            SELECT
                COUNT(*) AS inventory_count,
                COUNT(*) FILTER (
                    WHERE quantity <= minimum_quantity
                ) AS low_stock_count
            FROM inventory
            WHERE list_id = %s
        """, (user["list_id"],)).fetchone()

    monthly_budget = user["monthly_budget"]
    month_spending = summary["month_spending"] or Decimal("0")
    budget_percent = Decimal("0")

    if monthly_budget and Decimal(str(monthly_budget)) > 0:
        budget_percent = (
            Decimal(str(month_spending))
            / Decimal(str(monthly_budget))
            * Decimal("100")
        )

    budget_percent_display = min(float(budget_percent), 100.0)
    budget_class = ""

    if budget_percent >= 100:
        budget_class = "danger"
    elif budget_percent >= 80:
        budget_class = "warning"


    month_spending_value = float(summary["month_spending"] or 0)
    week_spending_value = float(summary["week_spending"] or 0)

    spending_scale = max(
        month_spending_value,
        week_spending_value,
        1.0
    )

    week_bar_percent = min(
        (week_spending_value / spending_scale) * 100,
        100.0
    )

    month_bar_percent = min(
        (month_spending_value / spending_scale) * 100,
        100.0
    )

    content = """
    <div class="navigation">
        <div>
            <strong>{{ user.username }}</strong><br>
            <span class="small">{{ user.list_name }}</span>
        </div>
        <div class="top-actions">
            <button id="theme-button" class="theme-button" type="button"
                    onclick="toggleTheme()">🌙 Dunkel</button>
            <a class="button gray" href="{{ url_for('logout') }}">Abmelden</a>
        </div>
    </div>

    <div class="dashboard-hero">
        <h1>👋 Hallo {{ user.username }}</h1>
        <div>{{ user.list_name }} · {{ user.store }}</div>
    </div>

    <div class="dashboard-overview">
        <div class="budget-ring-card">
            <div class="budget-ring {{ budget_class }}"
                 style="--progress: {{ budget_percent_display }};">
                <div class="budget-ring-content">
                    <span class="budget-ring-value">
                        {% if user.monthly_budget %}
                            {{ budget_percent_display|round(0)|int }} %
                        {% else %}
                            —
                        {% endif %}
                    </span>
                    <span class="budget-ring-label">Monatsbudget</span>
                </div>
            </div>

            <p class="small" style="text-align:center; margin-top:14px;">
                {% if user.monthly_budget %}
                    {{ '%.2f'|format(summary.month_spending or 0) }} €
                    von
                    {{ '%.2f'|format(user.monthly_budget) }} €
                {% else %}
                    Noch kein Budget festgelegt.
                {% endif %}
            </p>
        </div>

        <div class="dashboard-side">
            <div class="card" style="margin-bottom:0;">
                <h2>📈 Ausgabenübersicht</h2>

                <div class="spending-bars">
                    <div class="spending-row">
                        <div class="spending-head">
                            <span>Letzte 7 Tage</span>
                            <strong>{{ '%.2f'|format(summary.week_spending or 0) }} €</strong>
                        </div>
                        <div class="spending-track">
                            <div class="spending-fill"
                                 style="width: {{ week_bar_percent }}%;"></div>
                        </div>
                    </div>

                    <div class="spending-row">
                        <div class="spending-head">
                            <span>Aktueller Monat</span>
                            <strong>{{ '%.2f'|format(summary.month_spending or 0) }} €</strong>
                        </div>
                        <div class="spending-track">
                            <div class="spending-fill month"
                                 style="width: {{ month_bar_percent }}%;"></div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="status-grid">
                <a class="status-card" href="{{ url_for('shopping_list') }}">
                    <span class="status-icon">🛒</span>
                    <span class="status-value">{{ summary.open_items or 0 }}</span>
                    <span class="dashboard-label">offen</span>
                </a>

                <a class="status-card" href="{{ url_for('shopping_list') }}#favoriten">
                    <span class="status-icon">⭐</span>
                    <span class="status-value">{{ summary.favorite_count or 0 }}</span>
                    <span class="dashboard-label">Favoriten</span>
                </a>

                <a class="status-card" href="{{ url_for('inventory_page') }}">
                    <span class="status-icon">📦</span>
                    <span class="status-value">
                        {{ inventory_summary.low_stock_count or 0 }}
                    </span>
                    <span class="dashboard-label">knapp</span>
                </a>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>💳 Monatsbudget</h2>

        {% if user.monthly_budget %}
            <div>
                <strong>
                    {{ '%.2f'|format(summary.month_spending or 0) }} €
                    von
                    {{ '%.2f'|format(user.monthly_budget) }} €
                </strong>

                <div class="budget-progress">
                    <div class="budget-progress-fill {{ budget_class }}"
                         style="width: {{ budget_percent_display }}%;"></div>
                </div>

                <p class="small">
                    {% if budget_percent >= 100 %}
                        Budget überschritten.
                    {% elif budget_percent >= 80 %}
                        Du hast bereits mehr als 80 % des Budgets verwendet.
                    {% else %}
                        Noch {{ '%.2f'|format(user.monthly_budget - (summary.month_spending or 0)) }} € verfügbar.
                    {% endif %}
                </p>
            </div>
        {% else %}
            <p class="small">Noch kein Monatsbudget festgelegt.</p>
        {% endif %}

        <form class="budget-form" method="POST"
              action="{{ url_for('update_budget') }}">
            <input name="monthly_budget"
                   inputmode="decimal"
                   placeholder="Monatsbudget, z. B. 500,00"
                   value="{% if user.monthly_budget %}{{ user.monthly_budget }}{% endif %}">
            <button class="primary" type="submit">Budget speichern</button>
        </form>
    </div>

    <div class="card">
        <h2>⚡ Schnellzugriff</h2>
        <div class="quick-links modern">
            <a class="quick-link" href="{{ url_for('shopping_list') }}">
                ➕ Artikel hinzufügen
            </a>
            <a class="quick-link" href="{{ url_for('shopping_list') }}">
                📷 Barcode scannen
            </a>
            <a class="quick-link" href="{{ url_for('shopping_list') }}">
                🎤 Artikel sprechen
            </a>
            <a class="quick-link" href="{{ url_for('inventory_page') }}">
                📦 Vorrat verwalten
            </a>
            <a class="quick-link" href="{{ url_for('shopping_list') }}">
                🧾 Einkauf abschließen
            </a>
            <a class="quick-link" href="{{ url_for('receipt_upload') }}">
                📸 Kassenbon scannen
            </a>
        </div>
    </div>

    <div class="card">
        <h2>🧾 Letzter Einkauf</h2>
        {% if last_purchase %}
            <div class="recent-entry">
                <div>
                    <strong>{{ last_purchase.item_count }} Artikel</strong><br>
                    <span class="small">
                        {{ last_purchase.purchased_at.strftime('%d.%m.%Y %H:%M') }}
                    </span>
                </div>
                <strong>{{ '%.2f'|format(last_purchase.total or 0) }} €</strong>
            </div>
        {% else %}
            <p class="small">Noch kein abgeschlossener Einkauf vorhanden.</p>
        {% endif %}
    </div>

    <div class="card">
        <h2>🆕 Zuletzt hinzugefügt</h2>
        {% if recent_items %}
            <div class="recent-list">
                {% for item in recent_items %}
                    <div class="recent-entry">
                        <div>
                            <strong>{{ item.quantity }} × {{ item.name }}</strong><br>
                            <span class="small">{{ item.category }}</span>
                        </div>
                        <span class="small">
                            {{ item.created_at.strftime('%d.%m. %H:%M') }}
                        </span>
                    </div>
                {% endfor %}
            </div>
        {% else %}
            <p class="small">Noch keine Artikel vorhanden.</p>
        {% endif %}
    </div>
    """

    return page(
        "Dashboard",
        content,
        user=user,
        summary=summary,
        last_purchase=last_purchase,
        recent_items=recent_items,
        inventory_summary=inventory_summary,
        budget_percent=budget_percent,
        budget_percent_display=budget_percent_display,
        budget_class=budget_class,
        week_bar_percent=week_bar_percent,
        month_bar_percent=month_bar_percent
    )


@app.route("/budget", methods=["POST"])
@login_required
def update_budget():
    user = current_user()
    budget_text = request.form.get("monthly_budget", "").strip().replace(",", ".")

    monthly_budget = None

    if budget_text:
        try:
            monthly_budget = Decimal(budget_text)

            if monthly_budget <= 0 or monthly_budget > Decimal("999999.99"):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            flash("Bitte ein gültiges Monatsbudget eingeben.", "error")
            return redirect(url_for("dashboard"))

    with db_connection() as db:
        db.execute(
            "UPDATE shopping_lists SET monthly_budget = %s WHERE id = %s",
            (monthly_budget, user["list_id"])
        )

    flash("Monatsbudget wurde gespeichert.", "success")
    return redirect(url_for("dashboard"))


@app.route("/kassenbon")
@login_required
def receipt_upload():
    user = current_user()
    content = """
    <div class="navigation">
      <div><strong>{{ user.username }}</strong><br><span class="small">{{ user.list_name }}</span></div>
      <div class="top-actions"><a class="button blue" href="{{ url_for('dashboard') }}">📊 Dashboard</a><a class="button gray" href="{{ url_for('shopping_list') }}">🛒 Liste</a></div>
    </div>
    <div class="card">
      <h1>📸 Kassenbon erkennen</h1>
      <p class="small">Das Foto wird direkt auf deinem Gerät ausgewertet. Erst nach deiner Bestätigung werden Artikel gespeichert.</p>
      <div class="scan-help"><div><span>📄</span><span>Bon vollständig ins Bild legen.</span></div><div><span>💡</span><span>Gute Beleuchtung ohne Spiegelung.</span></div><div><span>📐</span><span>Möglichst gerade fotografieren.</span></div></div>
    </div>
    <div class="card"><div class="receipt-upload">
      <label class="receipt-dropzone" for="receipt-file"><div><strong>📷 Kamera oder Galerie öffnen</strong><span class="small">JPG, PNG oder WEBP · maximal 8 MB</span></div><input id="receipt-file" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onchange="previewReceipt(event)"></label>
      <div id="receipt-preview" class="receipt-preview"><img id="receipt-preview-image" alt="Vorschau"><div id="receipt-file-info" class="small"></div><div class="receipt-actions"><button class="gray" type="button" onclick="clearReceiptPreview()">Anderes Bild</button><button id="ocr-button" class="primary" type="button" onclick="runReceiptOcr()">🔍 Bon auslesen</button></div></div>
      <div id="ocr-progress" class="ocr-progress"><strong id="ocr-status">OCR wird vorbereitet …</strong><div class="ocr-progress-track"><div id="ocr-progress-fill" class="ocr-progress-fill"></div></div><span id="ocr-progress-text" class="small">0 %</span></div>
    </div></div>
    <div id="receipt-review" class="card receipt-review">
      <h2>✏️ Erkannte Daten prüfen</h2>
      <div class="receipt-summary"><div><label>Geschäft</label><input id="receipt-store" placeholder="z. B. Hofer"></div><div><label>Datum</label><input id="receipt-date" type="date"></div><div><label>Erkannte Summe €</label><input id="receipt-total" inputmode="decimal" placeholder="0,00"></div></div>
      <div class="receipt-table-wrap"><table class="receipt-table"><thead><tr><th>Übernehmen</th><th>Artikel</th><th>Preis €</th><th></th></tr></thead><tbody id="receipt-items-body"></tbody></table></div>
      <button class="blue" type="button" onclick="addReceiptRow()">➕ Zeile hinzufügen</button>
      <details><summary>Erkannten Rohtext anzeigen</summary><textarea id="raw-ocr-text" class="raw-ocr"></textarea></details>
      <button id="save-receipt-button" class="primary full" type="button" onclick="saveReceipt()">💾 Einkauf speichern</button><div id="receipt-save-status" class="small product-status"></div>
    </div>
    <script>
      const MAX_RECEIPT_SIZE=8*1024*1024; let receiptObjectUrl=null, selectedReceiptFile=null, ocrWorker=null;
      function previewReceipt(event){const file=event.target.files&&event.target.files[0]; if(!file){clearReceiptPreview();return;} if(!["image/jpeg","image/png","image/webp"].includes(file.type)){alert("Bitte JPG, PNG oder WEBP verwenden.");clearReceiptPreview();return;} if(file.size>MAX_RECEIPT_SIZE){alert("Das Bild ist größer als 8 MB.");clearReceiptPreview();return;} selectedReceiptFile=file; if(receiptObjectUrl)URL.revokeObjectURL(receiptObjectUrl); receiptObjectUrl=URL.createObjectURL(file); document.getElementById("receipt-preview-image").src=receiptObjectUrl; document.getElementById("receipt-file-info").textContent=file.name+" · "+(file.size/(1024*1024)).toFixed(2)+" MB"; document.getElementById("receipt-preview").classList.add("visible"); document.getElementById("receipt-review").classList.remove("visible");}
      function clearReceiptPreview(){selectedReceiptFile=null; document.getElementById("receipt-file").value=""; document.getElementById("receipt-preview").classList.remove("visible"); document.getElementById("receipt-review").classList.remove("visible"); if(receiptObjectUrl){URL.revokeObjectURL(receiptObjectUrl);receiptObjectUrl=null;}}
      function updateOcrProgress(message,progress){const p=Math.max(0,Math.min(100,Math.round((progress||0)*100)));document.getElementById("ocr-status").textContent=message||"OCR läuft …";document.getElementById("ocr-progress-fill").style.width=p+"%";document.getElementById("ocr-progress-text").textContent=p+" %";}
      async function runReceiptOcr(){if(!selectedReceiptFile){alert("Bitte zuerst ein Bonfoto auswählen.");return;} if(!window.Tesseract){alert("OCR-Bibliothek konnte nicht geladen werden.");return;} const button=document.getElementById("ocr-button"),box=document.getElementById("ocr-progress");button.disabled=true;box.classList.add("visible");updateOcrProgress("OCR wird gestartet …",0);try{if(ocrWorker){try{await ocrWorker.terminate();}catch(e){}} ocrWorker=await Tesseract.createWorker("deu",1,{logger:m=>updateOcrProgress(m.status,m.progress||0)});const result=await ocrWorker.recognize(selectedReceiptFile);const text=result.data.text||"";document.getElementById("raw-ocr-text").value=text;renderReceiptReview(parseReceiptText(text));updateOcrProgress("OCR abgeschlossen.",1);document.getElementById("receipt-review").classList.add("visible");document.getElementById("receipt-review").scrollIntoView({behavior:"smooth"});}catch(e){console.error(e);alert("Bon konnte nicht erkannt werden. Bitte schärferes Foto versuchen.");updateOcrProgress("OCR fehlgeschlagen.",0);}finally{button.disabled=false;if(ocrWorker){try{await ocrWorker.terminate();}catch(e){}ocrWorker=null;}}}
      function normalizeReceiptLine(line){return line.replace(/[|]/g,"I").replace(/\s+/g," ").trim();}
      function parseEuroValue(value){const cleaned=String(value).replace(/[^\d,.-]/g,"").replace(/\.(?=\d{2}$)/,",").replace(",", ".");const n=Number.parseFloat(cleaned);return Number.isFinite(n)?n:null;}
      function detectStore(lines){const stores=["HOFER","SPAR","BILLA","LIDL","MPREIS","M-PREIS","DM","MÜLLER"];const t=lines.slice(0,12).join(" ").toUpperCase();return stores.find(s=>t.includes(s))||"";}
      function detectDate(text){const m=text.match(/\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\b/);if(!m)return"";let y=m[3];if(y.length===2)y="20"+y;return y+"-"+m[2].padStart(2,"0")+"-"+m[1].padStart(2,"0");}
      function parseReceiptText(text){const lines=text.split(/\r?\n/).map(normalizeReceiptLine).filter(Boolean);const totals=["SUMME","GESAMT","ZU ZAHLEN","ENDSUMME","TOTAL"],ignore=["UST","MWST","BAR","KARTE","RÜCKGELD","WECHSELGELD","NETTO","BRUTTO","BON","BELEG","KASSE","FILIALE"];const rx=/(.+?)\s+(-?\d{1,5}[,.]\d{2})\s*[€E]?\s*$/;const items=[];let total=null;for(const line of lines){const upper=line.toUpperCase(),m=line.match(rx);if(!m)continue;const name=m[1].replace(/^[*#\-.\s]+/,"").replace(/\s+[A-Z]$/,"").trim(),price=parseEuroValue(m[2]);if(!name||price===null)continue;if(totals.some(k=>upper.includes(k))){total=price;continue;}if(ignore.some(k=>upper.includes(k)))continue;if(name.length>=2&&name.length<=100)items.push({name,price:Math.abs(price)});}if(total===null&&items.length)total=items.reduce((s,i)=>s+i.price,0);return{store:detectStore(lines),date:detectDate(text),total,items:items.slice(0,80)};}
      function renderReceiptReview(p){document.getElementById("receipt-store").value=p.store||"";document.getElementById("receipt-date").value=p.date||"";document.getElementById("receipt-total").value=p.total!=null?p.total.toFixed(2).replace(".",","):"";const b=document.getElementById("receipt-items-body");b.innerHTML="";(p.items.length?p.items:[{}]).forEach(i=>addReceiptRow(i.name,i.price));}
      function esc(v){return String(v||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");}
      function addReceiptRow(name,price){const row=document.createElement("tr");row.innerHTML=`<td><input class="receipt-use" type="checkbox" checked></td><td><input class="receipt-item-name name-input" value="${esc(name)}" placeholder="Artikelname"></td><td><input class="receipt-item-price" inputmode="decimal" value="${price!=null?Number(price).toFixed(2).replace(".",","):""}" placeholder="0,00"></td><td><button class="red" type="button" onclick="this.closest('tr').remove()">Löschen</button></td>`;document.getElementById("receipt-items-body").appendChild(row);}
      async function saveReceipt(){const items=[...document.querySelectorAll("#receipt-items-body tr")].filter(r=>r.querySelector(".receipt-use").checked).map(r=>({name:r.querySelector(".receipt-item-name").value.trim(),price:r.querySelector(".receipt-item-price").value.trim()})).filter(i=>i.name&&i.price);if(!items.length){alert("Bitte mindestens einen Artikel übernehmen.");return;}const button=document.getElementById("save-receipt-button"),status=document.getElementById("receipt-save-status");button.disabled=true;status.textContent="Einkauf wird gespeichert …";try{const res=await fetch("/api/kassenbon/speichern",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({store:document.getElementById("receipt-store").value.trim(),date:document.getElementById("receipt-date").value,total:document.getElementById("receipt-total").value.trim(),items})});const data=await res.json();if(!res.ok)throw new Error(data.message||"Speichern fehlgeschlagen.");status.textContent="✅ "+data.saved+" Artikel wurden gespeichert.";status.className="small product-status success";setTimeout(()=>location.href="/dashboard",900);}catch(e){status.textContent=e.message;status.className="small product-status error";}finally{button.disabled=false;}}
    </script>
    """
    return page("Kassenbon erkennen", content, user=user)


@app.route("/api/kassenbon/speichern", methods=["POST"])
@login_required
def save_receipt():
    user = current_user()
    payload = request.get_json(silent=True) or {}
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return jsonify({"message": "Ungültige Artikeldaten."}), 400
    cleaned = []
    for entry in raw_items[:100]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        price_text = str(entry.get("price") or "").strip().replace(",", ".")
        if not name or len(name) > 100:
            continue
        try:
            price = Decimal(price_text)
            if price < 0 or price > Decimal("999999.99"):
                continue
        except (InvalidOperation, ValueError):
            continue
        cleaned.append((name, price))
    if not cleaned:
        return jsonify({"message": "Keine gültigen Artikel gefunden."}), 400
    store = str(payload.get("store") or "").strip()[:100]
    date_text = str(payload.get("date") or "").strip()
    with db_connection() as db:
        if store:
            mapping = {"HOFER":"Hofer","SPAR":"Spar","BILLA":"Billa","LIDL":"Lidl","MPREIS":"MPreis","M-PREIS":"MPreis","MÜLLER":"Müller"}
            normalized = mapping.get(store.upper(), store)
            allowed = {"Hofer","Spar","Billa","Lidl","MPreis","DM","Müller"}
            if normalized in allowed:
                db.execute("UPDATE shopping_lists SET store = %s WHERE id = %s", (normalized, user["list_id"]))
        for name, price in cleaned:
            if date_text:
                db.execute("""INSERT INTO purchase_history (list_id,name,quantity,category,price,purchased_by,purchased_at) VALUES (%s,%s,'1','Sonstiges',%s,%s,%s::date)""", (user["list_id"], name, price, user["id"], date_text))
            else:
                db.execute("""INSERT INTO purchase_history (list_id,name,quantity,category,price,purchased_by) VALUES (%s,%s,'1','Sonstiges',%s,%s)""", (user["list_id"], name, price, user["id"]))
    return jsonify({"success": True, "saved": len(cleaned)})


@app.route("/liste")
@login_required
def shopping_list():
    user = current_user()

    if not user:
        session.clear()
        return redirect(url_for("login"))

    with db_connection() as db:
        items = db.execute("""
            SELECT items.id, items.name, items.quantity, items.completed,
                   items.category, items.barcode, items.price, items.image_url,
                   users.username AS creator,
                   EXISTS (
                       SELECT 1 FROM favorites
                       WHERE favorites.list_id = items.list_id
                         AND LOWER(favorites.name) = LOWER(items.name)
                   ) AS is_favorite
            FROM items
            LEFT JOIN users ON users.id = items.created_by
            WHERE items.list_id = %s
            ORDER BY
                items.completed ASC,
                CASE items.category
                    WHEN 'Obst & Gemüse' THEN 1
                    WHEN 'Brot & Backwaren' THEN 2
                    WHEN 'Milchprodukte' THEN 3
                    WHEN 'Fleisch & Fisch' THEN 4
                    WHEN 'Tiefkühl' THEN 5
                    WHEN 'Getränke' THEN 6
                    WHEN 'Haushalt' THEN 7
                    WHEN 'Drogerie' THEN 8
                    ELSE 9
                END,
                items.id DESC
        """, (user["list_id"],)).fetchall()

        favorites = db.execute("""
            SELECT id, name, quantity, category, barcode, price, image_url
            FROM favorites
            WHERE list_id = %s
            ORDER BY LOWER(name)
            LIMIT 20
        """, (user["list_id"],)).fetchall()

        history = db.execute("""
            SELECT id, name, quantity, category, barcode, price,
                   image_url, purchased_at
            FROM purchase_history
            WHERE list_id = %s
            ORDER BY purchased_at DESC
            LIMIT 20
        """, (user["list_id"],)).fetchall()

        stats = db.execute("""
            SELECT
                COUNT(*) FILTER (
                    WHERE purchased_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
                ) AS items_7_days,
                COALESCE(SUM(
                    CASE
                        WHEN purchased_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
                        THEN COALESCE(price, 0)
                        ELSE 0
                    END
                ), 0) AS spending_7_days,
                COALESCE(SUM(
                    CASE
                        WHEN purchased_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
                        THEN COALESCE(price, 0)
                        ELSE 0
                    END
                ), 0) AS spending_30_days
            FROM purchase_history
            WHERE list_id = %s
        """, (user["list_id"],)).fetchone()

        suggestions = db.execute("""
            SELECT DISTINCT ON (LOWER(name))
                   name, quantity, category, barcode, price, image_url
            FROM (
                SELECT name, quantity, category, barcode, price, image_url,
                       created_at AS sort_date
                FROM favorites
                WHERE list_id = %s

                UNION ALL

                SELECT name, quantity, category, barcode, price, image_url,
                       purchased_at AS sort_date
                FROM purchase_history
                WHERE list_id = %s
            ) AS suggestion_source
            ORDER BY LOWER(name), sort_date DESC
            LIMIT 60
        """, (user["list_id"], user["list_id"])).fetchall()

    total = Decimal("0.00")

    for item in items:
        if item["price"] is None:
            continue

        try:
            quantity_number = Decimal(
                str(item["quantity"]).replace(",", ".")
            )
        except (InvalidOperation, ValueError):
            quantity_number = Decimal("1")

        total += Decimal(str(item["price"])) * quantity_number

    content = """
    <div class="navigation">
        <div>
            <strong>{{ user.username }}</strong><br>
            <span class="small">{{ user.list_name }}</span>
        </div>
        <div class="top-actions">
            <a class="button blue" href="{{ url_for('dashboard') }}">📊 Dashboard</a>
            <button id="theme-button" class="theme-button" type="button"
                    onclick="toggleTheme()">🌙 Dunkel</button>
            <a class="button gray" href="{{ url_for('logout') }}">Abmelden</a>
        </div>
    </div>

    <div class="card">
        <h1>🛒 Einkauf+</h1>

        <form class="store-form" method="POST" action="{{ url_for('update_store') }}">
            <select name="store">
                {% for store_name in stores %}
                    <option value="{{ store_name }}"
                            {% if user.store == store_name %}selected{% endif %}>
                        {{ store_name }}
                    </option>
                {% endfor %}
            </select>
            <button class="blue" type="submit">Geschäft speichern</button>
        </form>

        <div class="list-tools">
            <div class="search-row">
                <input
                    id="list-search"
                    type="search"
                    placeholder="🔍 Artikel suchen …"
                    autocomplete="off"
                    oninput="applyListFilters()"
                >

                <select id="category-filter" onchange="applyListFilters()">
                    <option value="all">Alle Kategorien</option>
                    <option value="Obst & Gemüse">Obst & Gemüse</option>
                    <option value="Brot & Backwaren">Brot & Backwaren</option>
                    <option value="Milchprodukte">Milchprodukte</option>
                    <option value="Fleisch & Fisch">Fleisch & Fisch</option>
                    <option value="Tiefkühl">Tiefkühl</option>
                    <option value="Getränke">Getränke</option>
                    <option value="Haushalt">Haushalt</option>
                    <option value="Drogerie">Drogerie</option>
                    <option value="Sonstiges">Sonstiges</option>
                </select>
            </div>

            <div class="filter-chips" role="group" aria-label="Listenfilter">
                <button class="filter-chip active" type="button"
                        onclick="setListFilter('all', this)">
                    Alle
                </button>
                <button class="filter-chip" type="button"
                        onclick="setListFilter('open', this)">
                    Offen
                </button>
                <button class="filter-chip" type="button"
                        onclick="setListFilter('completed', this)">
                    Erledigt
                </button>
                <button class="filter-chip" type="button"
                        onclick="setListFilter('favorites', this)">
                    ⭐ Favoriten
                </button>
            </div>

            <div id="filter-result" class="filter-result"></div>
        </div>

        <div class="voice-box">
            <button id="voice-button" class="voice-button full" type="button"
                    onclick="startVoiceInput()">
                🎤 Artikel sprechen
            </button>
            <p id="voice-status" class="small">
                Beispiel: „Milch, Brot und Eier“
            </p>
        </div>

        <div class="notification-box">
            <button id="notification-button" class="blue full" type="button"
                    onclick="enableNotifications()">
                🔔 Benachrichtigungen aktivieren
            </button>
            <div id="notification-status" class="small notification-status">
                Du erhältst eine Nachricht, wenn jemand etwas zur gemeinsamen Liste hinzufügt.
            </div>
        </div>

        <div class="scanner-row">
            <input id="barcode" form="add-form" name="barcode"
                   inputmode="numeric" placeholder="Barcode optional" maxlength="40"
                   oninput="barcodeChanged()">
            <button class="blue" type="button" onclick="openScanner()">
                📷 Barcode scannen
            </button>
        </div>

        <div id="product-status" class="small product-status"></div>
        <input id="image-url" form="add-form" type="hidden" name="image_url">

        <form id="add-form" class="form-row" method="POST" action="{{ url_for('add_item') }}">
            <div class="suggestions">
                <label>Artikel</label>
                <input id="name" name="name" placeholder="Zum Beispiel: Milch"
                       autocomplete="off" oninput="showSuggestions()" required>
                <div id="suggestion-box" class="suggestion-box"></div>
            </div>

            <div>
                <label>Menge</label>
                <input name="quantity" value="1" maxlength="20" inputmode="decimal">
            </div>

            <div>
                <label>Preis €</label>
                <input id="price" name="price" placeholder="0,00"
                       maxlength="12" inputmode="decimal">
            </div>

            <div>
                <label>Kategorie</label>
                <select name="category">
                    <option>Obst & Gemüse</option>
                    <option>Milchprodukte</option>
                    <option>Brot & Backwaren</option>
                    <option>Fleisch & Fisch</option>
                    <option>Getränke</option>
                    <option>Tiefkühl</option>
                    <option>Haushalt</option>
                    <option>Drogerie</option>
                    <option selected>Sonstiges</option>
                </select>
            </div>

            <button class="primary" type="submit">Hinzufügen</button>
        </form>

        {% if items %}
            {% for item in items %}
                <div
                    class="item shopping-item"
                    data-name="{{ item.name|lower }}"
                    data-category="{{ item.category }}"
                    data-completed="{{ 'true' if item.completed else 'false' }}"
                    data-favorite="{{ 'true' if item.is_favorite else 'false' }}"
                >
                    <div>
                        {% if item.image_url %}
                            <img class="product-image"
                                 src="{{ item.image_url }}"
                                 alt="{{ item.name }}"
                                 loading="lazy">
                        {% endif %}
                        <div class="item-name {% if item.completed %}completed{% endif %}">
                            {{ item.quantity }} × {{ item.name }}
                        </div>

                        <span class="category-badge">{{ item.category }}</span>

                        {% if item.price is not none %}
                            <div class="price-line">
                                {{ '%.2f'|format(item.price) }} € je Stück
                            </div>
                        {% endif %}

                        {% if item.barcode %}
                            <div class="barcode-text">Barcode: {{ item.barcode }}</div>
                        {% endif %}

                        {% if item.creator %}
                            <div class="small">von {{ item.creator }}</div>
                        {% endif %}
                    </div>

                    <form method="POST" action="{{ url_for('toggle_item', item_id=item.id) }}">
                        <button class="blue" type="submit">
                            {% if item.completed %}Zurück{% else %}✓{% endif %}
                        </button>
                    </form>

                    <form method="POST" action="{{ url_for('toggle_favorite', item_id=item.id) }}">
                        <button class="favorite-button" type="submit">
                            {% if item.is_favorite %}★{% else %}☆{% endif %}
                        </button>
                    </form>

                    <form method="POST" action="{{ url_for('delete_item', item_id=item.id) }}">
                        <button class="red" type="submit">Löschen</button>
                    </form>
                </div>
            {% endfor %}

            <div id="empty-filter-result" class="empty-filter-result">
                Keine passenden Artikel gefunden.
            </div>

            <form method="POST" action="{{ url_for('finish_purchase') }}"
                  onsubmit="return confirm('Alle abgehakten Artikel in den Verlauf verschieben?');">
                <button class="finish-button" type="submit">
                    🧾 Einkauf abschließen
                </button>
            </form>
        {% else %}
            <p class="small">Die Liste ist noch leer.</p>
        {% endif %}

        <div class="total-box">
            💶 Gesamt: {{ '%.2f'|format(total) }} €
        </div>
    </div>

    <div class="card">
        <h2>📊 Ausgabenstatistik</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <span class="stat-value">{{ stats.items_7_days or 0 }}</span>
                <span class="small">Artikel in 7 Tagen</span>
            </div>
            <div class="stat-card">
                <span class="stat-value">{{ '%.2f'|format(stats.spending_7_days or 0) }} €</span>
                <span class="small">Ausgaben in 7 Tagen</span>
            </div>
            <div class="stat-card">
                <span class="stat-value">{{ '%.2f'|format(stats.spending_30_days or 0) }} €</span>
                <span class="small">Ausgaben in 30 Tagen</span>
            </div>
        </div>
        <p class="small">
            Gezählt werden Artikel, die über „Einkauf abschließen“ in den Verlauf verschoben wurden.
        </p>
    </div>

    <div class="card">
        <h2>⭐ Favoriten</h2>
        {% if favorites %}
            <div class="quick-grid">
                {% for favorite in favorites %}
                    <div class="quick-card">
                        {% if favorite.image_url %}
                            <img class="product-image" src="{{ favorite.image_url }}"
                                 alt="{{ favorite.name }}" loading="lazy">
                        {% endif %}
                        <strong>{{ favorite.name }}</strong>
                        <div class="small">{{ favorite.quantity }} × · {{ favorite.category }}</div>
                        {% if favorite.price is not none %}
                            <div class="price-line">{{ '%.2f'|format(favorite.price) }} €</div>
                        {% endif %}
                        <div class="quick-actions">
                            <form method="POST"
                                  action="{{ url_for('add_favorite_to_list', favorite_id=favorite.id) }}">
                                <button class="primary" type="submit">+ Liste</button>
                            </form>
                            <form method="POST"
                                  action="{{ url_for('delete_favorite', favorite_id=favorite.id) }}">
                                <button class="red" type="submit">Entfernen</button>
                            </form>
                        </div>
                    </div>
                {% endfor %}
            </div>
        {% else %}
            <p class="small">Tippe bei einem Artikel auf ☆, um ihn als Favorit zu speichern.</p>
        {% endif %}
    </div>

    <div class="card">
        <h2>🕘 Einkaufsverlauf</h2>
        {% if history %}
            <div class="quick-grid">
                {% for entry in history %}
                    <div class="quick-card">
                        {% if entry.image_url %}
                            <img class="product-image" src="{{ entry.image_url }}"
                                 alt="{{ entry.name }}" loading="lazy">
                        {% endif %}
                        <strong>{{ entry.name }}</strong>
                        <div class="small">{{ entry.quantity }} × · {{ entry.category }}</div>
                        {% if entry.price is not none %}
                            <div class="price-line">{{ '%.2f'|format(entry.price) }} €</div>
                        {% endif %}
                        <div class="history-date">
                            {{ entry.purchased_at.strftime('%d.%m.%Y %H:%M') }}
                        </div>
                        <div class="quick-actions">
                            <form method="POST"
                                  action="{{ url_for('add_history_to_list', history_id=entry.id) }}">
                                <button class="primary" type="submit">+ Liste</button>
                            </form>
                        </div>
                    </div>
                {% endfor %}
            </div>
        {% else %}
            <p class="small">Noch keine abgeschlossenen Einkäufe vorhanden.</p>
        {% endif %}
    </div>

    <div class="card install-note">
        <h2>📱 Als App installieren</h2>
        <p><strong>iPhone:</strong> In Safari auf Teilen und danach „Zum Home-Bildschirm“ tippen.</p>
        <p><strong>Android:</strong> Im Browser-Menü „App installieren“ oder „Zum Startbildschirm hinzufügen“ wählen.</p>
    </div>

    <div class="card">
        <h2>👥 Person einladen</h2>
        <p>Die andere Person registriert sich und wählt „Mit Einladungscode beitreten“.</p>
        <div class="code">{{ user.invite_code }}</div>
    </div>

    <dialog id="scanner-dialog">
        <h2>📷 Barcode scannen</h2>
        <div class="scanner-video-wrap">
            <video id="scanner-video" playsinline muted></video>
            <div class="scan-guide"></div>
        </div>
        <p id="scanner-status" class="small">Kamera wird vorbereitet … Auf dem iPhone bitte den Kamera-Zugriff erlauben.</p>
        <div class="dialog-buttons">
            <button class="gray" type="button" onclick="closeScanner()">Schließen</button>
        </div>
    </dialog>

    <script>
        const productSuggestions = {{ suggestions|tojson }};
        let activeListFilter = "all";

        function normalizeSearchText(value) {
            return String(value || "")
                .toLocaleLowerCase("de")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "");
        }

        function setListFilter(filterName, button) {
            activeListFilter = filterName;

            document.querySelectorAll(".filter-chip").forEach(function (chip) {
                chip.classList.toggle("active", chip === button);
            });

            applyListFilters();
        }

        function applyListFilters() {
            const searchInput = document.getElementById("list-search");
            const categorySelect = document.getElementById("category-filter");
            const resultText = document.getElementById("filter-result");
            const emptyResult = document.getElementById("empty-filter-result");

            if (!searchInput || !categorySelect) {
                return;
            }

            const query = normalizeSearchText(searchInput.value.trim());
            const selectedCategory = categorySelect.value;
            const items = Array.from(
                document.querySelectorAll(".shopping-item")
            );

            let visibleCount = 0;

            items.forEach(function (item) {
                const name = normalizeSearchText(item.dataset.name);
                const category = item.dataset.category || "";
                const completed = item.dataset.completed === "true";
                const favorite = item.dataset.favorite === "true";

                const matchesSearch = !query || name.includes(query);
                const matchesCategory =
                    selectedCategory === "all" ||
                    category === selectedCategory;

                let matchesStatus = true;

                if (activeListFilter === "open") {
                    matchesStatus = !completed;
                } else if (activeListFilter === "completed") {
                    matchesStatus = completed;
                } else if (activeListFilter === "favorites") {
                    matchesStatus = favorite;
                }

                const visible =
                    matchesSearch &&
                    matchesCategory &&
                    matchesStatus;

                item.classList.toggle("hidden-by-filter", !visible);

                if (visible) {
                    visibleCount += 1;
                }
            });

            if (resultText) {
                resultText.textContent =
                    visibleCount === 1
                        ? "1 Artikel angezeigt"
                        : visibleCount + " Artikel angezeigt";
            }

            if (emptyResult) {
                emptyResult.classList.toggle(
                    "visible",
                    items.length > 0 && visibleCount === 0
                );
            }
        }

        window.addEventListener("load", applyListFilters);
        const vapidPublicKey = {{ vapid_public_key|tojson }};

        function urlBase64ToUint8Array(base64String) {
            const padding = "=".repeat((4 - base64String.length % 4) % 4);
            const base64 = (base64String + padding)
                .replace(/-/g, "+")
                .replace(/_/g, "/");
            const rawData = window.atob(base64);
            return Uint8Array.from([...rawData].map(char => char.charCodeAt(0)));
        }

        async function enableNotifications() {
            const status = document.getElementById("notification-status");
            const button = document.getElementById("notification-button");

            if (!vapidPublicKey) {
                status.textContent =
                    "Push ist auf dem Server noch nicht eingerichtet. Die App funktioniert weiterhin normal.";
                return;
            }

            if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
                status.textContent =
                    "Dieser Browser unterstützt keine Web-Push-Benachrichtigungen.";
                return;
            }

            try {
                button.disabled = true;

                const permission = await Notification.requestPermission();

                if (permission !== "granted") {
                    status.textContent = "Benachrichtigungen wurden nicht erlaubt.";
                    return;
                }

                const registration = await navigator.serviceWorker.ready;
                let subscription = await registration.pushManager.getSubscription();

                if (!subscription) {
                    subscription = await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
                    });
                }

                const response = await fetch("/api/push/subscribe", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Accept": "application/json"
                    },
                    body: JSON.stringify(subscription)
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.message || "Push konnte nicht gespeichert werden.");
                }

                status.textContent = "✅ Benachrichtigungen sind aktiviert.";
                button.textContent = "🔔 Benachrichtigungen aktiv";

            } catch (error) {
                console.error(error);
                status.textContent = "Push konnte nicht aktiviert werden: " + error.message;
            } finally {
                button.disabled = false;
            }
        }

        async function refreshNotificationState() {
            if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
                return;
            }

            try {
                const registration = await navigator.serviceWorker.ready;
                const subscription = await registration.pushManager.getSubscription();

                if (subscription) {
                    const button = document.getElementById("notification-button");
                    const status = document.getElementById("notification-status");
                    button.textContent = "🔔 Benachrichtigungen aktiv";
                    status.textContent = "✅ Dieses Gerät ist für Benachrichtigungen registriert.";
                }
            } catch (error) {
                console.log(error);
            }
        }

        window.addEventListener("load", refreshNotificationState);

        function applySavedTheme() {
            const saved = localStorage.getItem("einkauf-theme") || "light";
            document.documentElement.dataset.theme = saved;
            updateThemeButton(saved);
        }

        function updateThemeButton(theme) {
            const button = document.getElementById("theme-button");
            if (!button) return;
            button.textContent = theme === "dark" ? "☀️ Hell" : "🌙 Dunkel";
        }

        function toggleTheme() {
            const current = document.documentElement.dataset.theme || "light";
            const next = current === "dark" ? "light" : "dark";
            document.documentElement.dataset.theme = next;
            localStorage.setItem("einkauf-theme", next);
            updateThemeButton(next);
        }

        applySavedTheme();

        let voiceRecognition = null;

        async function addSpokenItems(items) {
            const response = await fetch("/api/sprache-artikel", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                body: JSON.stringify({ items: items })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.message || "Artikel konnten nicht gespeichert werden.");
            }

            return data;
        }

        function startVoiceInput() {
            const Recognition =
                window.SpeechRecognition || window.webkitSpeechRecognition;
            const status = document.getElementById("voice-status");
            const button = document.getElementById("voice-button");

            if (!Recognition) {
                status.textContent =
                    "Spracheingabe wird von diesem Browser nicht unterstützt. " +
                    "Auf dem iPhone kannst du alternativ die Mikrofontaste der Tastatur verwenden.";
                return;
            }

            if (voiceRecognition) {
                try { voiceRecognition.stop(); } catch (error) {}
            }

            voiceRecognition = new Recognition();
            voiceRecognition.lang = "de-AT";
            voiceRecognition.interimResults = false;
            voiceRecognition.maxAlternatives = 1;

            button.disabled = true;
            status.textContent = "Ich höre zu …";

            voiceRecognition.onresult = async function (event) {
                const transcript = event.results[0][0].transcript.trim();
                status.textContent = "Erkannt: " + transcript;

                const items = transcript
                    .replace(/\s+und\s+/gi, ",")
                    .replace(/\s+sowie\s+/gi, ",")
                    .split(/[,;]+/)
                    .map(item => item.trim())
                    .filter(item => item.length > 0)
                    .slice(0, 20);

                if (!items.length) {
                    status.textContent = "Keine Artikel erkannt.";
                    return;
                }

                try {
                    const result = await addSpokenItems(items);
                    status.textContent =
                        result.added + " Artikel wurden hinzugefügt.";
                    window.setTimeout(function () {
                        window.location.reload();
                    }, 650);
                } catch (error) {
                    status.textContent = error.message;
                }
            };

            voiceRecognition.onerror = function (event) {
                status.textContent =
                    event.error === "not-allowed"
                        ? "Bitte den Mikrofonzugriff für diese Webseite erlauben."
                        : "Sprache konnte nicht erkannt werden.";
            };

            voiceRecognition.onend = function () {
                button.disabled = false;
            };

            voiceRecognition.start();
        }

        function showSuggestions() {
            const input = document.getElementById("name");
            const box = document.getElementById("suggestion-box");
            const query = input.value.trim().toLowerCase();

            if (query.length < 1) {
                box.style.display = "none";
                box.innerHTML = "";
                return;
            }

            const matches = productSuggestions
                .filter(item => item.name.toLowerCase().includes(query))
                .slice(0, 8);

            if (!matches.length) {
                box.style.display = "none";
                return;
            }

            box.innerHTML = "";

            matches.forEach(item => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "suggestion-entry";
                button.textContent = item.name + " · " + item.category;
                button.onclick = function () {
                    document.getElementById("name").value = item.name;
                    document.querySelector('#add-form input[name="quantity"]').value =
                        item.quantity || "1";
                    document.querySelector('#add-form select[name="category"]').value =
                        item.category || "Sonstiges";
                    document.getElementById("barcode").value = item.barcode || "";
                    document.getElementById("price").value =
                        item.price !== null && item.price !== undefined
                            ? String(item.price).replace(".", ",")
                            : "";
                    document.getElementById("image-url").value = item.image_url || "";
                    box.style.display = "none";
                };
                box.appendChild(button);
            });

            box.style.display = "block";
        }

        document.addEventListener("click", function (event) {
            if (!event.target.closest(".suggestions")) {
                document.getElementById("suggestion-box").style.display = "none";
            }
        });

        let scannerControls = null;
        let codeReader = null;
        let scanFinished = false;

        let lookupTimer = null;

        function barcodeChanged() {
            const barcode = document.getElementById("barcode").value.trim();

            if (lookupTimer) {
                window.clearTimeout(lookupTimer);
            }

            if (barcode.length < 8) {
                setProductStatus("", "");
                return;
            }

            lookupTimer = window.setTimeout(function () {
                lookupProduct(barcode);
            }, 650);
        }

        function setProductStatus(message, statusClass) {
            const element = document.getElementById("product-status");
            element.textContent = message;
            element.className = "small product-status" +
                (statusClass ? " " + statusClass : "");
        }

        async function lookupProduct(barcode) {
            const cleanedBarcode = String(barcode).replace(/[^0-9]/g, "");

            if (cleanedBarcode.length < 8 || cleanedBarcode.length > 14) {
                setProductStatus(
                    "Der Barcode muss zwischen 8 und 14 Ziffern haben.",
                    "warning"
                );
                document.getElementById("name").focus();
                return;
            }

            setProductStatus("Produkt wird gesucht …", "");

            try {
                const response = await fetch(
                    "/api/produkt/" + encodeURIComponent(cleanedBarcode),
                    {
                        headers: { "Accept": "application/json" },
                        cache: "no-store"
                    }
                );

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.message || "Produktsuche fehlgeschlagen");
                }

                if (!data.found) {
                    setProductStatus(
                        data.message ||
                        "Produkt nicht gefunden – bitte den Namen manuell eingeben.",
                        "warning"
                    );
                    document.getElementById("name").focus();
                    return;
                }

                document.getElementById("name").value = data.name || "";
                document.getElementById("image-url").value = data.image_url || "";

                const categorySelect = document.querySelector(
                    '#add-form select[name="category"]'
                );

                if (categorySelect && data.category) {
                    categorySelect.value = data.category;
                }

                const brandText = data.brand ? " · " + data.brand : "";
                setProductStatus(
                    "Gefunden: " + data.name + brandText,
                    "success"
                );

                document.querySelector(
                    '#add-form input[name="quantity"]'
                ).focus();

            } catch (error) {
                console.error(error);
                setProductStatus(
                    "Produktsuche derzeit nicht erreichbar – Name bitte manuell eingeben.",
                    "error"
                );
                document.getElementById("name").focus();
            }
        }

        async function openScanner() {
            const dialog = document.getElementById("scanner-dialog");
            const status = document.getElementById("scanner-status");
            const video = document.getElementById("scanner-video");

            scanFinished = false;

            if (!window.ZXingBrowser) {
                alert(
                    "Der Barcode-Scanner konnte nicht geladen werden. " +
                    "Bitte Internetverbindung prüfen und die Seite neu laden."
                );
                return;
            }

            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert("Dieser Browser erlaubt keinen Kamera-Zugriff.");
                return;
            }

            try {
                dialog.showModal();
                status.textContent = "Kamera wird gestartet …";

                codeReader = new ZXingBrowser.BrowserMultiFormatOneDReader();

                const constraints = {
                    video: {
                        facingMode: { ideal: "environment" },
                        width: { ideal: 1920 },
                        height: { ideal: 1080 },
                        frameRate: { ideal: 30, max: 60 }
                    },
                    audio: false
                };

                scannerControls = await codeReader.decodeFromConstraints(
                    constraints,
                    video,
                    function (result, error, controls) {
                        if (result && !scanFinished) {
                            scanFinished = true;

                            const barcode = result.getText();
                            document.getElementById("barcode").value = barcode;
                            status.textContent = "Barcode erkannt: " + barcode;

                            if (navigator.vibrate) {
                                navigator.vibrate(120);
                            }

                            window.setTimeout(async function () {
                                closeScanner();
                                await lookupProduct(barcode);
                            }, 500);
                        }

                        if (
                            error &&
                            !["NotFoundException", "ChecksumException", "FormatException"]
                                .includes(error.name)
                        ) {
                            console.log("Scanner-Hinweis:", error);
                        }
                    }
                );

                const stream = video.srcObject;
                const track = stream && stream.getVideoTracks
                    ? stream.getVideoTracks()[0]
                    : null;

                if (track) {
                    try {
                        const capabilities = track.getCapabilities
                            ? track.getCapabilities()
                            : {};

                        const advanced = [];

                        if (capabilities.focusMode &&
                            capabilities.focusMode.includes("continuous")) {
                            advanced.push({ focusMode: "continuous" });
                        }

                        if (capabilities.zoom) {
                            const zoomValue = Math.min(
                                capabilities.zoom.max,
                                Math.max(capabilities.zoom.min, 2)
                            );
                            advanced.push({ zoom: zoomValue });
                        }

                        if (advanced.length > 0) {
                            await track.applyConstraints({ advanced: advanced });
                        }
                    } catch (focusError) {
                        console.log("Kamera-Fokus konnte nicht angepasst werden:", focusError);
                    }
                }

                status.textContent =
                    "Barcode waagrecht halten, vollständig ins Bild bringen und 10–20 cm Abstand halten.";

            } catch (error) {
                console.error("Kamera konnte nicht gestartet werden:", error);

                let message = "Kamera konnte nicht geöffnet werden.";

                if (error && error.name === "NotAllowedError") {
                    message =
                        "Kamera-Zugriff wurde nicht erlaubt. " +
                        "Bitte in Safari die Kamera für diese Webseite erlauben.";
                } else if (error && error.name === "NotFoundError") {
                    message = "Auf diesem Gerät wurde keine Kamera gefunden.";
                } else if (error && error.name === "NotReadableError") {
                    message =
                        "Die Kamera wird möglicherweise bereits von einer anderen App verwendet.";
                }

                alert(message);
                closeScanner();
            }
        }

        function closeScanner() {
            if (scannerControls) {
                try {
                    scannerControls.stop();
                } catch (error) {
                    console.log(error);
                }
                scannerControls = null;
            }

            if (codeReader) {
                try {
                    codeReader.reset();
                } catch (error) {
                    console.log(error);
                }
                codeReader = null;
            }

            const video = document.getElementById("scanner-video");

            if (video && video.srcObject) {
                video.srcObject.getTracks().forEach(function (track) {
                    track.stop();
                });
                video.srcObject = null;
            }

            const dialog = document.getElementById("scanner-dialog");

            if (dialog && dialog.open) {
                dialog.close();
            }
        }

        document.addEventListener("visibilitychange", function () {
            if (document.hidden) {
                closeScanner();
            }
        });
    </script>
    """
    return page(
        "Einkaufsliste",
        content,
        user=user,
        items=items,
        favorites=favorites,
        history=history,
        suggestions=[dict(row) for row in suggestions],
        total=total,
        stats=stats,
        vapid_public_key=VAPID_PUBLIC_KEY,
        stores=[
            "Kein Geschäft", "Hofer", "Spar", "Billa",
            "Lidl", "MPreis", "DM", "Müller"
        ]
    )


def send_list_push(list_id, title, body, exclude_user_id=None):
    """Sendet Push an registrierte Geräte einer gemeinsamen Liste."""
    if not (
        webpush
        and VAPID_PUBLIC_KEY
        and VAPID_PRIVATE_KEY
        and VAPID_SUBJECT
    ):
        return

    with db_connection() as db:
        if exclude_user_id:
            subscriptions = db.execute("""
                SELECT id, endpoint, subscription_json
                FROM push_subscriptions
                WHERE list_id = %s AND user_id <> %s
            """, (list_id, exclude_user_id)).fetchall()
        else:
            subscriptions = db.execute("""
                SELECT id, endpoint, subscription_json
                FROM push_subscriptions
                WHERE list_id = %s
            """, (list_id,)).fetchall()

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": "/liste",
        "icon": "/icon.svg",
        "badge": "/icon.svg"
    }, ensure_ascii=False)

    expired_ids = []

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=subscription["subscription_json"],
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                timeout=8
            )
        except WebPushException as error:
            status_code = getattr(
                getattr(error, "response", None),
                "status_code",
                None
            )

            if status_code in (404, 410):
                expired_ids.append(subscription["id"])
            else:
                app.logger.warning("Push fehlgeschlagen: %s", error)
        except Exception as error:
            app.logger.warning("Push fehlgeschlagen: %s", error)

    if expired_ids:
        with db_connection() as db:
            db.execute(
                "DELETE FROM push_subscriptions WHERE id = ANY(%s)",
                (expired_ids,)
            )


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def subscribe_push():
    user = current_user()
    subscription = request.get_json(silent=True) or {}
    endpoint = str(subscription.get("endpoint") or "").strip()

    if not endpoint or not subscription.get("keys"):
        return jsonify({
            "message": "Ungültige Push-Registrierung."
        }), 400

    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        return jsonify({
            "message": "Ungültiger Push-Endpunkt."
        }), 400

    with db_connection() as db:
        db.execute("""
            INSERT INTO push_subscriptions (
                user_id, list_id, endpoint, subscription_json, updated_at
            )
            VALUES (%s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP)
            ON CONFLICT (endpoint)
            DO UPDATE SET
                user_id = EXCLUDED.user_id,
                list_id = EXCLUDED.list_id,
                subscription_json = EXCLUDED.subscription_json,
                updated_at = CURRENT_TIMESTAMP
        """, (
            user["id"],
            user["list_id"],
            endpoint,
            json.dumps(subscription)
        ))

    return jsonify({"success": True})


@app.route("/vorrat")
@login_required
def inventory_page():
    user = current_user()

    with db_connection() as db:
        inventory_items = db.execute("""
            SELECT id, name, quantity, minimum_quantity, unit,
                   category, barcode, updated_at
            FROM inventory
            WHERE list_id = %s
            ORDER BY
                CASE WHEN quantity <= minimum_quantity THEN 0 ELSE 1 END,
                LOWER(name)
        """, (user["list_id"],)).fetchall()

    content = """
    <div class="navigation">
        <div>
            <strong>{{ user.username }}</strong><br>
            <span class="small">{{ user.list_name }}</span>
        </div>
        <div class="top-actions">
            <a class="button blue" href="{{ url_for('dashboard') }}">📊 Dashboard</a>
            <a class="button gray" href="{{ url_for('shopping_list') }}">🛒 Liste</a>
        </div>
    </div>

    <div class="card">
        <h1>📦 Vorratsverwaltung</h1>

        <form class="inventory-form" method="POST"
              action="{{ url_for('add_inventory_item') }}">
            <div>
                <label>Produkt</label>
                <input name="name" placeholder="Zum Beispiel: Milch" required>
            </div>

            <div>
                <label>Bestand</label>
                <input name="quantity" value="1" inputmode="decimal" required>
            </div>

            <div>
                <label>Minimum</label>
                <input name="minimum_quantity" value="0" inputmode="decimal">
            </div>

            <div>
                <label>Einheit</label>
                <select name="unit">
                    <option>Stück</option>
                    <option>Packung</option>
                    <option>Flasche</option>
                    <option>kg</option>
                    <option>Liter</option>
                </select>
            </div>

            <div>
                <label>Kategorie</label>
                <select name="category">
                    <option>Obst & Gemüse</option>
                    <option>Milchprodukte</option>
                    <option>Brot & Backwaren</option>
                    <option>Fleisch & Fisch</option>
                    <option>Getränke</option>
                    <option>Tiefkühl</option>
                    <option>Haushalt</option>
                    <option>Drogerie</option>
                    <option selected>Sonstiges</option>
                </select>
            </div>

            <button class="primary" type="submit">Speichern</button>
        </form>
    </div>

    <div class="card">
        <h2>Aktueller Vorrat</h2>

        {% if inventory_items %}
            <div class="inventory-grid">
                {% for item in inventory_items %}
                    <div class="inventory-card">
                        <div class="inventory-header">
                            <div>
                                <strong>{{ item.name }}</strong><br>
                                <span class="small">{{ item.category }}</span>
                            </div>

                            <div class="inventory-quantity
                                {% if item.quantity <= item.minimum_quantity %}low-stock{% endif %}">
                                {{ item.quantity }} {{ item.unit }}
                            </div>
                        </div>

                        <div class="small">
                            Mindestbestand: {{ item.minimum_quantity }} {{ item.unit }}
                        </div>

                        {% if item.quantity <= item.minimum_quantity %}
                            <div class="low-stock">
                                ⚠️ Bestand ist niedrig.
                            </div>
                        {% endif %}

                        <div class="inventory-actions">
                            <form method="POST"
                                  action="{{ url_for('change_inventory', inventory_id=item.id) }}">
                                <input type="hidden" name="change" value="1">
                                <button class="primary" type="submit">+1</button>
                            </form>

                            <form method="POST"
                                  action="{{ url_for('change_inventory', inventory_id=item.id) }}">
                                <input type="hidden" name="change" value="-1">
                                <button class="gray" type="submit">−1</button>
                            </form>

                            <form method="POST"
                                  action="{{ url_for('inventory_to_list', inventory_id=item.id) }}">
                                <button class="blue" type="submit">+ Liste</button>
                            </form>

                            <form method="POST"
                                  action="{{ url_for('delete_inventory', inventory_id=item.id) }}"
                                  onsubmit="return confirm('Vorratsartikel löschen?');">
                                <button class="red" type="submit">Löschen</button>
                            </form>
                        </div>
                    </div>
                {% endfor %}
            </div>
        {% else %}
            <p class="small">Noch keine Vorratsartikel eingetragen.</p>
        {% endif %}
    </div>
    """

    return page(
        "Vorrat",
        content,
        user=user,
        inventory_items=inventory_items
    )


@app.route("/vorrat/hinzufuegen", methods=["POST"])
@login_required
def add_inventory_item():
    user = current_user()
    name = request.form.get("name", "").strip()
    quantity_text = request.form.get("quantity", "0").strip().replace(",", ".")
    minimum_text = request.form.get(
        "minimum_quantity", "0"
    ).strip().replace(",", ".")
    unit = request.form.get("unit", "Stück").strip()
    category = request.form.get("category", "Sonstiges").strip()

    allowed_units = {"Stück", "Packung", "Flasche", "kg", "Liter"}
    allowed_categories = {
        "Obst & Gemüse", "Milchprodukte", "Brot & Backwaren",
        "Fleisch & Fisch", "Getränke", "Tiefkühl",
        "Haushalt", "Drogerie", "Sonstiges"
    }

    if not name or len(name) > 100:
        flash("Bitte einen gültigen Produktnamen eingeben.", "error")
        return redirect(url_for("inventory_page"))

    if unit not in allowed_units:
        unit = "Stück"

    if category not in allowed_categories:
        category = "Sonstiges"

    try:
        quantity = Decimal(quantity_text)
        minimum_quantity = Decimal(minimum_text)

        if quantity < 0 or minimum_quantity < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        flash("Bestand und Mindestbestand müssen gültige Zahlen sein.", "error")
        return redirect(url_for("inventory_page"))

    with db_connection() as db:
        db.execute("""
            INSERT INTO inventory (
                list_id, name, quantity, minimum_quantity,
                unit, category, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (list_id, LOWER(name))
            DO UPDATE SET
                quantity = EXCLUDED.quantity,
                minimum_quantity = EXCLUDED.minimum_quantity,
                unit = EXCLUDED.unit,
                category = EXCLUDED.category,
                updated_at = CURRENT_TIMESTAMP
        """, (
            user["list_id"],
            name,
            quantity,
            minimum_quantity,
            unit,
            category
        ))

    flash("Vorrat wurde gespeichert.", "success")
    return redirect(url_for("inventory_page"))


@app.route("/vorrat/<int:inventory_id>/aendern", methods=["POST"])
@login_required
def change_inventory(inventory_id):
    user = current_user()
    change_text = request.form.get("change", "0").strip().replace(",", ".")

    try:
        change = Decimal(change_text)
    except (InvalidOperation, ValueError):
        return redirect(url_for("inventory_page"))

    with db_connection() as db:
        db.execute("""
            UPDATE inventory
            SET quantity = GREATEST(0, quantity + %s),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND list_id = %s
        """, (
            change,
            inventory_id,
            user["list_id"]
        ))

    return redirect(url_for("inventory_page"))


@app.route("/vorrat/<int:inventory_id>/liste", methods=["POST"])
@login_required
def inventory_to_list(inventory_id):
    user = current_user()

    with db_connection() as db:
        item = db.execute("""
            SELECT name, category, barcode
            FROM inventory
            WHERE id = %s AND list_id = %s
        """, (
            inventory_id,
            user["list_id"]
        )).fetchone()

        if item:
            db.execute("""
                INSERT INTO items (
                    list_id, name, quantity, category, barcode, created_by
                )
                VALUES (%s, %s, '1', %s, %s, %s)
            """, (
                user["list_id"],
                item["name"],
                item["category"],
                item["barcode"],
                user["id"]
            ))

    if item:
        send_list_push(
            user["list_id"],
            "Vorrat nachbestellen",
            f"{user['username']} hat {item['name']} zur Liste hinzugefügt.",
            exclude_user_id=user["id"]
        )

    return redirect(url_for("inventory_page"))


@app.route("/vorrat/<int:inventory_id>/loeschen", methods=["POST"])
@login_required
def delete_inventory(inventory_id):
    user = current_user()

    with db_connection() as db:
        db.execute(
            "DELETE FROM inventory WHERE id = %s AND list_id = %s",
            (
                inventory_id,
                user["list_id"]
            )
        )

    return redirect(url_for("inventory_page"))


@app.route("/api/sprache-artikel", methods=["POST"])
@login_required
def add_spoken_items():
    user = current_user()
    payload = request.get_json(silent=True) or {}
    raw_items = payload.get("items")

    if not isinstance(raw_items, list):
        return jsonify({
            "message": "Ungültige Artikelliste."
        }), 400

    cleaned_items = []

    for value in raw_items[:20]:
        name = str(value).strip()

        if 1 <= len(name) <= 100:
            cleaned_items.append(name)

    if not cleaned_items:
        return jsonify({
            "message": "Keine gültigen Artikel erkannt."
        }), 400

    with db_connection() as db:
        for name in cleaned_items:
            db.execute("""
                INSERT INTO items (
                    list_id, name, quantity, category, created_by
                )
                VALUES (%s, %s, '1', 'Sonstiges', %s)
            """, (
                user["list_id"],
                name,
                user["id"]
            ))

    send_list_push(
        user["list_id"],
        "Neue Artikel",
        f"{user['username']} hat {len(cleaned_items)} Artikel hinzugefügt.",
        exclude_user_id=user["id"]
    )

    return jsonify({
        "success": True,
        "added": len(cleaned_items)
    })


@app.route("/geschaeft", methods=["POST"])
@login_required
def update_store():
    user = current_user()
    store = request.form.get("store", "Kein Geschäft").strip()

    allowed_stores = {
        "Kein Geschäft", "Hofer", "Spar", "Billa",
        "Lidl", "MPreis", "DM", "Müller"
    }

    if store not in allowed_stores:
        store = "Kein Geschäft"

    with db_connection() as db:
        db.execute(
            "UPDATE shopping_lists SET store = %s WHERE id = %s",
            (store, user["list_id"])
        )

    return redirect(url_for("shopping_list"))


@app.route("/artikel/hinzufuegen", methods=["POST"])
@login_required
def add_item():
    user = current_user()
    name = request.form.get("name", "").strip()
    quantity = request.form.get("quantity", "1").strip() or "1"
    category = request.form.get("category", "Sonstiges").strip() or "Sonstiges"
    barcode = request.form.get("barcode", "").strip() or None
    image_url = request.form.get("image_url", "").strip() or None
    price_text = request.form.get("price", "").strip().replace(",", ".")

    price = None
    if price_text:
        try:
            price = Decimal(price_text)
            if price < 0 or price > Decimal("999999.99"):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            flash("Bitte einen gültigen Preis eingeben.", "error")
            return redirect(url_for("shopping_list"))

    if not name:
        flash("Bitte einen Artikel eingeben.", "error")
        return redirect(url_for("shopping_list"))

    allowed_categories = {
        "Obst & Gemüse",
        "Milchprodukte",
        "Brot & Backwaren",
        "Fleisch & Fisch",
        "Getränke",
        "Tiefkühl",
        "Haushalt",
        "Drogerie",
        "Sonstiges",
    }

    if category not in allowed_categories:
        category = "Sonstiges"

    if (
        len(name) > 100
        or len(quantity) > 20
        or (barcode and len(barcode) > 40)
        or (image_url and len(image_url) > 1000)
    ):
        flash("Artikel, Menge oder Barcode ist zu lang.", "error")
        return redirect(url_for("shopping_list"))

    with db_connection() as db:
        db.execute("""
            INSERT INTO items (
                list_id, name, quantity, category, barcode,
                price, image_url, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user["list_id"],
            name,
            quantity,
            category,
            barcode,
            price,
            image_url,
            user["id"]
        ))

    send_list_push(
        user["list_id"],
        "Neuer Artikel",
        f"{user['username']} hat {name} hinzugefügt.",
        exclude_user_id=user["id"]
    )

    return redirect(url_for("shopping_list"))


@app.route("/artikel/<int:item_id>/status", methods=["POST"])
@login_required
def toggle_item(item_id):
    user = current_user()

    with db_connection() as db:
        db.execute("""
            UPDATE items
            SET completed = NOT completed
            WHERE id = %s AND list_id = %s
        """, (item_id, user["list_id"]))

    return redirect(url_for("shopping_list"))


@app.route("/artikel/<int:item_id>/loeschen", methods=["POST"])
@login_required
def delete_item(item_id):
    user = current_user()

    with db_connection() as db:
        db.execute(
            "DELETE FROM items WHERE id = %s AND list_id = %s",
            (item_id, user["list_id"])
        )

    return redirect(url_for("shopping_list"))


@app.route("/artikel/<int:item_id>/favorit", methods=["POST"])
@login_required
def toggle_favorite(item_id):
    user = current_user()

    with db_connection() as db:
        item = db.execute("""
            SELECT name, quantity, category, barcode, price, image_url
            FROM items
            WHERE id = %s AND list_id = %s
        """, (item_id, user["list_id"])).fetchone()

        if item:
            existing = db.execute("""
                SELECT id FROM favorites
                WHERE list_id = %s AND LOWER(name) = LOWER(%s)
            """, (user["list_id"], item["name"])).fetchone()

            if existing:
                db.execute(
                    "DELETE FROM favorites WHERE id = %s AND list_id = %s",
                    (existing["id"], user["list_id"])
                )
            else:
                db.execute("""
                    INSERT INTO favorites (
                        list_id, name, quantity, category, barcode,
                        price, image_url
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    user["list_id"], item["name"], item["quantity"],
                    item["category"], item["barcode"],
                    item["price"], item["image_url"]
                ))

    return redirect(url_for("shopping_list"))


@app.route("/favorit/<int:favorite_id>/hinzufuegen", methods=["POST"])
@login_required
def add_favorite_to_list(favorite_id):
    user = current_user()

    with db_connection() as db:
        favorite = db.execute("""
            SELECT name, quantity, category, barcode, price, image_url
            FROM favorites
            WHERE id = %s AND list_id = %s
        """, (favorite_id, user["list_id"])).fetchone()

        if favorite:
            db.execute("""
                INSERT INTO items (
                    list_id, name, quantity, category, barcode,
                    price, image_url, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user["list_id"], favorite["name"], favorite["quantity"],
                favorite["category"], favorite["barcode"],
                favorite["price"], favorite["image_url"], user["id"]
            ))

    return redirect(url_for("shopping_list"))


@app.route("/favorit/<int:favorite_id>/loeschen", methods=["POST"])
@login_required
def delete_favorite(favorite_id):
    user = current_user()

    with db_connection() as db:
        db.execute(
            "DELETE FROM favorites WHERE id = %s AND list_id = %s",
            (favorite_id, user["list_id"])
        )

    return redirect(url_for("shopping_list"))


@app.route("/einkauf-abschliessen", methods=["POST"])
@login_required
def finish_purchase():
    user = current_user()

    with db_connection() as db:
        completed_items = db.execute("""
            SELECT name, quantity, category, barcode, price, image_url
            FROM items
            WHERE list_id = %s AND completed = TRUE
        """, (user["list_id"],)).fetchall()

        for item in completed_items:
            db.execute("""
                INSERT INTO purchase_history (
                    list_id, name, quantity, category, barcode,
                    price, image_url, purchased_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user["list_id"], item["name"], item["quantity"],
                item["category"], item["barcode"],
                item["price"], item["image_url"], user["id"]
            ))

        db.execute(
            "DELETE FROM items WHERE list_id = %s AND completed = TRUE",
            (user["list_id"],)
        )

    if completed_items:
        flash(f"{len(completed_items)} Artikel wurden in den Verlauf verschoben.", "success")
    else:
        flash("Es sind keine Artikel abgehakt.", "error")

    return redirect(url_for("shopping_list"))


@app.route("/verlauf/<int:history_id>/hinzufuegen", methods=["POST"])
@login_required
def add_history_to_list(history_id):
    user = current_user()

    with db_connection() as db:
        entry = db.execute("""
            SELECT name, quantity, category, barcode, price, image_url
            FROM purchase_history
            WHERE id = %s AND list_id = %s
        """, (history_id, user["list_id"])).fetchone()

        if entry:
            db.execute("""
                INSERT INTO items (
                    list_id, name, quantity, category, barcode,
                    price, image_url, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user["list_id"], entry["name"], entry["quantity"],
                entry["category"], entry["barcode"],
                entry["price"], entry["image_url"], user["id"]
            ))

    return redirect(url_for("shopping_list"))


def category_from_open_food_facts(product):
    """Ordnet Open-Food-Facts-Kategorien unseren App-Kategorien zu."""
    raw_values = []

    for key in ("categories_tags", "categories_hierarchy"):
        value = product.get(key)
        if isinstance(value, list):
            raw_values.extend(str(item).lower() for item in value)

    categories_text = product.get("categories")
    if categories_text:
        raw_values.append(str(categories_text).lower())

    text = " ".join(raw_values)

    category_rules = [
        (
            "Getränke",
            (
                "beverage", "drink", "water", "juice", "soda",
                "soft-drink", "coffee", "tea", "beer"
            ),
        ),
        (
            "Milchprodukte",
            (
                "dairy", "milk", "yogurt", "yoghurt", "cheese",
                "butter", "cream"
            ),
        ),
        (
            "Brot & Backwaren",
            (
                "bread", "bakery", "pastr", "cake", "biscuit",
                "cookie", "roll"
            ),
        ),
        (
            "Fleisch & Fisch",
            (
                "meat", "fish", "seafood", "sausage", "ham",
                "poultry", "chicken"
            ),
        ),
        (
            "Obst & Gemüse",
            (
                "fruit", "vegetable", "salad", "legume",
                "potato", "tomato"
            ),
        ),
        (
            "Tiefkühl",
            ("frozen", "ice-cream", "sorbet"),
        ),
    ]

    for app_category, keywords in category_rules:
        if any(keyword in text for keyword in keywords):
            return app_category

    return "Sonstiges"


@app.route("/api/produkt/<barcode>")
@login_required
def product_lookup(barcode):
    cleaned_barcode = "".join(character for character in barcode if character.isdigit())

    if not 8 <= len(cleaned_barcode) <= 14:
        return jsonify({
            "found": False,
            "message": "Ungültiger Barcode."
        }), 400

    endpoint = (
        "https://world.openfoodfacts.org/api/v2/product/"
        + cleaned_barcode
    )

    params = {
        "fields": (
            "code,product_name,product_name_de,generic_name,"
            "brands,categories,categories_tags,categories_hierarchy,"
            "image_front_small_url,image_front_url,image_url"
        )
    }

    headers = {
        "User-Agent": (
            "EinkaufPlus/3.2 "
            "(private shopping-list app; Open Food Facts lookup)"
        ),
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            endpoint,
            params=params,
            headers=headers,
            timeout=8
        )

        # Open Food Facts liefert bei unbekannten Barcodes teilweise HTTP 404.
        # Das bedeutet nicht, dass die Datenbank ausgefallen ist.
        if response.status_code == 404:
            return jsonify({
                "found": False,
                "message": "Produkt wurde in Open Food Facts nicht gefunden."
            })

        response.raise_for_status()
        payload = response.json()

    except (requests.RequestException, ValueError) as error:
        app.logger.warning(
            "Open Food Facts lookup failed for %s: %s",
            cleaned_barcode,
            error
        )
        return jsonify({
            "found": False,
            "message": "Produktdatenbank derzeit nicht erreichbar."
        }), 503

    if payload.get("status") != 1:
        return jsonify({"found": False})

    product = payload.get("product") or {}

    name = (
        product.get("product_name_de")
        or product.get("product_name")
        or product.get("generic_name")
        or ""
    ).strip()

    if not name:
        return jsonify({"found": False})

    brand = str(product.get("brands") or "").split(",")[0].strip()
    image_url = (
        product.get("image_front_small_url")
        or product.get("image_front_url")
        or product.get("image_url")
        or ""
    )

    return jsonify({
        "found": True,
        "barcode": cleaned_barcode,
        "name": name[:100],
        "brand": brand[:80],
        "category": category_from_open_food_facts(product),
        "image_url": image_url[:1000],
    })


@app.route("/manifest.json")
def manifest():
    response = jsonify({
        "name": "Einkauf+ – Gemeinsame Einkaufsliste",
        "short_name": "Einkauf+",
        "description": "Gemeinsame Einkaufsliste mit Kategorien und Barcode-Funktion",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#eef2f5",
        "theme_color": "#198754",
        "icons": [{
            "src": "/icon.svg",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "any maskable"
        }]
    })
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/icon.svg")
def icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
    <rect width="512" height="512" rx="110" fill="#198754"/>
    <path d="M105 145h50l35 180h200l40-120H175"
          fill="none" stroke="white" stroke-width="24"
          stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="235" cy="375" r="30" fill="white"/>
    <circle cx="365" cy="375" r="30" fill="white"/>
    </svg>"""
    return Response(svg, mimetype="image/svg+xml")


@app.route("/offline")
def offline_page():
    return """
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#198754">
<title>Einkauf+ – Offline</title>
<style>
body {
  margin: 0;
  padding: 24px;
  font-family: Arial, sans-serif;
  background: #eef2f5;
  color: #202124;
  text-align: center;
}
.card {
  max-width: 520px;
  margin: 80px auto;
  background: white;
  padding: 28px;
  border-radius: 18px;
  box-shadow: 0 5px 22px rgba(0,0,0,.1);
}
</style>
</head>
<body>
<div class="card">
<h1>🛒 Einkauf+</h1>
<p>Du bist gerade offline.</p>
<p>Die zuletzt geöffnete Liste kann eventuell angezeigt werden. Änderungen benötigen Internet.</p>
</div>
</body>
</html>
"""


@app.route("/service-worker.js")
def service_worker():
    script = """
const CACHE = "einkauf-plus-v8";
const CORE = ["/manifest.json", "/icon.svg", "/offline"];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(CORE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(event.request, copy));
          return response;
        })
        .catch(async () => {
          return (await caches.match(event.request)) || caches.match("/offline");
        })
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cached => {
      return cached || fetch(event.request);
    })
  );
});

self.addEventListener("push", event => {
  let data = {};

  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = { title: "Einkauf+", body: event.data ? event.data.text() : "" };
  }

  event.waitUntil(
    self.registration.showNotification(data.title || "Einkauf+", {
      body: data.body || "Die Einkaufsliste wurde aktualisiert.",
      icon: data.icon || "/icon.svg",
      badge: data.badge || "/icon.svg",
      data: { url: data.url || "/liste" }
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/liste";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true })
      .then(windowClients => {
        for (const client of windowClients) {
          if ("focus" in client) {
            client.navigate(targetUrl);
            return client.focus();
          }
        }

        return clients.openWindow(targetUrl);
      })
  );
});
"""
    response = Response(script, mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


create_tables()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
