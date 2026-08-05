# Einkauf+ V10.0

Neu:
- Dashboard direkt nach dem Login
- offene Artikel
- Favoriten-Anzahl
- Ausgaben für 7 Tage und aktuellen Monat
- letzter Einkauf
- zuletzt hinzugefügte Artikel
- Schnellzugriffe auf Liste, Barcode und Spracheingabe

Alle bisherigen Funktionen aus V8 bleiben erhalten.

Render:
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- `DATABASE_URL` und die VAPID-Variablen bleiben unverändert.
