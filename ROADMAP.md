# KACE Studio — Roadmap

🌐 [English](ROADMAP.md) · [Español](docs/es/ROADMAP.md) · [Português](docs/pt/ROADMAP.md)

Priorities for this source tree, not release dates or a claim of completed qualification. Release documents remain authoritative for gates; this roadmap does not change versions, hashes, firmware targets or pins.

## 📍 Available in source

Guided imaging, first-boot provisioning, discovery, SSH/SFTP, bootstrap progress and KACE checkpoint recovery exist. Python remains authoritative; the frontend must preserve validation, operation identities and terminal outcomes.

Source remediation now adds explicit remote discovery of configuration operations after reopening, identity-bound SFTP publication, bounded Moonraker requests and reviewed normal power-off with a separate Klipper emergency-stop action. Reconnection never resumes automatically; emergency stop is not electrical isolation. EN/ES/PT persistent-IP authorization is aligned. These local source changes do not qualify native WebView2, the historical EXE, first boot or physical power behavior. Clean-runner, immutable-package and manual E2E gates remain pending.

## 🧭 Priorities

Crowsnest help now states that an unchecked option preserves an existing installation. Verify that explanation in EN/ES/PT and native WebView2, and exercise both a clean image and one with Crowsnest already present during controlled first-boot qualification; this source change does not qualify a package.

| Priority | Required result | Reference |
| --- | --- | --- |
| 1 · Interface validation | Verify Imager selectors/icons, keyboard and focus, credentials, persistent SFTP and recovery states in source and native WebView2. | [Development](docs/DEVELOPMENT.md) |
| 2 · Source and package evidence | Reconcile the supported OS/Python test matrix; later validate source/bundled bootstrap, exact web assets and packaged renderer smoke. | [Release checklist](RELEASE_CHECKLIST.md) |
| 3 · Controlled qualification | Exercise real target identity, elevation, readback, eject, first boot, SSH/SFTP and KACE completion under operator control. | [Provisioning](docs/IMAGE_PROVISIONING.md) |
| 4 · Distribution and maintenance | Distribute the current candidate without Authenticode, retaining its SHA-256, release manifest, independent rebuild attestation and exact source commit. Defer signing to a future signed release while preserving its gate. Maintain language parity and regression evidence. | [Release checklist](RELEASE_CHECKLIST.md) |

## Scope boundaries

Linux test coverage is not Linux desktop/writer support. New firmware targets and printer safety decisions belong to KACE. Additional platforms or workflows require separate scope and validation.

[Back to README](README.md)

Official resume and configuration launch actions now refuse to import or run KACE during a runtime update or while its transaction journal remains unresolved. After an interrupted update, complete recovery with the verified installer before resuming. This source guard does not qualify the historical executable.
