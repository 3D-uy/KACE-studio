# Verified image provisioning

[README](../README.md) · [Development](DEVELOPMENT.md) · [Release checklist](../RELEASE_CHECKLIST.md)

## 🧭 Image and dashboard authority

The [image manifest](../image-manifest.json) owns image URLs, archive/raw hashes,
architecture and pre-baked attestations. [The bootstrap](../bootstrap.sh) and
[release contract](../release-contract.json) bind provisioning inputs. Do not
copy their version/hash values into a second operational authority.

Studio offers Fluidd on the verified MainsailOS base and installs the pinned
Fluidd dashboard during provisioning. It does not use the archived FluiddPI image
or claim Fluidd is already installed in that base. Dashboard choice does not
qualify the printer configuration or firmware.

## Existing contracts

- `fluidd_prebaked` selects the same URL, archive/raw SHA-256, reviewed source,
  services and capabilities as `mainsailos_prebaked` for the selected architecture.
  `fluiddpi_prebaked` is no longer accepted. There is no live-latest fallback.
- Both profiles use the existing resolver, downloader, decompressor, raw-image
  rehash, attestation gate and writer. Cache provenance names the attested base
  family, so switching dashboard does not download or decompress identical bytes.
- Boot injection still writes `PREBAKED=true` and `DASHBOARD=fluidd`.
  `PREBAKED` means the core Klipper/Moonraker stack is installed; it does not
  imply that every requested dashboard is installed. The bootstrap reuses its
  verified Fluidd installer and client configuration helper.
- Fluidd uses the existing single-dashboard Nginx configuration on port 80,
  including Moonraker HTTP/WebSocket proxying. The known MainsailOS site symlink
  is disabled without deleting its vendor configuration and restored when
  `nginx -t` fails. An unexpected site is rejected. Nginx restart/readiness
  failures stop provisioning. Mainsail-only keeps the vendor site; Both uses
  the preinstalled Mainsail directory on port 80 and Fluidd on port 81.
- Raspberry Pi OS Lite retains all three dashboard choices and installs the
  requested stack through its existing vanilla path.
- The authoritative bootstrap remains `KACE/scripts/bootstrap.sh`. Source
  mode prefers it; frozen mode uses the identical bundled copy. The release
  contract pins its commit and SHA-256 independently of the unchanged installer
  and KACE runtime revision.

## Validation boundary

Automated tests exercise profile selection, manifest/attestation rejection,
shared cache reuse, boot injection, frontend bridge arguments and shell
provisioning with sandboxed downloads/services. They do not prove a physical
SD write, Pi boot or printer readiness. Klipper requires the normal printer/MCU
configuration workflow before it can be ready to print.

Safe eject compares the same hardware identity fields in Python and PowerShell:
disk number, model, serial, path, capacity, bus type, and system/boot flags.
The content-derived UniqueId is excluded from eject comparisons because writing
an image can change it. A confirmed native eject ends the operation immediately.

## SSH recovery after installation

An unexpected SSH transport loss starts at most three reconnect attempts, using the session credentials in backend memory and the normal host-key validation. Manual disconnect or a newer device selection cancels recovery. A normal shell exit does not reconnect. No credentials are persisted by this recovery flow.

After reconnecting, Studio reads the validated KACE checkpoint and restores its progress panel. Only COMPLETE confirms that printer.cfg and required files were installed and verified. Pending or unavailable checkpoints explicitly ask the operator to continue KACE verification; reconnecting alone never completes or restarts the installation.
