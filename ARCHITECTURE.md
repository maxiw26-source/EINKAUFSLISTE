# Architektur

## app.py

Kleiner Einstiegspunkt für Render und Gunicorn.

## einkaufplus/application.py

Enthält die aktuell stabile Flask-Anwendung. Diese Datei bleibt zunächst
unverändert, damit der Umbau keine bestehenden Funktionen beschädigt.

## Nächste Modularisierungsschritte

Die Funktionen werden künftig schrittweise aus `application.py` ausgelagert:

1. `auth`
2. `shopping`
3. `inventory`
4. `notifications`
5. `statistics`
6. `receipts`

Jeder Schritt wird separat getestet, bevor er in `main` übernommen wird.
