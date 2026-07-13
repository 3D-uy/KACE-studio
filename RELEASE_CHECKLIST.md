# KACE Studio Release Checklist

Before cutting a new release of **KACE Studio**, the following steps must be completed to ensure security and integrity of the bootstrapping process.

## 1. Update KACE Agent Installer Integrity Hash (Critical)

Since KACE Studio verifies the integrity of the remote KACE Agent installation script (`install.sh`), the hardcoded SHA-256 hash inside [bootstrap.sh](file:///d:/Open%20World/GitHub/KACE-studio/bootstrap.sh) must be updated to match the latest agent release.

### Steps to Update:
1. Determine the SHA-256 checksum of the target remote `install.sh` file. You can fetch and calculate it using this command:
   ```bash
   curl -sSL https://raw.githubusercontent.com/3D-uy/KACE/main/install.sh | sha256sum
   ```
2. Open [bootstrap.sh](file:///d:/Open%20World/GitHub/KACE-studio/bootstrap.sh) in an editor.
3. Locate the `EXPECTED_HASH` variable in the `KACE Agent` section (around line 935):
   ```bash
   EXPECTED_HASH="<SHA-256-HASH-HERE>"
   ```
4. Replace it with the calculated SHA-256 checksum.
5. Commit and push this change to the repository.

*Note: Running KACE Studio without updating this hash on a new agent release will cause the bootstrap phase to fail with an integrity check error.*

---

## 2. Platform Verification & Testing
1. Ensure all backend tests pass locally:
   ```bash
   py -m pytest tests/test_backend.py -v
   ```
2. Build the windows executable using PyInstaller:
   ```bash
   pyinstaller --clean -y main.spec
   ```
3. Manually run `dist/KACE-studio.exe` to verify:
   - UI launches cleanly and the default theme loads without flashing.
   - Host drives are scanned and listed correctly.
   - An SSH connection can be established.
