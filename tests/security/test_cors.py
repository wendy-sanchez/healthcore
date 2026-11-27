"""Test: CORS está correctamente restringido (A05)."""

import httpx
import pytest

BASE_URL = "http://localhost:8000"


@pytest.mark.skip(reason="Requiere servidor en ejecución en localhost:8000")
def test_cors_rejects_random_origin():
    """Verifica que un origen no autorizado NO recibe access-control-allow-origin."""
    response = httpx.get(
        BASE_URL,
        headers={"Origin": "https://evil-site.com"},
    )
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.skip(reason="Requiere servidor en ejecución en localhost:8000")
def test_cors_allows_known_origin():
    """Verifica que un origen autorizado SÍ recibe access-control-allow-origin."""
    response = httpx.get(
        BASE_URL,
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.skip(reason="Requiere servidor en ejecución en localhost:8000")
def test_cors_allows_known_production_origin():
    """Verifica que el origen de producción está en la whitelist."""
    response = httpx.get(
        BASE_URL,
        headers={"Origin": "https://healthcore.app"},
    )
    assert response.headers.get("access-control-allow-origin") == "https://healthcore.app"