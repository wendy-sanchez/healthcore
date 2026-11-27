"""Test: Endpoints del agente requieren autenticación JWT (A01)."""

import httpx
import pytest

AGENT_QUERY_URL = "http://localhost:8000/agent/query"
AGENT_TRACES_URL = "http://localhost:8000/agent/traces"


@pytest.mark.skip(reason="Requiere servidor en ejecución en localhost:8000")
def test_agent_query_requires_auth():
    """Verifica que POST /agent/query rechaza peticiones sin token (401)."""
    response = httpx.post(
        AGENT_QUERY_URL,
        json={"question": "test"},
    )
    assert response.status_code == 401


@pytest.mark.skip(reason="Requiere servidor en ejecución en localhost:8000")
def test_agent_traces_requires_auth():
    """Verifica que GET /agent/traces rechaza peticiones sin token (401)."""
    response = httpx.get(AGENT_TRACES_URL)
    assert response.status_code == 401


@pytest.mark.skip(reason="Requiere servidor en ejecución en localhost:8000")
def test_agent_trace_by_id_requires_auth():
    """Verifica que GET /agent/trace/{run_id} rechaza peticiones sin token (401)."""
    response = httpx.get(f"{AGENT_QUERY_URL}/trace/nonexistent-run-id")
    assert response.status_code == 401


@pytest.mark.skip(reason="Requiere servidor en ejecución en localhost:8000")
def test_agent_query_with_invalid_token_returns_401():
    """Verifica que un token inválido también es rechazado."""
    response = httpx.post(
        AGENT_QUERY_URL,
        json={"question": "test"},
        headers={"Authorization": "Bearer invalid-token-that-is-not-a-jwt"},
    )
    assert response.status_code == 401