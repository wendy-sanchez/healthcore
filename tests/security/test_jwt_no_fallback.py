"""Test: JWT no tiene fallback hardcodeado (A02)."""

import os
import pytest

CHAT_WS_PATH = "/workspaces/healthcore/services/api/routes/chat_ws.py"


def test_jwt_secret_no_hardcoded_fallback():
    """Verifica que chat_ws.py NO contiene un secret hardcodeado como fallback."""
    assert os.path.exists(CHAT_WS_PATH), f"{CHAT_WS_PATH} no existe"

    with open(CHAT_WS_PATH, "r") as f:
        content = f.read()

    # Buscar cadenas que indiquen un fallback hardcodeado
    forbidden_patterns = [
        "dev-secret-key",
        "change-in-production",
        "secret-key",
        '"secret"' if "JWT_SECRET" in content else "__UNLIKELY__",
    ]

    for pattern in ["dev-secret-key", "change-in-production"]:
        assert pattern not in content, \
            f"Hardcoded JWT secret encontrado: '{pattern}' en {CHAT_WS_PATH}"


def test_jwt_secret_reads_from_env():
    """Verifica que _get_jwt_secret() usa os.getenv, no un string hardcodeado."""
    with open(CHAT_WS_PATH, "r") as f:
        content = f.read()

    assert "os.getenv" in content or "os.environ" in content, \
        "_get_jwt_secret() no lee de variable de entorno"
    assert "JWT_SECRET_KEY" in content, \
        "JWT_SECRET_KEY no referenciado en chat_ws.py"