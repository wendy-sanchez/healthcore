# ── HealthCore — Backend Services Dockerfile ─────────────────────────
# Imagen única para ambos servicios FastAPI (API principal e Incidents API).
# El servicio concreto se selecciona mediante el build-arg SERVICE_NAME.
# Ambos arrancan con Uvicorn --reload para recarga en caliente.

FROM python:3.12-slim

# ── Build-time args ────────────────────────────────────────────────────
ARG WORKSPACE=/workspace
WORKDIR ${WORKSPACE}

# ── Instalar uv (gestor de paquetes ultra-rápido) ─────────────────────
RUN pip install --no-cache-dir uv==0.6.14

# ── Copiar archivos de dependencias ──────────────────────────────────
COPY pyproject.toml ./pyproject.toml
COPY services/api/requirements.txt services/api/pyproject.toml ./services/api/
COPY services/incidents-api/requirements.txt ./services/incidents-api/

# ── Instalar dependencias ─────────────────────────────────────────────
# Primero las del proyecto principal (pyproject.toml),
# luego los requirements específicos de cada servicio.
RUN uv pip install --system --no-cache-dir \
    -r services/api/requirements.txt \
    -r services/incidents-api/requirements.txt \
    && uv pip install --system --no-cache-dir -e . \
    || true

# ── Copiar código fuente de ambos servicios ───────────────────────────
# (En desarrollo los bind mounts de docker-compose sobreescriben estos archivos)
COPY services/api ./services/api
COPY services/incidents-api ./services/incidents-api
COPY docs/telemetry ./docs/telemetry

# ── Puerto por defecto (lo sobreescribe la variable de entorno PORT) ──
EXPOSE 8000 8010

# ── Entrypoint dinámico según la variable SERVICE_NAME ───────────────
# Se define en docker-compose.yml mediante environment o build arg.
# Valores: "api" → uvicorn main:app --reload --host 0.0.0.0 --port 8000
#          "incidents-api" → uvicorn main:app --reload --host 0.0.0.0 --port 8010
COPY services/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["entrypoint.sh"]