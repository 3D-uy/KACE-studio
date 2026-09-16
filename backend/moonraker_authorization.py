"""Explicit single-client enrollment over an already authenticated SSH session."""

import json
import shlex
import threading


AUTHORIZATION_TIMEOUT_SECONDS = 20.0

# Executed with the authenticated user's environment, never via sudo. Keeping
# discovery and mutation in the same program prevents a supplied IP from being
# trusted without checking the actual SSH connection again.
REMOTE_PROGRAM = r'''
import configparser
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


def client_address(connection):
    fields = connection.split()
    if len(fields) != 4:
        raise ValueError("SSH_CONNECTION is missing or ambiguous")
    client, client_port, server, server_port = fields
    for address in (client, server):
        if "%" in address:
            raise ValueError("Scoped SSH addresses cannot be authorized")
        parsed = ipaddress.ip_address(address)
        if parsed.is_unspecified or parsed.is_multicast:
            raise ValueError("Invalid SSH endpoint")
    if not all(port.isdecimal() and 0 < int(port) < 65536
               for port in (client_port, server_port)):
        raise ValueError("Invalid SSH endpoint port")
    address = ipaddress.ip_address(client)
    return str(address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address)
               and address.ipv4_mapped else address)


def enroll_content(content, address):
    address = ipaddress.ip_address(address)
    text = content.decode("utf-8")
    parser = configparser.ConfigParser(
        interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read_string(text)
    if not parser.has_section("authorization") or parser.defaults():
        raise ValueError("An unambiguous [authorization] section is required")
    if any(section.startswith("include ") for section in parser.sections()):
        raise ValueError("Included Moonraker configurations require manual authorization")
    if parser.getboolean("authorization", "force_logins", fallback=False):
        raise ValueError("Moonraker requires login; IP enrollment is unavailable")
    values = parser.get("authorization", "trusted_clients", fallback="")
    for value in re.split(r"[,\n]", values):
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError:
            continue
        if network.prefixlen == network.max_prefixlen and network.network_address == address:
            return content
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    active = False
    insertion = None
    option = None
    indentation = "    "
    for index, line in enumerate(lines):
        header = re.fullmatch(r"\s*\[([^]]+)\]\s*(?:[#;].*)?", line.strip())
        if header:
            active = header.group(1) == "authorization"
            if active:
                insertion = index + 1
        elif active:
            match = re.match(r"^([ \t]*)trusted_clients\s*[:=]", line)
            if match:
                option = index + 1
                indentation = match.group(1) + "    "
    position = option if option is not None else insertion
    if position is None:
        raise ValueError("Cannot locate [authorization]")
    if not lines[position - 1].endswith(("\n", "\r")):
        lines[position - 1] += newline
    addition = ("" if option is not None else "trusted_clients:" + newline)
    lines.insert(position, addition + indentation + str(address) + newline)
    return "".join(lines).encode("utf-8")


def replace_config(path, expected, content, attrs):
    descriptor, temporary = tempfile.mkstemp(prefix=".kace-authorization-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            current = os.fstat(output.fileno())
            if (current.st_uid, current.st_gid) != (attrs.st_uid, attrs.st_gid):
                os.fchown(output.fileno(), attrs.st_uid, attrs.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(attrs.st_mode))
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        now = path.lstat()
        if (now.st_dev, now.st_ino) != (attrs.st_dev, attrs.st_ino) or path.read_bytes() != expected:
            raise ValueError("moonraker.conf changed concurrently; enrollment aborted")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def authorize(path, address):
    import fcntl
    # Serialize Studio enrollments on this file, including restart/rollback.
    with open(path.parent / ".kace-authorization.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attrs = path.lstat()
        if not stat.S_ISREG(attrs.st_mode) or attrs.st_size > 2 * 1024 * 1024:
            raise ValueError("moonraker.conf must be a small regular file")
        original = path.read_bytes()
        updated = enroll_content(original, address)
        if updated == original:
            return False
        replace_config(path, original, updated, attrs)
        try:
            subprocess.run(["sudo", "-n", "systemctl", "restart", "moonraker"],
                           check=True, capture_output=True, timeout=10)
        except Exception as error:
            try:
                replace_config(path, updated, original, path.lstat())
            except Exception as rollback_error:
                raise RuntimeError("Moonraker restart and configuration restoration failed: "
                                   + str(rollback_error)) from error
            raise RuntimeError("Moonraker restart failed; configuration restored") from error
        return True


def main():
    address = client_address(os.environ.get("SSH_CONNECTION", ""))
    operation = sys.argv[1]
    if operation == "inspect":
        return {"ip": address, "changed": False}
    if operation != "authorize" or sys.argv[2] != address:
        raise ValueError("The approved IP does not match this SSH connection")
    changed = authorize(Path.home() / "printer_data/config/moonraker.conf", address)
    return {"ip": address, "changed": changed}


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, **main()}))
    except Exception as error:
        print(json.dumps({"ok": False, "detail": str(error)}))
        sys.exit(1)
'''


def moonraker_client_access(session, approved_ip=None):
    """Inspect or explicitly enroll on this transport; never select another host."""
    client = session.client
    transport = client.get_transport() if client else None
    if not transport or not transport.is_active() or not transport.is_authenticated():
        raise ValueError("An authenticated SSH session is required")
    arguments = ["python3", "-c", REMOTE_PROGRAM, "inspect"]
    if approved_ip is not None:
        if not isinstance(approved_ip, str) or not approved_ip:
            raise ValueError("An explicitly approved client IP is required")
        arguments[-1:] = ["authorize", approved_ip]
    channel = transport.open_session(timeout=5.0)
    timer = threading.Timer(AUTHORIZATION_TIMEOUT_SECONDS, channel.close)
    timer.daemon = True
    try:
        channel.settimeout(AUTHORIZATION_TIMEOUT_SECONDS)
        channel.set_combine_stderr(True)
        timer.start()
        channel.exec_command(" ".join(shlex.quote(arg) for arg in arguments))
        chunks = bytearray()
        while True:
            chunk = channel.recv(4096)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > 16384:
                raise ValueError("Unexpected SSH authorization response")
        status = channel.recv_exit_status()
        result = json.loads(chunks.decode("utf-8"))
        if status != 0 or result.get("ok") is not True:
            raise ValueError(result.get("detail") or "Moonraker authorization failed")
        return result
    finally:
        timer.cancel()
        channel.close()
