from flask import Flask, request, redirect, url_for, session, render_template_string, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import secrets
import string
import os

app = Flask(__name__)

# Beim ersten Start wird automatisch ein geheimer Schlüssel erzeugt.
SECRET_FILE = "secret_key.txt"
DATABASE = "einkaufsliste.db"

def load_secret_key():
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r", encoding="utf-8") as file:
            return file.read().strip()

    key = secrets.token_hex(32)
    with open(SECRET_FILE, "w", encoding="utf-8") as file:
        file.write(key)
    return key

app.secret_key = load_secret_key()


def db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_tables():
    with db_connection() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS shopping_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                invite_code TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                list_id INTEGER NOT NULL,
                FOREIGN KEY (list_id) REFERENCES shopping_lists(id)
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                completed INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (list_id) REFERENCES shopping_lists(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );
        """)


def generate_invite_code(length=8):
    alphabet = string.ascii_uppercase + string.digits

    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))

        with db_connection() as db:
            exists = db.execute(
                "SELECT id FROM shopping_lists WHERE invite_code = ?",
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
            WHERE users.id = ?
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
            grid-template-columns: 1fr 110px auto;
            gap: 9px;
            align-items: end;
        }

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
                    "SELECT id FROM users WHERE username = ?",
                    (username,)
                ).fetchone()

                if existing_user:
                    flash("Dieser Benutzername ist bereits vergeben.", "error")
                    return redirect(url_for("register"))

                if mode == "join":
                    shopping_list = db.execute(
                        "SELECT id FROM shopping_lists WHERE invite_code = ?",
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

                    cursor = db.execute(
                        "INSERT INTO shopping_lists (name, invite_code) VALUES (?, ?)",
                        (list_name, new_code)
                    )
                    list_id = cursor.lastrowid

                cursor = db.execute("""
                    INSERT INTO users (username, password_hash, list_id)
                    VALUES (?, ?, ?)
                """, (
                    username,
                    generate_password_hash(password),
                    list_id
                ))

                user_id = cursor.lastrowid

            session.clear()
            session["user_id"] = user_id
            flash("Registrierung erfolgreich.", "success")
            return redirect(url_for("shopping_list"))

        except sqlite3.Error:
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
                "SELECT id, password_hash FROM users WHERE username = ?",
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
                   users.username AS creator
            FROM items
            LEFT JOIN users ON users.id = items.created_by
            WHERE items.list_id = ?
            ORDER BY items.completed ASC, items.id DESC
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
        <h1>🛒 Einkaufsliste</h1>

        <form class="form-row" method="POST" action="{{ url_for('add_item') }}">
            <div>
                <label>Artikel</label>
                <input name="name" placeholder="Zum Beispiel: Milch" required>
            </div>

            <div>
                <label>Menge</label>
                <input name="quantity" value="1">
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

    <div class="card">
        <h2>👥 Person einladen</h2>
        <p>Die andere Person registriert sich und wählt „Mit Einladungscode beitreten“.</p>
        <div class="code">{{ user.invite_code }}</div>
    </div>
    """
    return page("Einkaufsliste", content, user=user, items=items)


@app.route("/artikel/hinzufuegen", methods=["POST"])
@login_required
def add_item():
    user = current_user()
    name = request.form.get("name", "").strip()
    quantity = request.form.get("quantity", "1").strip() or "1"

    if not name:
        flash("Bitte einen Artikel eingeben.", "error")
        return redirect(url_for("shopping_list"))

    if len(name) > 100 or len(quantity) > 20:
        flash("Artikel oder Menge ist zu lang.", "error")
        return redirect(url_for("shopping_list"))

    with db_connection() as db:
        db.execute("""
            INSERT INTO items (list_id, name, quantity, created_by)
            VALUES (?, ?, ?, ?)
        """, (user["list_id"], name, quantity, user["id"]))

    return redirect(url_for("shopping_list"))


@app.route("/artikel/<int:item_id>/status", methods=["POST"])
@login_required
def toggle_item(item_id):
    user = current_user()

    with db_connection() as db:
        db.execute("""
            UPDATE items
            SET completed = CASE completed WHEN 0 THEN 1 ELSE 0 END
            WHERE id = ? AND list_id = ?
        """, (item_id, user["list_id"]))

    return redirect(url_for("shopping_list"))


@app.route("/artikel/<int:item_id>/loeschen", methods=["POST"])
@login_required
def delete_item(item_id):
    user = current_user()

    with db_connection() as db:
        db.execute(
            "DELETE FROM items WHERE id = ? AND list_id = ?",
            (item_id, user["list_id"])
        )

    return redirect(url_for("shopping_list"))


if __name__ == "__main__":
    create_tables()

    print()
    print("Gemeinsame Einkaufsliste wurde gestartet.")
    print("Auf diesem Handy: http://127.0.0.1:5000")
    print("Im selben WLAN/Hotspot: Nutze die angezeigte 172.x.x.x- oder 192.168.x.x-Adresse.")
    print()

    app.run(host="0.0.0.0", port=5000, debug=False)
