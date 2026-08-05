# Einkauf+ V6

Diese Version räumt das Projekt auf, ohne Funktionen zu entfernen.

## Dateien

- `app.py` – Startdatei für Render und lokale Tests
- `routes.py` – komplette Flask-App mit Login, Listen, Barcode, Favoriten,
  Verlauf, Preisen, Geschäften und Produktsuche
- `requirements.txt` – benötigte Python-Pakete
- `render.yaml` – Render-Konfiguration

## Render

Build Command:

    pip install -r requirements.txt

Start Command:

    gunicorn app:app

## Wichtig

Die Environment Variable `DATABASE_URL` muss im Render Web Service gesetzt bleiben.
Vorhandene PostgreSQL-Daten bleiben erhalten.
