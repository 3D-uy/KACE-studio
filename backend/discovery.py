import socket
import concurrent.futures
from typing import List, Dict


def _reverse_dns(ip: str, timeout: float = 0.5, default: str = "unknown") -> str:
    """Reverse DNS lookup with a short timeout to prevent thread pool starvation.

    Standard socket.gethostbyaddr blocks for the system resolver timeout
    (typically 2-5 seconds) on hosts without PTR records.  Wrapping it in a
    single-thread executor lets us bail out quickly.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(socket.gethostbyaddr, ip)
        try:
            result = future.result(timeout=timeout)
            return result[0] if result else default
        except (concurrent.futures.TimeoutError, Exception):
            return default

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

def get_local_subnet_ips() -> List[str]:
    """
    Discovers the active network interface and returns a list of all host IPs in its /24 subnet.
    Supports offline mode.
    """
    ips = []
    local_ip = ""
    
    # Method 1: Try dummy socket to public DNS (fastest if online)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
        
    # Method 2: Offline fallback via getaddrinfo on hostname
    if not local_ip or local_ip.startswith("127."):
        try:
            hostname = socket.gethostname()
            addr_infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for info in addr_infos:
                ip = info[4][0]
                if ip and not ip.startswith("127."):
                    local_ip = ip
                    break
        except Exception:
            pass
            
    # Method 3: Offline socket binding check
    if not local_ip or local_ip.startswith("127."):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    if local_ip and not local_ip.startswith("127."):
        parts = local_ip.split(".")
        if len(parts) == 4:
            subnet_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
            for i in range(1, 255):
                # Exclude local host IP to save time
                ip_str = f"{subnet_prefix}{i}"
                if ip_str != local_ip:
                    ips.append(ip_str)
    else:
        # Default fallback
        for i in range(1, 255):
            ips.append(f"192.168.1.{i}")
    return ips

def scan_network(custom_subnet_ips: List[str] = None) -> List[Dict]:
    """
    Scans the local network concurrently for active SSH and Moonraker ports.
    Returns a list of discovered devices.
    """
    discovered = []
    ips_to_scan = custom_subnet_ips if custom_subnet_ips is not None else get_local_subnet_ips()
    
    # We use a ThreadPoolExecutor for fast parallel socket probing
    # 50 threads can scan 254 IPs on two ports in ~2-3 seconds with a 0.5s timeout
    max_workers = 60
    
    def worker(ip: str):
        ports_status = probe_ip_ports(ip)
        ssh_open = ports_status.get(22, False)
        moonraker_open = ports_status.get(7125, False)
        
        if ssh_open or moonraker_open:
            hostname = _reverse_dns(ip, timeout=0.5, default="kace-discovered.local")
            return {
                "ip": ip,
                "hostname": hostname,
                "ssh": ssh_open,
                "moonraker": moonraker_open
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
    Probes SSH and Moonraker ports on a single manual IP to check if it's active.
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
            
    ports_status = probe_ip_ports(target_ip, [ssh_port, 7125], timeout=1.0)
    ssh_open = ports_status.get(ssh_port, False)
    moonraker_open = ports_status.get(7125, False)
    
    if ssh_open or moonraker_open:
        hostname = _reverse_dns(target_ip, timeout=1.0, default="kace-manual.local")
        return {
            "ip": ip,
            "hostname": hostname,
            "ssh": ssh_open,
            "moonraker": moonraker_open
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
