import os
import sys
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
import webview

# Adjust path to allow absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.imager import (
    list_drives,
    flash_drive,
    inject_config,
    _normalize_disk_identity,
    bootstrap_preflight_facts,
)
from backend.provisioning import (
    ImageType,
    ProvisioningData,
    ProvisioningValidationError,
    validate_provisioning,
)
from backend.ejector import request_safe_eject
from backend.discovery import scan_network, probe_manual_ip
from backend.ssh_client import SSHSession
from backend.power_controller import MoonrakerPowerController, PowerControllerError
from backend.remote_power_config import RemotePowerConfigError, parse_remote_power_config
from backend.workflow_events import KaceWorkflowEventParser
from backend.bootstrap_events import BootstrapEventParser
from backend.resources import bundled_path, verify_runtime_resources
from backend.app_paths import application_cache_dir
from backend.image_manifest import ImageManifest
from backend.prebaked_preflight import load_custom_attestation
import mimetypes

HTTP_TIMEOUT_SECONDS = 30
PYWEBVIEW_SMOKE_TIMEOUT_SECONDS = 30
IMAGE_PROVENANCE_SCHEMA = "kace-studio-image-provenance/v1"

# Prevent Windows registry pollution from overriding MIME types
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('text/html', '.html')

class KaceWsgiApp:
    """
    WSGI Application for serving frontend assets and handling specific local HTTP API routes.

    SECURITY NOTE:
    This WSGI app is loaded directly by PyWebView (using `webview.create_window(..., url=wsgi_app)`).
    PyWebView binds its internal WSGI server to localhost/127.0.0.1 on a randomly assigned high port.
    It does not bind to 0.0.0.0 or any external network interface, ensuring that the local file access
    and SFTP API routes (/api/sftp/list) are only accessible locally and not exposed to the local network.
    """
    def __init__(self, web_dir, api_instance):
        self.web_dir = os.path.realpath(os.path.abspath(web_dir))
        self.api = api_instance

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        
        # Route: GET /api/sftp/list
        if path == '/api/sftp/list':
            if environ.get('REQUEST_METHOD', 'GET').upper() != 'GET':
                data = b'{"error":"Method not allowed"}'
                start_response('405 Method Not Allowed', [
                    ('Content-Type', 'application/json'),
                    ('Content-Length', str(len(data))),
                    ('Allow', 'GET'),
                ])
                return [data]
            import urllib.parse
            import posixpath
            query = environ.get('QUERY_STRING', '')
            params = urllib.parse.parse_qs(query)
            raw_path = params.get('path', ['/home/kace'])[0]

            # H1 FIX: Sanitize path param — strip null bytes, then normalize with
            # posixpath.normpath to collapse any ../ traversal sequences.
            sftp_path = posixpath.normpath(raw_path.replace('\x00', ''))
            if not sftp_path.startswith('/'):
                sftp_path = '/' + sftp_path

            try:
                files = self.api._ssh.list_directory(sftp_path)
                data = json.dumps({"path": sftp_path, "items": files}).encode('utf-8')
                # M2 FIX: Removed wildcard CORS header. This is a localhost-only
                # internal API — no cross-origin access needed or permitted.
                start_response('200 OK', [
                    ('Content-Type', 'application/json'),
                    ('Content-Length', str(len(data))),
                ])
                return [data]
            except Exception as e:
                err_msg = json.dumps({"error": self.api._sanitize_error(e)}).encode('utf-8')
                start_response('500 Internal Server Error', [
                    ('Content-Type', 'application/json'),
                    ('Content-Length', str(len(err_msg)))
                ])
                return [err_msg]
        # Serve static files from web_dir
        else:
            # Prevent directory traversal
            clean_path = path.lstrip('/')
            if not clean_path or clean_path == 'index.html':
                clean_path = 'index.html'
                
            file_path = os.path.realpath(os.path.abspath(os.path.join(self.web_dir, clean_path)))
            try:
                path_is_contained = os.path.commonpath((self.web_dir, file_path)) == self.web_dir
            except ValueError:
                path_is_contained = False
            if not path_is_contained:
                start_response('403 Forbidden', [('Content-Type', 'text/plain')])
                return [b'Forbidden']
                
            if os.path.exists(file_path) and os.path.isfile(file_path):
                # Use mimetypes to guess the Content-Type
                mime_type, _ = mimetypes.guess_type(file_path)
                if not mime_type:
                    mime_type = 'application/octet-stream'

                with open(file_path, 'rb') as f:
                    file_data = f.read()

                # M5 FIX: Add security headers to prevent clickjacking, MIME-sniffing
                # and referrer leakage from static file responses.
                # LOW-01 FIX: Content-Security-Policy restricts script/style origins
                # to 'self' with unsafe-inline (required for inline handlers used throughout).
                start_response('200 OK', [
                    ('Content-Type', mime_type),
                    ('Content-Length', str(len(file_data))),
                    ('X-Frame-Options', 'SAMEORIGIN'),
                    ('X-Content-Type-Options', 'nosniff'),
                    ('Referrer-Policy', 'same-origin'),
                    ('Content-Security-Policy',
                     "default-src 'self'; "
                     "script-src 'self' 'unsafe-inline'; "
                     "style-src 'self' 'unsafe-inline'; "
                     "img-src 'self' data: blob:; "
                     "font-src 'self'; "
                     "connect-src 'self';"),
                ])
                return [file_data]
            else:
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'Not Found']

class Api:
    def __init__(self):
        self._ssh = SSHSession()
        self._power_controller = None
        self._power_target = None
        self._power_lock = threading.Lock()
        self._remote_power_authority = None
        self._ssh_lock = threading.Lock()
        self._ssh_gen = 0
        self._ssh_attempt_gen = 0
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_active = False
        self._bootstrap_workflow_id = None
        self._window = None
        # L8 FIX: Use threading.Event for cross-thread cancel signalling.
        self._flash_cancel_event = threading.Event()
        self._flash_lock = threading.Lock()
        self._flash_active = False
        self._drive_snapshots = {}
        # INFO-02 FIX: Timestamp for scan rate-limiting (one scan per 10 seconds max).
        self._last_scan_time = 0.0
        self._prefs_path = os.path.join(os.path.expanduser("~"), ".kace-studio", "prefs.json")
        self._prefs_lock = threading.Lock()

    def set_window(self, window):
        self._window = window

    def _forward_bootstrap_event(self, event: dict, ssh_generation: int) -> None:
        """Project an authoritative bootstrap event and update the run guard."""
        with self._ssh_lock:
            if ssh_generation != self._ssh_gen:
                return

        workflow_id = event["workflow_id"]
        event_name = event["event"]
        with self._bootstrap_lock:
            expected = self._bootstrap_workflow_id
            if expected is not None and workflow_id != expected:
                return
            if event_name in ("workflow_started", "stage_started"):
                self._bootstrap_active = True
                self._bootstrap_workflow_id = workflow_id
            elif BootstrapEventParser.is_terminal(event):
                self._bootstrap_active = False
                self._bootstrap_workflow_id = None

        if self._window is not None:
            try:
                self._window.evaluate_js(
                    f"window.updateBootstrapEvent({json.dumps(event)});"
                )
            except Exception as exc:
                print(f"[KACE] Could not forward bootstrap event: {exc}")

    def _interrupt_bootstrap(self, reason: str) -> bool:
        """Close an active local projection when SSH ends without a terminal event."""
        with self._bootstrap_lock:
            if not self._bootstrap_active:
                return False
            workflow_id = self._bootstrap_workflow_id or "pending-bootstrap"
            self._bootstrap_active = False
            self._bootstrap_workflow_id = None

        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "window.updateBootstrapInterrupted("
                    f"{json.dumps(workflow_id)}, {json.dumps(reason)});"
                )
            except Exception as exc:
                print(f"[KACE] Could not forward bootstrap interruption: {exc}")
        return True

    def _sanitize_error(self, e: Exception) -> str:
        """
        Sanitizes raw python exception strings to avoid path disclosure in the UI.
        """
        import re
        msg = str(e)
        # L2 FIX: Improved path regexes.
        # Windows paths: drive letter + colon + backslash + path components
        msg = re.sub(
            r'[a-zA-Z]:\\(?:[^\\\r\n"]+\\)*[^\\\r\n"]*',
            '[Protected Path]',
            msg
        )
        # Unix paths: leading / followed by path segments
        # Negative lookbehind avoids matching URL paths (http://host/path)
        msg = re.sub(
            r'(?<![:/])/(?:[^\r\n\'" ]+/)*[^\r\n\'" ]*',
            '[Protected Path]',
            msg
        )
        return msg

    @staticmethod
    def _ssh_session_is_active(session) -> bool:
        checker = getattr(session, "is_connected", None)
        if not callable(checker):
            return False
        try:
            return checker() is True
        except Exception:
            return False

    def set_device_state(self, state: str, progress: int = 0, message: str = ""):
        """
        Broadcasting helper to propagate device lifecycle states to the frontend.
        """
        if self._window:
            try:
                escaped_msg = json.dumps(message)
                # INFO-01 FIX: JSON-encode state to prevent JS injection if state
                # ever contains a single-quote character (e.g. from an unexpected code path).
                escaped_state = json.dumps(state)
                js_code = f"window.updateDeviceState({escaped_state}, {int(progress)}, {escaped_msg});"
                self._window.evaluate_js(js_code)
            except Exception as e:
                print(f"evaluate_js error (non-fatal): {e}", file=sys.stderr)

    def get_drives(self):
        """
        Invoked by JS to get a list of safe storage drives.
        """
        drives = list_drives()
        self._drive_snapshots = {int(drive["number"]): dict(drive) for drive in drives}
        return drives

    def browse_image(self):
        """
        Spawns a native file explorer to select a local OS .img file.
        """
        if not self._window:
            return ""
        
        # Compressed custom images are intentionally not accepted here. Automatic
        # images are extracted through the verified cache pipeline; custom files
        # must already be raw images so they can never reach the writer compressed.
        file_types = ('Raw Raspberry Pi Images (*.img)', 'All files (*.*)')
        result = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=file_types)
        if result and len(result) > 0:
            return result[0]
        return ""

    def cancel_flash(self):
        """
        Sets the cancellation flag to abort the flash worker at the next safe checkpoint.
        Only effective during download/decompress stages — not during raw disk writing.
        """
        self._flash_cancel_event.set()
        return True

    def eject_drive(self, disk_number: int) -> dict:
        """Safely removes a previously selected disk after revalidating its identity."""
        if isinstance(disk_number, bool):
            return {"success": False, "error": "Invalid disk number."}
        try:
            disk_number = int(disk_number)
            expected_identity = _normalize_disk_identity(self._drive_snapshots[disk_number])
        except (KeyError, TypeError, ValueError):
            return {
                "success": False,
                "error": "Target disk identity is unavailable. Refresh the drive list and try again.",
            }
        result = request_safe_eject(disk_number, expected_identity)
        if result.get("success") is not True:
            result["error"] = self._sanitize_error(
                result.get("error") or "Windows could not safely remove the disk."
            )
        return result

    def start_flash(self, drive_id: int, image_path: str, hostname: str, wifi_ssid: str, wifi_password: str, ssh_password: str, dashboard_ui: str, timezone: str = "", pi_model: str = "", os_arch: str = "", ssh_enabled: bool = True, crowsnest: bool = False, username: str = "kace", password_auth: bool = True, drive_identity: dict = None, high_risk_confirmed: bool = False, power_relay: bool = False, power_device: str = "", power_gpio: int | None = None, power_active_low: bool = False, restart_klipper_when_powered: bool = True, image_type: str = ImageType.RASPIOS_VANILLA.value, wifi_security: str = "wpa2"):
        """
        Triggers the block-flashing and boot config injection process in a background thread.
        """
        if not isinstance(drive_identity, dict):
            self.set_device_state("ERROR", 0, "Target disk identity is missing. Refresh the drive list and try again.")
            return False
        try:
            drive_id = int(drive_id)
            selected_snapshot = self._drive_snapshots[drive_id]
            client_identity = _normalize_disk_identity(drive_identity)
            selected_identity = _normalize_disk_identity(selected_snapshot)
            if client_identity != selected_identity:
                raise ValueError("disk identity mismatch")
        except (KeyError, TypeError, ValueError):
            self.set_device_state("ERROR", 0, "Target disk identity does not match the selected drive.")
            return False
        if selected_snapshot.get("high_risk") and high_risk_confirmed is not True:
            self.set_device_state("ERROR", 0, "High-risk HDD/SSD destination requires reinforced confirmation.")
            return False

        drive_identity = selected_snapshot

        # Gather read-only environmental facts, then validate the entire request
        # before creating a worker, resolving/downloading an image, or writing disk.
        try:
            _, bootstrap_exists, bootstrap_sha256 = bootstrap_preflight_facts()
            is_custom = image_type in {
                ImageType.CUSTOM_VANILLA.value,
                ImageType.CUSTOM_PREBAKED.value,
            }
            image_exists = os.path.isfile(image_path) if is_custom else True
            image_size = os.path.getsize(image_path) if image_exists and is_custom else None
            cache_dir = application_cache_dir()
            cache_free_bytes = shutil.disk_usage(cache_dir).free
            provisioning = validate_provisioning(
                image_type=image_type,
                image_path=image_path,
                hostname=hostname,
                wifi_ssid=wifi_ssid,
                wifi_password=wifi_password,
                wifi_security=wifi_security,
                ssh_password=ssh_password,
                dashboard_ui=dashboard_ui,
                timezone=timezone,
                pi_model=pi_model,
                os_arch=os_arch,
                ssh_enabled=ssh_enabled,
                crowsnest=crowsnest,
                username=username,
                password_auth=password_auth,
                power_relay=power_relay,
                power_device=power_device,
                power_gpio=power_gpio,
                power_active_low=power_active_low,
                restart_klipper_when_powered=restart_klipper_when_powered,
                bootstrap_exists=bootstrap_exists,
                bootstrap_sha256=bootstrap_sha256,
                validate_media=True,
                image_exists=image_exists,
                image_size_bytes=image_size,
                target_size_bytes=selected_snapshot.get("size_bytes"),
                cache_free_bytes=cache_free_bytes,
            )
        except (OSError, ProvisioningValidationError, ValueError) as exc:
            self.set_device_state("ERROR", 0, f"Provisioning preflight failed: {self._sanitize_error(exc)}")
            return False

        with self._flash_lock:
            if self._flash_active:
                self.set_device_state("ERROR", 0, "A flash operation is already running.")
                return False
            self._flash_active = True
            self._flash_cancel_event.clear()
        try:
            thread = threading.Thread(
                target=self._flash_worker,
                args=(drive_id, provisioning, drive_identity),
                daemon=True
            )
            thread.start()
        except Exception:
            with self._flash_lock:
                self._flash_active = False
            raise
        return True

    # ── Flash Worker Helpers ──────────────────────────────────────────────

    def _check_cancelled(self):
        """Raises ValueError if the flash operation was cancelled by the user."""
        if self._flash_cancel_event.is_set():
            raise ValueError("Flashing cancelled by user.")

    def _compute_sha256(self, file_path: str, action_message: str) -> str:
        """Computes SHA-256 hash of a file with progress reporting."""
        import hashlib
        sha256 = hashlib.sha256()
        file_size = os.path.getsize(file_path)
        bytes_read = 0
        chunk_size = 4 * 1024 * 1024  # 4MB chunks

        with open(file_path, "rb") as f:
            while True:
                self._check_cancelled()
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sha256.update(chunk)
                bytes_read += len(chunk)
                if file_size > 0:
                    pct = int((bytes_read / file_size) * 100)
                    self.set_device_state("FLASHING", pct, f"{action_message}: {pct}%")
        return sha256.hexdigest()

    @staticmethod
    def _remove_file_if_present(file_path: str):
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _validate_raw_image(image_path: str) -> int:
        """Rejects empty/non-disk files before they can reach the raw writer."""
        image_size = os.path.getsize(image_path)
        if image_size < 512:
            raise ValueError("OS image is too small to contain a partition table.")
        with open(image_path, "rb") as image_file:
            first_sector = image_file.read(512)
        if len(first_sector) != 512 or first_sector[510:512] != b"\x55\xaa":
            raise ValueError("OS image does not contain a valid MBR/GPT boot signature.")
        return image_size

    def _cached_file_is_valid(self, file_path: str, expected_sha256: str = "", *, raw_image: bool = False) -> bool:
        """Requires a matching checksum sidecar before reusing a cached file."""
        if not os.path.isfile(file_path):
            return False
        sidecar = file_path + ".sha256"
        if not os.path.isfile(sidecar):
            return False
        try:
            with open(sidecar, "r", encoding="utf-8") as checksum_file:
                recorded = checksum_file.read().strip().split()[0]
            if not re.fullmatch(r"[0-9a-fA-F]{64}", recorded):
                return False
            if expected_sha256 and recorded.lower() != expected_sha256.lower():
                return False
            actual = self._compute_sha256(file_path, "Verifying cached file")
            if actual.lower() != recorded.lower():
                return False
            if raw_image:
                self._validate_raw_image(file_path)
            return True
        except ValueError:
            if self._flash_cancel_event.is_set():
                raise
            return False
        except (OSError, IndexError):
            return False

    def _decompress_archive(self, cached_file: str, target_img: str, status_prefix: str = "Decompressing OS image"):
        """
        Decompresses a compressed archive (.xz or .zip) to a target .img file with progress reporting.
        Returns the SHA-256 hash of the decompressed image.
        """
        import lzma
        import zipfile

        target_part = target_img + ".part"
        sidecar_part = target_img + ".sha256.part"
        self._remove_file_if_present(target_part)
        self._remove_file_if_present(sidecar_part)

        try:
            if cached_file.lower().endswith(".zip"):
                self._check_cancelled()
                with zipfile.ZipFile(cached_file, "r") as archive:
                    img_names = [name for name in archive.namelist() if name.lower().endswith(".img")]
                    if len(img_names) != 1:
                        raise ValueError(
                            f"ZIP archive must contain exactly one .img file; found {len(img_names)}."
                        )
                    img_name = img_names[0]
                    info = archive.getinfo(img_name)
                    expected_size = info.file_size
                    bytes_written = 0
                    chunk_size = 4 * 1024 * 1024

                    with archive.open(img_name) as source, open(target_part, "wb") as target:
                        while True:
                            self._check_cancelled()
                            chunk = source.read(chunk_size)
                            if not chunk:
                                break
                            target.write(chunk)
                            bytes_written += len(chunk)
                            if expected_size > 0:
                                pct = int((bytes_written / expected_size) * 100)
                                self.set_device_state("FLASHING", pct, f"{status_prefix}: {pct}%")
                    if bytes_written != expected_size:
                        raise ValueError(
                            f"ZIP image size mismatch: expected {expected_size} bytes, extracted {bytes_written}."
                        )
            elif cached_file.lower().endswith((".xz", ".img.xz")):
                decompressor = lzma.LZMADecompressor()
                compressed_size = os.path.getsize(cached_file)
                bytes_read = 0
                chunk_size = 4 * 1024 * 1024

                with open(cached_file, "rb") as source, open(target_part, "wb") as target:
                    while True:
                        self._check_cancelled()
                        chunk = source.read(chunk_size)
                        if not chunk:
                            break
                        bytes_read += len(chunk)
                        data = decompressor.decompress(chunk)
                        if data:
                            target.write(data)
                        pct = int((bytes_read / compressed_size) * 100) if compressed_size else 0
                        self.set_device_state("FLASHING", pct, f"{status_prefix}: {pct}%")
                if not decompressor.eof:
                    raise ValueError("XZ archive ended before a complete compressed stream was read.")
            else:
                raise ValueError(f"Unsupported compressed image format: {cached_file}")

            self._validate_raw_image(target_part)
            self.set_device_state("FLASHING", 0, "Saving decompressed image checksum cache...")
            calculated_img_sha = self._compute_sha256(target_part, "Generating checksum cache")
            with open(sidecar_part, "w", encoding="utf-8", newline="\n") as sidecar:
                sidecar.write(calculated_img_sha + "\n")

            os.replace(target_part, target_img)
            os.replace(sidecar_part, target_img + ".sha256")
            return calculated_img_sha
        except Exception:
            self._remove_file_if_present(target_part)
            self._remove_file_if_present(sidecar_part)
            raise

    def _decompress_xz(self, cached_xz: str, target_img: str, status_prefix: str = "Decompressing OS image"):
        """Wrapper for backward compatibility."""
        return self._decompress_archive(cached_xz, target_img, status_prefix)

    @staticmethod
    def _image_provenance_path(image_path: str) -> str:
        return image_path + ".provenance.json"

    @staticmethod
    def _write_json_atomically(path: str, payload: dict) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.", suffix=".part", dir=directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(payload, output, sort_keys=True, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _image_provenance_payload(entry, image_sha256: str) -> dict:
        return {
            "schema": IMAGE_PROVENANCE_SCHEMA,
            "image_type": entry.image_type,
            "architecture": entry.architecture,
            "version": entry.version,
            "archive_sha256": entry.sha256,
            "image_sha256": image_sha256,
            "attested_image_sha256": (
                entry.attestation.image_sha256 if entry.attestation is not None else ""
            ),
        }

    def _image_provenance_is_valid(
        self, image_path: str, entry, *, verify_image: bool = False
    ) -> bool:
        provenance_path = self._image_provenance_path(image_path)
        checksum_path = image_path + ".sha256"
        try:
            with open(provenance_path, "r", encoding="utf-8") as source:
                provenance = json.load(source)
            with open(checksum_path, "r", encoding="utf-8") as source:
                image_sha256 = source.read().strip().split()[0].lower()
        except (OSError, UnicodeError, json.JSONDecodeError, IndexError):
            return False
        if provenance != self._image_provenance_payload(entry, image_sha256):
            return False
        if entry.attestation is not None and image_sha256 != entry.attestation.image_sha256:
            return False
        if verify_image:
            actual_sha256 = self._compute_sha256(
                image_path, "Verifying pre-baked image identity"
            )
            return actual_sha256.lower() == image_sha256
        return True

    def _preflight_prebaked_image(
        self, image_path: str, provisioning: ProvisioningData
    ):
        """Validate an immutable release/custom capability contract before disk writes."""
        self.set_device_state(
            "FLASHING", 0, "Validating pre-baked image services and capabilities..."
        )
        if provisioning.image_type is ImageType.CUSTOM_PREBAKED:
            return load_custom_attestation(image_path)
        if provisioning.image_type not in {
            ImageType.MAINSAILOS_PREBAKED,
            ImageType.FLUIDDPI_PREBAKED,
        }:
            raise ValueError("Pre-baked preflight received a non-pre-baked image type.")

        entry = ImageManifest.load_bundled().resolve(
            provisioning.image_type.value, provisioning.os_arch
        )
        if entry.attestation is None:
            raise ValueError("Pinned pre-baked image has no image attestation.")
        if not self._image_provenance_is_valid(image_path, entry, verify_image=True):
            raise ValueError(
                "Pre-baked image does not match its pinned, image-bound attestation."
            )
        return entry.attestation

    def _resolve_prebaked_image(self, image_type: ImageType, os_arch: str) -> str:
        """
        Resolves an immutable, checksummed pre-baked image manifest entry.
        Downloads, caches, verifies, and decompresses as needed.
        Returns the path to the ready-to-flash .img file.
        """
        cache_dir = application_cache_dir()
        if image_type not in {ImageType.MAINSAILOS_PREBAKED, ImageType.FLUIDDPI_PREBAKED}:
            raise ValueError(f"Unsupported automatic pre-baked image type: {image_type.value}")
        return self._resolve_manifest_image(image_type.value, os_arch, cache_dir)

    def _resolve_manifest_image(self, image_type: str, os_arch: str, cache_dir: str) -> str:
        entry = ImageManifest.load_bundled().resolve(image_type, os_arch)
        self.set_device_state(
            "FLASHING", 0, f"Resolving pinned {image_type} image {entry.version}..."
        )
        cached_archive = os.path.join(cache_dir, entry.filename)
        cached_archive_sha = cached_archive + ".sha256"
        base_name = entry.filename.replace(".xz", "").replace(".zip", "")
        if not base_name.endswith(".img"):
            base_name += ".img"
        target_img = os.path.join(cache_dir, base_name)
        self._check_cancelled()

        archive_valid = self._cached_file_is_valid(cached_archive, entry.sha256)
        image_valid = self._cached_file_is_valid(target_img, raw_image=True)
        provenance_valid = image_valid and self._image_provenance_is_valid(target_img, entry)
        need_download = not archive_valid
        need_decompress = not provenance_valid

        if need_download:
            self._download_os_image(
                entry.url, cached_archive, cached_archive_sha, entry.sha256, entry.url, os_arch
            )
            need_decompress = True

        if need_decompress:
            image_sha256 = self._decompress_archive(
                cached_archive, target_img, "Decompressing OS image"
            )
            if (
                entry.attestation is not None
                and image_sha256.lower() != entry.attestation.image_sha256
            ):
                self._remove_file_if_present(target_img)
                self._remove_file_if_present(target_img + ".sha256")
                self._remove_file_if_present(self._image_provenance_path(target_img))
                raise ValueError(
                    "Decompressed image SHA-256 does not match its trusted attestation."
                )
            self._write_json_atomically(
                self._image_provenance_path(target_img),
                self._image_provenance_payload(entry, image_sha256),
            )

        self._validate_raw_image(target_img)
        if not self._image_provenance_is_valid(target_img, entry):
            raise ValueError("Prepared image provenance verification failed.")
        return target_img

    def _download_os_image(self, download_url: str, cached_xz: str, cached_xz_sha: str, remote_sha256: str, redirected_url: str, arch_suffix: str):
        """Downloads and atomically publishes a validated image archive."""
        import shutil
        import ssl
        import time as _time
        import urllib.request

        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(remote_sha256 or "")):
            raise ValueError("Automatic image downloads require a pinned SHA-256 checksum.")
        if not redirected_url:
            raise ValueError("Cannot resolve download URL and no cached image is available.")

        cache_dir = os.path.dirname(cached_xz)
        free_bytes = shutil.disk_usage(cache_dir).free
        if free_bytes < 5 * 1024 ** 3:
            raise ValueError(
                f"Not enough disk space: {free_bytes // 1024**2} MB available, ~5 GB required."
            )

        self.set_device_state("FLASHING", 0, "Downloading latest official Raspberry Pi OS Lite...")
        archive_part = cached_xz + ".part"
        checksum_part = cached_xz_sha + ".part"
        self._remove_file_if_present(archive_part)
        self._remove_file_if_present(checksum_part)

        request = urllib.request.Request(
            redirected_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        download_started = _time.monotonic()
        ssl_ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(request, context=ssl_ctx, timeout=HTTP_TIMEOUT_SECONDS) as response:
                content_length = int(response.info().get('Content-Length', 0))
                bytes_downloaded = 0
                chunk_size = 1024 * 1024
                with open(archive_part, "wb") as output:
                    while True:
                        self._check_cancelled()
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        output.write(chunk)
                        bytes_downloaded += len(chunk)
                        downloaded_mb = round(bytes_downloaded / (1024 * 1024), 1)
                        if content_length > 0:
                            pct = int((bytes_downloaded / content_length) * 100)
                            elapsed = _time.monotonic() - download_started
                            if elapsed > 0.5 and bytes_downloaded > 0:
                                speed = bytes_downloaded / elapsed
                                remaining = max(content_length - bytes_downloaded, 0)
                                eta_secs = int(remaining / speed) if speed > 0 else 0
                                eta_min, eta_sec = divmod(eta_secs, 60)
                                eta = f"{eta_min}m {eta_sec:02d}s" if eta_min else f"{eta_sec}s"
                                message = f"Downloading OS image: {pct}% ({downloaded_mb} MB) — ~{eta} remaining"
                            else:
                                message = f"Downloading OS image: {pct}% ({downloaded_mb} MB)..."
                            self.set_device_state("FLASHING", pct, message)
                        else:
                            self.set_device_state("FLASHING", 15, f"Downloading OS image ({downloaded_mb} MB)...")

            if content_length and bytes_downloaded != content_length:
                raise ValueError(
                    f"Downloaded archive size mismatch: expected {content_length} bytes, received {bytes_downloaded}."
                )

            self.set_device_state("FLASHING", 0, "Verifying downloaded archive integrity...")
            calculated_sha256 = self._compute_sha256(archive_part, "Verifying archive integrity")
            if calculated_sha256.lower() != remote_sha256.lower():
                raise ValueError(
                    f"Integrity check failed: SHA256 mismatch.\nExpected: {remote_sha256}\nCalculated: {calculated_sha256}"
                )

            with open(checksum_part, "w", encoding="utf-8", newline="\n") as sidecar:
                sidecar.write(calculated_sha256 + "\n")
            os.replace(archive_part, cached_xz)
            os.replace(checksum_part, cached_xz_sha)
        except Exception:
            self._remove_file_if_present(archive_part)
            self._remove_file_if_present(checksum_part)
            raise

    def _resolve_default_image(self, os_arch: str) -> str:
        """
        Resolves the default Raspberry Pi OS Lite image path.
        Downloads, caches, verifies, and decompresses as needed.
        Returns the path to the ready-to-flash .img file.
        """
        cache_dir = application_cache_dir()
        return self._resolve_manifest_image(ImageType.RASPIOS_VANILLA.value, os_arch, cache_dir)

    def _resolve_custom_image(self, image_path: str) -> str:
        """Validate a custom raw image against a mandatory external SHA-256."""
        if not os.path.exists(image_path):
            raise ValueError(f"Custom image file not found: {image_path}")
        if not image_path.lower().endswith(".img"):
            raise ValueError("Custom images must be uncompressed .img files.")

        custom_sha_path = image_path + ".sha256"
        custom_stem_sha_path = os.path.join(
            os.path.dirname(image_path),
            os.path.splitext(os.path.basename(image_path))[0] + ".sha256",
        )
        checksum_paths = [
            path for path in (custom_sha_path, custom_stem_sha_path) if os.path.isfile(path)
        ]
        if not checksum_paths:
            raise ValueError(
                "Custom images require an external SHA-256 sidecar: "
                "<image>.img.sha256 or <image>.sha256."
            )

        expected_checksums = set()
        for checksum_path in checksum_paths:
            try:
                with open(checksum_path, "r", encoding="utf-8") as checksum_file:
                    checksum_tokens = checksum_file.read().strip().split()
            except (OSError, UnicodeError) as exc:
                raise ValueError("Custom image external SHA-256 is unreadable.") from exc
            if not checksum_tokens or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum_tokens[0]):
                raise ValueError("Custom image external SHA-256 is invalid.")
            expected_checksums.add(checksum_tokens[0].lower())
        if len(expected_checksums) != 1:
            raise ValueError("Custom image external SHA-256 sidecars disagree.")

        expected_custom_sha = expected_checksums.pop()
        self.set_device_state("FLASHING", 0, "Verifying custom image integrity...")
        calculated_custom_sha = self._compute_sha256(image_path, "Verifying custom image")
        if calculated_custom_sha.lower() != expected_custom_sha:
            raise ValueError(
                f"Custom image integrity check failed: SHA256 mismatch.\n"
                f"Expected: {expected_custom_sha}\nCalculated: {calculated_custom_sha}"
            )

        self._validate_raw_image(image_path)
        return image_path

    # ── Flash Worker Orchestrator ─────────────────────────────────────────

    def _flash_worker(self, drive_id: int, provisioning: ProvisioningData, drive_identity: dict = None):
        try:
            self.set_device_state("FLASHING", 0, "Initializing physical block-writing...")

            # Stage 1: Resolve image path (download/cache/verify)
            image_path = provisioning.image_path
            if provisioning.image_type in {
                ImageType.MAINSAILOS_PREBAKED,
                ImageType.FLUIDDPI_PREBAKED,
            }:
                image_path = self._resolve_prebaked_image(
                    provisioning.image_type, provisioning.os_arch
                )
            elif provisioning.image_type is ImageType.RASPIOS_VANILLA:
                image_path = self._resolve_default_image(provisioning.os_arch)
            else:
                image_path = self._resolve_custom_image(image_path)

            self._check_cancelled()
            self._validate_raw_image(image_path)
            if provisioning.image_type.is_prebaked:
                self._preflight_prebaked_image(image_path, provisioning)
                self._check_cancelled()

            # Stage 2: Flash image to drive
            self.set_device_state("FLASHING", 0, "Writing blocks to SD card...")

            def progress_callback(percent):
                self.set_device_state("FLASHING", percent, f"Writing blocks: {percent}%")

            success, err_msg = flash_drive(drive_id, image_path, progress_callback, drive_identity)
            if not success:
                self.set_device_state("ERROR", 0, f"Flashing failed: {err_msg}")
                return

            # Stage 3: Inject boot configuration files
            self.set_device_state("FLASHING", 95, "Injecting system configuration files...")
            inject_success = inject_config(
                drive_id,
                provisioning.hostname,
                provisioning.wifi_ssid,
                provisioning.wifi_password,
                provisioning.ssh_password,
                provisioning.dashboard_ui,
                provisioning.timezone,
                provisioning.pi_model,
                provisioning.os_arch,
                provisioning.ssh_enabled,
                provisioning.crowsnest,
                provisioning.username,
                provisioning.password_auth,
                image_type=provisioning.image_type,
                power_relay=provisioning.power_relay,
                power_device=provisioning.power_device,
                power_gpio=provisioning.power_gpio,
                power_active_low=provisioning.power_active_low,
                restart_klipper_when_powered=provisioning.restart_klipper_when_powered,
                wifi_security=provisioning.wifi_security,
            )

            if inject_success:
                self.set_device_state("FLASHED", 100, "SD Card successfully flashed and provisioned!")
            else:
                self.set_device_state("ERROR", 95, "Error injecting boot configurations to FAT32 partition.")

        except Exception as e:
            print(f"Flash worker exception: {e}", file=sys.stderr)
            if self._flash_cancel_event.is_set():
                self.set_device_state("ERROR", 0, "Flashing cancelled by user.")
            else:
                self.set_device_state("ERROR", 0, f"Exception occurred: {self._sanitize_error(e)}")
        finally:
            with self._flash_lock:
                self._flash_active = False

    def scan_network(self):
        """
        Runs subnet IP scanners to find active nodes.
        Rate-limited to one full scan every 10 seconds to prevent rapid successive
        calls from triggering IDS alerts on sensitive corporate networks.
        """
        import time as _time
        _MIN_SCAN_INTERVAL = 10.0  # seconds
        now = _time.monotonic()
        if now - self._last_scan_time < _MIN_SCAN_INTERVAL:
            return {"status": "rate_limited", "wait_seconds": _MIN_SCAN_INTERVAL - (now - self._last_scan_time)}
        self._last_scan_time = now
        return scan_network()

    def probe_device_ip(self, ip: str):
        """
        Probes a single manual IP to check if it's active.
        """
        res = probe_manual_ip(ip)
        if res:
            self.set_device_state("DISCOVERED", 100, f"Discovered manual target at {ip}.")
        return res

    def connect_ssh(self, ip: str, username: str, password: str, cols: int = 80, rows: int = 24) -> dict:
        """
        Connects paramiko client and routes stream data to the terminal.
        cols/rows: actual frontend terminal dimensions for correct PTY sizing.
        """
        # LOW-02 FIX: Validate IP/hostname format before any network call.
        # Accepts dotted-decimal IPs, hostnames, and optional :PORT suffix.
        # Rejects empty strings and inputs containing dangerous characters.
        import re as _re
        _IP_HOST_RE = _re.compile(r'^[\w.\-]{1,253}(:\d{1,5})?$')
        if not ip or not _IP_HOST_RE.match(ip.strip()):
            return {"status": "failed", "message": "Invalid IP address or hostname format."}

        candidate = SSHSession()
        with self._ssh_lock:
            self._ssh_attempt_gen += 1
            attempt_gen = self._ssh_attempt_gen

        self.set_device_state("CONNECTING", 50, f"Establishing SSH connection to {ip}...")
        
        import paramiko
        try:
            success = candidate.connect(ip, username, password)
        except paramiko.BadHostKeyException as host_key_err:
            candidate.close()
            with self._ssh_lock:
                current_attempt = attempt_gen == self._ssh_attempt_gen
                retained = current_attempt and self._ssh_session_is_active(self._ssh)
            if not current_attempt:
                return {"status": "failed", "message": "Connection superseded by a newer attempt."}
            if retained:
                self.set_device_state("SSH_READY", 100, "New SSH host key was rejected; existing session retained.")
            else:
                self.set_device_state("ERROR", 0, f"SSH Host Key Mismatch for {ip}.")
            return {"status": "host_key_mismatch", "message": str(host_key_err)}
        except Exception as conn_err:
            candidate.close()
            sanitized_msg = self._sanitize_error(conn_err)
            with self._ssh_lock:
                current_attempt = attempt_gen == self._ssh_attempt_gen
                retained = current_attempt and self._ssh_session_is_active(self._ssh)
            if not current_attempt:
                return {"status": "failed", "message": "Connection superseded by a newer attempt."}
            if retained:
                self.set_device_state("SSH_READY", 100, "New SSH connection failed; existing session retained.")
            else:
                self.set_device_state("ERROR", 0, f"SSH connection failed: {sanitized_msg}")
            return {"status": "failed", "message": sanitized_msg}

        if success:
            with self._ssh_lock:
                if attempt_gen != self._ssh_attempt_gen:
                    superseded = True
                    previous_session = None
                    current_gen = None
                else:
                    superseded = False
                    previous_session = self._ssh
                    self._ssh = candidate
                    self._ssh_gen += 1
                    current_gen = self._ssh_gen

            if superseded:
                candidate.close()
                return {"status": "failed", "message": "Connection superseded by a newer attempt."}

            self._interrupt_bootstrap("SSH session was replaced before bootstrap completed.")
            previous_session.close()
            with self._power_lock:
                self._remote_power_authority = None
                self._power_controller = None
                self._power_target = None

            # Reachability (including an open Moonraker port) is not evidence
            # that a KACE installation workflow completed successfully.
            self.set_device_state("SSH_READY", 100, f"SSH session connected to {ip}.")
            remote_power = self._refresh_remote_power_authority()
                
            # Write-coalescing buffer: collect rapid-fire SSH data chunks and flush
            # them as a single evaluate_js call every 15ms. This prevents interactive
            # TUI menus (like KACE installer's inquirer prompts) from rendering
            # intermediate states that cause visual stacking/flickering.
            write_buffer = []
            flush_timer = [None]  # mutable container for timer reference
            buffer_lock = threading.Lock()

            def forward_workflow_event(event):
                with self._ssh_lock:
                    if current_gen != self._ssh_gen or self._window is None:
                        return
                try:
                    self._window.evaluate_js(
                        f"window.updateKaceWorkflowEvent({json.dumps(event)});"
                    )
                except Exception as exc:
                    # Rendering progress is observational and must never interrupt
                    # the SSH command that owns the installation workflow.
                    print(f"[KACE] Could not forward workflow event: {exc}")

            workflow_parser = KaceWorkflowEventParser(forward_workflow_event)
            bootstrap_parser = BootstrapEventParser(
                lambda event: self._forward_bootstrap_event(event, current_gen)
            )
            
            def flush_write_buffer():
                with buffer_lock:
                    if write_buffer:
                        combined = ''.join(write_buffer)
                        write_buffer.clear()
                    else:
                        return
                    flush_timer[0] = None
                escaped = json.dumps(combined)
                self._window.evaluate_js(f"window.writeTerminalData({escaped});")
            
            # Setup bridge callbacks
            def on_data(text):
                with self._ssh_lock:
                    if current_gen != self._ssh_gen:
                        return

                workflow_parser.feed(text)
                bootstrap_parser.feed(text)
                
                flush_now = False
                with buffer_lock:
                    write_buffer.append(text)
                    # If the data contains the cursor position query (\x1b[6n) or a terminal mode query/setting (\x1b[?),
                    # flush immediately to prevent interactive TUI prompts (like prompt_toolkit/questionary)
                    # from timing out waiting for the cursor response, which causes scrambled/cascading rendering.
                    if "\x1b[6n" in text or "\x1b[?" in text:
                        flush_now = True
                        if flush_timer[0] is not None:
                            flush_timer[0].cancel()
                            flush_timer[0] = None
                    else:
                        if flush_timer[0] is None:
                            flush_timer[0] = threading.Timer(0.015, flush_write_buffer)
                            flush_timer[0].daemon = True
                            flush_timer[0].start()
                
                if flush_now:
                    flush_write_buffer()
                
            def on_close():
                # Flush any remaining buffered data before closing
                flush_write_buffer()
                with self._ssh_lock:
                    if current_gen != self._ssh_gen:
                        return
                interrupted = self._interrupt_bootstrap(
                    "SSH disconnected before bootstrap emitted a terminal event."
                )
                if interrupted:
                    self.set_device_state(
                        "BOOTSTRAP_INTERRUPTED",
                        0,
                        "SSH disconnected before bootstrap completed.",
                    )
                else:
                    # Stale callbacks should not clear the status
                    self.set_device_state("DISCOVERED", 0, "SSH connection disconnected.")
                
            candidate.run_command_stream("bash", on_data, on_close, cols=cols, rows=rows)
            return {"status": "success", "power_config": remote_power}
        else:
            candidate.close()
            with self._ssh_lock:
                current_attempt = attempt_gen == self._ssh_attempt_gen
                retained = current_attempt and self._ssh_session_is_active(self._ssh)
            if not current_attempt:
                return {"status": "failed", "message": "Connection superseded by a newer attempt."}
            if retained:
                self.set_device_state("SSH_READY", 100, "New SSH connection failed; existing session retained.")
            else:
                self.set_device_state("ERROR", 0, f"SSH connection failed to {ip}. Verify user password or network path.")
            return {"status": "failed", "message": "Verify user password or network path."}

    def start_bootstrap(self, dashboard_ui: str) -> dict:
        """Start exactly one guarded bootstrap command on the active SSH PTY."""
        if dashboard_ui not in {"mainsail", "fluidd", "both"}:
            return {"status": "failed", "message": "Invalid dashboard selection."}

        workflow_id = f"bootstrap-{uuid.uuid4().hex}"
        with self._bootstrap_lock:
            if self._bootstrap_active:
                return {
                    "status": "busy",
                    "message": "A bootstrap workflow is already active.",
                    "workflow_id": self._bootstrap_workflow_id,
                }
            self._bootstrap_active = True
            self._bootstrap_workflow_id = workflow_id

        missing_event = {
            "protocol": "kace-bootstrap/v1",
            "event": "workflow_failed",
            "workflow_id": workflow_id,
            "sequence": 1,
            "stage": "INIT",
            "code": "BOOTSTRAP_NOT_FOUND",
            "exit_code": 1,
        }
        missing_marker = (
            "=== KACE_BOOTSTRAP_EVENT: "
            f"{json.dumps(missing_event, separators=(',', ':'))} ==="
        )
        command = (
            f"if [ -f /boot/firmware/bootstrap.sh ]; then "
            f"KACE_BOOTSTRAP_WORKFLOW_ID='{workflow_id}' bash /boot/firmware/bootstrap.sh --dashboard {dashboard_ui}; "
            f"elif [ -f /boot/bootstrap.sh ]; then "
            f"KACE_BOOTSTRAP_WORKFLOW_ID='{workflow_id}' bash /boot/bootstrap.sh --dashboard {dashboard_ui}; "
            f"else printf '%s\\n' '{missing_marker}'; fi\n"
        )

        self.set_device_state("BOOTSTRAPPING", 0, "Starting KACE bootstrap.")
        if not self._ssh.send_input(command):
            with self._bootstrap_lock:
                self._bootstrap_active = False
                self._bootstrap_workflow_id = None
            self.set_device_state("SSH_READY", 100, "Bootstrap could not be sent to SSH.")
            return {"status": "failed", "message": "SSH terminal is not ready."}
        return {"status": "started", "workflow_id": workflow_id}

    def send_ssh_input(self, data: str):
        """
        Channels keystrokes/data from frontend terminal to paramiko SSH channel.
        """
        return self._ssh.send_input(data)

    def resize_ssh_pty(self, cols: int, rows: int):
        """
        Channels window resize signals from frontend term to active SSH PTY channel.
        """
        self._ssh.resize_pty(cols, rows)

    def disconnect_ssh(self):
        """
        Closes current SSH session.
        """
        self._interrupt_bootstrap("SSH session was closed before bootstrap completed.")
        with self._ssh_lock:
            previous_session = self._ssh
            self._ssh = SSHSession()
            self._ssh_attempt_gen += 1
            self._ssh_gen += 1
        previous_session.close()
        # Keep the last schema that was successfully read from this device.
        # Moonraker power control is independent of the interactive SSH session;
        # dropping this authority would silently fall back to stale local hints.
        return True

    def clear_stored_host_key(self, ip: str) -> bool:
        """
        Removes stored host keys for the target IP from the known_hosts file.
        """
        from backend.ssh_client import clear_host_key
        return clear_host_key(ip)


    def download_file(self, remote_path: str) -> bool:
        """
        Exposes a native save file dialog to select target location and downloads the file.
        """
        if not self._window:
            return False
        
        filename = os.path.basename(remote_path)
        chosen_path = self._window.create_file_dialog(
            webview.SAVE_DIALOG, 
            save_filename=filename
        )
        
        if not chosen_path:
            return False # Cancelled
            
        if isinstance(chosen_path, (list, tuple)):
            if len(chosen_path) > 0:
                chosen_path = chosen_path[0]
            else:
                return False
                
        return self._ssh.download_file(remote_path, chosen_path)

    def get_firmware_deployment_manifest(self):
        """Return KACE's non-secret firmware manifest for reconnect recovery."""
        raw = self._ssh.read_text_file("kace/deployment-manifest.json")
        if not raw:
            return None
        try:
            manifest = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict) or manifest.get("schema") != 1:
            return None
        deployment = manifest.get("deployment")
        if not isinstance(deployment, dict):
            return None
        return manifest

    def _read_remote_power_config(self) -> dict:
        reader = getattr(self._ssh, "read_text_file_result", None)
        if not callable(reader):
            return {"status": "error", "config": None, "detail": "SSH file reader is unavailable"}
        status, raw = reader(".config/kace/power.json", max_bytes=64 * 1024)
        if status == "absent":
            return {"status": "absent", "config": None, "detail": "No remote KACE power configuration exists"}
        if status != "ok" or not isinstance(raw, str):
            return {"status": "error", "config": None, "detail": "Remote power configuration could not be read"}
        try:
            config = parse_remote_power_config(raw)
        except RemotePowerConfigError as exc:
            return {"status": "invalid", "config": None, "detail": str(exc)}
        return {"status": "configured", "config": config, "detail": ""}

    def _refresh_remote_power_authority(self) -> dict:
        result = self._read_remote_power_config()
        with self._power_lock:
            self._remote_power_authority = result
            self._power_controller = None
            self._power_target = None
        return result

    def get_remote_power_config(self) -> dict:
        """Refresh the authoritative remote schema after SSH/bootstrap changes."""
        return self._refresh_remote_power_authority()

    def _resolve_power_device(self, suggested_device: str) -> str:
        authority = self._remote_power_authority
        if authority is None or authority.get("status") == "absent":
            return suggested_device
        if authority.get("status") != "configured":
            raise PowerControllerError(authority.get("detail") or "Remote power configuration is unavailable")
        config = authority.get("config")
        if not isinstance(config, dict) or config.get("enabled") is not True:
            raise PowerControllerError("Remote KACE power configuration is disabled")
        return config.get("device")

    def _run_power_action(self, host: str, device: str, action: str) -> dict:
        """Run one Moonraker-only power action without depending on SSH/KACE."""
        try:
            with self._power_lock:
                resolved_device = self._resolve_power_device(device)
                target = (host.strip() if isinstance(host, str) else host, resolved_device)
                if self._power_controller is None or self._power_target != target:
                    self._power_controller = MoonrakerPowerController(host, resolved_device)
                    self._power_target = target
                controller = self._power_controller
                if action == "status":
                    status = controller.get_status()
                elif action == "on":
                    status = controller.power_on()
                elif action == "off":
                    status = controller.power_off()
                elif action == "wait":
                    status = controller.wait_until_ready()
                else:
                    raise ValueError("invalid power action")
            return {
                "ok": status != "error",
                "available": True,
                "device": controller.device,
                "status": status,
                "detail": "" if status != "error" else "Moonraker reported an error state",
            }
        except (PowerControllerError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "available": False,
                "device": device if isinstance(device, str) else None,
                "status": "error",
                "detail": str(exc),
            }

    def get_power_status(self, host: str, device: str) -> dict:
        return self._run_power_action(host, device, "status")

    def power_on(self, host: str, device: str) -> dict:
        return self._run_power_action(host, device, "on")

    def power_off(self, host: str, device: str) -> dict:
        return self._run_power_action(host, device, "off")

    def wait_power_ready(self, host: str, device: str) -> dict:
        return self._run_power_action(host, device, "wait")

    def get_preferences(self) -> dict:
        """
        Reads user preferences from a local file, falling back to defaults.
        """
        defaults = {
            "theme": "dark",
            "kace_auto_scan": True,
            "form_state": {}
        }
        try:
            if os.path.exists(self._prefs_path):
                with open(self._prefs_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Merge default values for missing keys
                        for k, v in defaults.items():
                            if k not in data:
                                data[k] = v
                        return data
        except Exception as e:
            print(f"Error reading preferences: {e}", file=sys.stderr)
        return defaults

    def set_preferences(self, prefs: dict) -> bool:
        """
        Validates and atomically writes user preferences to a local file.
        """
        allowed_form_fields = {
            "hostname-input", "timezone-select", "os-arch-select",
            "image-source-select", "pi-model-select",
            "bootstrap-ui-select-imager", "ssh-enable",
            "crowsnest-enable", "ssh-username",
            "power-relay-enable", "power-device-name",
        }
        if not isinstance(prefs, dict):
            return False

        theme = prefs.get("theme", "dark")
        if theme not in ("dark", "light"):
            theme = "dark"
        auto_scan = prefs.get("kace_auto_scan", True)
        if not isinstance(auto_scan, bool):
            auto_scan = True
        raw_form_state = prefs.get("form_state", {})
        if not isinstance(raw_form_state, dict):
            raw_form_state = {}
        form_state = {
            key: value
            for key, value in raw_form_state.items()
            if key in allowed_form_fields
            and isinstance(value, (str, bool))
            and (not isinstance(value, str) or len(value) <= 1024)
        }
        validated = {
            "theme": theme,
            "kace_auto_scan": auto_scan,
            "form_state": form_state,
        }
        part_path = self._prefs_path + ".part"
        try:
            with self._prefs_lock:
                os.makedirs(os.path.dirname(self._prefs_path), exist_ok=True)
                with open(part_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(validated, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(part_path, self._prefs_path)
            return True
        except Exception as e:
            try:
                if os.path.exists(part_path):
                    os.remove(part_path)
            except OSError:
                pass
            print(f"Error saving preferences: {e}", file=sys.stderr)
            return False

def _create_application_window(*, smoke_mode: bool = False):
    api = Api()
    web_dir = bundled_path("web")
    html_path = web_dir / "index.html"
    
    if not os.path.exists(html_path):
        print(f"Error: Frontend assets not found at {html_path}", file=sys.stderr)
        sys.exit(1)
        
    wsgi_app = KaceWsgiApp(str(web_dir), api)
    
    window = webview.create_window(
        title="KACE Studio Desktop Launcher",
        url=wsgi_app,
        js_api=api,
        width=1050,
        height=700,
        resizable=True,
        min_size=(900, 600),
        background_color="#0b0f19",
        # On Windows, a window created with hidden=True does not initialize
        # WebView2 until it is shown. Put smoke windows off-screen instead; the
        # probe hides them immediately after the native window is initialized.
        hidden=False,
        focus=not smoke_mode,
        x=-32_000 if smoke_mode else None,
        y=-32_000 if smoke_mode else None,
    )
    api.set_window(window)
    return window


def _run_smoke_probe(window, result: dict, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    try:
        if not window.events.shown.wait(max(0.0, deadline - time.monotonic())):
            raise TimeoutError("PyWebView did not initialize a native window before the deadline")
        if not window.events.loaded.wait(max(0.0, deadline - time.monotonic())):
            raise TimeoutError("PyWebView did not load the Studio frontend before the deadline")
        window.hide()
        observed = window.evaluate_js(
            """(() => ({
                title: document.title,
                ready: document.readyState === 'complete',
                root: Boolean(document.querySelector('.app-container')),
                bridge: Boolean(window.pywebview && window.pywebview.api &&
                    typeof window.pywebview.api.get_preferences === 'function')
            }))()"""
        )
        expected = {
            "title": "KACE Studio",
            "ready": True,
            "root": True,
            "bridge": True,
        }
        if observed != expected:
            raise RuntimeError(f"PyWebView runtime contract mismatch: {observed!r}")
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        window.destroy()


def run_pywebview_smoke_test(timeout_seconds: float = PYWEBVIEW_SMOKE_TIMEOUT_SECONDS) -> None:
    """Load the real frontend and JS bridge in an off-screen native window."""
    verify_runtime_resources()
    window = _create_application_window(smoke_mode=True)
    result: dict = {}
    webview.start(_run_smoke_probe, args=(window, result, timeout_seconds))
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "PyWebView closed before the smoke probe completed"))


def main():
    window = _create_application_window()
    webview.start()

if __name__ == "__main__":
    # Elevated disk flashing mode trigger
    if len(sys.argv) > 1 and sys.argv[1] == "--verify-package":
        verify_runtime_resources()
        print("KACE Studio packaged resource contract passed.")
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--smoke-test":
        try:
            run_pywebview_smoke_test()
        except Exception as exc:
            if sys.stderr is not None:
                print(f"KACE Studio PyWebView smoke failed: {exc}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--write-disk":
        from backend.kace_writer import main as writer_main
        # Shift args to remove program name and the write-disk trigger
        sys.argv = sys.argv[1:]
        writer_main()
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--eject-disk":
        from backend.ejector import main as ejector_main
        sys.argv = sys.argv[1:]
        ejector_main()
        sys.exit(0)
    else:
        main()
