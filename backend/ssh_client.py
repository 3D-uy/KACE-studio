import os
import csv
from contextlib import contextmanager
from functools import lru_cache
import math
import subprocess
import tempfile
import threading
import time
import select
import posixpath
import paramiko
from typing import Callable


KNOWN_HOSTS_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_SSH_CONNECT_DEADLINE_SECONDS = 30.0
SSH_CONNECT_DEADLINE_ENV = "KACE_STUDIO_SSH_CONNECT_DEADLINE_S"
_known_hosts_thread_lock = threading.RLock()


class SSHConnectionDeadlineExceeded(TimeoutError):
    """Raised when the complete SSH trust/connect/retry transaction expires."""


def _validated_positive_seconds(value: object, label: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive finite number of seconds.") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{label} must be a positive finite number of seconds.")
    return seconds


def ssh_connect_deadline_seconds() -> float:
    """Read the fail-closed, configurable wall-clock SSH deadline."""
    value = os.environ.get(
        SSH_CONNECT_DEADLINE_ENV, str(DEFAULT_SSH_CONNECT_DEADLINE_SECONDS)
    )
    return _validated_positive_seconds(value, SSH_CONNECT_DEADLINE_ENV)


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SSHConnectionDeadlineExceeded("SSH connection deadline exceeded.")
    return remaining


@lru_cache(maxsize=1)
def _windows_current_user_sid() -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=flags,
    )
    if result.returncode != 0:
        raise PermissionError("Could not determine the Windows user SID for SSH trust storage.")
    try:
        row = next(csv.reader([result.stdout.strip()]))
        sid = row[1].strip()
    except (IndexError, StopIteration) as exc:
        raise PermissionError("Windows returned an invalid user SID.") from exc
    if not sid.startswith("S-"):
        raise PermissionError("Windows returned an invalid user SID.")
    return sid


def _restrict_path_permissions(path: str, *, directory: bool = False) -> None:
    os.chmod(path, 0o700 if directory else 0o600)
    if os.name != "nt":
        return
    inheritance = "(OI)(CI)F" if directory else "F"
    grants = (
        f"*{_windows_current_user_sid()}:{inheritance}",
        f"*S-1-5-18:{inheritance}",
        f"*S-1-5-32-544:{inheritance}",
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["icacls", path, "/inheritance:r", "/grant:r", *grants],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=flags,
    )
    if result.returncode != 0:
        raise PermissionError("Could not restrict the Windows ACL for SSH trust storage.")


def _atomic_write_known_hosts(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".known_hosts.", suffix=".part", dir=directory
    )
    descriptor_open = True
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor_open = False
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        _restrict_path_permissions(temporary_path)
        os.replace(temporary_path, path)
        _restrict_path_permissions(path)
    except Exception:
        if descriptor_open:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def _known_hosts_lock(known_hosts_path: str, *, deadline: float | None = None):
    lock_path = os.path.join(os.path.dirname(known_hosts_path), "known_hosts.lock")
    if deadline is None:
        thread_lock_acquired = _known_hosts_thread_lock.acquire()
    else:
        thread_lock_acquired = _known_hosts_thread_lock.acquire(
            timeout=_remaining_seconds(deadline)
        )
    if not thread_lock_acquired:
        raise TimeoutError("Timed out locking SSH known_hosts storage.")
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        lock_file = os.fdopen(descriptor, "r+b", closefd=True)
        acquired = False
        try:
            if os.path.getsize(lock_path) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            _restrict_path_permissions(lock_path)
            lock_deadline = time.monotonic() + KNOWN_HOSTS_LOCK_TIMEOUT_SECONDS
            if deadline is not None:
                lock_deadline = min(lock_deadline, deadline)
            while True:
                try:
                    lock_file.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= lock_deadline:
                        raise TimeoutError("Timed out locking SSH known_hosts storage.")
                    time.sleep(min(0.05, max(lock_deadline - time.monotonic(), 0)))
            yield
        finally:
            if acquired:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
    finally:
        _known_hosts_thread_lock.release()


def _get_known_hosts_path(*, deadline: float | None = None) -> str:
    """Returns the path to KACE's persistent SSH known_hosts file."""
    kace_dir = os.path.join(os.path.expanduser("~"), ".kace")
    os.makedirs(kace_dir, mode=0o700, exist_ok=True)
    _restrict_path_permissions(kace_dir, directory=True)
    path = os.path.join(kace_dir, "known_hosts")
    with _known_hosts_lock(path, deadline=deadline):
        if not os.path.exists(path):
            _atomic_write_known_hosts(path, "")
        else:
            _restrict_path_permissions(path)
    return path


def clear_host_key(ip: str):
    """
    Removes any stored keys for the target IP/hostname from the known_hosts file.
    Supports parsing port-bracketed formats (e.g., [192.168.1.47]:22).
    """
    known_hosts_path = _get_known_hosts_path()
    target_ip = ip
    if ":" in ip:
        parts = ip.split(":")
        target_ip = parts[0]

    try:
        with _known_hosts_lock(known_hosts_path):
            lines_to_keep = []
            with open(known_hosts_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        lines_to_keep.append(line)
                        continue
                    parts = stripped.split()
                    if parts:
                        host_part = parts[0]
                        hosts = [h.strip("[]") for h in host_part.split(",")]
                        hosts_clean = []
                        for host in hosts:
                            if "]" in host:
                                host = host.split("]")[0]
                            if ":" in host:
                                host = host.split(":")[0]
                            hosts_clean.append(host)
                        if target_ip in hosts_clean:
                            continue
                    lines_to_keep.append(line)
            _atomic_write_known_hosts(known_hosts_path, "".join(lines_to_keep))
        print(f"[SECURITY] Removed stored host keys for {ip} from {known_hosts_path}.")
        return True
    except Exception as e:
        print(f"Error clearing host key for {ip}: {e}")
        return False


def _persist_host_keys_atomically(
    client, known_hosts_path: str, *, lock_held: bool = False
) -> None:
    def merge_and_publish():
        merged = paramiko.HostKeys()
        if os.path.getsize(known_hosts_path) > 0:
            merged.load(known_hosts_path)
        for hostname, keys in client.get_host_keys().items():
            for key_type, key in keys.items():
                merged.add(hostname, key_type, key)
        lines = []
        for hostname in sorted(merged.keys()):
            for key_type, key in sorted(merged[hostname].items()):
                lines.append(f"{hostname} {key_type} {key.get_base64()}\n")
        _atomic_write_known_hosts(known_hosts_path, "".join(lines))

    if lock_held:
        merge_and_publish()
        return
    with _known_hosts_lock(known_hosts_path):
        merge_and_publish()



class SSHSession:
    def __init__(self):
        self.client = None
        self.channel = None
        self.running = False
        self._state_lock = threading.RLock()

    def _discard_client(self, client) -> None:
        with self._state_lock:
            if self.client is client:
                self.client = None
        try:
            client.close()
        except Exception:
            pass

    @staticmethod
    def _close_transport_objects(channel, client) -> None:
        if channel:
            try:
                channel.close()
            except Exception:
                pass
        if client:
            try:
                client.close()
            except Exception:
                pass

    def _detach_transport(self):
        self.running = False
        with self._state_lock:
            channel = self.channel
            client = self.client
            self.channel = None
            self.client = None
        return channel, client

    def _abort_connect_nonblocking(self) -> None:
        channel, client = self._detach_transport()
        closer = threading.Thread(
            target=self._close_transport_objects,
            args=(channel, client),
            daemon=True,
            name="kace-ssh-deadline-close",
        )
        closer.start()

    def connect(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        deadline_seconds: float | None = None,
    ) -> bool:
        """Establish the complete SSH transaction within one wall-clock deadline."""
        seconds = (
            ssh_connect_deadline_seconds()
            if deadline_seconds is None
            else _validated_positive_seconds(deadline_seconds, "deadline_seconds")
        )
        deadline = time.monotonic() + seconds
        cancelled = threading.Event()
        finished = threading.Event()
        outcome: dict[str, object] = {}

        def worker() -> None:
            try:
                outcome["success"] = self._connect_until_deadline(
                    ip, username, password, deadline, cancelled
                )
            except Exception as exc:
                outcome["error"] = exc
            finally:
                if cancelled.is_set():
                    self.close()
                finished.set()

        thread = threading.Thread(target=worker, daemon=True, name="kace-ssh-connect")
        thread.start()
        wait_seconds = max(deadline - time.monotonic(), 0.0)
        if not finished.wait(wait_seconds):
            cancelled.set()
            self._abort_connect_nonblocking()
            raise SSHConnectionDeadlineExceeded(
                f"SSH connection deadline exceeded after {seconds:g} seconds."
            )
        error = outcome.get("error")
        if isinstance(error, Exception):
            raise error
        return bool(outcome.get("success"))

    def _connect_until_deadline(
        self,
        ip: str,
        username: str,
        password: str,
        deadline: float,
        cancelled: threading.Event,
    ) -> bool:
        """
        Establishes an SSH connection to the target device with exponential backoff retries.
        Supports custom ports specified in the format IP:PORT (e.g. 127.0.0.1:2222).

        HOST KEY SECURITY (TOFU — Trust On First Use):
        - On the first connection to a host, the key is accepted and persisted to
          ~/.kace/known_hosts.
        - On subsequent connections, the server key is validated against the stored key.
          If it has changed (potential MITM), the connection is rejected with a clear error.
        - AutoAddPolicy is only applied to *missing* entries; existing entries are always
          validated by paramiko regardless of policy, so key changes are always detected.
        """
        retries = 5
        delay = 1
        max_delay = 5

        target_ip = ip
        port = 22
        if ":" in ip:
            parts = ip.split(":")
            target_ip = parts[0]
            try:
                port = int(parts[1])
            except ValueError:
                pass

        known_hosts_path = _get_known_hosts_path(deadline=deadline)

        for attempt in range(retries + 1):
            if cancelled.is_set():
                raise SSHConnectionDeadlineExceeded("SSH connection deadline exceeded.")
            remaining = _remaining_seconds(deadline)
            client = paramiko.SSHClient()
            try:
                with self._state_lock:
                    abandoned = cancelled.is_set()
                    if not abandoned:
                        self.client = client
                if abandoned:
                    self._discard_client(client)
                    raise SSHConnectionDeadlineExceeded(
                        "SSH connection deadline exceeded."
                    )

                # Treat load, verification and persistence as one serialized
                # trust transaction. Otherwise a concurrent explicit clear can
                # be undone by a connection that loaded the previous key.
                with _known_hosts_lock(known_hosts_path, deadline=deadline):
                    try:
                        client.load_host_keys(known_hosts_path)
                    except FileNotFoundError:
                        pass  # Defensive: the path is normally created above.

                    # AutoAddPolicy handles *new* hosts only.
                    # For known hosts, paramiko always validates the key against
                    # load_host_keys() entries regardless of policy, so a key change
                    # (MITM indicator) raises BadHostKeyException unconditionally.
                    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                    phase_timeout = min(5.0, remaining)
                    client.connect(
                        target_ip,
                        port=port,
                        username=username,
                        password=password,
                        timeout=phase_timeout,
                        banner_timeout=phase_timeout,
                        auth_timeout=phase_timeout,
                        channel_timeout=phase_timeout,
                    )
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise SSHConnectionDeadlineExceeded(
                            "SSH connection deadline exceeded."
                        )

                    # A connection is not accepted if its TOFU identity cannot
                    # be persisted durably for the next verification.
                    _persist_host_keys_atomically(
                        client, known_hosts_path, lock_held=True
                    )
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise SSHConnectionDeadlineExceeded(
                            "SSH connection deadline exceeded."
                        )

                return True

            except paramiko.BadHostKeyException as e:
                # The remote host key changed — surface a clear error and do NOT retry.
                print(f"[SECURITY] SSH host key mismatch for {ip}: {e}")
                print(
                    f"[SECURITY] Expected key differs from stored key in {known_hosts_path}. "
                    "If this is a freshly re-flashed Pi, remove the old entry from "
                    f"{known_hosts_path} and reconnect."
                )
                self._discard_client(client)
                raise e

            except SSHConnectionDeadlineExceeded:
                self._discard_client(client)
                raise

            except Exception as e:
                print(f"SSH connection attempt {attempt + 1}/{retries + 1} failed to {ip}: {e}")
                self._discard_client(client)
                if cancelled.is_set():
                    raise SSHConnectionDeadlineExceeded(
                        "SSH connection deadline exceeded."
                    ) from e
                if attempt < retries:
                    remaining = _remaining_seconds(deadline)
                    if cancelled.wait(min(delay, remaining)):
                        raise SSHConnectionDeadlineExceeded(
                            "SSH connection deadline exceeded."
                        ) from e
                    delay = min(delay * 2, max_delay)
                else:
                    return False

    def run_command_stream(self, command: str, on_data_callback: Callable[[str], None], on_close_callback: Callable[[], None], cols: int = 80, rows: int = 24):
        """
        Executes a command on the remote Pi, streaming the stdout/stderr
        back asynchronously via the data callback.
        """
        if not self.client:
            on_data_callback("\r\n[SSH Connection Error]: Not connected.\r\n")
            on_close_callback()
            return
            
        def run():
            self.running = True
            import codecs
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            try:
                transport = self.client.get_transport()
                self.channel = transport.open_session()
                # Request pty with actual frontend terminal dimensions and 256color support
                self.channel.get_pty(term='xterm-256color', width=cols, height=rows)
                self.channel.exec_command(command)
                
                while self.running:
                    # Use select() to block until data is available or timeout
                    # This prevents CPU-burning busy-wait and ensures escape
                    # sequences arrive as complete contiguous chunks
                    r, _, _ = select.select([self.channel], [], [], 0.1)
                    if r:
                        data = self.channel.recv(16384)
                        if not data:
                            break
                        text = decoder.decode(data)
                        on_data_callback(text)
                    
                    # Check exit status if no more data is available
                    if self.channel.exit_status_ready() and not self.channel.recv_ready():
                        break
                        
            except Exception as e:
                on_data_callback(f"\r\n[SSH Stream Error]: {e}\r\n")
            finally:
                self.running = False
                if self.channel:
                    self.channel.close()
                on_close_callback()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def send_input(self, data: str):
        """
        Sends keyboard input to the running terminal channel.
        """
        if self.channel and not self.channel.closed:
            try:
                self.channel.send(data)
                return True
            except Exception as e:
                print(f"Error sending SSH input: {e}")
        return False

    def resize_pty(self, cols: int, rows: int):
        """
        Resizes the remote pseudo-terminal (PTY) window size.
        """
        if self.channel and not self.channel.closed:
            try:
                self.channel.resize_pty(width=cols, height=rows)
            except Exception as e:
                print(f"Error resizing remote PTY: {e}")

    def is_connected(self) -> bool:
        """Return whether this isolated session still owns an active transport."""
        if self.client is None:
            return False
        try:
            transport = self.client.get_transport()
            return bool(transport and transport.is_active())
        except Exception:
            return False

    def close(self):
        """
        Safely tears down active channels and connections.
        """
        channel, client = self._detach_transport()
        self._close_transport_objects(channel, client)

    def get_sftp(self):
        """Returns an open paramiko SFTP client, or None if not connected."""
        if self.client and self.client.get_transport() and self.client.get_transport().is_active():
            try:
                return self.client.open_sftp()
            except Exception:
                return None
        return None

    def list_directory(self, path: str) -> list:
        """Returns list of dicts: {name, is_dir, size, modified}"""
        sftp = self.get_sftp()
        if not sftp:
            return []
        try:
            import stat
            results = []
            for attr in sftp.listdir_attr(path):
                is_dir = stat.S_ISDIR(attr.st_mode)
                results.append({
                    "name": attr.filename,
                    "is_dir": is_dir,
                    "size": attr.st_size,
                    "modified": int(attr.st_mtime) if attr.st_mtime else 0
                })
            return results
        except Exception as e:
            print(f"SFTP list_directory error on path '{path}': {e}")
            return []
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception as close_err:
                    # LOW-06 FIX: Log instead of silently swallowing to surface channel leaks.
                    print(f"Warning: SFTP list_directory channel close error: {close_err}")

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Downloads a file from the Pi to local_path. Returns True on success."""
        sftp = self.get_sftp()
        if not sftp:
            return False
        try:
            import os
            # Ensure local parent directories exist
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)
            sftp.get(remote_path, local_path)
            return True
        except Exception as e:
            print(f"SFTP download_file error from '{remote_path}' to '{local_path}': {e}")
            return False
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception as close_err:
                    # LOW-06 FIX: Log instead of silently swallowing to surface channel leaks.
                    print(f"Warning: SFTP download_file channel close error: {close_err}")

    def read_text_file_result(self, relative_path: str, max_bytes: int = 256 * 1024):
        """Read a small home-relative file and distinguish absence from failure."""
        if not isinstance(relative_path, str) or not relative_path or relative_path.startswith("/"):
            return "error", None
        normalized = posixpath.normpath(relative_path.replace("\\", "/"))
        if normalized == ".." or normalized.startswith("../"):
            return "error", None
        sftp = self.get_sftp()
        if not sftp:
            return "error", None
        try:
            home = sftp.normalize(".")
            remote_path = posixpath.join(home, normalized)
            attrs = sftp.stat(remote_path)
            if attrs.st_size < 0 or attrs.st_size > max_bytes:
                return "error", None
            with sftp.open(remote_path, "rb") as source:
                payload = source.read(max_bytes + 1)
            if len(payload) > max_bytes:
                return "error", None
            return "ok", payload.decode("utf-8")
        except (FileNotFoundError, OSError) as exc:
            if getattr(exc, "errno", None) == 2:
                return "absent", None
            print(f"SFTP read_text_file error for '{normalized}': {exc}")
            return "error", None
        except Exception as exc:
            print(f"SFTP read_text_file error for '{normalized}': {exc}")
            return "error", None
        finally:
            try:
                sftp.close()
            except Exception as close_err:
                print(f"Warning: SFTP read_text_file channel close error: {close_err}")

    def read_text_file(self, relative_path: str, max_bytes: int = 256 * 1024):
        status, content = self.read_text_file_result(relative_path, max_bytes=max_bytes)
        return content if status == "ok" else None
