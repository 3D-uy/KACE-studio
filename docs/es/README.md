# KACE Studio

🌐 [English](../../README.md) · [Español](../../docs/es/README.md) · [Português](../../docs/pt/README.md)

![KACE Studio](../../web/KACE-studio-banner.png)

KACE Studio es el escritorio Windows para preparar una Raspberry Pi con Klipper: imagen, primer arranque, descubrimiento, SSH y SFTP. Python controla validaciones y operaciones; JavaScript muestra sus estados. [KACE](https://github.com/3D-uy/KACE/blob/main/docs/es/README.md) en la Pi controla la configuración de la impresora y la instalación verificada.

**Candidato de prueba controlada.** [release-contract.json](../../release-contract.json) define versión e insumos de compilación; [CHANGELOG](../../CHANGELOG.md) describe el candidato actual. Cambios de código, validación del paquete, calificación física y publicación firmada son estados separados. Un EXE anterior no incluye los cambios actuales del código.

## Inicio rápido

Usa Windows 10/11 con Microsoft Edge WebView2 Runtime. El desarrollo desde código contempla Python 3.11/3.12; los paquetes requieren el toolchain exacto del contrato de release. El writer solicita elevación para la operación de disco seleccionada.

Ejecuta desde código en Windows:

```powershell
git clone https://github.com/3D-uy/KACE-studio.git
cd KACE-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

## 🧭 Uso

1. **Smart Imager:** elige modelo Pi, arquitectura, dashboard, fuente de imagen y SD/USB exacta.
2. **Credentials:** configura hostname, usuario, contraseña, red y SSH. Revisa el resumen antes de confirmar la escritura destructiva.
3. Espera escritura, lectura de verificación/provisión y expulsión segura; después arranca la Pi.
4. **Discovery → SSH Workspace:** selecciona la Pi, verifica su clave de host y conecta. Usa el navegador SFTP para listar/descargar archivos remotos; pertenece a la sesión SSH actual.
5. Inicia bootstrap y continúa KACE en la Pi. Sigue los pasos manuales de firmware y espera la verificación de KACE; realiza aparte la puesta en marcha física.

## ⚠️ Alcance y límites

- Las imágenes oficiales y sus identidades proceden del [manifiesto de imágenes](../../image-manifest.json). Fluidd usa la base MainsailOS verificada más bootstrap; no una imagen archivada de FluiddPI.
- Pi 5/500/500+/CM5 requieren 64-bit; Pi 4/400/CM4/CM4S, Pi 3/CM3 y Zero 2 W/CM2W ofrecen las rutas 32/64-bit admitidas; Zero W usa 32-bit. Python valida la combinación seleccionada.
- Las imágenes personalizadas requieren `.img` sin comprimir y `.sha256`; las pre-baked personalizadas también `.kace-attestation.json`. No omitas controles de checksum, identidad de disco ni capacidades.
- Los listados y descargas SFTP quedan ligados a la conexión que los originó. Reconectar no demuestra éxito de instalación; los checkpoints pendientes requieren verificación de KACE.
- La autorización de cliente Moonraker guarda explícitamente permiso para la IP de este equipo tras confirmar. Úsala solo cuando necesites acceso; una dirección obsoleta requiere eliminación manual.
- Ni las pruebas automáticas ni el preview del navegador prueban medios reales, USB/MCU, elevación Windows o WebView2 empaquetado.

## 🛠️ Desarrollo

Consulta [Desarrollo (EN)](../../docs/DEVELOPMENT.md) para arquitectura, pruebas, frontend y límites entre código y paquete. Ejecuta `python -m pytest -q` en el entorno configurado; los harnesses frontend requieren Node. La validación de release y compilación siguen el checklist, no el preview del navegador.

## 📚 Documentación

| Necesidad | Guía |
| --- | --- |
| Desarrollo y pruebas (EN) | [DEVELOPMENT.md](../../docs/DEVELOPMENT.md) |
| Provisión y recuperación (EN) | [IMAGE_PROVISIONING.md](../../docs/IMAGE_PROVISIONING.md) |
| Release y calificación de hardware (EN) | [RELEASE_CHECKLIST.md](../../RELEASE_CHECKLIST.md) |
| Roadmap | [ROADMAP.md](ROADMAP.md) |
| Notas del candidato actual | [CHANGELOG.md](../../CHANGELOG.md) |
| Seguridad (EN) | [SECURITY.md](../../SECURITY.md) |

## Licencia

[GPL-3.0](../../LICENSE).
