import os
import sys
import json
import threading
import webview
from typing import Dict, Any

# Adjust path to allow absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.imager import list_drives, flash_drive, inject_config
from backend.discovery import scan_network, probe_manual_ip
from backend.ssh_client import SSHSession
import mimetypes

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
        self.web_dir = os.path.abspath(web_dir)
        self.api = api_instance

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        
        # Route: GET /api/sftp/list
        if path == '/api/sftp/list':
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
                err_msg = json.dumps({"error": str(e)}).encode('utf-8')
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
                
            file_path = os.path.abspath(os.path.join(self.web_dir, clean_path))
            if not file_path.startswith(self.web_dir):
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
                start_response('200 OK', [
                    ('Content-Type', mime_type),
                    ('Content-Length', str(len(file_data))),
                    ('X-Frame-Options', 'SAMEORIGIN'),
                    ('X-Content-Type-Options', 'nosniff'),
                    ('Referrer-Policy', 'same-origin'),
                ])
                return [file_data]
            else:
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'Not Found']

class Api:
    def __init__(self):
        self._ssh = SSHSession()
        self._ssh_lock = threading.Lock()
        self._ssh_gen = 0
        self._window = None
        # L8 FIX: Use threading.Event for cross-thread cancel signalling.
        self._flash_cancel_event = threading.Event()

    def set_window(self, window):
        self._window = window

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

    def set_device_state(self, state: str, progress: int = 0, message: str = ""):
        """
        Broadcasting helper to propagate device lifecycle states to the frontend.
        """
        if self._window:
            try:
                escaped_msg = json.dumps(message)
                js_code = f"window.updateDeviceState('{state}', {progress}, {escaped_msg});"
                self._window.evaluate_js(js_code)
            except Exception as e:
                print(f"evaluate_js error (non-fatal): {e}", file=sys.stderr)

    def get_drives(self):
        """
        Invoked by JS to get a list of safe storage drives.
        """
        return list_drives()

    def browse_image(self):
        """
        Spawns a native file explorer to select a local OS .img file.
        """
        if not self._window:
            return ""
        
        file_types = ('Raspberry Pi Images (*.img;*.zip;*.xz)', 'All files (*.*)')
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

    def start_flash(self, drive_id: int, image_path: str, hostname: str, wifi_ssid: str, wifi_password: str, ssh_password: str, dashboard_ui: str, timezone: str = "", pi_model: str = "", os_arch: str = "", ssh_enabled: bool = True, crowsnest: bool = False, username: str = "kace", password_auth: bool = True):
        """
        Triggers the block-flashing and boot config injection process in a background thread.
        """
        self._flash_cancel_event.clear()
        thread = threading.Thread(
            target=self._flash_worker,
            args=(drive_id, image_path, hostname, wifi_ssid, wifi_password, ssh_password, dashboard_ui, timezone, pi_model, os_arch, ssh_enabled, crowsnest, username, password_auth),
            daemon=True
        )
        thread.start()
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
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sha256.update(chunk)
                bytes_read += len(chunk)
                if file_size > 0:
                    pct = int((bytes_read / file_size) * 100)
                    self.set_device_state("FLASHING", pct, f"{action_message}: {pct}%")
        return sha256.hexdigest()

    def _decompress_xz(self, cached_xz: str, target_img: str, status_prefix: str = "Decompressing OS image"):
        """
        Decompresses an .xz archive to a target .img file with progress reporting.
        Returns the SHA-256 hash of the decompressed image.
        """
        import lzma

        if os.path.exists(target_img):
            os.remove(target_img)

        decompressor = lzma.LZMADecompressor()
        compressed_size = os.path.getsize(cached_xz)
        bytes_read = 0
        chunk_size = 4 * 1024 * 1024  # 4MB chunks

        with open(cached_xz, "rb") as f_in, open(target_img, "wb") as f_out:
            while True:
                self._check_cancelled()
                chunk = f_in.read(chunk_size)
                if not chunk:
                    break
                bytes_read += len(chunk)
                decompressed_data = decompressor.decompress(chunk)
                if decompressed_data:
                    f_out.write(decompressed_data)

                pct = int((bytes_read / compressed_size) * 100)
                self.set_device_state("FLASHING", pct, f"{status_prefix}: {pct}%")

        # Calculate and save decompressed image checksum
        self.set_device_state("FLASHING", 0, "Saving decompressed image checksum cache...")
        target_img_sha = target_img + ".sha256"
        calculated_img_sha = self._compute_sha256(target_img, "Generating checksum cache")
        with open(target_img_sha, "w", encoding="utf-8") as f:
            f.write(calculated_img_sha)

        return calculated_img_sha

    def _download_os_image(self, download_url: str, cached_xz: str, cached_xz_sha: str, remote_sha256: str, redirected_url: str, arch_suffix: str):
        """
        Downloads the official Raspberry Pi OS Lite .xz archive with progress and ETA reporting.
        Verifies integrity against the remote SHA-256 hash.
        """
        import urllib.request
        import ssl
        import time as _time

        if not redirected_url:
            raise ValueError("Cannot resolve download URL and no cached image is available.")

        self.set_device_state("FLASHING", 0, "Downloading latest official Raspberry Pi OS Lite...")
        cache_dir = os.path.dirname(cached_xz)
        temp_xz = os.path.join(cache_dir, f"raspios_lite_{arch_suffix}_temp.img.xz")

        req_dl = urllib.request.Request(
            redirected_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )

        dl_start_time = _time.monotonic()
        # L6 FIX: Explicit SSL context ensures TLS certificates are validated against
        # the system CA store, protecting against MITM on the OS image download.
        ssl_ctx = ssl.create_default_context()

        with urllib.request.urlopen(req_dl, context=ssl_ctx) as response:
            content_length = int(response.info().get('Content-Length', 0))
            bytes_downloaded = 0
            chunk_size = 1024 * 1024  # 1MB chunks

            with open(temp_xz, "wb") as f_temp:
                while True:
                    self._check_cancelled()
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f_temp.write(chunk)
                    bytes_downloaded += len(chunk)

                    mb_downloaded = round(bytes_downloaded / (1024 * 1024), 1)

                    if content_length > 0:
                        pct = int((bytes_downloaded / content_length) * 100)
                        # Calculate ETA
                        elapsed = _time.monotonic() - dl_start_time
                        if elapsed > 0.5 and bytes_downloaded > 0:
                            speed = bytes_downloaded / elapsed
                            remaining_bytes = content_length - bytes_downloaded
                            eta_secs = int(remaining_bytes / speed) if speed > 0 else 0
                            eta_min, eta_sec = divmod(eta_secs, 60)
                            eta_str = f"{eta_min}m {eta_sec:02d}s" if eta_min > 0 else f"{eta_sec}s"
                            self.set_device_state("FLASHING", pct, f"Downloading OS image: {pct}% ({mb_downloaded} MB) — ~{eta_str} remaining")
                        else:
                            self.set_device_state("FLASHING", pct, f"Downloading OS image: {pct}% ({mb_downloaded} MB)...")
                    else:
                        self.set_device_state("FLASHING", 15, f"Downloading OS image ({mb_downloaded} MB)...")

        # Verify downloaded .xz integrity
        self.set_device_state("FLASHING", 0, "Verifying downloaded archive integrity...")
        calculated_xz_sha = self._compute_sha256(temp_xz, "Verifying archive integrity")

        if remote_sha256 and calculated_xz_sha != remote_sha256:
            if os.path.exists(temp_xz):
                os.remove(temp_xz)
            raise ValueError(f"Integrity check failed: SHA256 mismatch.\nExpected: {remote_sha256}\nCalculated: {calculated_xz_sha}")

        # Cache the validated .xz file and its hash
        if os.path.exists(cached_xz):
            os.remove(cached_xz)
        os.rename(temp_xz, cached_xz)

        if remote_sha256:
            with open(cached_xz_sha, "w", encoding="utf-8") as f:
                f.write(remote_sha256)

    def _resolve_default_image(self, os_arch: str) -> str:
        """
        Resolves the default Raspberry Pi OS Lite image path.
        Downloads, caches, verifies, and decompresses as needed.
        Returns the path to the ready-to-flash .img file.
        """
        import urllib.request

        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
        os.makedirs(cache_dir, exist_ok=True)

        arch_suffix = "arm64" if os_arch == "64bit" else "armhf"
        target_img = os.path.join(cache_dir, f"raspios_lite_{arch_suffix}.img")
        target_img_sha = os.path.join(cache_dir, f"raspios_lite_{arch_suffix}.img.sha256")
        cached_xz = os.path.join(cache_dir, f"raspios_lite_{arch_suffix}.img.xz")
        cached_xz_sha = os.path.join(cache_dir, f"raspios_lite_{arch_suffix}.img.xz.sha256")

        # Check for legacy fallback (raspios_lite.img in current dir)
        local_legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raspios_lite.img")
        if os.path.exists(local_legacy):
            target_img = local_legacy

        # Fetch remote SHA256 to check if cache is valid
        self.set_device_state("FLASHING", 0, "Checking for latest official Raspberry Pi OS Lite release online...")
        download_url = f"https://downloads.raspberrypi.org/raspios_lite_{arch_suffix}_latest"

        req = urllib.request.Request(
            download_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )

        remote_sha256 = ""
        redirected_url = ""
        # L6 FIX: Explicit SSL context for certificate validation on all remote requests.
        import ssl
        ssl_ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, context=ssl_ctx) as response:
                redirected_url = response.geturl()

            sha_url = redirected_url + ".sha256"
            sha_req = urllib.request.Request(
                sha_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(sha_req, context=ssl_ctx) as sha_response:
                sha_content = sha_response.read().decode('utf-8').strip()
                remote_sha256 = sha_content.split()[0]
        except Exception as net_err:
            print(f"Network check warning: {net_err}. Using cache if available.", file=sys.stderr)

        self._check_cancelled()

        # Determine if download is needed
        need_download = False
        need_decompress = False

        if not os.path.exists(cached_xz):
            need_download = True
        elif remote_sha256:
            if os.path.exists(cached_xz_sha):
                with open(cached_xz_sha, "r", encoding="utf-8") as f:
                    local_xz_sha = f.read().strip()
                if local_xz_sha != remote_sha256:
                    need_download = True
            else:
                need_download = True

        if not os.path.exists(target_img):
            need_decompress = True

        # Download stage
        if need_download:
            self._download_os_image(download_url, cached_xz, cached_xz_sha, remote_sha256, redirected_url, arch_suffix)
            need_decompress = True

        # Decompression stage
        if need_decompress:
            self._decompress_xz(cached_xz, target_img, "Decompressing OS image")
        else:
            # Verify existing cached .img integrity
            self.set_device_state("FLASHING", 0, "Verifying cached image integrity...")
            if os.path.exists(target_img_sha):
                with open(target_img_sha, "r", encoding="utf-8") as f:
                    expected_img_sha = f.read().strip()
            else:
                expected_img_sha = ""

            calculated_img_sha = self._compute_sha256(target_img, "Verifying cached image")

            if expected_img_sha and calculated_img_sha != expected_img_sha:
                print("Cache verification failed. Re-decompressing image...", file=sys.stderr)
                self.set_device_state("FLASHING", 0, "Cached image corrupted. Re-decompressing OS image...")
                self._decompress_xz(cached_xz, target_img, "Decompressing OS image")

        return target_img

    def _resolve_custom_image(self, image_path: str) -> str:
        """
        Validates a custom OS image path and verifies its SHA-256 integrity
        if a matching .sha256 sidecar file exists.
        Returns the validated image path.
        """
        if not os.path.exists(image_path):
            raise ValueError(f"Custom image file not found: {image_path}")

        # Check for matching .sha256 file next to the custom image
        custom_sha_path = image_path + ".sha256"
        custom_stem_sha_path = os.path.join(
            os.path.dirname(image_path),
            os.path.splitext(os.path.basename(image_path))[0] + ".sha256"
        )

        expected_custom_sha = ""
        if os.path.exists(custom_sha_path):
            with open(custom_sha_path, "r", encoding="utf-8") as f:
                expected_custom_sha = f.read().strip().split()[0]
        elif os.path.exists(custom_stem_sha_path):
            with open(custom_stem_sha_path, "r", encoding="utf-8") as f:
                expected_custom_sha = f.read().strip().split()[0]

        if expected_custom_sha:
            self.set_device_state("FLASHING", 0, "Verifying custom image integrity...")
            calculated_custom_sha = self._compute_sha256(image_path, "Verifying custom image")
            if calculated_custom_sha.lower() != expected_custom_sha.lower():
                raise ValueError(
                    f"Custom image integrity check failed: SHA256 mismatch.\n"
                    f"Expected: {expected_custom_sha}\nCalculated: {calculated_custom_sha}"
                )
        else:
            # No checksum file — calculate and log to verify readability
            self.set_device_state("FLASHING", 0, "Calculating custom image checksum...")
            calculated_custom_sha = self._compute_sha256(image_path, "Reading custom image")
            print(f"Custom image SHA256: {calculated_custom_sha}", file=sys.stderr)

        return image_path

    # ── Flash Worker Orchestrator ─────────────────────────────────────────

    def _flash_worker(self, drive_id: int, image_path: str, hostname: str, wifi_ssid: str, wifi_password: str, ssh_password: str, dashboard_ui: str, timezone: str, pi_model: str, os_arch: str, ssh_enabled: bool, crowsnest: bool, username: str, password_auth: bool):
        try:
            self.set_device_state("FLASHING", 0, "Initializing physical block-writing...")

            # Stage 1: Resolve image path (download/cache/verify)
            if image_path == "default_lite":
                image_path = self._resolve_default_image(os_arch)
            else:
                image_path = self._resolve_custom_image(image_path)

            self._check_cancelled()

            # Stage 2: Flash image to drive
            self.set_device_state("FLASHING", 0, "Writing blocks to SD card...")

            def progress_callback(percent):
                self.set_device_state("FLASHING", percent, f"Writing blocks: {percent}%")

            success, err_msg = flash_drive(drive_id, image_path, progress_callback)
            if not success:
                self.set_device_state("ERROR", 0, f"Flashing failed: {err_msg}")
                return

            # Stage 3: Inject boot configuration files
            self.set_device_state("FLASHING", 95, "Injecting system configuration files...")
            inject_success = inject_config(drive_id, hostname, wifi_ssid, wifi_password, ssh_password, dashboard_ui, timezone, pi_model, os_arch, ssh_enabled, crowsnest, username, password_auth)

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

    def scan_network(self):
        """
        Runs subnet IP scanners to find active nodes.
        """
        return scan_network()

    def probe_device_ip(self, ip: str):
        """
        Probes a single manual IP to check if it's active.
        """
        res = probe_manual_ip(ip)
        if res:
            self.set_device_state("DISCOVERED", 100, f"Discovered manual target at {ip}.")
        return res

    def connect_ssh(self, ip: str, username: str, password: str, cols: int = 80, rows: int = 24) -> bool:
        """
        Connects paramiko client and routes stream data to the terminal.
        cols/rows: actual frontend terminal dimensions for correct PTY sizing.
        """
        with self._ssh_lock:
            self._ssh.close()
            self._ssh_gen += 1
            current_gen = self._ssh_gen

        self.set_device_state("CONNECTING", 50, f"Establishing SSH connection to {ip}...")
        
        success = self._ssh.connect(ip, username, password)
        if success:
            # Check if Moonraker port (7125) is open to distinguish SSH_READY vs BOOTSTRAPPED
            from backend.discovery import probe_ip_ports
            ports_status = probe_ip_ports(ip, [7125], timeout=0.5)
            is_bootstrapped = ports_status.get(7125, False)
            
            with self._ssh_lock:
                # If a newer connection attempt started while probing, abort this connection
                if current_gen != self._ssh_gen:
                    return False

            if is_bootstrapped:
                self.set_device_state("BOOTSTRAPPED", 100, f"Connected to bootstrapped node at {ip}.")
            else:
                self.set_device_state("SSH_READY", 100, f"Connected to raw node at {ip}. Ready for KACE bootstrap.")
                
            # Write-coalescing buffer: collect rapid-fire SSH data chunks and flush
            # them as a single evaluate_js call every 15ms. This prevents interactive
            # TUI menus (like KACE installer's inquirer prompts) from rendering
            # intermediate states that cause visual stacking/flickering.
            write_buffer = []
            flush_timer = [None]  # mutable container for timer reference
            buffer_lock = threading.Lock()
            
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
                with buffer_lock:
                    write_buffer.append(text)
                    if flush_timer[0] is None:
                        flush_timer[0] = threading.Timer(0.015, flush_write_buffer)
                        flush_timer[0].daemon = True
                        flush_timer[0].start()
                
            def on_close():
                # Flush any remaining buffered data before closing
                flush_write_buffer()
                with self._ssh_lock:
                    if current_gen != self._ssh_gen:
                        return
                    # Stale callbacks should not clear the status
                    self.set_device_state("DISCOVERED", 0, "SSH connection disconnected.")
                
            self._ssh.run_command_stream("bash", on_data, on_close, cols=cols, rows=rows)
            return True
        else:
            with self._ssh_lock:
                if current_gen == self._ssh_gen:
                    self.set_device_state("ERROR", 0, f"SSH connection failed to {ip}. Verify user password or network path.")
            return False

    def send_ssh_input(self, data: str):
        """
        Channels keystrokes/data from frontend terminal to paramiko SSH channel.
        """
        self._ssh.send_input(data)

    def resize_ssh_pty(self, cols: int, rows: int):
        """
        Channels window resize signals from frontend term to active SSH PTY channel.
        """
        self._ssh.resize_pty(cols, rows)

    def disconnect_ssh(self):
        """
        Closes current SSH session.
        """
        with self._ssh_lock:
            self._ssh.close()
            self._ssh_gen += 1
        return True

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

def main():
    api = Api()
    if hasattr(sys, '_MEIPASS'):
        current_dir = sys._MEIPASS
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(current_dir, "web")
    html_path = os.path.join(web_dir, "index.html")
    
    if not os.path.exists(html_path):
        print(f"Error: Frontend assets not found at {html_path}", file=sys.stderr)
        sys.exit(1)
        
    wsgi_app = KaceWsgiApp(web_dir, api)
    
    window = webview.create_window(
        title="KACE Studio Desktop Launcher",
        url=wsgi_app,
        js_api=api,
        width=1050,
        height=700,
        resizable=True,
        min_size=(900, 600),
        background_color="#0b0f19"
    )
    api.set_window(window)
    webview.start()

if __name__ == "__main__":
    # Elevated disk flashing mode trigger
    if len(sys.argv) > 1 and sys.argv[1] == "--write-disk":
        from backend.kace_writer import main as writer_main
        # Shift args to remove program name and the write-disk trigger
        sys.argv = sys.argv[1:]
        writer_main()
        sys.exit(0)
    else:
        main()

