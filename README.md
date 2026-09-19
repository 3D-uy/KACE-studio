<p align="center">
  <img src="web/KACE-studio-banner.png" width="1000" alt="KACE Studio — Windows provisioning for the KACE ecosystem">
</p>

<h1 align="center">KACE Studio</h1>

<p align="center">
  <strong>Prepare a Klipper Raspberry Pi from one guided Windows workspace.</strong><br>
  Image the host, configure first boot, discover the Pi and continue into KACE over SSH.
</p>

<p align="center">
  <a href="https://github.com/3D-uy/KACE-studio/actions/workflows/ci.yml"><img src="https://github.com/3D-uy/KACE-studio/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status"></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/version-0.5.0--rc.1-f59e0b?style=flat-square" alt="Version 0.5.0-rc.1"></a>
  <img src="https://img.shields.io/badge/status-hardware_qualification-f59e0b?style=flat-square" alt="Hardware qualification pending">
  <img src="https://img.shields.io/badge/Windows-10_%7C_11-0078D4?style=flat-square&amp;logo=windows11&amp;logoColor=white" alt="Windows 10 and 11">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-2ea44f?style=flat-square" alt="GPL-3.0 license"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Windows-desktop-0078D4?style=for-the-badge&amp;logo=windows11&amp;logoColor=white" alt="Windows desktop">
  <img src="https://img.shields.io/badge/Raspberry_Pi-imaging-A22846?style=for-the-badge&amp;logo=raspberrypi&amp;logoColor=white" alt="Raspberry Pi imaging">
  <img src="https://img.shields.io/badge/Klipper-via_KACE-F2A900?style=for-the-badge" alt="Klipper via KACE">
  <img src="https://img.shields.io/badge/SSH_%2B_SFTP-workspace-4D4D4D?style=for-the-badge&amp;logo=gnometerminal&amp;logoColor=white" alt="SSH and SFTP workspace">
</p>

> [!IMPORTANT]
> **0.5.0-rc.1 is a controlled test candidate paired with KACE 0.9.4-rc.2.** The automated and packaging contracts are in place; physical provisioning qualification and a signed public binary channel are still pending.

## Start here

| I want to… | Go to… |
| --- | --- |
| See what the desktop workflow covers | [The Studio experience](#the-studio-experience) |
| Run Studio from source | [Quick start](#quick-start) |
| Check available operating-system images | [Supported images and platforms](#supported-images-and-platforms) |
| Understand disk and package safety | [Safety and trust model](#safety-and-trust-model) |
| Configure the printer after provisioning | [KACE](https://github.com/3D-uy/KACE) |
| Diagnose a problem | [Troubleshooting](#troubleshooting) |
| Prepare or verify a release artifact | [Release checklist](RELEASE_CHECKLIST.md) |

## The Studio experience

| 1 · Choose | 2 · Prepare | 3 · Write |
| --- | --- | --- |
| Select the Raspberry Pi model, image source, architecture and dashboard. | Enter network, account and first-boot settings in the guided interface. | Review the exact removable disk, approve elevation and follow verified write progress. |

| 4 · Discover | 5 · Provision | 6 · Continue in KACE |
| --- | --- | --- |
| Find the new Pi on the local network or enter its address directly. | Use the embedded SSH terminal and SFTP manager while Studio tracks bootstrap stages. | Run KACE in the same terminal for board, printer configuration and optional MCU firmware work. |

Studio brings the host setup into one desktop application:

- choose an official image or a verified custom image, with download caching and checksum validation;
- configure networking, credentials and provisioning before first boot;
- identify the destination disk and write it through a guarded elevated helper;
- discover the Pi and work through the embedded SSH terminal and SFTP manager;
- follow bootstrap progress, failures and reconnect recovery in the same interface;
- view KACE-owned firmware progress and authorize Moonraker controls explicitly.

## One ecosystem, two tools

```text
KACE Studio on Windows  →  Raspberry Pi bootstrap  →  KACE on Linux  →  Klipper commissioning
 image · first boot · SSH      pinned contract        config · MCU       physical validation
```

| Component | Owns |
| --- | --- |
| 🪟 **KACE Studio** | Raspberry Pi imaging, first-boot configuration, discovery, SSH/SFTP and provisioning visibility. |
| 🍓 **Bootstrap** | Installs the selected Klipper host stack and launches the pinned KACE runtime. |
| 🧩 **[KACE](https://github.com/3D-uy/KACE)** | Printer-specific configuration, exact controller contracts and optional MCU firmware workflows. |
| 🔧 **Klipper + operator** | Printer runtime and controlled electrical, thermal, homing and motion commissioning. |

Studio and KACE remain separate applications. A versioned KACE bootstrap connects them: Studio verifies and launches it, then displays its progress while it installs the pinned KACE revision.

## Quick start

KACE Studio does not yet have a stable signed binary release channel. For development or controlled evaluation, run it from source:

```powershell
git clone https://github.com/3D-uy/KACE-studio.git
Set-Location KACE-studio
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock
python main.py
```

### Requirements

| | Requirement |
| --- | --- |
| Operating system | Windows 10 or 11 for the desktop application and physical writer |
| Runtime | Python 3.11 or 3.12 for source use; the release contract pins Python 3.12.10 for packaged builds |
| Desktop engine | Microsoft Edge WebView2 |
| Privileges | Administrator approval only when the isolated raw-disk writer starts |
| Hardware | A supported Raspberry Pi and an explicitly accepted removable target |
| Network | Internet access for downloads and a local path to the booted Pi |

> [!CAUTION]
> Writing a raw image erases the selected device. Confirm its model, serial, capacity and connection before approving elevation.

## Supported images and platforms

The authoritative image list and checksums live in [`image-manifest.json`](image-manifest.json).

| Image path | Architectures | Pinned version | Verification |
| --- | --- | --- | --- |
| Raspberry Pi OS Lite | 32-bit / 64-bit | `2026-06-18` | Pinned archive SHA-256; configured on first boot |
| MainsailOS | 32-bit / 64-bit | `3.0.0` | Pinned archive and raw-image SHA-256 plus image-bound capability attestation |
| Fluidd (MainsailOS base) | 32-bit / 64-bit | Base `3.0.0`, Fluidd `v1.37.3` | Same verified base and attestation; SHA-256 verified Fluidd installation during provisioning |
| Custom raw image | As supplied by the user | User-managed | Uncompressed `.img` plus an external `.sha256` sidecar |
| Custom pre-baked image | As supplied by the user | User-managed | Raw-image checksum plus a compatible `.kace-attestation.json` |

Select **Fluidd** with **Pre-baked OS Image** to use the verified MainsailOS base
with Klipper and Moonraker already installed. Provisioning installs the pinned
Fluidd release and serves it on port 80 automatically. Studio shares the image
download/cache and attestation with MainsailOS; it does not maintain a separate
Fluidd image or use the archived FluiddPI project. Raspberry Pi OS Lite still
supports installing Mainsail, Fluidd, or both during provisioning.
See [image provisioning and upstream evidence](docs/IMAGE_PROVISIONING.md).

### Raspberry Pi models

| Family represented in Studio | 32-bit image | 64-bit image |
| --- | :---: | :---: |
| Raspberry Pi 5 / 500 / 500+ / Compute Module 5 | — | ✓ |
| Raspberry Pi 4 / 400 / Compute Module 4 / 4S | ✓ | ✓ |
| Raspberry Pi 3 A+ / B / B+ / Compute Module 3 / 3+ | ✓ | ✓ |
| Raspberry Pi Zero 2 W / Compute Module 2 W | ✓ | ✓ |
| Raspberry Pi Zero W | ✓ | — |

| Platform / integration | Current scope | Boundary |
| --- | --- | --- |
| Windows 10/11 | Full PyWebView application and physical raw-disk writer | UAC is required for the isolated writer helper. |
| Raspberry Pi | Supported image provisioning and first-boot configuration | The exact board/image combination still needs physical qualification. |
| Klipper + KACE | Pinned host provisioning and read-only firmware workflow display | KACE owns MCU decisions and success state. |
| Moonraker | Discovery, per-client authorization and power controls | Existing or ambiguous authorization files may require manual administration. |
| Linux | Non-destructive backend CI tests | No Linux physical writer or packaged desktop release. |

## Safety and trust model

- The normal desktop process stays unelevated. Only the small physical writer helper requests administrator access.
- Studio records the selected disk identity, and the elevated helper checks the same device again before writing.
- System and boot disks, incomplete identities, changed devices, undersized targets and unsupported buses are rejected.
- Studio verifies the approved image checksum and size before and during the protected write process.
- A write succeeds only when the matching helper finishes and reports success.
- Custom images require checksum evidence supplied independently from the selected image. Pre-baked custom images also require a matching capability attestation.
- Studio displays KACE firmware events but does not infer, authorize or manufacture firmware success.

Moonraker begins with local-only access. **Authorize this computer for Moonraker** adds the client IP reported by the Pi after confirmation. A new computer or changed address must be authorized separately; removing an old entry remains a manual configuration task.

## Current status

| Item | State |
| --- | --- |
| Studio version | `0.5.0-rc.1` |
| Paired KACE version | `0.9.4-rc.2` |
| Runtime contract | Pinned in `release-contract.json` |
| Packaged build contract | Python `3.12.10` · PyInstaller `6.21.0` |
| Binary channel | No stable signed public download yet |
| Physical qualification | Pending |

CI tests Studio on Windows and Linux, verifies its pinned KACE resources, builds the Windows executable and checks the real desktop renderer without writing hardware. Regular CI artifacts are unsigned development builds.

`release-contract.json` pins the KACE runtime and packaging toolchain. Each release executable has its own matching manifest; the two must stay together. Signing, reproducibility and manifest checks are documented in the [release checklist](RELEASE_CHECKLIST.md).

## Troubleshooting

| Symptom | First check |
| --- | --- |
| No writable disk appears | Confirm the media is removable, not the Windows system/boot disk, fully identified and large enough for the resolved raw image. |
| Download or extraction stops | Retry through Studio so it can reuse supported cached data. An XZ image above the decoder limit must be supplied as a verified raw IMG. |
| The Pi is not discovered | Confirm it completed first boot, joined the same reachable network and exposes SSH; local routing and firewall policy affect discovery. |
| SSH retries keep failing | Recheck address, credentials and host-key prompt. The full connect/trust transaction has a bounded deadline. |
| Moonraker controls are denied | Connect through SSH, choose **Authorize this computer for Moonraker**, and confirm the client IP shown by the Pi. |
| Provisioning or firmware is pending | Follow the stage-specific instructions. Studio will not convert a transfer, reconnect or prepared artifact into success. |

For a useful report, include the Studio version, image type, non-secret error text and bootstrap stage. Never publish credentials, host keys or configuration secrets. Use [KACE Studio Issues](https://github.com/3D-uy/KACE-studio/issues) for provisioning problems and [KACE Issues](https://github.com/3D-uy/KACE/issues) for printer generation.

## Documentation and project links

| Topic | Resource |
| --- | --- |
| Changes in this candidate | [Changelog](CHANGELOG.md) |
| Build, signing and artifact verification | [Release checklist](RELEASE_CHECKLIST.md) |
| KACE configuration and firmware | [KACE repository](https://github.com/3D-uy/KACE) |
| Hardware qualification | [KACE hardware testing guide](https://github.com/3D-uy/KACE/blob/main/docs/HARDWARE_TESTING.md) |
| Studio support | [Issues](https://github.com/3D-uy/KACE-studio/issues) |
| License | [GNU GPL v3.0](LICENSE) |

## Development

Install locked dependencies and run the non-destructive suite:

```powershell
python -m pip install --require-hashes -r requirements.lock
python -m pytest -v
```

On Windows, `python main.py --smoke-test` checks the source-mode interface. Packaged validation belongs to the [release checklist](RELEASE_CHECKLIST.md). Disk and writer tests use mocks and temporary files; they must never target real hardware.

<details>
<summary><strong>Repository map for contributors</strong></summary>

The main implementation areas are:

| Path | Responsibility |
| --- | --- |
| `main.py` | PyWebView API, desktop lifecycle, image/cache orchestration and KACE event forwarding |
| `web/` | Guided interface, validation, progress, terminal and bundled front-end assets |
| `backend/imager.py` | Disk policy, identity capture, writer launch and boot-partition injection |
| `backend/kace_writer.py` | Elevated physical-device revalidation and raw image writing |
| `backend/discovery.py` | Local host and service discovery |
| `backend/ssh_client.py` | SSH terminal, SFTP and bounded recovery reads |
| `bootstrap.sh` | Verified build input copied from the pinned KACE revision |

</details>

## Contributing

Open an issue before a broad workflow or writer change. Keep the unelevated/elevated boundary intact, add non-destructive regression coverage and document changes to bootstrap markers or packaged resources.

### Protected main workflow

Create a branch and open a pull request against `main`. All required CI checks
must succeed on an up-to-date branch before merging. No manual review approval
is required. The protection applies to administrators too: direct pushes,
force pushes and deletion of `main` are prohibited.

Keep examples relative or use runtime/environment-derived paths. Personal
computer paths must not be committed; the portability gate scans all tracked
files, including test fixtures. Path-detection tests use synthetic inputs.

## License

KACE Studio is licensed under the [GNU General Public License v3.0](LICENSE).
