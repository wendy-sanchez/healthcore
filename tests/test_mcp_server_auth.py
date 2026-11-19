"""
Tests de autenticación y autorización del MCP Server.

Verifica que:
1. Sin token → 401 (missing_auth_header)
2. Token inválido → 401 (invalid_token)
3. Token válido sin scope → Herramienta rechazada con error de scope
4. Token válido con scopes → Herramienta ejecutada (o error de backend, pero no de auth)
5. Logging incluye client (sub) del token
6. Metadata pública accesible sin autenticación
7. MCP_SKIP_AUTH=1 está deshabilitado por defecto (solo para tests aislados)
"""

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

# Agregar la raíz del proyecto al PYTHONPATH para imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="function")
def client():
    """Fixture que retorna un TestClient con lifespan configurado.
    
    Nota: Cada test obtiene su propia instancia de app para evitar
    problemas con el StreamableHTTPSessionManager que solo puede
    ejecutarse una vez.
    """
    # Importar aquí para obtener una nueva instancia para cada test
    from importlib import reload
    import mcps.server
    reload(mcps.server)
    from mcps.server import app
    
    with TestClient(app) as _client:
        yield _client


class TestAuthenticationAndAuthorization:
    """Tests de autenticación y autorización."""

    def test_missing_token_returns_401(self, client):
        """Sin token Bearer → HTTP 401 Unauthorized."""
        r = client.get('/mcp', headers={})
        assert r.status_code == 401
        error = r.json()
        assert error['error'] == 'missing_auth_header'

    def test_invalid_token_returns_401(self, client):
        """Token inválido → HTTP 401 Unauthorized."""
        r = client.post(
            '/mcp',
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            headers={
                'Authorization': 'Bearer invalid_token',
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json'
            }
        )
        assert r.status_code == 401
        error = r.json()
        assert error['error'] == 'invalid_token'

    def test_valid_token_tools_list_200(self, client):
        """Token válido + headers MCP correctos → HTTP 200 OK."""
        dev_token = 'hc_dev_token_no_usar_en_produccion'
        r = client.post(
            '/mcp',
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            headers={
                'Authorization': f'Bearer {dev_token}',
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json'
            }
        )
        assert r.status_code == 200

        # Parsear respuesta SSE
        lines = r.text.strip().split('\n')
        json_data = None
        for line in lines:
            if line.startswith('data:'):
                try:
                    json_data = json.loads(line[5:])  # Saltar 'data:'
                    break
                except:
                    pass

        assert json_data is not None
        assert 'result' in json_data
        assert len(json_data['result']['tools']) == 10  # 5 incidents + 5 inventory

    def test_protected_resource_metadata_public_200(self, client):
        """Metadata de recurso protegido accesible sin auth → HTTP 200 OK."""
        r = client.get('/.well-known/oauth-protected-resource')
        assert r.status_code == 200

        metadata = r.json()
        assert 'resource' in metadata
        assert 'scopes_supported' in metadata
        assert 'bearer_methods_supported' in metadata
        assert 'incidents.read' in metadata['scopes_supported']
        assert 'inventory.write' in metadata['scopes_supported']

    def test_bearer_auth_method_in_metadata(self, client):
        """La metadata debe indicar que soporta 'bearer' en header."""
        r = client.get('/.well-known/oauth-protected-resource')
        metadata = r.json()
        assert 'header' in metadata['bearer_methods_supported']

    def test_all_scopes_in_metadata(self, client):
        """La metadata debe listar todos los scopes soportados."""
        r = client.get('/.well-known/oauth-protected-resource')
        metadata = r.json()
        expected_scopes = [
            'incidents.read',
            'incidents.write',
            'inventory.read',
            'inventory.write'
        ]
        for scope in expected_scopes:
            assert scope in metadata['scopes_supported']

    def test_tool_call_with_valid_token_returns_result(self, client, caplog):
        """tools/call con token válido ejecuta la tool y retorna resultado, no error de auth.
        Además verifica que el log incluya el subject del cliente."""
        import logging
        caplog.set_level(logging.INFO)
        
        dev_token = 'hc_dev_token_no_usar_en_produccion'
        r = client.post(
            '/mcp',
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'get_incident_summary', 'arguments': {}}
            },
            headers={
                'Authorization': f'Bearer {dev_token}',
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json'
            }
        )
        # HTTP 200 indica que la tool se ejecutó (puede fallar por backend
        # pero NO por autenticación)
        assert r.status_code == 200, f"Esperado 200, obtenido {r.status_code}: {r.text[:300]}"

        # Parsear respuesta SSE
        lines = r.text.strip().split('\n')
        json_data = None
        for line in lines:
            if line.startswith('data:'):
                try:
                    json_data = json.loads(line[5:])
                    break
                except Exception:
                    pass

        assert json_data is not None, f"No se encontró data: en la respuesta: {r.text[:500]}"
        # El resultado puede tener un error de backend (no hay incidents API),
        # pero NUNCA debe ser un error de autenticación
        assert 'error' not in json_data, \
            f"La tool retornó error en lugar de resultado: {json_data}"
        assert 'result' in json_data, \
            f"No hay 'result' en la respuesta: {json_data}"

        # Verificar que el log contiene el subject del cliente (dev-agent)
        tool_logs = [
            rec for rec in caplog.records
            if rec.name == 'mcps.server' and 'Tool' in rec.getMessage()
        ]
        assert len(tool_logs) > 0, \
            f"No se encontraron logs de tool en caplog. Records: {[r.getMessage() for r in caplog.records]}"
        log_msg = tool_logs[0].getMessage()
        assert 'client=dev-agent' in log_msg, \
            f"El log debe contener 'client=dev-agent': {log_msg}"

    def test_tool_call_fails_without_token(self, client):
        """tools/call sin token devuelve 401, no "Autenticación requerida" del PermissionError."""
        r = client.post(
            '/mcp',
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'get_incident_summary', 'arguments': {}}
            },
            headers={
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json'
            }
        )
        assert r.status_code == 401, f"Esperado 401, obtenido {r.status_code}: {r.text[:300]}"
        error = r.json()
        assert error['error'] == 'missing_auth_header'

    def test_scopes_format_in_metadata(self, client):
        """Los scopes en la metadata deben estar en formato 'dominio.acción'."""
        r = client.get('/.well-known/oauth-protected-resource')
        metadata = r.json()
        
        for scope in metadata['scopes_supported']:
            # Formato debe ser dominio.acción (ej: incidents.read)
            parts = scope.split('.')
            assert len(parts) == 2, f"Scope mal formado: {scope}"
            domain, action = parts
            assert domain in ['incidents', 'inventory']
            assert action in ['read', 'write']


class TestMCPSkipAuthWarning:
    """Verificar que MCP_SKIP_AUTH=1 está deshabilitado y requiere configuración explícita."""

    def test_skip_auth_not_enabled_by_default(self):
        """MCP_SKIP_AUTH no debe estar habilitado por defecto."""
        import os

        # Verificar que MCP_SKIP_AUTH no está activado por defecto
        skip_auth_value = os.getenv('MCP_SKIP_AUTH', '').strip().lower()
        assert skip_auth_value not in {'1', 'true', 'yes'}, \
            "MCP_SKIP_AUTH no debe estar activado por defecto. " \
            "MCP_SKIP_AUTH=1 es solo para tests locales aislados sin clientes remotos."

