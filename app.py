from routes import app, create_tables

# Tabellen werden auch beim Start über Gunicorn angelegt bzw. erweitert.
create_tables()

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
