from flask import Flask, request, redirect, url_for, session, render_template_string, flash, Response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import os
import secrets
import string
import hashlib
import psycopg
import requests
from decimal import Decimal, InvalidOperation
from psycopg.rows import dict_row

app = Flask(__name__)

# PostgreSQL-Verbindung aus Render Environment.
DATABASE_URL = os.environ.get("DATABASE_URL")

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
                   shopping_lists.store
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

        body {
            margin: 0;
            padding: 18px;
            font-family: Arial, sans-serif;
            background: #eef2f5;
            color: #202124;
        }

        .container {
            width: 100%;
            max-width: 680px;
            margin: 20px auto;
        }

        .card {
            background: white;
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
            color: #6c757d;
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
            border: 1px solid #e1e5e9;
            border-radius: 12px;
            padding: 12px;
            background: #fafbfc;
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
            color: #6c757d;
            font-size: 13px;
            margin-top: 4px;
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
            background: #f4f4f4;
            border: 1px solid #e5e5e5;
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
            background: white;
            border: 1px solid #d6d9dc;
            border-radius: 10px;
            box-shadow: 0 8px 24px rgba(0,0,0,.14);
            max-height: 230px;
            overflow-y: auto;
        }

        .suggestion-entry {
            width: 100%;
            border-radius: 0;
            background: white;
            color: #202124;
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
            color: #6c757d;
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

    <script>
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
        return redirect(url_for("shopping_list"))

    return redirect(url_for("login"))


@app.route("/registrieren", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("shopping_list"))

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
            return redirect(url_for("shopping_list"))

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
        return redirect(url_for("shopping_list"))

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
        return redirect(url_for("shopping_list"))

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
        <a class="button gray" href="{{ url_for('logout') }}">Abmelden</a>
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
                <div class="item">
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
        stores=[
            "Kein Geschäft", "Hofer", "Spar", "Billa",
            "Lidl", "MPreis", "DM", "Müller"
        ]
    )


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


@app.route("/service-worker.js")
def service_worker():
    script = """
const CACHE = "einkauf-plus-v2";
const ASSETS = ["/manifest.json", "/icon.svg"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
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
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
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
