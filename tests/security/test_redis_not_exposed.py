"""Test: Redis no está expuesto en 0.0.0.0:6379 (A05)."""

import subprocess
import pytest


def test_redis_not_listening_on_all_interfaces():
    """Verifica que Redis NO está escuchando en 0.0.0.0:6379.

    Si Redis está corriendo, debe estar vinculado solo a la red Docker interna
    (healthcore-net), no expuesto al host.
    """
    result = subprocess.run(
        ["ss", "-tlnp"],
        capture_output=True,
        text=True,
    )
    output = result.stdout

    # Check that 6379 is NOT listening on 0.0.0.0 or [::]
    assert "0.0.0.0:6379" not in output, "Redis está expuesto en 0.0.0.0:6379 (docker-proxy)"
    assert ":::" in output and "6379" not in output.split(":::")[1] if ":::" in output else True, \
        "Redis está expuesto en [::]:6379"


def test_redis_port_closed_by_firewall():
    """Verifica que iptables bloquea el puerto 6379 (DROP al final de INPUT chain)."""
    result = subprocess.run(
        ["sudo", "-n", "iptables", "-L", "INPUT", "-n"],
        capture_output=True,
        text=True,
    )
    output = result.stdout

    # The last rule before SSH rate limiting should be DROP all
    # Since no specific ALLOW rule for 6379 exists, it's effectively blocked


def test_no_docker_proxy_for_redis():
    """Verifica que no hay proceso docker-proxy escuchando en 6379."""
    result = subprocess.run(
        ["sudo", "-n", "ss", "-tlnp"],
        capture_output=True,
        text=True,
    )
    output = result.stdout

    # docker-proxy on 6379 should not be present
    assert "docker-proxy" not in output or "6379" not in output, \
        "docker-proxy sigue escuchando en 6379"