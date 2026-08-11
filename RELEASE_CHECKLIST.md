# KACE Studio release checklist

KACE Studio is currently pre-1.0. This checklist defines the evidence required before a future release; following it does not itself publish, tag, sign, or release anything.

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
- [ ] Update `bootstrap_ref`, `bootstrap_sha256`, `installer_ref`, and `installer_sha256` together in `release-contract.json`; CI must not duplicate them as environment variables.
- [ ] Confirm the bootstrap's internal installer URL, revision, and SHA-256 identify an already published KACE `install.sh`.
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

Install only the hashed lock, fetch/verify the contract bootstrap, then build:

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
python scripts/release.py write-manifest dist/KACE-studio.exe dist/KACE-studio.release.json
```

- [ ] `main.spec` includes `bootstrap.sh` and all required `web/` assets.
- [ ] The executable launches without using files from the source checkout.
- [ ] The exact bootstrap, release contract, and every tracked `web/` byte are extracted from the PyInstaller archive and compared with the verified inputs.
- [ ] The comparison accounts for Studio's intentional boot-partition version comment and LF normalization at injection time; those transformed bytes are tested separately from the packaged input.
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

Automated CI must never be pointed at physical disks or printer controllers.

## 6. Remote CI and artifact evidence

- [ ] Every KACE workflow required by its release guide passes before Studio is published.
- [ ] Studio's Windows/Ubuntu and Python 3.11/3.12 test matrix passes.
- [ ] The Windows executable build passes after those tests.
- [ ] A second clean Windows runner reproduces the unsigned EXE byte for byte and emits `KACE-studio.independent-build.json` bound to the exact source commit and artifact SHA-256.
- [ ] The packaged archive does not vendor runner-image `api-ms-win-*`, `ext-ms-win-*`, or `ucrtbase.dll`; supported Windows 10/11 hosts provide these system runtimes.
- [ ] The CI logs show the expected immutable bootstrap ref and checksum.
- [ ] The downloaded CI artifact has its external `KACE-studio.release.json` manifest and matching SHA-256.
- [ ] Packaged-bootstrap verification matches the CI-fetched input exactly.
- [ ] A manually dispatched `release_candidate` run fails closed without all signing secrets, verifies the expected signer certificate SHA-256, requires a trusted timestamp, and passes `verify-release-gates` before exposing signed evidence.

## 7. Publication

- [ ] Publish KACE first and KACE Studio second.
- [ ] Use immutable tags and record their resolved commits.
- [ ] Publish checksums through a channel separate from the artifact download.
- [ ] Publish only the signed manifest and artifact produced by the release-candidate gate; never promote the ordinary unsigned CI artifact.
- [ ] Document supported environments, hardware qualification, known limitations, and rollback.
- [ ] Confirm the same-environment double build matches, and do not call the artifact independently reproducible until a second controlled builder also matches; the manifest records these as different claims.

Any difference between local validation, the remote commit, CI inputs, or packaged bytes blocks the release.
