"""Control de tasa para endpoints sensibles de HealthCore API.

Previene abusos limitando el número de solicitudes por clave en una
ventana de tiempo configurable.

Tablas de TinyDB usadas:
  - ``password_reset_rate_limits`` — Restablecimiento de contraseña (por email)
  - ``login_rate_limits`` — Inicio de sesión (por IP + email)

También expone una función de rate limiting en memoria para endpoints
que no requieren persistencia (ej. agente, WebSocket).
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable

import database
from inventory_telemetry import signal

logger = logging.getLogger(__name__)

# ── Configuración de límites ─────────────────────────────────────────

# Password reset: 5 solicitudes por email cada 60 minutos
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
RATE_LIMIT_WINDOW_MINUTES = int(os.getenv("RATE_LIMIT_WINDOW_MINUTES", "60"))

# Login: 10 intentos por IP cada 60 segundos
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "10"))
LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60"))

# Agent: 30 queries por usuario cada 60 segundos
AGENT_MAX_REQUESTS = int(os.getenv("AGENT_RATE_LIMIT_MAX_REQUESTS", "30"))
AGENT_WINDOW_SECONDS = int(os.getenv("AGENT_RATE_LIMIT_WINDOW_SECONDS", "60"))


def _now() -> datetime:
    """Devuelve el timestamp UTC actual."""
    return datetime.now(timezone.utc)


# ── Rate limiter en memoria ──────────────────────────────────────────
# Para endpoints que no requieren persistencia (agente, etc.)

class _InMemoryRateLimiter:
    """Rate limiter en memoria con ventana deslizante.

    Almacena timestamps por clave en un dict. Las entradas antiguas
    se limpian perezosamente al momento de la verificación.
    """

    def __init__(self, default_max_requests: int, default_window_seconds: int):
        self._max_requests = default_max_requests
        self._window_seconds = default_window_seconds
        self._records: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, max_requests: int | None = None, window_seconds: int | None = None) -> bool:
        """Verifica si la clave ha superado el límite.

        Args:
            key: Identificador único (ej. user_id, IP).
            max_requests: Límite máximo de solicitudes (usa default si None).
            window_seconds: Ventana de tiempo en segundos (usa default si None).

        Returns:
            True si está dentro del límite (permitido), False si se superó.
        """
        max_r = max_requests or self._max_requests
        window = window_seconds or self._window_seconds
        now = time.monotonic()
        cutoff = now - window

        # Limpiar y contar
        self._records[key] = [ts for ts in self._records[key] if ts > cutoff]

        if len(self._records[key]) >= max_r:
            return False

        self._records[key].append(now)
        return True


# Singleton global — compartido entre módulos
_agent_rate_limiter = _InMemoryRateLimiter(
    default_max_requests=AGENT_MAX_REQUESTS,
    default_window_seconds=AGENT_WINDOW_SECONDS,
)

_login_ip_limiter = _InMemoryRateLimiter(
    default_max_requests=LOGIN_MAX_ATTEMPTS,
    default_window_seconds=LOGIN_WINDOW_SECONDS,
)


def check_agent_rate_limit(user_id: str | int | None = None, ip: str | None = None) -> bool:
    """Verifica rate limit para el endpoint del agente.

    Usa user_id si está disponible, fallback a IP.
    """
    key = f"agent:{user_id or ip or 'unknown'}"
    return _agent_rate_limiter.check(key)


def check_login_ip_rate_limit(ip: str) -> bool:
    """Verifica rate limit para login por dirección IP."""
    return _login_ip_limiter.check(f"login_ip:{ip}")


# ── Rate limiter persistente (TinyDB) ────────────────────────────────

def _get_table_or_log(table_name: str) -> database.Table | None:
    """Obtiene una tabla de TinyDB, logueando error y retornando None si falla.

    Esto evita que un fallo en TinyDB bloquee el endpoint (fail-open seguro).
    """
    try:
        return getattr(database, f"get_{table_name}_table")()
    except Exception:
        logger.exception("Error al acceder a la tabla %s de rate limit", table_name)
        return None


def check_rate_limit(email: str) -> bool:
    """Verifica si el email ha superado el límite de solicitudes en la ventana.

    Para el endpoint de restablecimiento de contraseña (por email).

    Devuelve ``True`` si la solicitud está dentro del límite (permitida),
    ``False`` si se ha superado el límite (denegada).
    """
    table = _get_table_or_log("rate_limits")
    if table is None:
        return True  # Fail-open: permitir si no podemos acceder a la tabla

    window_start = _now() - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)

    # Limpiar entradas antiguas (ventana expirada) para esta dirección.
    _clean_stale_entries(table, email, window_start)

    try:
        # Contar solicitudes activas (dentro de la ventana) para este email.
        active_count = table.count(
            lambda d: d.get("email") == email
            and datetime.fromisoformat(d["timestamp"]) >= window_start
        )

        if active_count >= RATE_LIMIT_MAX_REQUESTS:
            signal("auth_rate_limit_triggered", {"application": "healthcore_api", "endpoint_group": "password_reset",
                "limit_window_seconds": RATE_LIMIT_WINDOW_MINUTES * 60, "observed_count": active_count + 1, "limit_action": "blocked"})
            return False

        # Registrar la nueva solicitud.
        table.insert({"email": email, "timestamp": _now().isoformat()})
        return True
    except Exception:
        logger.exception("Error al contar/insertar rate limit para %s", email)
        return True


def _clean_stale_entries(table, email: str, window_start: datetime) -> None:
    """Elimina entradas antiguas para un email, ignorando fallos de TinyDB."""
    try:
        stale_ids: list[int] = []
        for doc in table:
            if doc.get("email") == email:
                ts = doc.get("timestamp")
                if ts is None or datetime.fromisoformat(ts) < window_start:
                    stale_ids.append(doc.doc_id)
        if stale_ids:
            table.remove(doc_ids=stale_ids)
    except Exception:
        logger.exception("Error al limpiar entradas antiguas de rate limit para %s", email)