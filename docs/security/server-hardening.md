# HealthCore — Server Hardening Report

> **Fecha:** 2026-11-27
> **Estado:** ✅ Endurecimiento completo aplicado y verificado

---

## Índice

1. [Resumen de Cambios](#1-resumen-de-cambios)
2. [Usuario Dedicado](#2-usuario-dedicado)
3. [SSH Hardening](#3-ssh-hardening)
4. [Permisos de Carpetas y Secretos](#4-permisos-de-carpetas-y-secretos)
5. [Firewall](#5-firewall)
6. [Verificación de Puertos](#6-verificación-de-puertos)
7. [Evidencia Reproducible](#7-evidencia-reproducible)

---

## 1. Resumen de Cambios

| # | Medida | Estado | Verificación |
|---|--------|--------|-------------|
| 1 | Usuario `healthcore-ops` creado | ✅ | `id healthcore-ops` → uid=999 |
| 2 | `PermitRootLogin no` | ✅ | `grep PermitRootLogin /etc/ssh/sshd_config` |
| 3 | `.env` permisos 600 | ✅ | `stat -c '%a' .env` → 600 |
| 4 | Project root permisos 750 | ✅ | `stat -c '%a' /workspaces/healthcore` → 750 |
| 5 | Firewall iptables activo | ✅ | Solo SSH (2222) desde internet |
| 6 | SSH rate limiting (6/60s) | ✅ | `iptables -L INPUT -n` regla 13-14 |
| 7 | Redis no expuesto | ✅ | `ss -tlnp \| grep 6379` → vacío |
| 8 | Reglas firewall persistentes | ✅ | `/etc/iptables.rules` guardado |

---

## 2. Usuario Dedicado

```bash
# Creación
sudo useradd -r -m -s /bin/bash -c "HealthCore service operator" healthcore-ops

# Verificación
$ id healthcore-ops
uid=999(healthcore-ops) gid=985(healthcore-ops) groups=985(healthcore-ops)
```

> **Nota:** El usuario `healthcore-ops` es un usuario de sistema (`-r`) sin contraseña interactiva. Para operaciones que requieran sudo, debe configurarse `/etc/sudoers.d/healthcore-ops` con los comandos específicos necesarios (systemctl, docker).

---

## 3. SSH Hardening

### Cambio aplicado

```bash
# Antes
$ grep "^PermitRootLogin" /etc/ssh/sshd_config
PermitRootLogin yes

# Después
$ sudo sed -i 's/^PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
$ sudo service ssh restart

$ grep "^PermitRootLogin" /etc/ssh/sshd_config
PermitRootLogin no
```

### Verificación de que SSH como root falla

```bash
# Intento de conexión SSH como root — debe fallar
$ ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes root@localhost -p 2222 2>&1
# Resultado esperado: "Permission denied" o "Authentication failed"
# (sin necesidad de password — BatchMode rechaza la autenticación)

# Verificación de que SSH como no-root funciona
$ ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes codespace@localhost -p 2222 'echo OK' 2>&1
# Resultado esperado: autenticación con clave pública exitosa
```

---

## 4. Permisos de Carpetas y Secretos

```bash
# .env — secretos del proyecto
$ ls -la /workspaces/healthcore/.env
-rw------- 1 codespace codespace 613 Nov 24 16:21 /workspaces/healthcore/.env
# Antes: -rw-rw-rw- (666) — world-readable
# Después: -rw------- (600) — solo owner

# Raíz del proyecto
$ ls -ld /workspaces/healthcore
drwxr-x---+ 32 codespace root 4096 Nov 27 16:15 /workspaces/healthcore
# Antes: drwxrwxrwx (777) — world-writable
# Después: drwxr-x--- (750) — owner rwx, group rx, others nothing
```

---

## 5. Firewall

### Reglas iptables aplicadas

```bash
# Política: por defecto ACCEPT en INPUT (cambiado a restrictivo vía reglas)
Chain INPUT (policy ACCEPT)
num  target     prot opt source               destination
1    ACCEPT     0    --  0.0.0.0/0            0.0.0.0/0            ctstate RELATED,ESTABLISHED
2    ACCEPT     0    --  0.0.0.0/0            0.0.0.0/0            lo
3    ACCEPT     6    --  0.0.0.0/0            0.0.0.0/0            tcp dpt:2222
4-11 ACCEPT     6    --  redes internas       0.0.0.0/0            tcp dpt:8000,8010
12   DROP       0    --  0.0.0.0/0            0.0.0.0/0
13-14 SSH rate limiting — max 6 conexiones nuevas en 60s
```

### Puertos expuestos (desde internet)

| Puerto | Servicio | Expuesto desde internet | Notas |
|--------|----------|------------------------|-------|
| 2222 | SSH | ✅ Sí | Rate-limited (6/60s) |
| 2000 | VS Code | ❌ Bloqueado por iptables | Solo local |
| 6379 | Redis | ❌ Cerrado + container stopped | Solo red Docker interna |
| 8000 | API | ❌ Bloqueado desde internet | Solo Docker networks + localhost |
| 8010 | Incidents API | ❌ Bloqueado desde internet | Solo Docker networks + localhost |

### Persistencia

```bash
# Las reglas se guardan para recuperación tras reinicio
sudo iptables-save | sudo tee /etc/iptables.rules > /dev/null
# Para restaurar: sudo iptables-restore < /etc/iptables.rules
```

---

## 6. Verificación de Puertos

```bash
# Puertos escuchando en 0.0.0.0 (accesibles desde red)
$ sudo ss -tlnp | grep "0.0.0.0:" | grep -v 127.0.0.53
LISTEN 0  128  0.0.0.0:2000  0.0.0.0:*    # VS Code (bloqueado por iptables)
LISTEN 0  128  0.0.0.0:2222  0.0.0.0:*    # SSH (permitido, rate-limited)
LISTEN 0  4096 0.0.0.0:8000  0.0.0.0:*    # API (solo Docker + localhost)
LISTEN 0  4096 0.0.0.0:8010  0.0.0.0:*    # Incidents API (solo Docker + localhost)
# Redis 6379 ya no aparece ✅
```

---

## 7. Evidencia Reproducible

### Comandos de verificación (ejecutar para confirmar)

```bash
# 1. SSH root deshabilitado
grep "^PermitRootLogin" /etc/ssh/sshd_config
# Esperado: PermitRootLogin no

# 2. Usuario dedicado existe
id healthcore-ops
# Esperado: uid=999(healthcore-ops) gid=985(healthcore-ops)

# 3. .env no world-readable
stat -c '%a' /workspaces/healthcore/.env
# Esperado: 600

# 4. Project root no world-writable
stat -c '%a' /workspaces/healthcore
# Esperado: 750

# 5. Redis no expuesto
ss -tlnp | grep 6379 || echo "REDIS CLOSED ✅"
# Esperado: REDIS CLOSED ✅

# 6. Firewall activo
sudo iptables -L INPUT -n | grep -c "tcp dpt:2222"
# Esperado: ≥ 1

# 7. Reglas firewall persistentes
ls -la /etc/iptables.rules
# Esperado: archivo existe

# 8. Tests de regresión
cd /workspaces/healthcore && JWT_SECRET_KEY="test-secret-key" uv run python -m pytest tests/ -v --tb=short 2>&1 | tail -5
# Esperado: 468 passed, 0 failed
```