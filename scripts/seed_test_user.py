#!/usr/bin/env python3
"""Crea un usuario de prueba para el chat WebSocket del Compliance Assistant.

Uso:
    uv run python scripts/seed_test_user.py

Crea (o recupera si ya existe) un usuario con:
    email:    test@healthcore.local
    password: test1234
    role:     admin

Esto permite probar el WebSocket generando un token JWT vía:
    curl -X POST http://localhost:8000/auth/login \\
        -H "Content-Type: application/x-www-form-urlencoded" \\
        -d "username=test@healthcore.local&password=test1234"
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Asegurar que el proyecto está en sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import user_service
from models import Role
from services.api.security import hash_password

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_test_user")

TEST_EMAIL = "test@healthcore.local"
TEST_PASSWORD = "test1234"
TEST_NAME = "Test User"


def main() -> None:
    """Crea o recupera el usuario de prueba."""

    existing = user_service.get_user_by_email(TEST_EMAIL)
    if existing:
        logger.info(
            "Usuario de prueba ya existe: id=%d email=%s role=%s",
            existing.id,
            existing.email,
            existing.role,
        )
        print(f"\n✅ Usuario existente: id={existing.id}, email={existing.email}")
    else:
        user = user_service.create_user(
            email=TEST_EMAIL,
            hashed_password=hash_password(TEST_PASSWORD),
            role=Role.ADMIN,
        )
        user_service.create_profile(
            user_id=user.id,
            name=TEST_NAME,
        )
        logger.info(
            "Usuario de prueba creado: id=%d email=%s",
            user.id,
            user.email,
        )
        print(f"\n✅ Usuario creado: id={user.id}, email={user.email}")

    print(f"   Email:    {TEST_EMAIL}")
    print(f"   Password: {TEST_PASSWORD}")
    print(f"   Role:     admin")
    print()
    print("Para obtener un token JWT:")
    print(
        '  curl -X POST http://localhost:8000/auth/login '
        '-H "Content-Type: application/x-www-form-urlencoded" '
        '-d "username=test@healthcore.local&password=test1234"'
    )
    print()


if __name__ == "__main__":
    main()