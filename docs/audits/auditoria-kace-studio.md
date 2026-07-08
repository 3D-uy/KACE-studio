# Auditoría de Seguridad y Calidad — KACE Studio v0.1.0

**Fecha:** 2026-07-08  
**Archivos auditados:** `main.py` (706 líneas), `bootstrap.sh` (583 líneas), `requirements.txt`  
**Cobertura:** Completa sobre archivos disponibles en el repo público

---

## Resumen ejecutivo

KACE Studio es una aplicación desktop Python+PyWebView que provisiona Raspberry Pi para stacks Klipper. El código demuestra un nivel de madurez de seguridad notablemente alto para un v0.1.0: la mayoría de vectores obvios ya están mitigados y documentados con comentarios `# FIX`. La arquitectura es sólida. Los problemas que quedan son en su mayoría mejoras de robustez, no vulnerabilidades activas.

**Puntuación general: 7.5 / 10**

---

## Lo que está bien (fortalezas confirmadas)

### Seguridad en `main.py`

1. **Protección contra path traversal — doble capa correcta**
   - En el WSGI static file server: `os.path.abspath` + `startswith(self.web_dir)` → 403.
   - En la API SFTP: `posixpath.normpath(raw_path.replace('\x00', ''))` + verificación de prefijo `/`.
   - Ambos bien implementados.

2. **CORS eliminado correctamente (M2 FIX)**
   - El endpoint `/api/sftp/list` no emite cabecera `Access-Control-Allow-Origin`. El comentario explica correctamente que al ser localhost-only no se necesita ni se permite.

3. **SSL/TLS explícito con validación de certificados (L6 FIX)**
   - `ssl.create_default_context()` aplicado a todas las descargas remotas (imagen del OS + hash SHA-256). Protege contra MITM en el proceso de descarga.

4. **Verificación de integridad SHA-256**
   - La imagen `.xz` descargada se verifica contra el hash remoto antes de descomprimir.
   - La imagen `.img` descomprimida se verifica en caché contra su propio `.sha256` antes de flashear.
   - Si falla, borra el archivo temporal y aborta con mensaje claro.

5. **Sanitización de errores para evitar path disclosure (L2 FIX)**
   - `_sanitize_error()` aplica regex para reemplazar rutas Windows y Unix con `[Protected Path]` antes de enviarlas al frontend.

6. **Cabeceras de seguridad en respuestas estáticas (M5 FIX)**
   - `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` en todas las respuestas de archivos estáticos.

7. **Threading seguro**
   - `_ssh_lock` para serializar acceso a la sesión SSH.
   - `_ssh_gen` (generation counter) para detectar y descartar conexiones supersedidas.
   - `threading.Event` para cancelación thread-safe del flash.
   - Buffer de escritura con timer de 15ms para evitar flickering en xterm.js, con su propio `buffer_lock`.

8. **Prevención de MIME sniffing**
   - MIME types hardcodeados para `.js`, `.css`, `.html` previenen que el registro de Windows los sobreescriba (relevante en PyInstaller).

### Seguridad en `bootstrap.sh`

9. **Allowlist validation para parámetros de entrada**
   - `DASHBOARD` validado contra `^(mainsail|fluidd|both)$`.
   - `CROWSNEST` validado contra `^(true|false)$`.
   - `TIMEZONE` validado contra `^[A-Za-z0-9/_+-]+$` con mensaje explícito de "rejected to prevent command injection".
   - En todos los casos, valores inválidos se resetean a defaults seguros, no abortan.

10. **`DEFAULT_UI` re-sanitizado antes de usarse en nginx config**
    - Segunda validación `^(mainsail|fluidd)$` justo antes de escribir el bloque nginx, a pesar de que ya fue validado antes. Defensivo correcto.

11. **Validación de ZIP antes de descomprimir**
    - `file /tmp/mainsail.zip | grep -q 'Zip archive'` verifica que el archivo descargado es realmente un ZIP antes de pasarlo a `unzip`.
    - `index.html` verificado post-extracción.

12. **Wait-for-apt-locks con doble método**
    - Combina `pgrep` + `ps aux` + `python3 fcntl` para verificar locks de dpkg. Robusto y portable.

13. **`set -e` + trap ERR con número de línea**
    - Cualquier comando que falle aborta el script y reporta la línea exacta del fallo. Buen DX para debugging.

14. **Cleanup trap**
    - `trap cleanup EXIT` elimina `/tmp/mainsail.zip` y `/tmp/fluidd.zip` incluso en error.

15. **Lectura segura del boot config (`kace-bootstrap.txt`)**
    - `grep -E "^KEY=" | cut -d'=' -f2` es un patrón seguro para parsear key=value, evitando injection via `source` o `eval`.

---

## Problemas encontrados

### CRÍTICO — Ninguno
No se encontraron vulnerabilidades críticas activas en el código auditado.

---

### MODERADO — 3 hallazgos

#### MOD-1: `bash <(curl -sSL ...)` para el KACE Agent sin verificación de integridad

**Archivo:** `bootstrap.sh`, línea ~553  
**Código:**
```bash
if bash <(curl -sSL https://raw.githubusercontent.com/3D-uy/KACE/main/install.sh); then
```

El script instala el agente KACE desde un repositorio externo (`3D-uy/KACE`) via `curl | bash`. Aunque hay HTTPS, **no se verifica el hash del script**. Si ese repo es comprometido, se ejecuta código arbitrario como root en la Raspberry Pi del usuario.

Esto es diferente al issue previo: este script **sí está** en el repo actual, y el destino es un repo de **misma organización** pero diferente (`3D-uy/KACE`). Es más controlable pero sigue siendo una superficie de ataque.

**Recomendación:** Incluir `install.sh` en este repo, o verificar un hash SHA-256 del script antes de ejecutarlo:
```bash
EXPECTED_HASH="abc123..."
SCRIPT=$(curl -sSL https://...)
ACTUAL_HASH=$(echo "$SCRIPT" | sha256sum | cut -d' ' -f1)
[ "$ACTUAL_HASH" = "$EXPECTED_HASH" ] && bash <<< "$SCRIPT" || { log_err "Hash mismatch!"; exit 1; }
```

---

#### MOD-2: Timeout ausente en `connect_ssh` / `self._ssh.connect()`

**Archivo:** `main.py`, línea ~528  
**Código:**
```python
success = self._ssh.connect(ip, username, password)
```

No hay timeout visible en esta llamada desde `main.py`. Si el host responde SYN pero no completa el handshake SSH (firewall con drop silencioso, host sobrecargado), Paramiko puede bloquear indefinidamente el thread de conexión. La UI mostrará "CONNECTING" para siempre sin posibilidad de cancelar.

Paramiko soporta `ssh.connect(..., timeout=10)`. Sin ver `backend/ssh_client.py` no puedo confirmar si el timeout está ahí, pero si no lo está, es un problema de UX severo.

**Recomendación:** Verificar que `SSHSession.connect()` pase `timeout=15` (o similar) al método `.connect()` de Paramiko.

---

#### MOD-3: Sin verificación de espacio en disco antes de descargar la imagen del OS

**Archivo:** `main.py`, `_download_os_image()`  
**Problema:** Las imágenes de Raspberry Pi OS Lite son ~500MB-900MB comprimidas y ~2-4GB descomprimidas. El código descarga y descomprime sin verificar espacio disponible en disco.

Si el usuario tiene poco espacio, el proceso falla con un error de I/O genérico en mitad de la descarga/descompresión, dejando un estado parcialmente limpio (el `temp_xz` se elimina, pero la experiencia es confusa).

**Recomendación:**
```python
import shutil
free = shutil.disk_usage(cache_dir).free
if free < 5 * 1024**3:  # 5GB mínimo
    raise ValueError(f"Insufficient disk space: {free // 1024**2}MB available, ~5GB required.")
```

---

### MENOR — 4 hallazgos

#### MEN-1: `pcrypt` — dependencia de baja visibilidad

**Archivo:** `requirements.txt`
```
pcrypt>=1.0.5,<2.0
```

`pcrypt` tiene actividad muy limitada en PyPI. Para hash SHA-512 de contraseñas en Linux, alternativas más auditadas incluyen `passlib` (ampliamente mantenida) o directamente el módulo `crypt` de la stdlib (disponible en Python ≤3.12, movido a `legacycrypt` en 3.13).

**Recomendación:** Evaluar reemplazo por `passlib.hash.sha512_crypt`.

---

#### MEN-2: Badges de plataforma inconsistentes con el README

El badge muestra `Windows | Linux | macOS` pero el README dice explícitamente:
> "Prerequisites: Windows OS (for disk flashing capabilities)"

El flashing de discos físicos en Windows usa WinAPI (acceso a `\\.\PhysicalDriveN`). Linux/macOS tienen APIs completamente diferentes. Los badges actuales crean expectativas incorrectas.

**Recomendación:** Cambiar badge a `Windows` solamente, o agregar nota "Linux/macOS: planned".

---

#### MEN-3: El `_sanitize_error` no se aplica en todos los paths de error

**Archivo:** `main.py`, `connect_ssh()`  
```python
except Exception as conn_err:
    self.set_device_state("ERROR", 0, f"SSH connection failed: {conn_err}")
```

En el bloque de error de SSH, `conn_err` se envía **sin pasar por `_sanitize_error()`**. El método existe y funciona, pero no se usa consistentemente. Las excepciones de Paramiko pueden incluir paths del sistema o nombres de archivos internos.

**Recomendación:** Cambiar a:
```python
self.set_device_state("ERROR", 0, f"SSH connection failed: {self._sanitize_error(conn_err)}")
```

---

#### MEN-4: `localStorage` para el tema en una app desktop

**README dice:**
> "The selected theme state is preserved via `localStorage`."

En PyWebView, `localStorage` persiste entre sesiones en algunos backends (WebKit/Chromium) pero no en todos. No es una vulnerabilidad pero sí un comportamiento inconsistente dependiendo del sistema operativo y backend de WebView.

**Recomendación:** Guardar la preferencia de tema en un archivo `~/.kace-studio/prefs.json` y cargarlo via la API Python. Más predecible y portable.

---

## Tabla resumen

| ID | Hallazgo | Severidad | Archivo |
|----|----------|-----------|---------|
| MOD-1 | `curl\|bash` sin hash para KACE Agent | Moderado | `bootstrap.sh:553` |
| MOD-2 | SSH connect sin timeout visible | Moderado | `main.py:528` |
| MOD-3 | Sin check de espacio en disco pre-descarga | Moderado | `main.py:_download_os_image` |
| MEN-1 | Dependencia `pcrypt` de baja visibilidad | Menor | `requirements.txt` |
| MEN-2 | Badges de plataforma inconsistentes | Menor | `README.md` |
| MEN-3 | `_sanitize_error` no aplicado en SSH errors | Menor | `main.py:connect_ssh` |
| MEN-4 | `localStorage` para tema en app desktop | Menor | `web/` (inferido) |

---

## Recomendaciones prioritarias

1. **(MOD-1)** Agregar verificación de hash SHA-256 al script del KACE Agent, o moverlo a este mismo repo.
2. **(MOD-2)** Confirmar que `SSHSession.connect()` pasa `timeout` a Paramiko. Si no, agregarlo.
3. **(MOD-3)** Agregar `shutil.disk_usage()` check antes de `_download_os_image()`.
4. **(MEN-3)** Aplicar `_sanitize_error()` consistentemente en todos los `except` que llaman a `set_device_state("ERROR", ...)`.
