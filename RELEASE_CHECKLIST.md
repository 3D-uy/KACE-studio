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
- [ ] Update `KACE_BOOTSTRAP_REF` and `KACE_BOOTSTRAP_SHA256` together in `.github/workflows/ci.yml`.
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
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest -v
```

- [ ] The full test suite passes on the supported CI Python/OS matrix.
- [ ] Disk-selection and writer tests use mocks and never open a physical device.
- [ ] Cancellation, partial cache, ZIP/XZ truncation, custom-image, capacity, and identity-reassignment tests pass.
- [ ] Bootstrap stage/error markers still match the parser in `web/app.js`.
- [ ] Source mode uses the sibling `KACE/scripts/bootstrap.sh` when both repositories are checked out together.

## 4. Windows build

Fetch and verify the pinned bootstrap first, place it at the Studio repository root, then build:

```powershell
pyinstaller --clean -y main.spec
```

- [ ] `main.spec` includes `bootstrap.sh` and all required `web/` assets.
- [ ] The executable launches without using files from the source checkout.
- [ ] The exact bootstrap bytes packaged by PyInstaller are extracted or inspected and compared with the verified input.
- [ ] The comparison accounts for Studio's intentional boot-partition version comment and LF normalization at injection time; those transformed bytes are tested separately from the packaged input.
- [ ] The executable contains no unexpected development paths, caches, logs, or credentials.

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
- [ ] The CI logs show the expected immutable bootstrap ref and checksum.
- [ ] The downloaded CI artifact has a recorded SHA-256.
- [ ] Packaged-bootstrap verification matches the CI-fetched input exactly.

## 7. Publication

- [ ] Publish KACE first and KACE Studio second.
- [ ] Use immutable tags and record their resolved commits.
- [ ] Publish checksums through a channel separate from the artifact download.
- [ ] Document supported environments, hardware qualification, known limitations, and rollback.
- [ ] Do not call an artifact signed or reproducible without evidence.

Any difference between local validation, the remote commit, CI inputs, or packaged bytes blocks the release.
