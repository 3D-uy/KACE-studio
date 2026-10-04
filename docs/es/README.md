![KACE Studio](../../web/KACE-studio-banner.png)

# KACE Studio

### Preparación de Raspberry Pi para el ecosistema KACE

**Prepara tu Raspberry Pi para Klipper desde una aplicación de Windows.**

[![KACE Studio 0.5.0-rc.3](https://img.shields.io/badge/Studio-0.5.0--rc.3-e88c30?style=flat-square)](../../release-contract.json) [![Status: pre-release](https://img.shields.io/badge/status-pre--release-d29b32?style=flat-square)](#project-status) [![Windows 10/11 x64](https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-0078D4?style=flat-square)](#platform-and-requirements) [![Python 3.11 / 3.12](https://img.shields.io/badge/Python-3.11%20%2F%203.12-3776AB?style=flat-square&logo=python&logoColor=white)](../DEVELOPMENT.md) [![CI / tests](https://img.shields.io/github/actions/workflow/status/3D-uy/KACE-studio/ci.yml?branch=main&style=flat-square&label=CI%20%2F%20tests&logo=githubactions&logoColor=white)](https://github.com/3D-uy/KACE-studio/actions/workflows/ci.yml) [![License: GPLv3](https://img.shields.io/badge/license-GPLv3-2d718f?style=flat-square)](../../LICENSE) [![GitHub stars](https://img.shields.io/github/stars/3D-uy/KACE-studio?style=flat-square&logo=github&label=stars&color=e3b341)](https://github.com/3D-uy/KACE-studio) [![WebView2 Runtime](https://img.shields.io/badge/renderer-WebView2-0078D4?style=flat-square)](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) [![KACE integration](https://img.shields.io/badge/integration-KACE-e88c30?style=flat-square)](#kace-integration) [![PyWebView](https://img.shields.io/badge/desktop-PyWebView-454545?style=flat-square)](../../requirements.txt)

🌐 [English](../../README.md) · [Español](README.md) · [Português](../pt/README.md)

KACE Studio te guía para elegir una imagen de Raspberry Pi, configurar el primer arranque y escribir una tarjeta SD o unidad USB. Una vez iniciada la Pi, utiliza descubrimiento, SSH y SFTP para continuar la instalación con KACE.

**KACE Studio prepara el host; KACE configura la impresora.**

**[⬇ Descargar para Windows x64 (ZIP)](https://github.com/3D-uy/KACE-studio/releases/download/v0.5.0-rc.3/KACE-Studio-0.5.0-rc.3-Windows-x64.zip)** · [Releases](https://github.com/3D-uy/KACE-studio/releases) · [KACE](https://github.com/3D-uy/KACE)

## ✨ Qué hace Studio

| Tarea | En Studio |
| --- | --- |
| 💾 **Preparar la Pi** | Elige modelo de Pi, imagen, arquitectura y dashboard Mainsail/Fluidd. |
| 🔧 **Configurar el primer arranque** | Define hostname, cuenta, red y SSH antes de arrancar. |
| ✅ **Escribir y verificar** | Revisa la SD/USB seleccionada, escribe la imagen, verifica y expulsa de forma segura. |
| 🔗 **Conectar y continuar** | Encuentra la Pi, usa SSH/SFTP y sigue el progreso del bootstrap y de la instalación de KACE. |

## 🧭 De la imagen a la configuración de la impresora

> **Elegir hardware → Elegir imagen → Configurar primer arranque → Escribir y verificar<br>→ Arrancar la Pi → Conectar por SSH → Continuar con KACE**

Imager alinea los ajustes por pares y abre las opciones del relé GPIO al activarlo. La búsqueda se pausa cuando responden equipos nuevos. Elegí Conectar, Seguir buscando (las direcciones ya revisadas no vuelven a pausarla) o Detener búsqueda. Cada período de búsqueda dura hasta diez minutos; encontrar un equipo no verifica una instalación de KACE.

<a id="quick-start"></a>
<a id="download-and-install"></a>

## 🚀 Descarga e instalación

Requiere **Windows 10/11 x64** y [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). Si Studio no abre porque falta WebView2, instala el **Evergreen Standalone Installer (x64)** de Microsoft e inténtalo de nuevo.

1. Abre [Releases](https://github.com/3D-uy/KACE-studio/releases) y selecciona **v0.5.0-rc.3** (pre-release).
2. En **Assets**, descarga **`KACE-Studio-0.5.0-rc.3-Windows-x64.zip`**. Los archivos automáticos “Source code” son para desarrollo.
3. Haz clic derecho sobre el ZIP, elige **Extraer todo** y abre la carpeta extraída.
4. Ejecuta **`KACE-studio.exe`** con doble clic. No necesitas instalar Python ni Git.
5. Si Windows muestra **«Windows protegió su PC»**, esta prerelease **no tiene firma digital**. Primero comprueba que el ZIP provenga de este repositorio y que su SHA-256 coincida con el publicado. Si coincide y decides continuar, selecciona **Más información → Ejecutar de todas formas**. Si no aparece esa opción en un equipo administrado, contacta a su administrador.
6. Comienza en **Smart Imager**. Conecta la SD/USB que quieres usar y revisa su identidad antes de escribir: **se borrará la unidad seleccionada**. La operación de disco solicitará autorización de administrador.

<details>
<summary>🔐 Verificar la descarga</summary>

Compara el resultado con el SHA-256 publicado en las notas de la release y en `SHA256SUMS.txt`:

```powershell
Get-FileHash .\KACE-Studio-0.5.0-rc.3-Windows-x64.zip -Algorithm SHA256
```

</details>

## 🖼️ Studio por dentro

![Smart Imager: selección de hardware, imagen y unidad de destino.](../assets/studio-imager.png)

*Smart Imager: selección de hardware, imagen y unidad de destino.*

![Credentials: preparación de la cuenta y la red para el primer arranque de la Pi.](../assets/studio-credentials.png)

*Credentials: preparación de la cuenta y la red para el primer arranque de la Pi.*

Capturas reales de la aplicación Windows. Muestran la preparación, no una escritura de disco completada ni una impresora conectada.

<a id="kace-integration"></a>

## 🔗 Integración con KACE

Studio se ocupa de la imagen, el primer arranque y el acceso remoto. [KACE](https://github.com/3D-uy/KACE/blob/main/docs/es/README.md) se ocupa de la configuración de impresora, los flujos de firmware MCU admitidos, la aplicación y la verificación de la instalación.

Inicia el bootstrap desde el espacio de trabajo SSH y continúa con el asistente de KACE en la Pi. Algunos pasos de firmware requieren intervención manual; completa la verificación de KACE antes de poner en marcha la impresora.

<a id="platform-and-requirements"></a>

## 🖥️ Hardware y requisitos

| Área | Qué necesitas |
| --- | --- |
| Equipo | Windows 10/11 x64 y WebView2; internet para las descargas. |
| Host de impresora | Una Raspberry Pi admitida, una SD/USB y acceso a la red local. |
| Imágenes | Raspberry Pi OS Lite o la base MainsailOS verificada; Mainsail, Fluidd o ambos como dashboards. |
| Imágenes propias | Una `.img` sin comprimir con `.sha256`; las imágenes preconfiguradas propias también necesitan `.kace-attestation.json`. |

La [guía de imágenes (EN)](../IMAGE_PROVISIONING.md) detalla las combinaciones de Pi/arquitectura y los requisitos de imágenes propias. Las pruebas de CI en Linux no implican soporte de escritorio Linux.

<a id="project-status"></a>

## 🧪 Estado del proyecto

**0.5.0-rc.3 es una prerelease sin firma digital.** Las pruebas automáticas y del paquete no acreditan validación física del hardware. La escritura real, el primer arranque y la puesta en marcha de la impresora todavía requieren validación en tu equipo.

Las distribuciones incluyen commit de origen, checksums y evidencia de reconstrucción independiente en Windows. Consulta la política de firma, los contratos de build, los límites de recuperación y la validación en el [checklist de release (EN)](../../RELEASE_CHECKLIST.md) y el [roadmap](ROADMAP.md).

## 📚 Documentación

| Para | Consulta |
| --- | --- |
| Imágenes, provisión y recuperación | [Guía de imágenes (EN)](../IMAGE_PROVISIONING.md) |
| Arquitectura, ejecución desde código y pruebas | [Desarrollo (EN)](../DEVELOPMENT.md) |
| Evidencia de build, firma y validación | [Checklist de release (EN)](../../RELEASE_CHECKLIST.md) |
| Cambios y trabajo previsto | [Changelog (EN)](../../CHANGELOG.md) · [Roadmap](ROADMAP.md) |

## 🛠️ Desarrollo y contribuciones

Para ejecutar desde código, instala Git y Python **3.12** (también se admite 3.11) y utiliza PowerShell:

```powershell
git clone https://github.com/3D-uy/KACE-studio.git
cd KACE-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

Ejecuta `python -m pytest -q` y `python scripts/check_portability.py` en el entorno virtual. Las pruebas de frontend requieren Node. Consulta el flujo completo en [Desarrollo (EN)](../DEVELOPMENT.md) y los reportes de vulnerabilidades en [Seguridad (EN)](../../SECURITY.md).

## ❤️ Comunidad y agradecimientos

**Un agradecimiento especial a Klipper y a su comunidad** por el firmware, la documentación y el conocimiento compartido que hacen posible este ecosistema.

Studio se apoya en [KACE](https://github.com/3D-uy/KACE), [Moonraker](https://moonraker.readthedocs.io/en/latest/), [Mainsail / MainsailOS](https://docs.mainsail.xyz/), [Fluidd](https://docs.fluidd.xyz/), [Raspberry Pi](https://www.raspberrypi.com/software/) y [Crowsnest](https://docs.mainsail.xyz/crowsnest/) opcional. Su ventana de escritorio utiliza [PyWebView](https://github.com/r0x0r/pywebview) y [WebView2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).

KACE Studio es un proyecto independiente y no está oficialmente afiliado ni respaldado por Klipper ni por los demás proyectos de terceros mencionados aquí.

## 📜 Licencia

KACE Studio es software open source bajo la [GNU GPL v3](../../LICENSE).
