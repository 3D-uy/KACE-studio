# KACE Studio

🌐 [English](README.md) · [Español](docs/es/README.md) · [Português](docs/pt/README.md)

![KACE Studio](web/KACE-studio-banner.png)

KACE Studio is the Windows desktop workspace for preparing a Klipper Raspberry Pi: imaging, first-boot configuration, discovery, SSH and SFTP. Python owns validation and operations; JavaScript presents their state. [KACE](https://github.com/3D-uy/KACE) on the Pi owns printer configuration and verified installation.

**Controlled test candidate.** [release-contract.json](release-contract.json) owns version and build inputs; [CHANGELOG](CHANGELOG.md) records current candidate notes. Source changes, packaged validation, physical qualification and signed publication are separate states. An old EXE does not contain current source changes.

**Unsigned distribution.** The current product decision is to distribute KACE Studio without an Authenticode signature. Every distributed EXE must retain its SHA-256, release manifest, independent rebuild attestation and exact source commit. Signing remains available as a separate future gate; its absence does not block this unsigned distribution. See the [release checklist](RELEASE_CHECKLIST.md).

## Quick start

Use Windows 10/11 with Microsoft Edge WebView2 Runtime. Source development targets Python 3.11/3.12; packaged builds require the exact toolchain in the release contract. The writer requests elevation for the chosen disk operation.

Run from source on Windows:

```powershell
git clone https://github.com/3D-uy/KACE-studio.git
cd KACE-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

## 🧭 Use

1. **Smart Imager:** choose Pi model, architecture, dashboard, image source and exact SD/USB target.
2. **Credentials:** configure hostname, user, password, network and SSH. Review the summary before confirming the destructive write.
3. Wait for writing, readback/provisioning and safe eject; then boot the Pi.
4. **Discovery → SSH Workspace:** select the Pi, verify its host key and connect. Use the SFTP browser to list/download remote files; it belongs to the current SSH session.
5. Start bootstrap, then continue KACE on the Pi. Follow manual firmware steps and wait for KACE verification; commission the physical printer separately.

## ⚠️ Scope and limits

- Official image choices and integrity data come from [the image manifest](image-manifest.json). Fluidd uses the verified MainsailOS base plus bootstrap provisioning; it is not an archived FluiddPI image.
- Pi 5/500/500+/CM5 require 64-bit; Pi 4/400/CM4/CM4S, Pi 3/CM3 and Zero 2 W/CM2W offer the supported 32/64-bit paths; Zero W uses 32-bit. Python validates the selected combination.
- Custom images require raw `.img` plus `.sha256`; custom pre-baked images also require `.kace-attestation.json`. Do not bypass checksum, disk identity or capability checks.
- SFTP results and downloads are bound to the originating connection. Reconnection does not prove installation success; pending checkpoints need KACE verification.
- Moonraker client authorization explicitly persists permission for this computer’s IP after confirmation. Use it only when access is required; an obsolete address requires manual removal.
- No automated test or browser preview proves real media, USB/MCU behavior, Windows elevation or packaged WebView2 operation.

## 🛠️ Development

Use [Development](docs/DEVELOPMENT.md) for architecture, tests, frontend checks and source/package boundaries. Run `python -m pytest -q` in the configured environment; Node is needed for frontend harnesses. Release validation and building follow the checklist, not a browser preview.

## 📚 Documentation

| Purpose | Guide |
| --- | --- |
| Development and tests | [DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| Image provisioning and recovery | [IMAGE_PROVISIONING.md](docs/IMAGE_PROVISIONING.md) |
| Release and hardware qualification | [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) |
| Roadmap | [ROADMAP.md](ROADMAP.md) |
| Current candidate notes | [CHANGELOG.md](CHANGELOG.md) |
| Security | [SECURITY.md](SECURITY.md) |

## License

[GPL-3.0](LICENSE).
