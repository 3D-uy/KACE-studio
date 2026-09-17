<p align="center">
  <img src="web/KACE-studio-banner.png" width="1000" alt="KACE Studio banner">
</p>

<h1 align="center">KACE Studio</h1>

<p align="center">
  Windows provisioning and management companion for KACE
</p>

<p align="center">
  <a href="https://github.com/3D-uy/KACE-studio/actions/workflows/ci.yml"><img src="https://github.com/3D-uy/KACE-studio/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/release-0.5.0--rc.1-orange" alt="Project status: pre-1.0">
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue" alt="Python 3.11 and 3.12">
  <img src="https://img.shields.io/badge/platform-Windows-0078D4" alt="Windows">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="GPL-3.0 license"></a>
</p>

## Platforms and firmware

<p align="center">
  <img src="https://img.shields.io/badge/Windows-10%20%2F%2011-0078D4?style=for-the-badge" alt="Windows 10 and 11">
  <img src="https://img.shields.io/badge/Raspberry_Pi-provisioning-A22846?style=for-the-badge&amp;logo=raspberrypi&amp;logoColor=white" alt="Raspberry Pi provisioning">
  <img src="https://img.shields.io/badge/Klipper-via_KACE-F2A900?style=for-the-badge" alt="Klipper via KACE">
  <img src="https://img.shields.io/badge/Moonraker-API-2471A3?style=for-the-badge" alt="Moonraker API">
</p>

| Component | Current capability | Boundary |
| --- | --- | --- |
| 🪟 Windows 10/11 | PyWebView desktop, imaging, SSH/SFTP | WebView2 and administrator approval for the isolated disk writer. |
| 🍓 Raspberry Pi OS | Pinned 32/64-bit image provisioning | Exact images and hashes in `image-manifest.json`. |
| 🌐 MainsailOS / dashboards | Attested pre-baked image entries; Mainsail/Fluidd bootstrap selection | An unavailable image family is rejected; dashboard support is not an image attestation. |
| ⚙️ Klipper + KACE | Host provisioning and firmware workflow display | KACE owns firmware decisions; Studio does not authorize MCU flashing. |
| 🧪 Linux | Non-destructive backend tests | No Linux physical writer or packaged desktop release. |

> [!IMPORTANT]
> **0.5.0-rc.1 is a controlled test candidate paired with KACE 0.9.4-rc.2.** Physical qualification remains pending. A local unsigned executable is usable only as an explicitly trusted test artifact with its matching manifest; it does not satisfy the signed-release gate. Follow the [release checklist](RELEASE_CHECKLIST.md) and KACE's [hardware qualification guide](https://github.com/3D-uy/KACE/blob/main/docs/HARDWARE_TESTING.md).

## Overview

KACE Studio is the Windows-first desktop provisioner in the KACE ecosystem. It writes a supported Raspberry Pi operating-system image, injects first-boot configuration, discovers the new host, provides SSH/SFTP management, and launches the pinned KACE provisioning bootstrap.

Studio is not the printer-configuration generator. That responsibility belongs to [KACE](https://github.com/3D-uy/KACE), which runs on the Linux printer host after provisioning.

Writing a raw disk image is destructive. Studio includes target checks and an elevated helper boundary, but the user remains responsible for confirming the selected physical device and preserving any data on it.

## How KACE and KACE Studio work together

The two projects are independent repositories with a deliberately narrow integration boundary:

1. Studio downloads or accepts a raw Raspberry Pi image, validates it, and writes it to the selected device.
2. Studio injects network, credentials, first-boot settings, and `bootstrap.sh` onto the boot partition.
3. After the Pi boots, Studio discovers it and connects over SSH.
4. The Studio UI launches the bootstrap and follows its machine-readable stage and error markers.
5. The bootstrap installs Klipper, Moonraker, the selected web interface, optional Crowsnest support, and KACE, then launches the KACE wizard in the same SSH terminal.
6. Studio reports success only after the wizard exits successfully and the bootstrap verifies the final requested relay configuration.

New Moonraker configurations trust only `127.0.0.1` and `::1`. After installing Moonraker and connecting over SSH, use **Authorize this computer for Moonraker** and confirm the client IP reported by the Pi. This grants persistent access to that individual IP for Studio's HTTP power controls and Mainsail in a browser using the same source IP. SSH login alone does not grant access. A changed DHCP address or another computer needs separate approval; an IP shared through NAT also shares this permission. Existing Moonraker authorization settings are not migrated. Enrollment requires a writable `~/printer_data/config/moonraker.conf` and permission to run `sudo -n systemctl restart moonraker`; included/ambiguous configurations require manual administration. Removing an old permission remains a manual edit of `trusted_clients`.

`release-contract.json` is the single machine-readable source for the immutable KACE bootstrap/installer tuple and the exact packaging toolchain. Studio CI downloads that bootstrap, verifies it before the build, then inspects the finished PyInstaller archive and compares every contract-owned resource byte for byte. Source mode prefers the sibling `KACE/scripts/bootstrap.sh` checkout when both repositories share this workspace; packaged mode resolves only the `_MEIPASS` copy. Resource resolution never depends on the process working directory.

After editing bootstrap, synchronize both local copies and the SHA-256 for source validation. Before fetching or publishing a release, `bootstrap_ref` must point to a published KACE commit containing those exact bytes; a local hash update alone does not update that immutable source reference.

The build also emits `KACE-studio.release.json` next to the executable. It records the Studio commit and dirty state, KACE bootstrap and installed-source refs/hashes, Python/PyInstaller identity, runner image, dependency-lock/spec hashes, every bundled-resource hash, contractual Windows version metadata, Authenticode status, and the final EXE SHA-256. This is an external manifest so hashing it cannot change the executable it identifies. CI fixes `PYTHONHASHSEED` and the PE timestamp to the source commit, builds twice from a clean checkout, and rejects different hashes. A second Windows job rebuilds from the same commit on a fresh runner and emits an image- and commit-bound independent-build attestation only when the executable is byte-identical. Both Windows build jobs also launch the packaged EXE under a hard deadline, load the real PyWebView renderer off-screen, and require the Studio DOM and Python-to-JavaScript bridge before accepting the artifact.

Manual CI exposes a separate `release_candidate` gate. It is fail-closed: publication evidence is produced only when the configured PFX, password, and expected signer-certificate SHA-256 are present; `signtool` signs with SHA-256 and a trusted timestamp; and the packaged bytes, PE metadata, same-runner rebuild, independent rebuild, manifest, signer identity, and timestamp all verify. Ordinary CI artifacts remain explicitly unsigned development outputs. The workflow never publishes a GitHub release.

## Current status

KACE Studio is in active pre-1.0 development. Its backend tests run on Windows and Linux with Python 3.11 and 3.12, and CI builds a Windows executable after the tests pass. Automated tests use mocks and temporary files: they do not write to physical disks or validate a complete printer installation on real hardware.

The versioned release candidate is for controlled qualification. `main` remains mutable, and an automated pass does not establish physical compatibility. See [CHANGELOG.md](CHANGELOG.md) for the stabilization changes.

## Features

- Guided desktop flow built with PyWebView and a local HTML/CSS/JavaScript interface.
- Official image discovery, download, checksum handling, per-user cache reuse under `%LOCALAPPDATA%\KACE Studio\cache`, and atomic `.part` publication.
- Complete ZIP and XZ extraction checks before a raw image becomes flashable.
- Custom-image support limited to uncompressed `.img` files with mandatory externally supplied SHA-256 sidecars. Custom pre-baked images additionally require an image-bound capability attestation.
- Minimum raw-image plausibility and destination-capacity checks.
- Windows disk discovery with system/boot exclusions and allowed-bus filtering.
- A full selected-device identity snapshot passed to the elevated writer for revalidation.
- Reinforced confirmation for higher-risk USB HDD and SSD targets.
- UAC-elevated raw writer isolated from the normal desktop process.
- Boot-partition injection for supported prebuilt, first-boot, and cloud-init paths.
- Local network discovery for SSH and Moonraker endpoints.
- Embedded SSH terminal and SFTP file management.
- Bootstrap progress and failure reporting in the desktop UI.
- Read-only firmware progress, including KACE-owned MCU identity evidence and manual-confirmation states, instructions, native artifact download, and reconnect recovery from KACE's deployment manifest.

## Requirements

### End users

- Windows for physical raw-disk flashing.
- A supported Raspberry Pi and an SD card or other explicitly accepted removable target.
- Administrator approval when the writer helper is launched.
- Network access for image downloads, provisioning dependencies, and GitHub-hosted KACE contracts.
- A local network path from the Windows machine to the newly booted Pi.

### Contributors

- Python 3.11 or 3.12.
- Git.
- Windows 10 or 11 for the desktop application and real writer validation; the packaged EXE uses the OS-provided UCRT/API-set runtime. Linux is supported for non-destructive CI tests.
- PyInstaller when building the executable.

Docker is not required to run Studio. It is used by the KACE repository for configuration and firmware validation.

## Installation

KACE Studio does not currently document a stable binary release channel. Run it from source for development or use a Windows executable produced by a trusted CI run after verifying its provenance.

### Run from source

```powershell
git clone https://github.com/3D-uy/KACE-studio.git
Set-Location KACE-studio
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock
python main.py
```

### Build the Windows executable

```powershell
# Use exactly Python 3.12.10 and a clean, published checkout.
python -m pip install --require-hashes -r requirements.lock
python scripts/release.py fetch-bootstrap
python scripts/release.py verify-remote-installer
python scripts/release.py verify-inputs
$env:PYTHONHASHSEED = '1'
$env:SOURCE_DATE_EPOCH = (git show -s --format=%ct HEAD)
python -m PyInstaller --clean -y main.spec
```

Before building, place the bootstrap file verified against the pinned KACE commit and SHA-256 at `bootstrap.sh`. The CI workflow performs that download and verification automatically. See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for the complete contract check.

## End-to-end workflow

1. Install or launch KACE Studio on Windows.
2. Choose the Raspberry Pi model, operating-system source, target device, network, credentials, and provisioning options.
3. Review the selected disk identity and destructive-operation warning. Higher-risk external HDD/SSD targets require reinforced confirmation.
4. Let Studio resolve a complete raw `.img`, confirm that it fits, and launch the elevated writer.
5. The writer re-queries the selected physical device and rejects changed or incomplete identity before writing.
6. Studio injects first-boot configuration and the verified bootstrap onto the new boot partition.
7. Boot the Raspberry Pi, wait for it to join the network, and discover or enter its address in Studio.
8. Connect through the embedded SSH workspace and start provisioning.
9. The bootstrap launches the interactive KACE wizard in Studio's SSH terminal after installing KACE.
10. Studio enables Finish only after the wizard exits successfully, the final relay configuration is verified, and the bootstrap reports completion. Then follow Klipper's official commissioning checks before operating the printer.

## Architecture

| Area | Responsibility |
| --- | --- |
| `main.py` | PyWebView API, desktop lifecycle, image acquisition, cache orchestration, and forwarding of KACE-owned workflow events |
| `web/` | Local user interface, read-only workflow projections, validation, terminal, and bundled front-end assets |
| `backend/imager.py` | Disk discovery, identity policy, elevated-writer invocation, and boot-partition injection |
| `backend/kace_writer.py` | UAC-elevated physical-device revalidation and raw image writing |
| `backend/discovery.py` | Local-network host and service discovery |
| `backend/ssh_client.py` | SSH terminal, SFTP operations, and size-limited deployment-manifest recovery |
| `backend/sha512_crypt.py` | Salted SHA-512 crypt password hashes for injected Linux account data, implemented by the pinned `libpass` dependency |
| `bootstrap.sh` | Build input copied from a pinned KACE revision |
| `main.spec` | PyInstaller definition, including the verified bootstrap and web assets |
| `tests/` | Unit and regression coverage using disk, PowerShell, process, network, cache, and filesystem mocks |

The application normally runs without elevation. Only the physical writer helper crosses the administrative boundary.

## Technologies

- Python and PyWebView.
- Vanilla HTML, CSS, and JavaScript.
- xterm.js for the embedded terminal.
- Paramiko for SSH/SFTP.
- `libpass` for Linux-compatible `$6$` account-password hashing; the abandoned `pcrypt` package is not used.
- PowerShell and Win32 disk APIs for Windows imaging.
- PyInstaller for the Windows executable.
- Pytest and GitHub Actions for validation and CI.

## Testing and validation

Install both runtime and development dependencies, then run:

```powershell
python -m pip install --require-hashes -r requirements.lock
python -m pytest -v
```

For a Windows source-mode renderer check, run `python main.py --smoke-test`. After building, the release-relevant check is `python scripts/smoke_executable.py dist/KACE-studio.exe --timeout 45`; it is distinct from `--verify-package`, which verifies bundled bytes without initializing PyWebView. A timeout, missing WebView2 runtime, renderer startup failure, DOM mismatch, or missing bridge is a hard failure.

The suite covers the image cache and extraction paths, cancellation cleanup, raw-image checks, disk filtering, device-identity snapshots, elevated-helper revalidation, bootstrap delivery, discovery, SSH behavior, and UI-facing backend contracts. Disk and writer tests are mocked and must never target real hardware.

For bootstrap-sensitive changes, validate both delivery modes:

- Source mode with a sibling `KACE/scripts/bootstrap.sh`.
- Packaged mode with the verified `bootstrap.sh` included by `main.spec`.

## Docker

Studio has no supported Docker runtime image because a container cannot represent its Windows desktop, UAC, and physical-disk workflow. Linux CI installs GTK/WebKit system libraries only to import and exercise non-destructive application paths.

Docker-based Klipper parser matrices and firmware builds live in the [KACE repository](https://github.com/3D-uy/KACE). They validate generated printer artifacts after Studio has provisioned the host; they do not validate the Windows writer itself.

## CI/CD

GitHub Actions currently:

- Runs the Pytest suite on Windows and Ubuntu with Python 3.11 and 3.12.
- Fetches KACE's bootstrap from a fixed commit and rejects a SHA-256 mismatch.
- Builds `KACE-studio.exe` on Windows only after the test matrix passes.
- Includes the verified `bootstrap.sh` and local web assets through `main.spec`.
- Uploads the executable as a CI artifact.

CI does not publish a release or flash physical media. Normal CI builds remain unsigned; the manual release-candidate path can sign only when repository signing secrets and the expected certificate identity are configured.

## Compatibility and limits

- The local `/api/sftp/list` endpoint requires the current PyWebView session token
  in `X-Pywebview-Token`. The renderer receives this token through the native bridge;
  it is not embedded in static assets or query strings. Listings are not cached.
- XZ extraction bounds expanded output to 4 MiB per call and decoder memory to
  256 MiB, checking cancellation between output blocks. It requires one complete
  stream, permits valid XZ zero padding, and rejects extra streams/trailing data
  rather than silently publishing a truncated image. Dictionary requirements above
  the memory limit fail before publication; use a smaller XZ dictionary or raw IMG.
- A locally rebuilt executable needs a new release manifest. Older manifests and
  independent-build attestations must stay with their original executable; they
  do not attest a rebuild from a modified worktree. Local validation does not
  replace the signing and independent-builder release gates described above.

- Physical imaging is Windows-only.
- Automated official-image paths handle the ZIP/XZ formats implemented by the acquisition pipeline.
- Manually selected custom images must already be raw `.img` files; compressed custom files are rejected before the writer.
- Every custom image must be accompanied by an external `<image>.img.sha256` or `<image>.sha256` file containing its expected SHA-256. Studio never creates or infers this trust input from the selected image; a missing, malformed, conflicting, or mismatched checksum stops before any block write.
- A custom image classified as pre-baked must also have `<image>.img.kace-attestation.json`. The `kace-studio-prebaked-attestation/v1` document binds `image_sha256` to the selected raw image and declares a supported `family`, `version`, full `source_commit`, required systemd `services`, and provisioning `capabilities`. Automatic pre-baked entries carry the same attestation in `image-manifest.json`, additionally bind the pinned archive checksum, and are checked against the upstream raw-image checksum before flashing. Missing, incompatible, or mismatched attestations stop before any block write.
- The current removable-target policy accepts supported USB, SD, and MMC paths after system/boot and identity checks. External USB HDD/SSD devices remain high-risk even with reinforced confirmation.
- Network discovery depends on local routing, firewall rules, SSH availability, and Moonraker port visibility.
- The complete SSH trust/connect/retry transaction has a 30-second wall-clock deadline. Set `KACE_STUDIO_SSH_CONNECT_DEADLINE_S` to a positive finite number of seconds to override it; invalid values fail before a network attempt.
- Studio can provision the dashboard choices implemented by the KACE bootstrap. Upstream images and installers can change independently.
- Automated tests do not prove electrical, storage, or printer safety on real hardware.

## Roadmap

Before 1.0, the project should prioritize signed and reproducible Windows artifacts, physical-device qualification, end-to-end provisioning evidence, explicit compatibility records, and continuous verification of the cross-repository bootstrap contract. After 1.0, work should focus on upgrade stability, diagnostics, accessibility, and broader verified platform coverage. Additional provisioning features belong in a later roadmap only when they have ownership and automated coverage.

Roadmap items are intentions, not shipped features.

## Contributing

Open an issue before making a broad workflow or writer change. Keep changes narrow, preserve the unelevated/elevated boundary, add non-destructive regression coverage, and document any change to the bootstrap markers or packaged data contract.

Issues and pull requests are managed in the [KACE Studio repository](https://github.com/3D-uy/KACE-studio).

## License

KACE Studio is licensed under the [GNU General Public License v3.0](LICENSE).

## Versioned runtime and recovery contract

`release-contract.json` binds the installed KACE runtime and installer to the same
immutable commit, and binds bootstrap delivery to a separate immutable commit
containing that runtime pin. Every required runtime file is verified against its
committed hash. `runtime_status: pinned` permits packaging only after this binding
is finalized; a future `pending_commit` state deliberately blocks delivery,
launch and packaging until the next candidate is committed and verified.

Source mode checks the sibling bootstrap; packaged mode checks the bundled copy.
Remote launch also checks its bytes, so media prepared by an older Studio cannot
silently launch a different bootstrap. Preserve the EXE and its own external
manifest together; a manifest from another commit does not attest a rebuild.

KACE may stop with a reviewed configuration proposal when a transport cannot
atomically protect existing files from concurrent edits. It may also require
manual recovery with preserved snapshots. Studio reports these states rather
than treating transferred files, media preparation or an SSH reconnect as success.

Raw disk writes open the selected device interface and validate the number,
capacity, bus and serial on the same Windows handle used for writing. Missing or
conflicting evidence blocks writing. Number-based offline/online commands are no
longer used. SFTP listings/downloads carry the SSH generation; downloads publish
via an atomic replacement only after transfer completion.

Image resolution carries the approved raw-image SHA-256 and size through to the
elevated writer. Windows denies modifications to the source handle while it is
verified and copied. Each flash operation uses its own status path and identity;
completion requires both its matching report and a successful helper exit.
The bootstrap copy preserves the exact approved bytes, including its shebang.
Firmware artifact downloads also retain the originating SSH generation, and
reconnect recovery discards replies from superseded sessions.

Disk volume locking/dismounting occurs only after the opened physical handle and
each opened volume's device number/storage descriptor match the authorized disk.
Writes remain disabled until that protection completes; constructor or identity
failure closes handles without dismounting unverified volumes.

Safe eject distinguishes a successful empty disk enumeration from an inspection
error. Query failures, identity changes, and online disks with no drive letters
cannot produce a safe-removal result. The fallback operates on the inspected disk
object and requires verified offline state. These paths are tested with simulated
Win32 devices and the real PowerShell script against fake storage providers.
