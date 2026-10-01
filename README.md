![KACE Studio](web/KACE-studio-banner.png)

# KACE Studio

### Raspberry Pi setup for the KACE ecosystem

**Prepare your Raspberry Pi for Klipper — from a Windows desktop.**

[![KACE Studio 0.5.0-rc.2](https://img.shields.io/badge/Studio-0.5.0--rc.2-e88c30?style=flat-square)](release-contract.json)
[![Status: pre-release](https://img.shields.io/badge/status-pre--release-d29b32?style=flat-square)](#project-status)
[![Windows 10/11 x64](https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-0078D4?style=flat-square)](#platform-and-requirements)
[![Python 3.11 / 3.12](https://img.shields.io/badge/Python-3.11%20%2F%203.12-3776AB?style=flat-square&logo=python&logoColor=white)](docs/DEVELOPMENT.md)
[![CI / tests](https://img.shields.io/github/actions/workflow/status/3D-uy/KACE-studio/ci.yml?branch=main&style=flat-square&label=CI%20%2F%20tests&logo=githubactions&logoColor=white)](https://github.com/3D-uy/KACE-studio/actions/workflows/ci.yml)<br>
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-2d718f?style=flat-square)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/3D-uy/KACE-studio?style=flat-square&logo=github&label=stars&color=e3b341)](https://github.com/3D-uy/KACE-studio)
[![WebView2 Runtime](https://img.shields.io/badge/renderer-WebView2-0078D4?style=flat-square)](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
[![KACE integration](https://img.shields.io/badge/integration-KACE-e88c30?style=flat-square)](#kace-integration)
[![PyWebView](https://img.shields.io/badge/desktop-PyWebView-454545?style=flat-square)](requirements.txt)

🌐 [English](README.md) · [Español](docs/es/README.md) · [Português](docs/pt/README.md)

KACE Studio guides you through choosing a Raspberry Pi image, setting up first boot and writing an SD card or USB drive. Once the Pi starts, use discovery, SSH and SFTP to continue setup with KACE.

**KACE Studio prepares the host; KACE configures the printer.**

**[⬇ Download for Windows x64 (ZIP)](https://github.com/3D-uy/KACE-studio/releases/download/v0.5.0-rc.2/KACE-Studio-0.5.0-rc.2-Windows-x64.zip)** · [Releases](https://github.com/3D-uy/KACE-studio/releases) · [KACE](https://github.com/3D-uy/KACE)

## ✨ What Studio does

| Task | In Studio |
| --- | --- |
| 💾 **Prepare the Pi** | Choose the Pi model, image, architecture and Mainsail/Fluidd dashboard. |
| 🔧 **Set up first boot** | Configure hostname, account, network and SSH before booting. |
| ✅ **Write and verify** | Review the selected SD/USB drive, write the image, verify it and eject safely. |
| 🔗 **Connect and continue** | Find the Pi, use SSH/SFTP and follow bootstrap and KACE installation progress. |

## 🧭 From image to printer setup

> **Choose hardware → Choose image → Configure first boot → Write & verify<br>→ Boot the Pi → Connect over SSH → Continue with KACE**

<a id="quick-start"></a>
<a id="download-and-install"></a>

## 🚀 Download & Install

Requires **Windows 10/11 x64** and [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). If Studio cannot open because WebView2 is missing, install Microsoft's **Evergreen Standalone Installer (x64)** and try again.

1. Open [Releases](https://github.com/3D-uy/KACE-studio/releases) and select **v0.5.0-rc.2** (pre-release).
2. Under **Assets**, download **`KACE-Studio-0.5.0-rc.2-Windows-x64.zip`**. The automatically generated “Source code” archives are for developers.
3. Right-click the ZIP, choose **Extract All**, and open the extracted folder.
4. Double-click **`KACE-studio.exe`**. You do not need to install Python or Git.
5. If Windows shows **“Windows protected your PC”**, this prerelease is **unsigned**. First check that the ZIP came from this repository and that its SHA-256 matches the release. If it matches and you choose to proceed, select **More info → Run anyway**. If that option is unavailable on a managed computer, contact its administrator.
6. Start in **Smart Imager**. Connect the intended SD/USB drive and review its identity before writing: **the selected drive will be erased**. Administrator approval is requested for the disk operation.

<details>
<summary>🔐 Verify the download</summary>

Compare the result with the SHA-256 published in the release notes and `SHA256SUMS.txt`:

```powershell
Get-FileHash .\KACE-Studio-0.5.0-rc.2-Windows-x64.zip -Algorithm SHA256
```

</details>

## 🖼️ A look inside Studio

![Smart Imager: select hardware, image and target drive.](docs/assets/studio-imager.png)

*Smart Imager: select hardware, image and target drive.*

![Credentials: prepare the Pi's first-boot account and network settings.](docs/assets/studio-credentials.png)

*Credentials: prepare the Pi's first-boot account and network settings.*

Real captures of the Windows application. These screens show preparation, not a completed disk write or a connected printer.

<a id="kace-integration"></a>

## 🔗 KACE integration

Studio handles imaging, first-boot setup and remote access. [KACE](https://github.com/3D-uy/KACE) handles printer configuration, supported MCU firmware workflows, application and installation verification.

Start bootstrap from the SSH workspace, then follow KACE's guided setup on the Pi. Some firmware steps require manual action; complete KACE's verification before commissioning the printer.

<a id="platform-and-requirements"></a>

## 🖥️ Hardware and requirements

| Area | What you need |
| --- | --- |
| Computer | Windows 10/11 x64 and WebView2; internet access for downloads. |
| Printer host | A supported Raspberry Pi, an SD/USB drive and local network access. |
| Images | Raspberry Pi OS Lite or the verified MainsailOS base; Mainsail, Fluidd or both as dashboard choices. |
| Custom images | An uncompressed `.img` with `.sha256`; custom pre-baked images also need `.kace-attestation.json`. |

Pi model/architecture combinations and custom-image requirements are detailed in the [image guide](docs/IMAGE_PROVISIONING.md). Linux CI tests do not imply Linux desktop support.

<a id="project-status"></a>

## 🧪 Project status

**0.5.0-rc.2 is an unsigned prerelease.** Automated tests and package checks do not establish physical hardware qualification. Real media writing, first boot and printer commissioning still need validation on your equipment.

Release builds carry their source commit, checksums and independent Windows rebuild evidence. For signing policy, build contracts, recovery boundaries and qualification, see the [release checklist](RELEASE_CHECKLIST.md) and [roadmap](ROADMAP.md).

## 📚 Documentation

| Looking for | Read |
| --- | --- |
| Images, provisioning and recovery | [Image guide](docs/IMAGE_PROVISIONING.md) |
| Architecture, source setup and tests | [Development](docs/DEVELOPMENT.md) |
| Build evidence, signing and qualification | [Release checklist](RELEASE_CHECKLIST.md) |
| Changes and planned work | [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) |

## 🛠️ Development and contributing

To run from source, install Git and Python **3.12** (3.11 is also supported), then use PowerShell:

```powershell
git clone https://github.com/3D-uy/KACE-studio.git
cd KACE-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

Run `python -m pytest -q` and `python scripts/check_portability.py` in the virtual environment. Node is required for frontend tests. See [Development](docs/DEVELOPMENT.md) for the complete workflow and [Security](SECURITY.md) for vulnerability reports.

## ❤️ Community & Acknowledgements

**Special thanks to Klipper and its community** for the firmware, documentation and shared knowledge behind this ecosystem.

Studio builds on [KACE](https://github.com/3D-uy/KACE), [Moonraker](https://moonraker.readthedocs.io/en/latest/), [Mainsail / MainsailOS](https://docs.mainsail.xyz/), [Fluidd](https://docs.fluidd.xyz/), [Raspberry Pi](https://www.raspberrypi.com/software/) and optional [Crowsnest](https://docs.mainsail.xyz/crowsnest/). Its desktop window uses [PyWebView](https://github.com/r0x0r/pywebview) and [WebView2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).

KACE Studio is an independent project and is not officially affiliated with or endorsed by Klipper or the other third-party projects mentioned here.

## 📜 License

KACE Studio is open source under the [GNU GPL v3](LICENSE).
