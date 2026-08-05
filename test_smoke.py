import os

import pytest

# Für lokale Tests muss eine erreichbare Test-Datenbank gesetzt sein.
if not os.environ.get("DATABASE_URL"):
    pytest.skip(
        "DATABASE_URL ist für den Integrationstest erforderlich.",
        allow_module_level=True,
    )

from einkaufplus import app, create_tables


@pytest.fixture(scope="module")
def client():
    create_tables()
    app.config.update(TESTING=True)

    with app.test_client() as test_client:
        yield test_client


def test_index_redirects(client):
    response = client.get("/")
    assert response.status_code in {200, 302}


def test_login_page(client):
    response = client.get("/anmelden")
    assert response.status_code == 200


def test_registration_page(client):
    response = client.get("/registrieren")
    assert response.status_code == 200


def test_manifest(client):
    response = client.get("/manifest.json")
    assert response.status_code == 200
    assert response.is_json


def test_service_worker(client):
    response = client.get("/service-worker.js")
    assert response.status_code == 200
    assert "javascript" in response.content_type
