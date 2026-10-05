import socket
import ipaddress
import json
import os
import subprocess
import concurrent.futures
import threading
from typing import List, Dict

from backend.moonraker_client import MoonrakerHttpClient, MoonrakerHttpError


def _reverse_dns(ip: str, timeout: float = 0.5, default: str = "unknown") -> str:
    """Reverse DNS lookup with a short timeout to prevent thread pool starvation.

    Standard socket.gethostbyaddr blocks for the system resolver timeout
    (typically 2-5 seconds) on hosts without PTR records.  A daemon lookup
    thread lets the caller honor its deadline without ThreadPoolExecutor's
    context-manager shutdown waiting for the blocked resolver.
    """
    result = []

    def lookup():
        try:
            resolved = socket.gethostbyaddr(ip)
            if resolved:
                result.append(resolved[0])
        except Exception:
            pass

    worker = threading.Thread(target=lookup, name="kace-reverse-dns", daemon=True)
    worker.start()
    worker.join(max(float(timeout), 0.0))
    return result[0] if result else default

def resolve_hostname(hostname: str = "kace.local") -> str:
    """
    Attempts to resolve the hostname to an IP address.
    """
    try:
        ip = socket.gethostbyname(hostname)
        return ip
    except socket.gaierror:
        # Fallback in case of temporary failure
        return ""

def probe_ip_ports(ip: str, ports: List[int] = None, timeout: float = 0.5) -> Dict[int, bool]:
    """
    Probes specific ports on an IP to check if they are open.

    M3 FIX: Default ports list changed from a mutable list literal to None sentinel.
    Using a mutable list as a default argument is a Python footgun \u2014 the same list object
    is shared across all call sites, so any in-place mutation would persist across calls.
    """
    if ports is None:
        ports = [22, 7125]
    results = {}
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            # Connect to port
            res = s.connect_ex((ip, port))
            results[port] = (res == 0)
        except Exception:
            results[port] = False
        finally:
            s.close()
    return results

MAX_AUTOMATIC_SCAN_ADDRESSES = 1024


class DiscoveryError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def get_ipv4_interfaces() -> list[dict]:
    """Read active IPv4 prefixes without guessing a mask or contacting a host."""
    try:
        if os.name == "nt":
            script = (
                "$connected = @(Get-NetIPInterface -AddressFamily IPv4 | "
                "Where-Object ConnectionState -eq 'Connected'); "
                "@(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { "
                "$_.InterfaceIndex -in $connected.InterfaceIndex -and "
                "$_.AddressState -eq 'Preferred' } | "
                "Select-Object IPAddress,PrefixLength) | ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=5, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            records = json.loads(result.stdout.strip() or "[]")
            if isinstance(records, dict):
                return [records]
            return records if isinstance(records, list) else []
        result = subprocess.run(
            ["ip", "-j", "-4", "address", "show", "up"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return [
            {"IPAddress": address["local"], "PrefixLength": address["prefixlen"]}
            for interface in json.loads(result.stdout)
            for address in interface.get("addr_info", [])
            if address.get("family") == "inet"
        ]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return []


def get_local_subnet_ips() -> List[str]:
    """Use a single unambiguous active subnet; retain manual discovery otherwise."""
    networks = set()
    local_addresses = set()
    for record in get_ipv4_interfaces():
        try:
            interface = ipaddress.IPv4Interface(
                f"{record['IPAddress']}/{record['PrefixLength']}"
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise DiscoveryError("discoveryInvalidPrefix", "The interface prefix is invalid. Use a manual address.") from exc
        address = interface.ip
        if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
            continue
        local_addresses.add(address)
        networks.add(interface.network)
    if not networks:
        return []
    if len(networks) != 1:
        raise DiscoveryError("discoveryAmbiguous", "Several local networks are active. Use a manual address.")
    network = next(iter(networks))
    host_count = network.num_addresses if network.prefixlen >= 31 else network.num_addresses - 2
    if host_count > MAX_AUTOMATIC_SCAN_ADDRESSES:
        raise DiscoveryError("discoveryTooLarge", "This subnet exceeds the automatic scan limit. Use a manual address.")
    return [str(address) for address in network.hosts() if address not in local_addresses]


def check_klipper(ip: str) -> bool:
    """
    Checks if Klipper is running/configured on the device by querying Moonraker's /printer/info endpoint.
    """
    try:
        data = MoonrakerHttpClient(ip, timeout=0.5).get("/printer/info")
        if "result" in data and "state" in data["result"]:
            return True
    except (MoonrakerHttpError, ValueError):
        pass
    return False

def scan_network(custom_subnet_ips: List[str] = None) -> List[Dict]:
    """
    Scans the local network concurrently for active SSH, Moonraker, and Crowsnest ports.
    Returns a list of discovered devices.
    """
    discovered = []
    ips_to_scan = custom_subnet_ips if custom_subnet_ips is not None else get_local_subnet_ips()
    if custom_subnet_ips is None and not ips_to_scan:
        raise DiscoveryError("discoveryUnavailable", "No usable local interface prefix was found. Use a manual address.")
    if len(ips_to_scan) > MAX_AUTOMATIC_SCAN_ADDRESSES:
        raise DiscoveryError("discoveryTooLarge", "This subnet exceeds the automatic scan limit. Use a manual address.")
    
    # We use a ThreadPoolExecutor for fast parallel socket probing
    # 50 threads can scan 254 IPs on three ports in ~2-3 seconds with a 0.5s timeout
    max_workers = 60
    
    def worker(ip: str):
        ports_status = probe_ip_ports(ip, [22, 7125, 8080])
        ssh_open = ports_status.get(22, False)
        moonraker_open = ports_status.get(7125, False)
        crowsnest_open = ports_status.get(8080, False)
        
        if ssh_open or moonraker_open:
            hostname = _reverse_dns(ip, timeout=0.5, default="kace-discovered.local")
            klipper_detected = False
            if moonraker_open:
                klipper_detected = check_klipper(ip)
            return {
                "ip": ip,
                "hostname": hostname,
                "ssh": ssh_open,
                "moonraker": moonraker_open,
                "klipper": klipper_detected,
                "crowsnest": crowsnest_open
            }
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(worker, ips_to_scan)
        for r in results:
            if r:
                discovered.append(r)
                
    return discovered

def probe_manual_ip(ip: str) -> dict:
    """
    Probes SSH, Moonraker, and Crowsnest ports on a single manual IP to check if it's active.
    Supports custom ports specified in the format IP:PORT (e.g. 127.0.0.1:2222).
    """
    target_ip = ip
    ssh_port = 22
    if ":" in ip:
        parts = ip.split(":")
        target_ip = parts[0]
        try:
            ssh_port = int(parts[1])
        except ValueError:
            pass
            
    ports_status = probe_ip_ports(target_ip, [ssh_port, 7125, 8080], timeout=1.0)
    ssh_open = ports_status.get(ssh_port, False)
    moonraker_open = ports_status.get(7125, False)
    crowsnest_open = ports_status.get(8080, False)
    
    if ssh_open or moonraker_open:
        hostname = _reverse_dns(target_ip, timeout=1.0, default="kace-manual.local")
        klipper_detected = False
        if moonraker_open:
            klipper_detected = check_klipper(target_ip)
        return {
            "ip": ip,
            "hostname": hostname,
            "ssh": ssh_open,
            "moonraker": moonraker_open,
            "klipper": klipper_detected,
            "crowsnest": crowsnest_open
        }
    return None


if __name__ == "__main__":
    print("Testing resolve_hostname of kace.local:")
    ip = resolve_hostname("kace.local")
    print(f"Resolved: {ip}")
    
    print("\nScanning local subnet (this may take a few seconds)...")
    devices = scan_network()
    print(f"Found {len(devices)} active device(s):")
    for d in devices:
        print(f" - {d['hostname']} ({d['ip']}) | SSH: {d['ssh']} | Moonraker: {d['moonraker']}")
