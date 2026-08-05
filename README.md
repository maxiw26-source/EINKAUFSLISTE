# Einkauf+ V11

V11 ist die stabile, professionell organisierte Grundlage der App.

## Struktur

```text
app.py
einkaufplus/
  __init__.py
  application.py
tests/
  test_smoke.py
.github/workflows/
  checks.yml
requirements.txt
requirements-dev.txt
render.yaml
```

## Enthaltene Funktionen

Alle Funktionen aus V10.1 bleiben erhalten:

- Benutzerkonten und gemeinsame Listen
- Dashboard
- Barcode-Scanner und Produkterkennung
- Spracheingabe
- Push-Benachrichtigungen
- Favoriten und Einkaufsverlauf
- Preise, Budget und Statistiken
- Vorratsverwaltung
- PWA und Dunkelmodus

## Render

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
gunicorn app:app
```

Die bestehenden Environment Variables bleiben unverändert:

- `DATABASE_URL`
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `VAPID_SUBJECT`

## Entwicklung

Neue Funktionen künftig zuerst in einem Branch wie `develop` oder
`feature/receipt-scanner` entwickeln und anschließend nach `main` übernehmen.
