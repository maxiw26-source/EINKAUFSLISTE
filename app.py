import os

from einkaufplus import app, create_tables

# Tabellen und neue Spalten automatisch anlegen.
create_tables()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
