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

Discovery regression coverage lives in `tests/test_discovery_controls.py`: it
executes production JavaScript with deferred scan replies, a controlled clock and
EN/ES/PT catalogs. It covers pause on new addresses, continuation past reviewed
addresses, the ten-minute bound, discarded replies after Stop and relay expansion.
The browser fixture accepts `?preview&discovery&lang=es` for synthetic candidates.
Stopping prevents subsequent scans and ignores a pending scan's result; the
backend probe can still finish before another scan starts. Reconnecting remains
subject to the existing SSH identity and authentication checks.

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

### Commit subjects

Every commit subject, including merge commits, must use `<emoji> <type>: <summary>` with exactly one of the mappings below. Choose the type for the main purpose of the change. Do not invent emojis, replace the assigned emoji or use an emoji from another type. `wip` commits must not reach `main`. Tags and release preparation use `🚀 release`; `📦` is not an allowed commit prefix.

| Prefix | Main change |
| --- | --- |
| ✨ feat | New functionality |
| 🔧 fix | Bug fix |
| 🧹 chore | Maintenance, cleanup or internal tasks |
| 🧪 test | Tests |
| 📚 docs | Documentation |
| ♻️ refactor | Refactoring without behavior changes |
| ⚡ perf | Performance improvements |
| 🎨 style | Formatting, style or lint without functional changes |
| 🔒 security | Security |
| 🔨 build | Build system, packaging or build dependencies |
| 🤖 ci | CI/CD, workflows or GitHub Actions |
| 🚀 release | Releases, tags or version preparation |
| 🔀 merge | Branch merges |
| ⏪ revert | Reverting changes |
| 🗃️ data | Data, fixtures, catalogs or datasets |
| 🧩 config | Configuration |
| 🗑️ remove | Removing code, files or functionality |
| 🚧 wip | Incomplete work; must not reach main |

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

## Remote network and provisioning contracts

Moonraker requests reject HTTP redirects. Configure the final endpoint explicitly;
power cannot be confirmed using a response from a redirected destination. Image
downloads retain their separate HTTP policy.

Prebaked WiFi provisioning uses the pinned MainsailOS headless_nm parser format,
not TOML escaping. Delimiters are chosen without changing the credential value.
Values that its echo/parser cannot preserve are rejected before disk writing and
again during direct injection. The upstream GPLv3 parser fixture records its exact
source revision and hash; Bash is required for its round-trip regression.

SFTP listing failures raise explicit errors and use stable UI codes. Empty
successful listings remain empty lists. Request/session generations still reject
stale results and errors. Collecting backend tests never creates bootstrap.sh;
missing real release inputs must fail rather than be replaced with a mock.

Automatic discovery reads active IPv4 interface prefixes from Windows PowerShell
or Linux ip JSON, with a five-second enumeration bound. It scans only a single
unambiguous subnet and at most 1024 addresses. Missing/ambiguous interfaces,
invalid prefixes or larger networks require manual connection; no /24 guess or
silent truncation is allowed. Stop and rate-limit controls remain in effect.

Critical Imager, Credentials and recovery messages share the existing EN/ES/PT
catalogs. Python owns preflight validation and exposes field/code for presentation;
JavaScript must not infer hardware success. Large Node harness fragments use stdin
to avoid Windows command-line limits. Native/package/hardware checks remain
separate from browser fixtures.
