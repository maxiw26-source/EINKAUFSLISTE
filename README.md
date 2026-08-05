# Einkauf+ V8

Neu:
- Push-Benachrichtigungen für gemeinsame Listen
- verbesserter Offline-Modus
- installierbare PWA-Grundlage
- alle Funktionen aus V7 bleiben erhalten

## Render

Build Command:

    pip install -r requirements.txt

Start Command:

    gunicorn app:app

`DATABASE_URL` muss weiterhin gesetzt sein.

## Push einrichten

1. `generate_vapid_keys.py` einmal in Pydroid ausführen.
2. Die drei Ausgaben als Render Environment Variables speichern:
   - `VAPID_PUBLIC_KEY`
   - `VAPID_PRIVATE_KEY`
   - `VAPID_SUBJECT`
3. Bei `VAPID_SUBJECT` eine eigene E-Mail-Adresse verwenden, z. B.
   `mailto:deinname@example.com`.
4. Danach Save, rebuild and deploy.
5. In der App auf „Benachrichtigungen aktivieren“ tippen.

Ohne diese Variablen funktioniert die App normal weiter; nur Push bleibt deaktiviert.

## iPhone

Web Push funktioniert bei installierten Web-Apps. Die Seite zuerst in Safari
über „Teilen → Zum Home-Bildschirm“ installieren und danach die
Benachrichtigungen in der installierten App aktivieren.
