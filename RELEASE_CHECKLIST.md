# KACE Studio release checklist

KACE Studio is currently pre-1.0. The current product decision (2026-10-01)
permits distribution of the `0.5.0-rc.3` candidate **without an Authenticode
signature**, with independently reproduced build evidence. Signing is deferred
for this unsigned distribution, not reported as passed.
Following this checklist does not itself publish, tag, sign, or release anything.

## Current unsigned distribution policy

The unsigned distribution must pass source tests, immutable input verification,
package/resource and PE metadata checks, packaged WebView2 smoke, same-builder
reproduction and byte-identical reproduction on an independent Windows runner.
Use the artifact from the successful CI run on the exact published `main` commit.

Retain and distribute together:

- `KACE-studio.exe`, explicitly identified as **unsigned / no Authenticode**.
- Its SHA-256, verified against the downloaded executable.
- `KACE-studio.release.json`, including the exact source commit, pinned KACE
  identities, toolchain and unsigned signature status.
- `KACE-studio.independent-build.json`, bound to that same source commit and
  executable SHA-256; its hash must match the release manifest.
- CI run/job references and the separately published checksum.

Keep `artifact.authenticode.status` as `NotSigned` and `verified` as false.
Do not relabel an unsigned artifact or its manifest as signed. An EXE from a PR
merge-test commit or an older build cannot replace the exact-main artifact.

The `Signed Release Gates` job, `release_candidate=true` dispatch mode,
certificate/timestamp checks and `verify-release-gates` command remain unchanged
and mandatory for a future **signed** release. They must still fail closed when
signing inputs are missing or invalid. Their lack of signing evidence does not
block the currently approved **unsigned** distribution. Do not use the signed
verifier as an unsigned approval or bypass it by modifying its checks.

Hardware qualification, version/tag selection and publication remain separate
decisions. The unsigned policy does not claim physical qualification or authorize
a tag or release on its own.

## Controlled test candidate

An explicitly approved unsigned local candidate may be built once from clean,
published source for controlled qualification. It must pass contract, bundle,
PE metadata and packaged renderer checks and carry its own external manifest and
SHA-256. Record the actual test results, CI gaps and unsigned status. Leave
same-runner/independent reproduction unverified unless matching evidence exists;
never reuse an older artifact's attestation. This does not weaken the signed
publication gates below or claim physical qualification.

Read KACE's [hardware qualification guide](https://github.com/3D-uy/KACE/blob/main/docs/HARDWARE_TESTING.md).
Existing configuration replacement may stop with a reviewed proposal; recovery
may require manual intervention. Resolve those states before claiming completion.

## 1. Clean inputs

- [ ] The KACE and KACE Studio worktrees contain only reviewed release changes.
- [ ] No credentials, cache files, generated images, test reports, temporary files, or local paths are tracked.
- [ ] Version and changelog changes, if any, are isolated in an explicit release commit.
- [ ] The KACE revision to be packaged is already published and reachable from GitHub.

## 2. Pin the KACE bootstrap contract

KACE is the source of `scripts/bootstrap.sh`. Studio must not maintain an independent editable copy.

- [ ] Select a full, immutable KACE commit SHA.
- [ ] Download `scripts/bootstrap.sh` from that commit's raw GitHub URL.
- [ ] Calculate SHA-256 from the downloaded bytes.
- [ ] Update `candidate_ref`, `bootstrap_ref`, `bootstrap_sha256`, `installer_ref`, and `installer_sha256` together in `release-contract.json`; `candidate_ref` and `installer_ref` must be identical, and CI must not duplicate them as environment variables.
- [ ] Confirm the bootstrap's internal installer URL, revision, and SHA-256 identify an already published KACE `install.sh`.
- [ ] Hash required runtime files from the committed Git bytes and run
      `python scripts/release.py verify-local-candidate ../KACE <runtime-commit>`.
- [ ] Run `fetch-bootstrap`, `verify-remote-installer` and `verify-inputs`; only then
      set `runtime_status` to `pinned`. Publish Studio's final commit before building.
- [ ] Fetch the remote installer and verify its SHA-256 without executing it.
- [ ] Run the tests that reject a mismatched or mutable contract.

Example read-only verification:

```powershell
$ref = '<full-kace-commit>'
$expected = '<expected-bootstrap-sha256>'
Invoke-WebRequest "https://raw.githubusercontent.com/3D-uy/KACE/$ref/scripts/bootstrap.sh" -OutFile bootstrap.remote.sh
$actual = (Get-FileHash bootstrap.remote.sh -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "bootstrap SHA-256 mismatch: $actual != $expected" }
```

Remove the temporary downloaded file after inspection. Do not calculate a hash from `main` and later reuse it for different bytes.

## 3. Source validation

```powershell
python -m pip install --require-hashes -r requirements.lock
python -m pytest -v
```

- [ ] The full test suite passes on the supported CI Python/OS matrix.
- [ ] Disk-selection and writer tests use mocks and never open a physical device.
- [ ] Cancellation, partial cache, ZIP/XZ truncation, custom-image, capacity, and identity-reassignment tests pass.
- [ ] Bootstrap stage/error markers still match the parser in `web/app.js`.
- [ ] Simulated KACE workflow transcripts leave `TIMEOUT`, cancellation, flash
      failure, and rollback failure terminal; `ACTION_REQUIRED` is never styled
      or interpreted as success.
- [ ] Source mode uses the sibling `KACE/scripts/bootstrap.sh` when both repositories are checked out together.

## 4. Windows build

Use exactly the Python and PyInstaller versions in `release-contract.json`.
Install only the hashed lock, fetch/verify the contract bootstrap, then build from
the clean, published Studio commit:

```powershell
python -m pip install --require-hashes -r requirements.lock
python scripts/release.py fetch-bootstrap
python scripts/release.py verify-remote-installer
python scripts/release.py verify-inputs
$env:PYTHONHASHSEED = '1'
$env:SOURCE_DATE_EPOCH = (git show -s --format=%ct HEAD)
python -m PyInstaller --clean -y main.spec
python scripts/release.py verify-bundle dist/KACE-studio.exe
python scripts/release.py verify-metadata dist/KACE-studio.exe
dist\KACE-studio.exe --verify-package
python scripts/smoke_executable.py dist/KACE-studio.exe --timeout 45
python scripts/release.py write-manifest dist/KACE-studio.exe dist/KACE-studio.release.json
```

- [ ] `main.spec` includes `bootstrap.sh` and all required `web/` assets.
- [ ] The executable launches without using files from the source checkout.
- [ ] The packaged PyWebView smoke loads the real DOM and JavaScript bridge before its external 45-second deadline; `--verify-package` alone does not satisfy this gate.
- [ ] The exact bootstrap, release contract, and every tracked `web/` byte are extracted from the PyInstaller archive and compared with the verified inputs.
- [ ] Boot-partition injection preserves the approved bootstrap bytes exactly,
      including the shebang; source and packaged copies match the contract.
- [ ] The executable contains no unexpected development paths, caches, logs, or credentials.
- [ ] PE numeric/string version metadata matches `release-contract.json` exactly.

## 5. End-to-end qualification

Perform physical tests only in a controlled manual qualification environment:

- [ ] Confirm the displayed target identity before accepting the destructive write.
- [ ] Validate a normal SD/USB target and the reinforced external HDD/SSD warning path.
- [ ] Provision each documented official-image path.
- [ ] Verify first boot, network configuration, discovery, SSH, and SFTP.
- [ ] Run the complete bootstrap and confirm Studio does not report success when KACE is absent.
- [ ] Launch KACE on the Pi and generate/deploy a representative printer configuration.

Record native Windows eject confirmation separately from UI state; inspect
automatic discovery, pending power before bootstrap, progress in the same SSH
session and session-bound SFTP. An old EXE with the same version label is not the
same candidate: record its exact manifest and checksum.

Follow KACE's [hardware qualification guide](https://github.com/3D-uy/KACE/blob/main/docs/HARDWARE_TESTING.md)
for loaded configuration, firmware identity, unchanged retries, safe interruption
and physical commissioning. On the Pi, local Moonraker uses `127.0.0.1:7125`;
a LAN address retains the remote-publication boundary.

Automated CI must never be pointed at physical disks or printer controllers.

## 6. Remote CI and artifact evidence

- [ ] Every KACE workflow required by its release guide passes before Studio is published.
- [ ] Studio's Windows/Ubuntu and Python 3.11/3.12 test matrix passes.
- [ ] The Windows executable build passes after those tests.
- [ ] A second clean Windows runner reproduces the unsigned EXE byte for byte and emits `KACE-studio.independent-build.json` bound to the exact source commit and artifact SHA-256.
- [ ] The packaged archive does not vendor runner-image `api-ms-win-*`, `ext-ms-win-*`, or `ucrtbase.dll`; supported Windows 10/11 hosts provide these system runtimes.
- [ ] Wheel `.dist-info/RECORD` installation receipts are absent from the package; launcher hashes in those receipts are builder-path-dependent and are not release inputs.
- [ ] The CI logs show the expected immutable bootstrap ref and checksum.
- [ ] The downloaded CI artifact has its external `KACE-studio.release.json` manifest and matching SHA-256.
- [ ] Packaged-bootstrap verification matches the CI-fetched input exactly.
- [ ] For a signed release only, a manually dispatched `release_candidate` run fails closed without all signing secrets, verifies the expected signer certificate SHA-256, requires a trusted timestamp, and passes `verify-release-gates` before exposing signed evidence.

## 7. Publication

- [ ] Publish KACE first and KACE Studio second.
- [ ] Use immutable tags and record their resolved commits.
- [ ] Publish checksums through a channel separate from the artifact download.
- [ ] For the currently approved unsigned distribution, publish only the exact-main independently reproduced unsigned artifact and its matching manifest, independent attestation and SHA-256. Clearly state that it has no Authenticode signature.
- [ ] For a future signed release, publish only the signed manifest and artifact produced by the release-candidate gate. An ordinary unsigned CI artifact never satisfies the signed-release requirements.
- [ ] Document supported environments, hardware qualification, known limitations, and rollback.
- [ ] Confirm the same-environment double build matches, and do not call the artifact independently reproducible until a second controlled builder also matches; the manifest records these as different claims.

Any difference between local validation, the remote commit, CI inputs, or packaged bytes blocks the release.
