# KACE Studio development

[README](../README.md) · [Roadmap](../ROADMAP.md) · [Release checklist](../RELEASE_CHECKLIST.md)

## 🧭 Architecture and authority

| Layer | Responsibility |
| --- | --- |
| [main.py](../main.py) | PyWebView bridge, session/operation coordination and backend API |
| [backend/imager.py](../backend/imager.py), [kace_writer.py](../backend/kace_writer.py) | Image preparation and separately elevated Windows disk writing |
| [image-manifest.json](../image-manifest.json), [backend/image_manifest.py](../backend/image_manifest.py) | Image identity and supported image inputs |
| [backend/provisioning.py](../backend/provisioning.py), [prebaked_preflight.py](../backend/prebaked_preflight.py) | First-boot injection and capability validation |
| [backend/discovery.py](../backend/discovery.py), [ssh_client.py](../backend/ssh_client.py) | Discovery, host-key validation and SSH/SFTP |
| [backend/bootstrap_events.py](../backend/bootstrap_events.py), [workflow_events.py](../backend/workflow_events.py) | Validated progress and outcome interpretation |
| [web/](../web/) | HTML/CSS/JavaScript presentation, accessibility and interaction |
| [backend/resources.py](../backend/resources.py), [main.spec](../main.spec) | Source/frozen resource resolution and package contents |

Python owns image validation, device/session identities, gates and operations.
JavaScript presents backend outcomes and UI state; it must not duplicate KACE
hardware decisions or infer installation success from SSH reconnection. KACE on
the Pi remains the printer configuration and installation authority.

Disk approval, writing, readback, injection and eject retain the same operation
identity. SFTP results/downloads remain bound to their originating SSH generation.
Preserve gates, receipts, validations and rollback; terminal failures and manual
recovery must not become success states. Credentials must not enter logs/fixtures.

## 🛠️ Source environment and tests

Use Windows 10/11, WebView2 and Python 3.11/3.12 for source development. From the
repository root, inside the virtual environment described in the README:

```powershell
python -m pip install --require-hashes -r requirements.lock
python -m pytest -q
python scripts/check_portability.py
```

Install Node for frontend harness coverage and PowerShell for platform-script
checks. Linux pytest coverage does not qualify the Windows writer or GUI. Inspect
skips and failures explicitly; do not equate collection or a partial run to a
passed release gate. [CI](../.github/workflows/ci.yml) defines the supported matrix.

Start with the narrowest affected suites, for example:

```powershell
python -m pytest tests/test_sftp_frontend_identity.py tests/test_power_session_identity.py -q
python -m pytest tests/test_image_manifest.py tests/test_imager_persistence.py -q
python -m pytest tests/test_bootstrap_frontend_session.py tests/test_robin_download.py -q
node --check web/app.js
```

Choose files by behavior; these examples do not replace the full suite. Tests must
use temporary files, mocks and simulated endpoints rather than real storage or
printers. Keep dependency intent in `requirements.in` / `requirements-dev.txt`
and synchronize the hashed `requirements.lock` when dependencies intentionally
change. Release toolchain requirements remain in `release-contract.json`.

## Frontend review

Use the local browser fixture only as a simulated UI, never as hardware evidence.
Its [runner](../tests/ux_browser_fixture.py) exposes fixture/preview modes; preview
credentials and disks are synthetic. Test both success and rejected/pending states.

- Open every Imager selector, including dynamic drives/timezones; verify icons,
  selection, disabled states, labels, validation messages and keyboard focus.
- Check Credentials, Discovery and SSH navigation; SFTP must remain available for
  the current connected session, with usable height and disconnected/error states.
- Exercise narrow and wide windows, scrolling, zoom, Escape/Tab and keyboard
  selection. Confirm overlays stay within the viewport and console errors are absent.
- Review EN/ES/PT visible messages, failure/recovery actions and completion states.
- Run `python main.py --smoke-test` on Windows for the native source renderer.
  Browser screenshots cannot replace this or the packaged renderer smoke gate.

## Bootstrap, source and packaged behavior

Source mode prefers the sibling `KACE/scripts/bootstrap.sh`; packaged mode uses
the bundled bootstrap. Resource lookup must be independent of the working
directory. The bootstrap's stage/error markers are an integration contract with
`web/app.js`; preserve their backend interpretation and final checkpoint checks.
See [provisioning](IMAGE_PROVISIONING.md) for image, dashboard and recovery policy.

[release-contract.json](../release-contract.json) owns the immutable KACE runtime,
installer, bootstrap and build identities. A source change does not update a
previous EXE or prove those identities match. Follow the complete release
checklist for a separately authorized build/publication; never weaken release
checks to make a dirty or unpinned source tree look publishable.

## Contribution and documentation

Keep changes scoped and add meaningful defect coverage before broader regression.
Use a branch and pull request; preserve required CI checks and the protected-main
workflow. Keep personal paths, secrets and generated reports out of tracked files.
Report vulnerabilities through [Security](../SECURITY.md).

Root README/ROADMAP are English; `docs/es/` and `docs/pt/` are equivalent localized
entry points. Update all three when user steps, limits or priorities change.
Detailed engineering procedures have one canonical English source linked from
each language, avoiding three drifting copies of release/security contracts.

## Remote-session and integration boundaries

SFTP listings and downloads belong to the originating SSH connection.
An SSH reconnection does not confirm KACE installation success: pending
checkpoints still require KACE verification. Moonraker client authorization,
when needed, stores permission for this computer's IP only after confirmation;
removing an obsolete authorized address is currently a manual operation.

The source-mode bootstrap preference and packaged bootstrap identity remain
as documented above. GUI screenshots illustrate the interface; they do not
establish successful storage writing, Pi first boot or printer qualification.
