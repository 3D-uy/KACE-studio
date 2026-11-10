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

The writer publishes `kace-studio-readback/v1` evidence with the operation ID,
disk identity, image digest/size and observed `verified` or `skipped` outcome.
Imager validates it against the accepted write contract; the worker validates it
again and emits `FLASHED` only after successful boot injection and a current,
uncancelled operation. Missing or incompatible evidence fails. The frontend
correlates the accepted operation/disk and presents the backend outcome, without
inferring verification from preferences. Worker errors and rejected-start
callbacks carry that operation's context; an independent SSH error cannot
invalidate its completion. Eject replies
are discarded after a newer request or flash starts, including on the same disk.
Flash and eject share the existing media-operation lock. Eject remains reserved
until the native helper returns, even if its modal is closed; another flash or
eject is rejected while either operation is active. Exceptions release the guard.

Checkpoint parsing is read-only and validates the v1 wire structure. Artifact
evidence is required from `ARTIFACT_READY`; from `MCU_VERIFIED`, both KACE and
Studio require a saved non-empty verified serial path and positive integer flash
evidence timestamp. Studio also validates projection types, board identity and
internal artifact/build/transformation references. It does not access Pi paths
through the Windows filesystem or repeat physical verification of historical
`COMPLETE`. MCU model aliases, BoardContracts and physical receipts remain KACE
authority. The producer-generated corpus in `tests/fixtures/firmware_checkpoints.json`
includes all states, a historical v1 checkpoint, a legitimate MCU alias and a
Robin transformation. Legacy baseline identity, VID:PID lists and build firmware
identity may be absent; recorded references must remain consistent. Regenerate
it using `tests/fixtures/generate_firmware_checkpoints.py` with KACE's development
Python and the producer checkout path after an intentional contract change.

The v1 `deployment-manifest.json` fallback describes firmware delivery only.
Its Python reader validates schema types, workflow/deployment identity, sequence,
the six persisted delivery states and presentation/download fields. If a result
is present, its status must agree with the recorded state. Missing state or
sequence is rejected, never inferred from automation flags. `DONE`, `COMPLETE`
and configuration-installation states are invalid for this fallback, including
when the durable checkpoint is missing or rejected. Invalid input is ignored
without replacing valid progress. The frontend also rejects installation success
from manifest origin; `FLASHED` remains delivery progress pending MCU/configuration
verification. Valid checkpoints and supported legacy authoritative events retain
their completion semantics, session guards and priority over manifests.
Original v1 profile, transformation and firmware-identity fields remain optional:
Studio does not repeat KACE's hardware verification or open Pi paths locally.
`tests/fixtures/deployment_manifests.json` contains current Robin and original
v1 wire serializers/writer output using synthetic artifacts and result states.
Its generator accepts the KACE checkout path using KACE's development Python;
normal Studio tests need no sibling checkout. Run `test_deployment_manifest.py`
with the existing workflow/checkpoint/frontend and download suites. Source checks
do not update an existing packaged EXE or bootstrap/runtime pin.

Critical reconnect, readback, eject and accessible service-help labels use the
existing EN/ES/PT catalog. Visible modal/eject labels update when language changes;
terminal history remains unchanged. The explicit Imager Next action requests
focus in Credentials, while sidebar, validation and background updates retain
their own focus behavior. Regressions: `test_readback_outcome.py`,
`test_readback_frontend.py`, `test_firmware_checkpoint.py`,
`test_recovery_localization.py` and `test_navigation_focus.py`.

The SSH workspace keeps the current installation phase and next action visible;
its stage history and technical details are collapsed by default. Shell EOF with
a live authenticated transport closes only the terminal presentation, without
starting reconnection or invalidating remote power authority. Transport loss
invalidates authority and pending responses while retaining the selected host,
so recovery can bind a new session to that selection. Power is shown as unknown
while disconnected, not as an observed Moonraker error. A recovered DEPLOYING
checkpoint remains pending until KACE resumes and verifies installation. Automatic
SSH recovery has at most three attempts, separated by 2, 10 and 20 seconds;
the waits check cancellation every 250 ms. Once SSH returns, the terminal is usable
immediately. For a validated DEPLOYING checkpoint without a recorded error,
Studio observes that same workflow for a 45-second polling window (every 3 seconds)
before requesting manual continuation. Reads retain the existing bounded SFTP
timeouts; an in-flight read can finish after the polling window. Only a newer,
validated KACE checkpoint can report completion. Changed selections, sessions or
workflow identities cancel observation. Studio does not launch KACE, restart
Klipper or advance the checkpoint. Recovery preserves previously observed workflow
history, without replacing it with an older initial snapshot. If the first read
is unavailable, a later validated watcher checkpoint can rebind that same saved
sequence to the recovered session and restore pending-verification guidance.
Exercise these boundaries
with `test_workspace_recovery_ux.py`, `test_power_frontend_identity.py`,
`test_power_session_identity.py` and `test_ssh_recovery.py`. Compact layout needs
native WebView2 and packaged-renderer validation in addition to JavaScript tests.

The compact tracker puts its title and current stage on one wrapping row and
omits the repeated terminal instruction during ordinary progress. Recovery,
connection-loss and completion guidance remain visible. When KACE emits
`DISCARDED` after archiving a checkpoint for an explicitly confirmed new flow,
Studio hides only that workflow's panel and disables its artifact download. Its
view and cursor remain in memory, so delayed checkpoint/manifest replies cannot
revive the archived installation. No terminal wording or reconnect infers this
transition. A different validated workflow starts with its history collapsed;
updates to the same workflow preserve the operator's disclosure choice.
Studio starts its existing authenticated terminal as
`env KACE_MACHINE_OUTPUT=1 bash`, opting manual `kace` commands into the existing
event stream without changing the remote shell profile or relying on sshd
`AcceptEnv`. The backend parses those events before the existing display filter
removes protocol markers from visible terminal output. This environment setting
is limited to that shell and does not enable automatic hardware actions.

Bootstrap launch captures the authenticated SSH session before preflight and
revalidates its existing host/selection/session context at command delivery.
Terminal, bootstrap, interruption and disconnect callbacks retain that identity
through buffering and presentation. Initial visible output or shell EOF that
precedes a login/reconnect reply is held until its authenticated generation is
known; failed or superseded logins discard it. Session boundaries clear partial legacy markers.
Recovery from manual login uses the same pending-verification guidance as automatic
reconnection. A validated firmware workflow event/checkpoint takes precedence over
the deployment-manifest fallback regardless of reply order or their separate IDs.
Manifest sequence numbers never suppress the authoritative checkpoint. Cover
these races with `test_operation_session_guards.py` and the production-JavaScript
scenarios in `test_bootstrap_frontend_session.py`.

## 🛠️ Source environment and tests

### PC-hosted Klipper preflight

`backend/klipper_preflight.py` prepares Docker on the PC before `start_bootstrap`
sends a launch command. The Pi does not need Docker for this Studio route. Keep
Docker Desktop's Linux engine running. A missing image is built explicitly at
launch; preparation failures stop the launch and never become CFG rejection.
The image receipt lives in the application cache, outside source/bundled assets.

`backend/studio_preflight_bridge.py` uses the authenticated SSH transport to
request a loopback reverse forward on the Pi; it does not open a PC LAN listener.
`SSHSession.start_preflight` delivers a random session capability through a
non-PTY channel into a private mode-0600 temporary file. The launch subshell
sources and removes it, without putting its token in terminal text/history.
The bootstrap explicitly preserves the three fields when switching to the
printer user, using a bounded three-line stdin frame and a non-secret presence
flag; capability values are not passed in sudo/env command-line arguments.
Absent Studio context preserves the standalone input path. Invalid or truncated
context stops the launch, while partial context remains fail-closed in Core.
Disconnect/replacement revokes the bridge; a temporary file left
by a disconnect holds a revoked token and grants no approval. The server's SSH
forwarding policy must allow the loopback request. Requests are bounded to 128
files/16 MiB decoded and one validation at a time; unsupported staging paths or
transport limits are unverified, not Klipper incompatibility.

Both peers bind each result to the session, nonce, effective-tree SHA-256, pin,
and engine/recipe/worker hashes. Replay, mismatch, Docker failure, timeout or
disconnect blocks publication. No disconnected/manual-shell fallback bypasses
the KACE gate. Actual writes, review and firmware boundaries remain in KACE.

The runner in `KACE/core/klipper_preflight_engine.py` is authoritative. Run
`python scripts/sync_preflight.py` to copy canonical LF engine/recipe/worker
resources into `preflight/`, or `--check` to detect drift. The manifest and all
three resources are verified in source and frozen builds. This is an explicitly
bundled resource contract, not a shared installed package. Update immutable
runtime/bootstrap release pins before distributing a new executable; an old
runtime does not implement this protocol.

Focused tests: `tests/test_studio_preflight*.py`. The opt-in integration
`test_studio_preflight_integration.py` requires a sibling KACE checkout,
`KACE_STUDIO_PREFLIGHT_INTEGRATION=1` and `KACE_STUDIO_PREFLIGHT_RECEIPT` pointing
to an explicitly provisioned PC receipt. It uses the real Docker loader and a
simulated forwarded transport; native Pi/Studio qualification is separate.

This preserves the bounded loader proof (object load and fan references). It
does not attest destination extensions, another Klipper version, MCU, wiring,
movement, heaters or physical safety.

The Crowsnest Imager checkbox controls whether bootstrap installs it. An unchecked
option preserves an installation or directory already present in the chosen image;
it does not remove or disable a service. EN/ES/PT help must describe this behavior.
KACE separately reports files and observed service status on the Pi. Verify help
and first-boot behavior without inferring package or physical qualification from
source tests, preserving both source and bundled bootstrap delivery contracts.

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

The packaged smoke also creates the default TLS context before opening WebView2.
The standard urllib opener initializes HTTPS support even for Moonraker HTTP, so
a missing TLS runtime resource can otherwise leave a rendered application unable
to query printer power. Build with the Python version and environment specified
by `release-contract.json`; a development interpreter with external certificate
dependencies is not an interchangeable packaging input. This check is offline and
does not replace network, certificate-chain or target-device verification.
`main.spec` checks the pinned Python and PyInstaller versions before source
analysis, using the same verifier as the release manifest. Source development
retains its supported Python matrix; the packaging gate applies only to builds.
Regressions: `test_packaging_toolchain.py` and `test_executable_smoke_contract.py`.

Start with the narrowest affected suites, for example:

```powershell
python -m pytest tests/test_sftp_frontend_identity.py tests/test_power_session_identity.py -q
python -m pytest tests/test_image_manifest.py tests/test_imager_persistence.py -q
python -m pytest tests/test_bootstrap_frontend_session.py tests/test_robin_download.py -q
node --check web/app.js
```

Choose files by behavior; these examples do not replace the full suite. Tests must
use temporary files, mocks and simulated endpoints rather than real storage or
printers. Keep dependency intent in `requirements.txt` / `requirements-dev.txt`
and synchronize the hashed `requirements.lock` when dependencies intentionally
change. Release toolchain requirements remain in `release-contract.json`.

The opt-in `isolated_boot_refresh` fixture in `tests/conftest.py` applies only
to three injection-content tests in `test_backend.py` and the nine upstream WiFi
round trips in `test_headless_wifi.py`. It replaces imager's local time/subprocess
bindings and verifies the exact wait/refresh calls in teardown. Password hashing,
atomic publication, bootstrap checks and the real Bash consumer still run.
Other injection and platform tests retain their existing behavior. Do not broaden
this fixture to storage, timing or process-contract tests without checking their
assertions; it does not change the source, package or release gates.

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

Use `?preview&startup&lang=es` to review the disconnected opening screen. Check
1050x665 (the initial window's content area), 900x565 and 840x532 viewports in
EN/ES/PT: above 950px Imager pairs model/hostname, dashboard/architecture and
image/storage in equal-width columns, with timezone below on the left. At 950px
and below these settings stack in the same DOM and keyboard order. Keep uniform
32px gaps, Next visible in short windows and avoid
horizontal scrolling. Verify the timezone dropdown
above the sticky action row and keyboard access after vertical scrolling.

The flag button above the theme button cycles Español → English → Português.
Language selection uses the existing catalogs, updates accessible names and
illustrated selectors in place, and persists through the safe preferences
allowlist plus a local startup cache. It must preserve form values, the active tab
and SSH/discovery state. Backend language writes wait until initial preferences
have loaded, so an early click cannot replace saved settings with defaults.
Flags are local SVG assets under `web/flags/` and fill
the circular button, retaining a visible keyboard focus outline.
Run `tests/test_language_switch.py`, `tests/test_preferences_safety.py` and the
existing localization/frontend suites when changing this behavior.

Discovery regression coverage lives in `tests/test_discovery_controls.py`: it
executes production JavaScript with deferred scan replies, a controlled clock and
EN/ES/PT catalogs. It covers pause on new addresses, continuation past reviewed
addresses, the ten-minute bound, discarded replies after Stop and relay expansion.
The browser fixture accepts `?preview&discovery&lang=es` for synthetic candidates.
Stopping prevents subsequent scans and ignores a pending scan's result; the
backend probe can still finish before another scan starts. Reconnecting remains
subject to the existing SSH identity and authentication checks.

On Windows, discovery prefers the single subnet whose active interface has a
default IPv4 route when several networks are present. This prevents WSL/Hyper-V
networks without a default route from blocking the LAN scan. If no unique subnet
can be selected, it requires a manual address. The actual interface prefix and
1024-address scan limit still apply; discovery never falls back to scanning all
interfaces or guessing a /24. Tests in `tests/test_discovery_prefixes.py` cover
route selection and ambiguous/oversized networks without network probes.

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

Printer homing and custom-probe choices belong to the pinned KACE runtime.
KACE offers automatic/minimum/maximum homing per axis and keeps the automatic
`homing_positive_dir` example commented. Its custom-probe selector uses the
effective board sockets and selected drivers, prioritizes declared probe or
replaced Z inputs and preserves pin collisions and firmware reservations.
Studio must not duplicate these decisions. Runtime changes require the
bootstrap installer ref, `release-contract.json` runtime hashes and bundled
bootstrap to be updated together, followed by a new packaged build; source-mode
checks do not qualify a previous EXE. Keep the runtime commit separate from the
subsequent bootstrap pin commit to avoid a self-referential source hash.

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

SSH first-use trust rejects an unknown host key before authentication and presents
its SHA-256 fingerprint for explicit confirmation. Approval is short-lived, single-use
and bound to the host, key, selection and attempt; login asks for credentials again.
Paramiko rechecks the exact approved key before authentication and stages it only in
its in-memory key set. Automatic recovery cannot approve an unknown identity.
After authentication and the cancellation check, the existing locked writer
merges and atomically publishes `known_hosts`. A temporary-write or replace
failure preserves the previous file and fails the connection; known-key
mismatches remain rejected. The atomic replacement is the publication boundary:
a later cancellation or permission error does not roll back published trust.
Tests in `test_known_hosts_safety.py` execute Paramiko's real host-key policy with
a simulated transport and temporary trust files.

SFTP listings and downloads belong to the originating SSH connection.
The workflow header also offers **Download configuration** after KACE publishes
the generated-configuration stage. It saves one ZIP containing generated
`~/kace/printer.cfg` and `~/kace/macros.cfg` only when the validated checkpoint's
`macros_generated` choice is explicitly true. A leftover macros file is ignored.
This is an export, not the effective installed configuration or a completion claim.
The bridge accepts the originating workflow ID, sequence and SSH generation,
selects fixed home-relative paths, bounds file reads, and rereads the checkpoint
and selected files after the native save dialog. Changed/missing files, changed
session/flow, cancellation and failed local publication preserve an existing
download. The ZIP is staged locally and atomically replaces the chosen target;
temporary files are cleaned. Disconnect/discard disables the action and stale
callbacks cannot restore it. Cover with `tests/test_configuration_download.py`
and the production JavaScript session harness. Native save-dialog/WebView2 and
packaged-EXE qualification remain separate from Docker tests.

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

## Recoverable SSH loss

For a controlled E2E, set `KACE_STUDIO_SSH_DIAGNOSTICS` to an absolute local
JSONL filename before launching Studio. This opt-in log records SSH session IDs,
wall/monotonic timestamps, lifecycle/heartbeat events and the failing stream phase,
exception class, OS error number and traceback locations. It excludes terminal
input/output, credentials, exception messages and frame locals. Rotation retains
one 2 MiB active file and three backups. Storage failures do not affect SSH.
Correlate this evidence with a separately captured Pi journal and boot ID; a client
timeout alone cannot establish why the host stopped responding. Diagnostic logging
does not alter existing deadlines or resume installation. Tests:
`tests/test_ssh_diagnostics.py` and `tests/test_ssh_stream_liveness.py`.

A recoverable disconnect invalidates SSH/SFTP and power request identities while
retaining the displayed workflow as historical progress. It disables installation,
downloads and power controls and displays Disconnected. The existing reconnect
selection remains valid, but the new session must supply its own checkpoint and
authority. Delayed replies from the lost session cannot mark completion or restore
controls. `tests/test_bootstrap_frontend_session.py` exercises the actual app.js
through disconnect, delayed replies and reconnect. Bootstrap supplies bounded
persistent Linux diagnostics; source and packaged delivery retain the same contract.

Connection readiness is published only after the remote-configuration probe
returns with an active SSH transport. An SFTP channel-open timeout discards its
pending transport and must fail login instead of returning success or starting
a terminal on a closed client. An isolated SFTP failure may still leave SSH usable.
Exhausted SSH retries preserve their final exception for the sanitized UI error.
Coverage: `tests/test_sftp_timeouts.py` and `tests/test_ssh_deadline.py`.

Terminal sessions probe an idle connection every ten seconds by opening and
closing an SSH channel with a five-second response deadline. This executes no
remote command. A refusal counts as a server reply. One timed-out probe leaves an
otherwise active transport and its interactive KACE process intact; probes remain
bounded, and 45 seconds without any server reply closes the transport and invokes
the existing reconnect worker. An inactive transport still disconnects immediately.
This grace applies only to idle terminal liveness, not login, SFTP or completion.
Terminal setup also
has a five-second channel/PTY/command-response deadline. Post-bootstrap SSH loss
uses the same disconnected controls and preserved workflow as bootstrap loss.
Failed automatic retries preserve this pending view instead of clearing it through
the generic login-error path. Manual login failures retain their existing error UI.
Coverage: `tests/test_ssh_stream_liveness.py`, `tests/test_power_session_identity.py`
and `tests/test_bootstrap_frontend_session.py`. These simulated failures do not
establish the cause of a Raspberry Pi kernel lockup or qualify printer hardware.
