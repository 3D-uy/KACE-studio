"""Pure provisioning validation shared by flashing and boot injection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backend.resources import load_release_contract


EXPECTED_BOOTSTRAP_SHA256 = load_release_contract()["kace"]["bootstrap_sha256"]
MIN_CACHE_FREE_BYTES = 5 * 1024**3
MIN_AUTOMATIC_TARGET_BYTES = 8_000_000_000


class ImageType(str, Enum):
    RASPIOS_VANILLA = "raspios_vanilla"
    MAINSAILOS_PREBAKED = "mainsailos_prebaked"
    FLUIDDPI_PREBAKED = "fluiddpi_prebaked"
    CUSTOM_VANILLA = "custom_vanilla"
    CUSTOM_PREBAKED = "custom_prebaked"

    @property
    def is_prebaked(self) -> bool:
        return self in {
            ImageType.MAINSAILOS_PREBAKED,
            ImageType.FLUIDDPI_PREBAKED,
            ImageType.CUSTOM_PREBAKED,
        }

    @property
    def is_custom(self) -> bool:
        return self in {ImageType.CUSTOM_VANILLA, ImageType.CUSTOM_PREBAKED}


class WifiSecurity(str, Enum):
    WPA2 = "wpa2"
    OPEN = "open"
    NONE = "none"


class ProvisioningValidationError(ValueError):
    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


@dataclass(frozen=True)
class ProvisioningData:
    image_type: ImageType
    image_path: str
    hostname: str
    wifi_ssid: str
    wifi_password: str
    wifi_security: WifiSecurity
    ssh_password: str
    dashboard_ui: str
    timezone: str
    pi_model: str
    os_arch: str
    ssh_enabled: bool
    crowsnest: bool
    username: str
    password_auth: bool
    power_relay: bool
    power_device: str
    power_gpio: Optional[int]
    power_active_low: bool
    restart_klipper_when_powered: bool


_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_POWER_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SUPPORTED_PI_ARCHES = {
    "pi5": {"64bit"},
    "pi4": {"32bit", "64bit"},
    "pi3": {"32bit", "64bit"},
    "pizero2w": {"32bit", "64bit"},
    "pizerow": {"32bit"},
}


def _invalid(field: str, message: str):
    raise ProvisioningValidationError(field, message)


def _coerce_image_type(value) -> ImageType:
    try:
        return value if isinstance(value, ImageType) else ImageType(str(value))
    except (TypeError, ValueError):
        _invalid("image_type", "Select a supported OS image family.")


def _coerce_wifi_security(value, ssid: str) -> WifiSecurity:
    if not ssid:
        return WifiSecurity.NONE
    try:
        security = value if isinstance(value, WifiSecurity) else WifiSecurity(str(value or "wpa2"))
    except (TypeError, ValueError):
        _invalid("wifi_security", "Wi-Fi security must be WPA2 or open.")
    if security is WifiSecurity.NONE:
        _invalid("wifi_security", "A configured Wi-Fi network must be WPA2 or open.")
    return security


def validate_provisioning(
    *,
    image_type,
    image_path: str,
    hostname: str,
    wifi_ssid: str,
    wifi_password: str,
    wifi_security="wpa2",
    ssh_password: str,
    dashboard_ui: str,
    timezone: str = "",
    pi_model: str = "",
    os_arch: str = "",
    ssh_enabled: bool = True,
    crowsnest: bool = False,
    username: str = "kace",
    password_auth: bool = True,
    power_relay: bool = False,
    power_device: str = "",
    power_gpio: Optional[int] = None,
    power_active_low: bool = False,
    restart_klipper_when_powered: bool = True,
    bootstrap_exists: bool = True,
    bootstrap_sha256: str = EXPECTED_BOOTSTRAP_SHA256,
    validate_media: bool = False,
    image_exists: bool = True,
    image_size_bytes: Optional[int] = None,
    target_size_bytes: Optional[int] = None,
    cache_free_bytes: Optional[int] = None,
) -> ProvisioningData:
    """Validate facts supplied by callers and return normalized immutable data.

    This function performs no filesystem, network, UI, or disk-writer operations.
    Callers gather those facts first, then both flashing and injection use this
    same authority for the provisioning contract.
    """

    resolved_image_type = _coerce_image_type(image_type)
    normalized_path = str(image_path or "").strip()

    normalized_hostname = str(hostname or "").strip().lower()
    if normalized_hostname.endswith(".local"):
        normalized_hostname = normalized_hostname[:-6]
    if not normalized_hostname or not _HOSTNAME_RE.fullmatch(normalized_hostname):
        _invalid(
            "hostname",
            "Hostname must be one DNS label using letters, numbers, and internal hyphens.",
        )

    normalized_username = str(username or "").strip()
    if not _USERNAME_RE.fullmatch(normalized_username):
        _invalid(
            "username",
            "Username must be 1-32 lowercase characters and start with a letter or underscore.",
        )

    normalized_ssh_password = str(ssh_password or "")
    if not 8 <= len(normalized_ssh_password) <= 1024 or any(
        char in normalized_ssh_password for char in ("\x00", "\r", "\n")
    ):
        _invalid("ssh_password", "Account password must contain 8-1024 characters without line breaks.")

    normalized_ssid = str(wifi_ssid or "")
    normalized_wifi_password = str(wifi_password or "")
    if len(normalized_ssid.encode("utf-8")) > 32 or any(char in normalized_ssid for char in ("\x00", "\r", "\n")):
        _invalid("wifi_ssid", "SSID must be at most 32 UTF-8 bytes without line breaks.")
    security = _coerce_wifi_security(wifi_security, normalized_ssid)
    if not normalized_ssid and normalized_wifi_password:
        _invalid("wifi_ssid", "SSID is required when a Wi-Fi password is provided.")
    if security is WifiSecurity.OPEN and normalized_wifi_password:
        _invalid("wifi_password", "Open Wi-Fi networks must not include a password.")
    if security is WifiSecurity.WPA2:
        is_hex_psk = bool(re.fullmatch(r"[0-9A-Fa-f]{64}", normalized_wifi_password))
        if not is_hex_psk and not 8 <= len(normalized_wifi_password) <= 63:
            _invalid("wifi_password", "WPA2 passphrases must contain 8-63 characters or 64 hexadecimal digits.")
        if any(char in normalized_wifi_password for char in ("\x00", "\r", "\n")):
            _invalid("wifi_password", "Wi-Fi passwords cannot contain line breaks.")

    normalized_dashboard = str(dashboard_ui or "").strip().lower()
    if normalized_dashboard not in {"mainsail", "fluidd", "both"}:
        _invalid("dashboard_ui", "Dashboard must be mainsail, fluidd, or both.")
    if resolved_image_type is ImageType.MAINSAILOS_PREBAKED and normalized_dashboard not in {"mainsail", "both"}:
        _invalid("dashboard_ui", "MainsailOS supports the Mainsail or both-dashboard provisioning modes.")
    if resolved_image_type is ImageType.FLUIDDPI_PREBAKED and normalized_dashboard != "fluidd":
        _invalid("dashboard_ui", "FluiddPi supports only the Fluidd provisioning mode.")

    normalized_pi_model = str(pi_model or "pi4").strip().lower()
    normalized_arch = str(os_arch or "64bit").strip().lower()
    allowed_arches = _SUPPORTED_PI_ARCHES.get(normalized_pi_model)
    if allowed_arches is None:
        _invalid("pi_model", "Select a supported Raspberry Pi model.")
    if normalized_arch not in allowed_arches:
        _invalid("os_arch", f"{normalized_pi_model} does not support the selected {normalized_arch} image.")
    if resolved_image_type is ImageType.FLUIDDPI_PREBAKED and normalized_arch != "32bit":
        _invalid("os_arch", "FluiddPi provides only a 32-bit Raspberry Pi image.")

    boolean_fields = {
        "ssh_enabled": ssh_enabled,
        "crowsnest": crowsnest,
        "password_auth": password_auth,
        "power_relay": power_relay,
        "power_active_low": power_active_low,
        "restart_klipper_when_powered": restart_klipper_when_powered,
    }
    for field, value in boolean_fields.items():
        if not isinstance(value, bool):
            _invalid(field, f"{field} must be a boolean.")

    normalized_power_device = str(power_device or "").strip()
    normalized_power_gpio = power_gpio
    if power_relay:
        if not _POWER_DEVICE_RE.fullmatch(normalized_power_device):
            _invalid("power_device", "Power device name contains unsupported characters.")
        if isinstance(normalized_power_gpio, bool) or not isinstance(normalized_power_gpio, int) or not 0 <= normalized_power_gpio <= 999:
            _invalid("power_gpio", "GPIO must be an integer between 0 and 999.")
    else:
        normalized_power_device = ""
        normalized_power_gpio = None

    if not bootstrap_exists:
        _invalid("bootstrap", "The required bootstrap.sh resource is missing.")
    if str(bootstrap_sha256 or "").lower() != EXPECTED_BOOTSTRAP_SHA256:
        _invalid("bootstrap", "bootstrap.sh does not match the pinned release hash.")

    if validate_media:
        if resolved_image_type.is_custom:
            if not normalized_path.lower().endswith(".img"):
                _invalid("image_path", "Custom images must be uncompressed .img files.")
            if not image_exists or image_size_bytes is None or image_size_bytes < 512:
                _invalid("image_path", "Custom image is missing or too small to be a disk image.")
            required_target_bytes = image_size_bytes
        else:
            expected_source = (
                "default_lite"
                if resolved_image_type is ImageType.RASPIOS_VANILLA
                else "default_prebaked"
            )
            if normalized_path not in {expected_source, "prebaked" if expected_source == "default_prebaked" else expected_source}:
                _invalid("image_path", "Image source does not match the selected image family.")
            required_target_bytes = MIN_AUTOMATIC_TARGET_BYTES
            if cache_free_bytes is None or cache_free_bytes < MIN_CACHE_FREE_BYTES:
                _invalid("free_space", "At least 5 GiB of local free space is required for image preparation.")
        if target_size_bytes is None or target_size_bytes < required_target_bytes:
            _invalid("target_drive", "Target drive is too small for the selected image.")

    return ProvisioningData(
        image_type=resolved_image_type,
        image_path=normalized_path,
        hostname=normalized_hostname,
        wifi_ssid=normalized_ssid,
        wifi_password=normalized_wifi_password,
        wifi_security=security,
        ssh_password=normalized_ssh_password,
        dashboard_ui=normalized_dashboard,
        timezone=str(timezone or "").strip() or "UTC",
        pi_model=normalized_pi_model,
        os_arch=normalized_arch,
        ssh_enabled=ssh_enabled,
        crowsnest=crowsnest,
        username=normalized_username,
        password_auth=password_auth,
        power_relay=power_relay,
        power_device=normalized_power_device,
        power_gpio=normalized_power_gpio,
        power_active_low=power_active_low,
        restart_klipper_when_powered=restart_klipper_when_powered,
    )
