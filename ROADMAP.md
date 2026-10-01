# KACE Studio — Roadmap

🌐 [English](ROADMAP.md) · [Español](docs/es/ROADMAP.md) · [Português](docs/pt/ROADMAP.md)

Priorities for this source tree, not release dates or a claim of completed qualification. Release documents remain authoritative for gates; this roadmap does not change versions, hashes, firmware targets or pins.

## 📍 Available in source

Guided imaging, first-boot provisioning, discovery, SSH/SFTP, bootstrap progress and KACE checkpoint recovery exist. Python remains authoritative; the frontend must preserve validation, operation identities and terminal outcomes.

## 🧭 Priorities

| Priority | Required result | Reference |
| --- | --- | --- |
| 1 · Interface validation | Verify Imager selectors/icons, keyboard and focus, credentials, persistent SFTP and recovery states in source and native WebView2. | [Development](docs/DEVELOPMENT.md) |
| 2 · Source and package evidence | Reconcile the supported OS/Python test matrix; later validate source/bundled bootstrap, exact web assets and packaged renderer smoke. | [Release checklist](RELEASE_CHECKLIST.md) |
| 3 · Controlled qualification | Exercise real target identity, elevation, readback, eject, first boot, SSH/SFTP and KACE completion under operator control. | [Provisioning](docs/IMAGE_PROVISIONING.md) |
| 4 · Distribution and maintenance | Distribute the current candidate without Authenticode, retaining its SHA-256, release manifest, independent rebuild attestation and exact source commit. Defer signing to a future signed release while preserving its gate. Maintain language parity and regression evidence. | [Release checklist](RELEASE_CHECKLIST.md) |

## Scope boundaries

Linux test coverage is not Linux desktop/writer support. New firmware targets and printer safety decisions belong to KACE. Additional platforms or workflows require separate scope and validation.

[Back to README](README.md)
