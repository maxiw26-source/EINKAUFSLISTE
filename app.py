from flask import Flask, request, redirect, url_for, session, render_template_string, flash, Response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import os
import secrets
import string
import hashlib
import psycopg
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
                   shopping_lists.invite_code
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
            grid-template-columns: 1fr 105px 150px auto;
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

        .install-note {
            background: #e7f5ec;
            border: 1px solid #b7dfc5;
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
                   items.category, items.barcode,
                   users.username AS creator
            FROM items
            LEFT JOIN users ON users.id = items.created_by
            WHERE items.list_id = %s
            ORDER BY items.completed ASC, items.category ASC, items.id DESC
        """, (user["list_id"],)).fetchall()

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

        <div class="scanner-row">
            <input id="barcode" form="add-form" name="barcode"
                   inputmode="numeric" placeholder="Barcode optional" maxlength="40">
            <button class="blue" type="button" onclick="openScanner()">
                📷 Barcode scannen
            </button>
        </div>

        <form id="add-form" class="form-row" method="POST" action="{{ url_for('add_item') }}">
            <div>
                <label>Artikel</label>
                <input id="name" name="name" placeholder="Zum Beispiel: Milch" required>
            </div>

            <div>
                <label>Menge</label>
                <input name="quantity" value="1" maxlength="20">
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
                        <div class="item-name {% if item.completed %}completed{% endif %}">
                            {{ item.quantity }} × {{ item.name }}
                        </div>

                        <span class="category-badge">{{ item.category }}</span>

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

                    <form method="POST" action="{{ url_for('delete_item', item_id=item.id) }}">
                        <button class="red" type="submit">Löschen</button>
                    </form>
                </div>
            {% endfor %}
        {% else %}
            <p class="small">Die Liste ist noch leer.</p>
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
        let scannerControls = null;
        let codeReader = null;
        let scanFinished = false;

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

                            window.setTimeout(function () {
                                closeScanner();
                                document.getElementById("name").focus();
                            }, 700);
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
    return page("Einkaufsliste", content, user=user, items=items)


@app.route("/artikel/hinzufuegen", methods=["POST"])
@login_required
def add_item():
    user = current_user()
    name = request.form.get("name", "").strip()
    quantity = request.form.get("quantity", "1").strip() or "1"
    category = request.form.get("category", "Sonstiges").strip() or "Sonstiges"
    barcode = request.form.get("barcode", "").strip() or None

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

    if len(name) > 100 or len(quantity) > 20 or (barcode and len(barcode) > 40):
        flash("Artikel, Menge oder Barcode ist zu lang.", "error")
        return redirect(url_for("shopping_list"))

    with db_connection() as db:
        db.execute("""
            INSERT INTO items (
                list_id, name, quantity, category, barcode, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            user["list_id"],
            name,
            quantity,
            category,
            barcode,
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
