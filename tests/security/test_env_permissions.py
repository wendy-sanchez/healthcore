"""Test: .env y directorios sensibles tienen permisos restringidos (A02)."""

import os
import stat
import pytest

ENV_PATH = "/workspaces/healthcore/.env"
PROJECT_ROOT = "/workspaces/healthcore"


def test_env_file_not_world_readable():
    """Verifica que .env NO es world-readable (permisos ≤ 600)."""
    assert os.path.exists(ENV_PATH), f"{ENV_PATH} no existe"
    mode = os.stat(ENV_PATH).st_mode
    # World-readable bits (others) must be 0
    assert mode & stat.S_IROTH == 0, f".env es world-readable (octal: {oct(mode & 0o777)})"
    assert mode & stat.S_IWOTH == 0, f".env es world-writable (octal: {oct(mode & 0o777)})"


def test_env_file_owner_only():
    """Verifica que .env tiene permisos exactamente 600 (o más restrictivo)."""
    mode = os.stat(ENV_PATH).st_mode & 0o777
    # Must be 600 or stricter (e.g., 400, 0)
    assert mode <= 0o600, f".env tiene permisos {oct(mode)}, esperado ≤ 600"


def test_project_root_not_world_writable():
    """Verifica que la raíz del proyecto NO es world-writable."""
    mode = os.stat(PROJECT_ROOT).st_mode
    assert mode & stat.S_IWOTH == 0, f"Project root es world-writable (octal: {oct(mode & 0o777)})"


def test_project_root_max_perms_755():
    """Verifica que la raíz del proyecto tiene permisos ≤ 755."""
    mode = os.stat(PROJECT_ROOT).st_mode & 0o777
    assert mode <= 0o755, f"Project root tiene permisos {oct(mode)}, esperado ≤ 755"