#!/bin/bash
# KACE Studio Bootstrapper Script
# Auto-installs Klipper, Moonraker, Mainsail/Fluidd, and Crowsnest based on user selections.

set -e

# ── Color Logging Helpers ────────────────────────────────────────────────────
# These functions emit consistently formatted, colored output for each stage.
# The === STAGE: <id> === markers are parsed by the KACE Studio frontend
# to drive the UI progress bar.

C_RESET="\033[0m"
C_CYAN="\033[1;36m"
C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"
C_RED="\033[1;31m"
C_BOLD="\033[1m"

BOOTSTRAP_PROTOCOL="kace-bootstrap/v1"
BOOTSTRAP_WORKFLOW_ID="${KACE_BOOTSTRAP_WORKFLOW_ID:-bootstrap-$(date +%s)-$$}"
case "$BOOTSTRAP_WORKFLOW_ID" in
    *[!A-Za-z0-9._-]*|'') BOOTSTRAP_WORKFLOW_ID="bootstrap-$(date +%s)-$$" ;;
esac
BOOTSTRAP_SEQUENCE=0
BOOTSTRAP_TERMINAL_EMITTED=0
CURRENT_BOOTSTRAP_STAGE="INIT"
POWER_RECONCILE_CONFIG=""
POWER_RECONCILE_BACKUP=""
POWER_RECONCILE_STATE=""
POWER_RECONCILE_STATE_BACKUP=""
POWER_RECONCILIATION_COMMITTED=0

emit_bootstrap_event() {
    local event="$1"
    local stage="${2:-$CURRENT_BOOTSTRAP_STAGE}"
    local code="${3:-}"
    local exit_code="${4:-0}"
    BOOTSTRAP_SEQUENCE=$((BOOTSTRAP_SEQUENCE + 1))
    printf '=== KACE_BOOTSTRAP_EVENT: {"protocol":"%s","event":"%s","workflow_id":"%s","sequence":%d,"stage":"%s","code":"%s","exit_code":%d} ===\n' \
        "$BOOTSTRAP_PROTOCOL" "$event" "$BOOTSTRAP_WORKFLOW_ID" \
        "$BOOTSTRAP_SEQUENCE" "$stage" "$code" "$exit_code"
}

emit_bootstrap_terminal() {
    local event="$1"
    local code="${2:-}"
    local exit_code="${3:-0}"
    if [ "$BOOTSTRAP_TERMINAL_EMITTED" -eq 1 ]; then
        return 0
    fi
    BOOTSTRAP_TERMINAL_EMITTED=1
    emit_bootstrap_event "$event" "$CURRENT_BOOTSTRAP_STAGE" "$code" "$exit_code"
}

log_stage() {
    # Usage: log_stage "STAGE_ID" "Human readable label"
    local id="$1"
    local label="$2"
    CURRENT_BOOTSTRAP_STAGE="$id"
    echo -e "\n${C_CYAN}=== ${label} ===${C_RESET}"
    echo -e "=== STAGE: ${id} ==="   # Machine-parseable marker (no color codes)
    emit_bootstrap_event "stage_started" "$id"
}

log_ok() {
    echo -e "${C_GREEN}✔  $1${C_RESET}"
}

log_warn() {
    echo -e "${C_YELLOW}⚠  $1${C_RESET}"
}

log_err() {
    echo -e "${C_RED}✘  $1${C_RESET}" >&2
}

# BEGIN KACE_DEPENDENCY_PINS
# Update these values only through an explicit, reviewed dependency update.
KLIPPER_REPOSITORY="https://github.com/Klipper3d/klipper.git"
KLIPPER_REF="9c1ae230eaebd5ec4df76d5a87537e2f35defab0"
MOONRAKER_REPOSITORY="https://github.com/Arksine/moonraker.git"
MOONRAKER_REF="d5ee17128bb88434aacdab90c2e9e990e2b64e4a"
CROWSNEST_REPOSITORY="https://github.com/mainsail-crew/crowsnest.git"
CROWSNEST_REF="cf936dabdcf6a3d1eb0138d8eea9044745b40db6"

MAINSAIL_VERSION="v2.18.2"
MAINSAIL_URL="https://github.com/mainsail-crew/mainsail/releases/download/${MAINSAIL_VERSION}/mainsail.zip"
MAINSAIL_SHA256="df2ba7c301f7bfc8ac9f122741a6ba08356d679ecfa1f62f898d0337802d5de5"
FLUIDD_VERSION="v1.37.3"
FLUIDD_URL="https://github.com/fluidd-core/fluidd/releases/download/${FLUIDD_VERSION}/fluidd.zip"
FLUIDD_SHA256="48e712e5f2cc59f7cfebd458174ddedff60e532ebcba3f9b844167fa27a22571"

MAINSAIL_CONFIG_REF="ff3869a621db17ce3ef660adbbd3fa321995ac42"
MAINSAIL_CONFIG_URL="https://raw.githubusercontent.com/mainsail-crew/mainsail-config/${MAINSAIL_CONFIG_REF}/client.cfg"
MAINSAIL_CONFIG_SHA256="29d4c97b099e481c25c0a875b3f0696850a6aafa67775aee8d05e8682352ffb4"
FLUIDD_CONFIG_REF="807175d72e3a00cdc6b5e249444a4630e1e03a55"
FLUIDD_CONFIG_URL="https://raw.githubusercontent.com/fluidd-core/fluidd-config/${FLUIDD_CONFIG_REF}/client.cfg"
FLUIDD_CONFIG_SHA256="f5511c153c36ab21513c2f9d12d59a4e7f34fc403ea1d2c199d82d99925675c0"

KACE_INSTALL_REF="5d07ec4ada156df552e45e81128cb2555b4a3045"
KACE_INSTALL_SHA256="2b805f348e429e3f35183f9b9989bf3ea4d56f5af95aefac9807447dbd256805"
KACE_INSTALL_URL="https://raw.githubusercontent.com/3D-uy/KACE/${KACE_INSTALL_REF}/install.sh"
readonly KLIPPER_REPOSITORY KLIPPER_REF MOONRAKER_REPOSITORY MOONRAKER_REF
readonly CROWSNEST_REPOSITORY CROWSNEST_REF MAINSAIL_VERSION MAINSAIL_URL MAINSAIL_SHA256
readonly FLUIDD_VERSION FLUIDD_URL FLUIDD_SHA256 MAINSAIL_CONFIG_REF MAINSAIL_CONFIG_URL
readonly MAINSAIL_CONFIG_SHA256 FLUIDD_CONFIG_REF FLUIDD_CONFIG_URL FLUIDD_CONFIG_SHA256
readonly KACE_INSTALL_REF KACE_INSTALL_SHA256 KACE_INSTALL_URL
# END KACE_DEPENDENCY_PINS

run_as_printer() {
    if [ "$(id -un)" = "$PRINTER_USER" ]; then
        "$@"
    else
        sudo -u "$PRINTER_USER" "$@"
    fi
}

ensure_pinned_git_checkout() {
    local label="$1"
    local repository="$2"
    local expected_ref="$3"
    local target="$4"
    local actual_ref=""
    local staging=""

    if [ -e "$target" ]; then
        if [ ! -d "$target/.git" ]; then
            log_err "$label exists but is not a Git checkout: $target"
            return 1
        fi
        if ! actual_ref=$(run_as_printer git -C "$target" rev-parse HEAD); then
            log_err "Could not determine the installed $label revision."
            return 1
        fi
        if [ "$actual_ref" = "$expected_ref" ]; then
            log_ok "$label already matches pinned revision $expected_ref."
            return 0
        fi
        log_err "$label revision mismatch. Expected $expected_ref, got $actual_ref."
        return 1
    fi

    if ! staging=$($SUDO mktemp -d "${target}.kace-staging.XXXXXX"); then
        log_err "Could not create a staging directory for $label."
        return 1
    fi
    $SUDO chown "$PRINTER_USER:$PRINTER_GROUP" "$staging"

    if ! run_as_printer git init "$staging" || \
       ! run_as_printer git -C "$staging" remote add origin "$repository" || \
       ! run_as_printer git -C "$staging" fetch --depth=1 origin "$expected_ref" || \
       ! run_as_printer git -C "$staging" checkout --detach "$expected_ref"; then
        log_err "Failed to fetch pinned $label revision $expected_ref."
        $SUDO rm -rf "$staging"
        return 1
    fi

    if ! actual_ref=$(run_as_printer git -C "$staging" rev-parse HEAD) || \
       [ "$actual_ref" != "$expected_ref" ]; then
        log_err "$label checkout verification failed. Expected $expected_ref, got ${actual_ref:-unknown}."
        $SUDO rm -rf "$staging"
        return 1
    fi

    if [ -e "$target" ]; then
        log_err "$label target appeared during installation: $target"
        $SUDO rm -rf "$staging"
        return 1
    fi
    $SUDO mv "$staging" "$target"
    log_ok "$label pinned revision $expected_ref installed."
}

download_verified_file() {
    local label="$1"
    local url="$2"
    local destination="$3"
    local expected_sha256="$4"
    local temporary=""
    local actual_sha256=""

    if ! temporary=$($SUDO mktemp "${destination}.kace-download.XXXXXX"); then
        log_err "Could not create a temporary download for $label."
        return 1
    fi
    if ! curl --fail --silent --show-error --location --retry 3 --retry-delay 5 \
        "$url" -o "$temporary"; then
        log_err "Failed to download $label."
        $SUDO rm -f "$temporary"
        return 1
    fi
    actual_sha256=$(sha256sum "$temporary" | cut -d" " -f1)
    if [ "$actual_sha256" != "$expected_sha256" ]; then
        log_err "$label integrity check failed. Expected $expected_sha256, got $actual_sha256."
        $SUDO rm -f "$temporary"
        return 1
    fi
    $SUDO mv -f "$temporary" "$destination"
}

install_verified_dashboard() {
    local label="$1"
    local url="$2"
    local expected_sha256="$3"
    local archive="$4"
    local target="$5"
    local identity="${url}#${expected_sha256}"
    local staging=""

    if [ -e "$target" ]; then
        if [ -f "$target/.kace-bootstrap-release" ] && \
           [ -f "$target/index.html" ] && \
           [ "$(cat "$target/.kace-bootstrap-release")" = "$identity" ]; then
            log_ok "$label already matches its verified release."
            return 0
        fi
        log_err "$label target exists without a verified release marker: $target"
        return 1
    fi

    download_verified_file "$label release archive" "$url" "$archive" "$expected_sha256" || return 1
    if ! staging=$($SUDO mktemp -d "$(dirname "$target")/.${label,,}.kace-staging.XXXXXX"); then
        log_err "Could not create a staging directory for $label."
        return 1
    fi
    if ! $SUDO unzip -q "$archive" -d "$staging" || [ ! -f "$staging/index.html" ]; then
        log_err "$label extraction failed before publication."
        $SUDO rm -rf "$staging"
        return 1
    fi
    printf '%s\n' "$identity" | $SUDO tee "$staging/.kace-bootstrap-release" > /dev/null
    $SUDO chown -R www-data:www-data "$staging"
    $SUDO chmod -R 755 "$staging"
    if [ -e "$target" ]; then
        log_err "$label target appeared during installation: $target"
        $SUDO rm -rf "$staging"
        return 1
    fi
    $SUDO mv "$staging" "$target"
}

# BEGIN KACE_CONFIG_DEFAULT_HELPER
# Add a missing INI-style section or option without changing existing values.
# Writes are atomic and repeated calls are no-ops once the entry exists.
ensure_config_entry() {
    local config_path="$1"
    local section_name="$2"
    local option_name="${3:-}"
    local default_value="${4:-}"

    python3 - "$config_path" "$section_name" "$option_name" "$default_value" <<'PY'
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

path = Path(sys.argv[1])
section_name = sys.argv[2]
option_name = sys.argv[3]
default_value = sys.argv[4]

content = path.read_text(encoding="utf-8") if path.exists() else ""
newline = "\r\n" if "\r\n" in content else "\n"
lines = content.splitlines()
section_re = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*(?:[#;].*)?$", re.IGNORECASE)

section_indexes = []
for index, line in enumerate(lines):
    match = section_re.match(line)
    if match and match.group(1).strip().casefold() == section_name.casefold():
        section_indexes.append(index)

changed = False
if not section_indexes:
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"[{section_name}]")
    if option_name:
        lines.append(f"{option_name}: {default_value}")
    changed = True
elif option_name:
    option_re = re.compile(
        rf"^\s*{re.escape(option_name)}\s*[:=]", re.IGNORECASE
    )
    option_found = False
    for section_start in section_indexes:
        section_end = len(lines)
        for index in range(section_start + 1, len(lines)):
            if section_re.match(lines[index]):
                section_end = index
                break
        if any(option_re.match(line) for line in lines[section_start + 1:section_end]):
            option_found = True
            break

    if not option_found:
        insert_at = len(lines)
        for index in range(section_indexes[0] + 1, len(lines)):
            if section_re.match(lines[index]):
                insert_at = index
                break
        while insert_at > section_indexes[0] + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"{option_name}: {default_value}")
        changed = True

if not changed:
    raise SystemExit(0)

path.parent.mkdir(parents=True, exist_ok=True)
mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
fd, temporary_name = tempfile.mkstemp(
    prefix=f".{path.name}.", suffix=".part", dir=str(path.parent)
)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as temporary:
        temporary.write(newline.join(lines) + newline)
        temporary.flush()
        os.fsync(temporary.fileno())
    os.chmod(temporary_name, mode)
    os.replace(temporary_name, path)
except BaseException:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise
PY
}
# END KACE_CONFIG_DEFAULT_HELPER

validate_power_relay_settings() {
    if [ "$POWER_RELAY" = "false" ] || [ -z "$POWER_RELAY" ]; then
        POWER_RELAY="false"
        return 0
    fi

    if [ "$POWER_RELAY" != "true" ]; then
        log_err "Invalid POWER_RELAY value '$POWER_RELAY'. Expected true or false."
        return 1
    fi
    if [[ ! "$POWER_DEVICE" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
        log_err "GPIO relay device name is missing or invalid."
        return 1
    fi
    if [[ ! "$POWER_GPIO" =~ ^[0-9]{1,3}$ ]]; then
        log_err "GPIO relay pin is missing or invalid."
        return 1
    fi
    if [[ ! "$POWER_ACTIVE_LOW" =~ ^(true|false)$ ]]; then
        log_err "GPIO relay active-low setting is missing or invalid."
        return 1
    fi
    if [[ ! "$POWER_RESTART_KLIPPER" =~ ^(true|false)$ ]]; then
        log_err "GPIO relay restart_klipper_when_powered setting is missing or invalid."
        return 1
    fi
    if [[ ! "$POWER_INITIAL_STATE" =~ ^(on|off)$ ]]; then
        log_err "GPIO relay initial_state setting is missing or invalid."
        return 1
    fi
    if [[ ! "$POWER_OFF_WHEN_SHUTDOWN" =~ ^(true|false)$ ]]; then
        log_err "GPIO relay off_when_shutdown setting is missing or invalid."
        return 1
    fi
}

begin_power_reconciliation() {
    local config_path="$1"
    local state_path="${2:-${POWER_CONFIG_PATH:-${PRINTER_HOME:+$PRINTER_HOME/.config/kace/power.json}}}"
    POWER_RECONCILE_CONFIG="$config_path"
    POWER_RECONCILE_STATE="$state_path"
    POWER_RECONCILIATION_COMMITTED=0
    if [ -f "$config_path" ]; then
        if ! POWER_RECONCILE_BACKUP=$(mktemp "${config_path}.kace-power-backup.XXXXXX") || \
           ! cp -p "$config_path" "$POWER_RECONCILE_BACKUP"; then
            [ -n "$POWER_RECONCILE_BACKUP" ] && rm -f "$POWER_RECONCILE_BACKUP"
            POWER_RECONCILE_BACKUP=""
            return 1
        fi
    else
        POWER_RECONCILE_BACKUP="__ABSENT__"
    fi
    if [ -n "$state_path" ] && [ -f "$state_path" ]; then
        if ! POWER_RECONCILE_STATE_BACKUP=$(mktemp "${state_path}.kace-power-backup.XXXXXX") || \
           ! cp -p "$state_path" "$POWER_RECONCILE_STATE_BACKUP"; then
            [ -n "$POWER_RECONCILE_STATE_BACKUP" ] && rm -f "$POWER_RECONCILE_STATE_BACKUP"
            [ -n "$POWER_RECONCILE_BACKUP" ] && [ "$POWER_RECONCILE_BACKUP" != "__ABSENT__" ] && \
                rm -f "$POWER_RECONCILE_BACKUP"
            POWER_RECONCILE_BACKUP=""
            POWER_RECONCILE_STATE_BACKUP=""
            return 1
        fi
    else
        POWER_RECONCILE_STATE_BACKUP="__ABSENT__"
    fi
}

rollback_power_reconciliation() {
    if [ "$POWER_RECONCILIATION_COMMITTED" -eq 1 ] || [ -z "$POWER_RECONCILE_CONFIG" ]; then
        return 0
    fi
    if [ "$POWER_RECONCILE_BACKUP" = "__ABSENT__" ]; then
        rm -f "$POWER_RECONCILE_CONFIG"
    elif [ -n "$POWER_RECONCILE_BACKUP" ] && [ -f "$POWER_RECONCILE_BACKUP" ]; then
        mv -f "$POWER_RECONCILE_BACKUP" "$POWER_RECONCILE_CONFIG"
    fi
    if [ -n "$POWER_RECONCILE_STATE" ]; then
        if [ "$POWER_RECONCILE_STATE_BACKUP" = "__ABSENT__" ]; then
            rm -f "$POWER_RECONCILE_STATE"
        elif [ -n "$POWER_RECONCILE_STATE_BACKUP" ] && [ -f "$POWER_RECONCILE_STATE_BACKUP" ]; then
            mv -f "$POWER_RECONCILE_STATE_BACKUP" "$POWER_RECONCILE_STATE"
        fi
    fi
    POWER_RECONCILIATION_COMMITTED=1
    log_warn "Rolled back moonraker.conf and power.json because power reconciliation did not commit."
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet moonraker 2>/dev/null; then
        if ! $SUDO systemctl restart moonraker; then
            log_warn "Moonraker could not be restarted after restoring its previous configuration."
        fi
    fi
}

commit_power_reconciliation() {
    # The verified Moonraker configuration and power.json are authoritative at
    # this point. Backup cleanup must not turn a committed transaction into a
    # partial rollback if unlinking one backup fails.
    POWER_RECONCILIATION_COMMITTED=1
    if [ -n "$POWER_RECONCILE_BACKUP" ] && [ "$POWER_RECONCILE_BACKUP" != "__ABSENT__" ]; then
        rm -f "$POWER_RECONCILE_BACKUP" || log_warn "Could not remove moonraker.conf power backup."
    fi
    if [ -n "$POWER_RECONCILE_STATE_BACKUP" ] && [ "$POWER_RECONCILE_STATE_BACKUP" != "__ABSENT__" ]; then
        rm -f "$POWER_RECONCILE_STATE_BACKUP" || log_warn "Could not remove power.json backup."
    fi
}

persist_power_controller_config() {
    local config_path="$PRINTER_HOME/.config/kace/power.json"
    python3 - "$config_path" "$POWER_RELAY" "$POWER_DEVICE" "$POWER_GPIO" \
        "$POWER_ACTIVE_LOW" "$POWER_RESTART_KLIPPER" "$POWER_INITIAL_STATE" \
        "$POWER_OFF_WHEN_SHUTDOWN" <<'PY'
import json
import os
from pathlib import Path
import sys
import tempfile

path = Path(sys.argv[1])
enabled = sys.argv[2] == "true"
desired = {
    "schema": "kace-power/v1",
    "revision": 1,
    "enabled": enabled,
    "device": sys.argv[3] if enabled else None,
    "pin": f"gpiochip0/gpio{sys.argv[4]}" if enabled else None,
    "active_low": sys.argv[5] == "true" if enabled else False,
    "restart_klipper_when_powered": sys.argv[6] == "true" if enabled else False,
    "initial_state": sys.argv[7] if enabled else "off",
    "off_when_shutdown": sys.argv[8] == "true" if enabled else True,
}
previous = None
try:
    previous = json.loads(path.read_text(encoding="utf-8"))
except FileNotFoundError:
    pass
except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
    raise RuntimeError(f"cannot replace invalid power.json safely: {exc}") from exc
if isinstance(previous, dict) and previous.get("schema") == "kace-power/v1":
    old_revision = previous.get("revision")
    if isinstance(old_revision, int) and not isinstance(old_revision, bool) and old_revision > 0:
        comparable = dict(previous)
        comparable["revision"] = 1
        if comparable == desired:
            desired["revision"] = old_revision
        else:
            desired["revision"] = old_revision + 1
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=".power.", suffix=".tmp", dir=str(path.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as target:
        json.dump(desired, target, indent=2, sort_keys=True)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
    $SUDO chown "$PRINTER_USER:$PRINTER_GROUP" \
        "$PRINTER_HOME/.config/kace" "$config_path"
}

reconcile_power_relay_section() {
    local config_path="$1"
    local state_path="${POWER_CONFIG_PATH:-${PRINTER_HOME:+$PRINTER_HOME/.config/kace/power.json}}"
    python3 - "$config_path" "$state_path" "$POWER_RELAY" "$POWER_DEVICE" "$POWER_GPIO" \
        "$POWER_ACTIVE_LOW" "$POWER_RESTART_KLIPPER" "$POWER_INITIAL_STATE" \
        "$POWER_OFF_WHEN_SHUTDOWN" <<'PY'
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

path = Path(sys.argv[1])
state_path = Path(sys.argv[2]) if sys.argv[2] else None
enabled = sys.argv[3] == "true"
device = sys.argv[4] if enabled else None
pin = f"gpiochip0/gpio{sys.argv[5]}" if enabled else None
if enabled and sys.argv[6] == "true":
    pin = f"!{pin}"
expected = [
    "type: gpio",
    f"pin: {pin}" if enabled else None,
    f"restart_klipper_when_powered: {sys.argv[7]}" if enabled else None,
    f"initial_state: {sys.argv[8]}" if enabled else None,
    f"off_when_shutdown: {sys.argv[9]}" if enabled else None,
]
expected = [line for line in expected if line is not None]
begin_marker = "# BEGIN KACE MANAGED: power"
end_marker = "# END KACE MANAGED: power"

content = path.read_text(encoding="utf-8")
newline = "\r\n" if "\r\n" in content else "\n"
lines = content.splitlines()
section_re = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*(?:[#;].*)?$", re.IGNORECASE)
managed_option_re = re.compile(
    r"^\s*(type|pin|restart_klipper_when_powered|initial_state|off_when_shutdown)\s*[:=]",
    re.IGNORECASE,
)

def spans(source):
    headers = []
    for index, line in enumerate(source):
        match = section_re.match(line)
        if match:
            headers.append((match.group(1).strip(), index))
    return [
        (name, start, headers[i + 1][1] if i + 1 < len(headers) else len(source))
        for i, (name, start) in enumerate(headers)
    ]

previous_device = None
if state_path and state_path.exists():
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot reconcile against invalid power.json: {exc}") from exc
    if not isinstance(previous, dict) or previous.get("schema") not in (1, "kace-power/v1"):
        raise RuntimeError("cannot reconcile against unsupported power.json schema")
    if not isinstance(previous.get("enabled"), bool):
        raise RuntimeError("cannot reconcile against invalid power.json enabled value")
    if previous["enabled"]:
        candidate = previous.get("device")
        if not isinstance(candidate, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", candidate):
            raise RuntimeError("cannot reconcile against invalid power.json device")
        previous_device = candidate
    if previous.get("schema") == "kace-power/v1":
        required = {
            "revision", "device", "pin", "active_low", "initial_state",
            "restart_klipper_when_powered", "off_when_shutdown",
        }
        if not required.issubset(previous):
            raise RuntimeError("cannot reconcile against incomplete versioned power.json")
        revision = previous.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise RuntimeError("cannot reconcile against invalid power.json revision")
        for key in ("active_low", "restart_klipper_when_powered", "off_when_shutdown"):
            if not isinstance(previous.get(key), bool):
                raise RuntimeError(f"cannot reconcile against invalid power.json {key}")
        if previous.get("initial_state") not in ("on", "off"):
            raise RuntimeError("cannot reconcile against invalid power.json initial_state")
        if previous["enabled"]:
            if not isinstance(previous.get("pin"), str) or not re.fullmatch(
                r"gpiochip[0-9]+/gpio[0-9]{1,3}", previous["pin"]
            ):
                raise RuntimeError("cannot reconcile against invalid power.json pin")
        elif previous.get("device") is not None or previous.get("pin") is not None:
            raise RuntimeError("disabled power.json must not name a device or pin")

begin_indexes = [i for i, line in enumerate(lines) if line.strip() == begin_marker]
end_indexes = [i for i, line in enumerate(lines) if line.strip() == end_marker]
owned = {previous_device.casefold()} if previous_device else set()
for marker in begin_indexes:
    next_index = marker + 1
    while next_index < len(lines) and (
        not lines[next_index].strip()
        or lines[next_index].lstrip().startswith(("#", ";"))
    ):
        next_index += 1
    if next_index < len(lines):
        match = section_re.match(lines[next_index])
        if match and match.group(1).strip().casefold().startswith("power "):
            owned.add(match.group(1).strip()[6:].strip().casefold())
marker_set = set(begin_indexes + end_indexes)
lines = [line for index, line in enumerate(lines) if index not in marker_set]

existing_names = {
    name[6:].strip().casefold()
    for name, _start, _end in spans(lines)
    if name.casefold().startswith("power ")
}
if enabled and device.casefold() in existing_names and device.casefold() not in owned:
    raise RuntimeError(f"unmanaged Moonraker [power {device}] already exists")

preserved = []
for name, start, end in spans(lines):
    if name.casefold().startswith("power ") and name[6:].strip().casefold() in owned:
        preserved.extend(
            line for line in lines[start + 1:end]
            if line.strip() and not managed_option_re.match(line)
        )
for name, start, end in reversed(spans(lines)):
    if name.casefold().startswith("power ") and name[6:].strip().casefold() in owned:
        del lines[start:end]
while lines and not lines[-1].strip():
    lines.pop()
if enabled:
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend([begin_marker, f"[power {device}]", *expected, *preserved, end_marker])

new_content = newline.join(lines) + newline
if new_content == content:
    raise SystemExit(0)

mode = stat.S_IMODE(path.stat().st_mode)
fd, temporary_name = tempfile.mkstemp(
    prefix=f".{path.name}.", suffix=".part", dir=str(path.parent)
)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as temporary:
        temporary.write(new_content)
        temporary.flush()
        os.fsync(temporary.fileno())
    os.chmod(temporary_name, mode)
    os.replace(temporary_name, path)
except BaseException:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise
PY
}

upsert_power_relay_section() {
    reconcile_power_relay_section "$1"
}

verify_power_relay_section() {
    local config_path="$1"
    local state_path="${POWER_CONFIG_PATH:-${PRINTER_HOME:+$PRINTER_HOME/.config/kace/power.json}}"
    python3 - "$config_path" "$state_path" "$POWER_RELAY" "$POWER_DEVICE" "$POWER_GPIO" \
        "$POWER_ACTIVE_LOW" "$POWER_RESTART_KLIPPER" "$POWER_INITIAL_STATE" \
        "$POWER_OFF_WHEN_SHUTDOWN" <<'PY'
import json
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
state_path = Path(sys.argv[2]) if sys.argv[2] else None
enabled = sys.argv[3] == "true"
device = sys.argv[4] if enabled else None
pin = f"gpiochip0/gpio{sys.argv[5]}" if enabled else None
if enabled and sys.argv[6] == "true":
    pin = f"!{pin}"
expected = {
    "type": "gpio",
    "pin": pin,
    "restart_klipper_when_powered": sys.argv[7],
    "initial_state": sys.argv[8],
    "off_when_shutdown": sys.argv[9],
} if enabled else {}
previous_device = None
if state_path and state_path.exists():
    previous = json.loads(state_path.read_text(encoding="utf-8"))
    if isinstance(previous, dict) and previous.get("enabled") is True:
        previous_device = previous.get("device")

content = path.read_text(encoding="utf-8")
lines = content.splitlines()
section_re = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*(?:[#;].*)?$", re.IGNORECASE)
begin_marker = "# BEGIN KACE MANAGED: power"
end_marker = "# END KACE MANAGED: power"
if enabled:
    if sum(line.strip() == begin_marker for line in lines) != 1 or sum(line.strip() == end_marker for line in lines) != 1:
        raise RuntimeError("expected exactly one KACE-managed power block")
else:
    if any(line.strip() in (begin_marker, end_marker) for line in lines):
        raise RuntimeError("disabled power configuration retained KACE managed markers")
    if previous_device:
        stale = [
            line for line in lines
            if (match := section_re.match(line))
            and match.group(1).strip().casefold() == f"power {previous_device}".casefold()
        ]
        if stale:
            raise RuntimeError(f"disabled power configuration retained [power {previous_device}]")
    raise SystemExit(0)

section_name = f"power {device}"
section_indexes = [
    index
    for index, line in enumerate(lines)
    if (match := section_re.match(line))
    and match.group(1).strip().casefold() == section_name.casefold()
]
if len(section_indexes) != 1:
    raise RuntimeError(f"expected exactly one [{section_name}] section")

section_start = section_indexes[0]
section_end = len(lines)
for index in range(section_start + 1, len(lines)):
    if section_re.match(lines[index]):
        section_end = index
        break

actual = {}
option_re = re.compile(r"^\s*([A-Za-z0-9_]+)\s*[:=]\s*(.*?)\s*$")
for line in lines[section_start + 1:section_end]:
    match = option_re.match(line)
    if not match:
        continue
    key = match.group(1).casefold()
    if key in expected:
        if key in actual:
            raise RuntimeError(f"duplicate option '{key}' in [{section_name}]")
        actual[key] = match.group(2)

for key, expected_value in expected.items():
    actual_value = actual.get(key)
    if actual_value is None or actual_value.casefold() != expected_value.casefold():
        raise RuntimeError(
            f"[{section_name}] {key} mismatch: expected {expected_value!r}, got {actual_value!r}"
        )
PY
}

verify_requested_power_relay() {
    local config_path="$1"
    if ! verify_power_relay_section "$config_path"; then
        echo "=== KACE_BOOTSTRAP_ERROR: GPIO_RELAY_VERIFY ==="
        log_err "GPIO relay verification failed; preserving bootstrap configuration for diagnosis."
        return 1
    fi
}

cleanup_bootstrap_config() {
    local boot_config="$1"
    [ -n "$boot_config" ] && [ -f "$boot_config" ] || return 0
    if [ -n "$SUDO" ]; then
        if ! $SUDO rm -f "$boot_config"; then
            log_warn "Could not remove $boot_config after successful verification."
        fi
    elif ! rm -f "$boot_config"; then
        log_warn "Could not remove $boot_config after successful verification."
    fi
}

finalize_bootstrap_success() {
    local config_path="$1"
    local boot_config="$2"
    verify_requested_power_relay "$config_path" || return 1
    cleanup_bootstrap_config "$boot_config"
    echo -e "\n${C_GREEN}${C_BOLD}"
    echo "========================================================"
    echo " Bootstrap complete! KACE wizard finished successfully. "
    echo "========================================================"
    echo -e "${C_RESET}"
    emit_bootstrap_terminal "workflow_succeeded" "SUCCESS" 0
}

ensure_moonraker_config() {
    local config_path="$1"
    local socket_path="$2"

    if [ ! -f "$config_path" ]; then
        local temporary_config
        temporary_config=$(mktemp "${config_path}.XXXXXX")
        cat > "$temporary_config" <<EOF
[server]
host: 0.0.0.0
port: 7125
klippy_uds_address: $socket_path

[authorization]
trusted_clients:
    127.0.0.1
    10.0.0.0/8
    127.0.0.0/8
    172.16.0.0/12
    192.168.0.0/16
    FE80::/10
    ::1/128
cors_domains:
    *.lan
    *.local
    *://my.mainsail.xyz
    *://app.fluidd.xyz

[octoprint_compat]

[history]

[file_manager]
enable_object_processing: True
EOF
        chmod 644 "$temporary_config"
        mv -f "$temporary_config" "$config_path"
    fi

    ensure_config_entry "$config_path" "file_manager" "enable_object_processing" "True" || return 1
    # Power is reconciled last so generic INI insertion cannot split the
    # ownership marker from its immediately adjacent [power ...] section.
    reconcile_power_relay_section "$config_path" || return 1
    verify_requested_power_relay "$config_path" || return 1
}

_positive_timeout_or_default() {
    local value="$1"
    local default_value="$2"
    if [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
        printf '%s\n' "$value"
    else
        printf '%s\n' "$default_value"
    fi
}

wait_for_moonraker_api() {
    local base_url="$1"
    local timeout_seconds="$2"
    local deadline=$((SECONDS + timeout_seconds))

    while (( SECONDS <= deadline )); do
        if curl --fail --silent --max-time 5 "$base_url/server/info" > /dev/null; then
            log_ok "Moonraker API is ready."
            return 0
        fi
        sleep 1
    done

    log_err "Moonraker API did not become ready within ${timeout_seconds}s."
    return 1
}

read_power_device_state() {
    local base_url="$1"
    local device_name="$2"
    local response=""

    if ! response=$(curl --fail --silent --max-time 5 \
        "$base_url/machine/device_power/devices"); then
        return 2
    fi

    MOONRAKER_POWER_RESPONSE="$response" python3 - "$device_name" <<'PY'
import json
import os
import sys

device_name = sys.argv[1]
try:
    payload = json.loads(os.environ["MOONRAKER_POWER_RESPONSE"])
    result = payload.get("result", payload)
    devices = result["devices"]
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(4)

if not isinstance(devices, list):
    raise SystemExit(4)

for device in devices:
    if isinstance(device, dict) and device.get("device") == device_name:
        state = str(device.get("status", "")).strip().lower()
        if not state:
            raise SystemExit(4)
        print(state)
        raise SystemExit(0)

raise SystemExit(3)
PY
}

verify_power_api_configuration() {
    local base_url="$1"
    local state_path="${POWER_CONFIG_PATH:-${PRINTER_HOME:+$PRINTER_HOME/.config/kace/power.json}}"
    local response=""
    if ! response=$(curl --fail --silent --max-time 5 \
        "$base_url/machine/device_power/devices"); then
        log_err "Moonraker Power API could not be queried after reconciliation."
        return 1
    fi
    if ! MOONRAKER_POWER_RESPONSE="$response" python3 - "$state_path" "$POWER_RELAY" "$POWER_DEVICE" <<'PY'
import json
import os
from pathlib import Path
import sys

state_path = Path(sys.argv[1]) if sys.argv[1] else None
enabled = sys.argv[2] == "true"
desired_device = sys.argv[3] if enabled else None
previous_device = None
if state_path and state_path.exists():
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot verify against invalid power.json: {exc}") from exc
    if isinstance(previous, dict) and previous.get("enabled") is True:
        previous_device = previous.get("device")

payload = json.loads(os.environ["MOONRAKER_POWER_RESPONSE"])
result = payload.get("result", payload)
devices = result.get("devices") if isinstance(result, dict) else None
if not isinstance(devices, list):
    raise RuntimeError("Moonraker returned an invalid power device list")
if enabled:
    matches = [item for item in devices if isinstance(item, dict) and item.get("device") == desired_device]
    if len(matches) != 1 or str(matches[0].get("type", "")).casefold() != "gpio":
        raise RuntimeError(
            f"expected exactly one gpio Moonraker power device named {desired_device!r}"
        )
if previous_device and previous_device != desired_device:
    if any(isinstance(item, dict) and item.get("device") == previous_device for item in devices):
        raise RuntimeError(f"stale Moonraker power device {previous_device!r} is still active")
PY
    then
        log_err "Moonraker Power API does not match the requested KACE power configuration."
        return 1
    fi
    if [ "$POWER_RELAY" = "true" ]; then
        log_ok "Moonraker exposes exactly one intended GPIO power device '$POWER_DEVICE'."
    else
        log_ok "Moonraker confirms the previous KACE-managed power device is disabled."
    fi
}

wait_for_power_device_ready() {
    local base_url="$1"
    local device_name="$2"
    local timeout_seconds="$3"
    local deadline=$((SECONDS + timeout_seconds))
    local state=""
    local status=0

    while (( SECONDS <= deadline )); do
        if state=$(read_power_device_state "$base_url" "$device_name"); then
            case "$state" in
                on|off)
                    log_ok "Moonraker power device '$device_name' is ready (state: $state)."
                    return 0
                    ;;
                init)
                    sleep 1
                    continue
                    ;;
                error)
                    log_err "Moonraker power device '$device_name' entered the error state."
                    return 1
                    ;;
                *)
                    log_err "Moonraker power device '$device_name' reported an unknown state: $state"
                    return 1
                    ;;
            esac
        else
            status=$?
            case "$status" in
                2)
                    sleep 1
                    continue
                    ;;
                3)
                    log_err "Moonraker power device '$device_name' was not found in /machine/device_power/devices."
                    return 1
                    ;;
                *)
                    log_err "Moonraker returned an invalid power-device response."
                    return 1
                    ;;
            esac
        fi
    done

    log_err "Moonraker power device '$device_name' remained in init for more than ${timeout_seconds}s."
    return 1
}

request_power_device_on() {
    local base_url="$1"
    local device_name="$2"
    local payload="{\"device\":\"${device_name}\",\"action\":\"on\"}"

    if ! curl --fail --silent --show-error --max-time 10 \
        -X POST \
        -H "Content-Type: application/json" \
        --data "$payload" \
        "$base_url/machine/device_power/device" > /dev/null; then
        log_err "Moonraker failed to power on device '$device_name'."
        return 1
    fi

    log_ok "Moonraker accepted the explicit ON command for '$device_name'."
}

wait_for_power_device_on() {
    local base_url="$1"
    local device_name="$2"
    local timeout_seconds="$3"
    local deadline=$((SECONDS + timeout_seconds))
    local state=""
    local status=0

    while (( SECONDS <= deadline )); do
        if state=$(read_power_device_state "$base_url" "$device_name"); then
            case "$state" in
                on)
                    log_ok "Moonraker power device '$device_name' is confirmed ON."
                    return 0
                    ;;
                off|init)
                    sleep 1
                    continue
                    ;;
                error)
                    log_err "Moonraker power device '$device_name' entered the error state after the ON command."
                    return 1
                    ;;
                *)
                    log_err "Moonraker power device '$device_name' reported an unknown state: $state"
                    return 1
                    ;;
            esac
        else
            status=$?
            if [ "$status" -eq 2 ]; then
                sleep 1
                continue
            fi
            if [ "$status" -eq 3 ]; then
                log_err "Moonraker power device '$device_name' disappeared after the ON command."
            else
                log_err "Moonraker returned an invalid power-device response after the ON command."
            fi
            return 1
        fi
    done

    log_err "Moonraker power device '$device_name' did not reach ON within ${timeout_seconds}s."
    return 1
}

find_connected_mcu_path() {
    local device_root="${KACE_MCU_DEVICE_ROOT:-/dev}"
    local candidate=""

    for candidate in \
        "$device_root"/serial/by-id/* \
        "$device_root"/serial/by-path/* \
        "$device_root"/ttyUSB* \
        "$device_root"/ttyACM*; do
        if [ -e "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

wait_for_powered_mcu() {
    local timeout_seconds="$1"
    local deadline=$((SECONDS + timeout_seconds))
    local mcu_path=""

    while (( SECONDS <= deadline )); do
        if mcu_path=$(find_connected_mcu_path); then
            log_ok "MCU detected after printer power-on: $mcu_path"
            return 0
        fi
        sleep 1
    done

    log_err "No MCU appeared in /dev/serial/by-id, /dev/serial/by-path, /dev/ttyUSB*, or /dev/ttyACM* within ${timeout_seconds}s after power-on."
    return 1
}

prepare_power_relay_for_kace() {
    if [ "$POWER_RELAY" != "true" ]; then
        return 0
    fi

    local base_url="http://127.0.0.1:7125"
    local api_timeout
    local device_timeout
    local power_on_timeout
    local mcu_timeout
    api_timeout=$(_positive_timeout_or_default "${KACE_POWER_API_TIMEOUT:-}" 90)
    device_timeout=$(_positive_timeout_or_default "${KACE_POWER_DEVICE_TIMEOUT:-}" 30)
    power_on_timeout=$(_positive_timeout_or_default "${KACE_POWER_ON_TIMEOUT:-}" 30)
    mcu_timeout=$(_positive_timeout_or_default "${KACE_POWER_MCU_TIMEOUT:-}" 120)

    wait_for_moonraker_api "$base_url" "$api_timeout" || return 1
    wait_for_power_device_ready "$base_url" "$POWER_DEVICE" "$device_timeout" || return 1
    request_power_device_on "$base_url" "$POWER_DEVICE" || return 1
    wait_for_power_device_on "$base_url" "$POWER_DEVICE" "$power_on_timeout" || return 1
    wait_for_powered_mcu "$mcu_timeout" || return 1
}

if [ "${KACE_BOOTSTRAP_LIB_ONLY:-}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

wait_for_apt_locks() {
    echo "Checking for package manager locks..."
    local count=0
    # Wait up to 5 minutes (60 * 5s)
    while [ $count -lt 60 ]; do
        local locked=0
        if pgrep -f "apt-get|dpkg|unattended-upgrades" >/dev/null 2>&1; then
            locked=1
        fi
        
        # Check file locks on standard apt/dpkg lock files using flock (no interpreter overhead)
        if [ $locked -eq 0 ]; then
            for _lockfile in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock \
                             /var/lib/apt/lists/lock /var/cache/apt/archives/lock; do
                if [ -f "$_lockfile" ] && ! $SUDO flock -n "$_lockfile" true 2>/dev/null; then
                    locked=1
                    break
                fi
            done
        fi
        
        if [ $locked -eq 0 ]; then
            echo "No background package manager is active."
            return 0
        fi
        
        echo "Apt or dpkg is currently locked by a background process. Waiting 5s (attempt $((count+1))/60)..."
        sleep 5
        count=$((count+1))
    done
    echo "Warning: package locks were not released after 5 minutes. Proceeding anyway..."
}

# ── Camera Hardware Detection ────────────────────────────────────────────────
# Returns 0 (true) if any physical camera is detected, 1 (false) otherwise.
#
# Three detection layers are attempted in order:
#   1. USB/UVC cameras  — checks /dev/v4l/by-id/ (udev only populates this for
#      real devices; BCM2835 VPU codec nodes are NOT symlinked here, so no
#      false positives from the Raspberry Pi's built-in video codecs).
#   2. Modern CSI cameras — libcamera-hello --list-cameras (Pi Camera v2/v3,
#      Arducam IMX519, etc. on the libcamera stack).
#   3. Legacy CSI cameras — vcgencmd get_camera (Pi Camera v1 on legacy MMAL).
#
# This function is idempotent: safe to call multiple times in a run.
detect_camera_hardware() {
    # 1. USB / UVC webcams
    if [ -d /dev/v4l/by-id ] && [ "$(ls -A /dev/v4l/by-id 2>/dev/null)" ]; then
        echo "Camera detected via /dev/v4l/by-id."
        return 0
    fi

    # 2. Modern CSI cameras (libcamera stack)
    if command -v libcamera-hello &>/dev/null; then
        if libcamera-hello --list-cameras 2>/dev/null | grep -q -E '^[0-9]+\s*:'  ; then
            echo "Camera detected via libcamera-hello."
            return 0
        fi
    fi

    # 3. Legacy CSI cameras (MMAL/vcgencmd stack)
    if command -v vcgencmd &>/dev/null; then
        if vcgencmd get_camera 2>/dev/null | grep -q -E 'detected=1'; then
            echo "Camera detected via vcgencmd."
            return 0
        fi
    fi

    return 1
}

# ── Privileges & Logging ─────────────────────────────────────────────────────
SUDO=""
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
fi

# Fix local hostname resolution if missing (prevents "sudo: unable to resolve host" warnings)
if command -v getent &>/dev/null && command -v hostname &>/dev/null; then
    _HOSTNAME=$(hostname)
    if [ -n "$_HOSTNAME" ] && ! getent hosts "$_HOSTNAME" &>/dev/null; then
        echo -e "${C_YELLOW}⚠  Local hostname '${_HOSTNAME}' is not resolvable.${C_RESET}"
        echo "Attempting to add '${_HOSTNAME}' to /etc/hosts to prevent sudo warnings..."
        echo "127.0.1.1 $_HOSTNAME" | $SUDO tee -a /etc/hosts >/dev/null
        echo -e "${C_GREEN}✔  Added ${_HOSTNAME} to /etc/hosts${C_RESET}"
    fi
fi

LOG_FILE=""
if [ "$EUID" -eq 0 ]; then
    LOG_FILE="/var/log/kace-bootstrap.log"
else
    LOG_FILE="$HOME/kace-bootstrap.log"
fi

# Redirect stdout and stderr to the log file while preserving console output
exec > >(tee -i "$LOG_FILE") 2>&1

echo -e "\n${C_CYAN}${C_BOLD}"
echo "========================================================"
echo "    KACE Studio: Klipper Automated Setup Bootstrapper   "
echo "========================================================"
echo -e "${C_RESET}"
echo "Logging execution output to: $LOG_FILE"

# ── Traps & Cleanup ──────────────────────────────────────────────────────────
cleanup() {
    rm -f /tmp/mainsail.zip /tmp/fluidd.zip /tmp/kace-install.sh
}

exit_handler() {
    local exit_status=$?
    if [ "$exit_status" -ne 0 ]; then
        rollback_power_reconciliation
    fi
    if [ "$BOOTSTRAP_TERMINAL_EMITTED" -ne 1 ]; then
        if [ "$exit_status" -eq 2 ]; then
            emit_bootstrap_terminal "workflow_cancelled" "CANCELLED" "$exit_status"
        else
            emit_bootstrap_terminal "workflow_failed" "MISSING_TERMINAL" "$exit_status"
        fi
    fi
    cleanup
}
trap exit_handler EXIT

failure_handler() {
    local exit_status=$?
    local line_num=$1
    emit_bootstrap_terminal "workflow_failed" "BOOTSTRAP_ERROR" "$exit_status"
    echo -e "\n${C_RED}"
    echo "========================================================"
    echo " ERROR: KACE Bootstrap failed at line $line_num (Exit code: $exit_status)."
    echo " For details, inspect the log file: $LOG_FILE"
    echo "========================================================"
    echo -e "${C_RESET}"
    exit $exit_status
}
trap 'failure_handler $LINENO' ERR

cancel_handler() {
    local signal_name="$1"
    emit_bootstrap_terminal "workflow_cancelled" "SIGNAL_${signal_name}" 2
    exit 2
}
trap 'cancel_handler INT' INT
trap 'cancel_handler TERM' TERM

emit_bootstrap_event "workflow_started" "INIT"

# ── Parse Arguments ──────────────────────────────────────────────────────────
DASHBOARD=""
CROWSNEST=""
TIMEZONE=""
PREBAKED=""
POWER_RELAY=""
POWER_DEVICE=""
POWER_GPIO=""
POWER_ACTIVE_LOW=""
POWER_RESTART_KLIPPER=""
POWER_INITIAL_STATE=""
POWER_OFF_WHEN_SHUTDOWN=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --dashboard) DASHBOARD="$2"; shift ;;
        --crowsnest) CROWSNEST="$2"; shift ;;
        --timezone)  TIMEZONE="$2";  shift ;;
        --prebaked)  PREBAKED="$2";  shift ;;
    esac
    shift
done

# ── Read Injected Config from Boot Partition ─────────────────────────────────
BOOT_CFG=""
if [ -f "/boot/firmware/kace-bootstrap.txt" ]; then
    BOOT_CFG="/boot/firmware/kace-bootstrap.txt"
elif [ -f "/boot/kace-bootstrap.txt" ]; then
    BOOT_CFG="/boot/kace-bootstrap.txt"
fi

if [ -n "$BOOT_CFG" ]; then
    echo "Loaded configurations from $BOOT_CFG"

    # Parse boot config in a single pass (avoids 4× file open + grep + cut chains)
    FILE_DASHBOARD="" FILE_CROWSNEST="" FILE_TIMEZONE="" FILE_PREBAKED=""
    FILE_POWER_RELAY="" FILE_POWER_DEVICE="" FILE_POWER_GPIO=""
    FILE_POWER_ACTIVE_LOW="" FILE_POWER_RESTART_KLIPPER=""
    FILE_POWER_INITIAL_STATE="" FILE_POWER_OFF_WHEN_SHUTDOWN=""
    while IFS='=' read -r key value; do
        case "$key" in
            DASHBOARD) FILE_DASHBOARD="$value" ;;
            CROWSNEST) FILE_CROWSNEST="$value" ;;
            TIMEZONE)  FILE_TIMEZONE="$value"  ;;
            PREBAKED)  FILE_PREBAKED="$value"   ;;
            POWER_RELAY) FILE_POWER_RELAY="$value" ;;
            POWER_DEVICE) FILE_POWER_DEVICE="$value" ;;
            POWER_GPIO) FILE_POWER_GPIO="$value" ;;
            POWER_ACTIVE_LOW) FILE_POWER_ACTIVE_LOW="$value" ;;
            POWER_RESTART_KLIPPER) FILE_POWER_RESTART_KLIPPER="$value" ;;
            POWER_INITIAL_STATE) FILE_POWER_INITIAL_STATE="$value" ;;
            POWER_OFF_WHEN_SHUTDOWN) FILE_POWER_OFF_WHEN_SHUTDOWN="$value" ;;
        esac
    done < "$BOOT_CFG"

    DASHBOARD="${DASHBOARD:-$FILE_DASHBOARD}"
    CROWSNEST="${CROWSNEST:-$FILE_CROWSNEST}"
    TIMEZONE="${TIMEZONE:-$FILE_TIMEZONE}"
    PREBAKED="${PREBAKED:-$FILE_PREBAKED}"
    POWER_RELAY="${POWER_RELAY:-$FILE_POWER_RELAY}"
    POWER_DEVICE="${POWER_DEVICE:-$FILE_POWER_DEVICE}"
    POWER_GPIO="${POWER_GPIO:-$FILE_POWER_GPIO}"
    POWER_ACTIVE_LOW="${POWER_ACTIVE_LOW:-$FILE_POWER_ACTIVE_LOW}"
    POWER_RESTART_KLIPPER="${POWER_RESTART_KLIPPER:-$FILE_POWER_RESTART_KLIPPER}"
    POWER_INITIAL_STATE="${POWER_INITIAL_STATE:-$FILE_POWER_INITIAL_STATE}"
    POWER_OFF_WHEN_SHUTDOWN="${POWER_OFF_WHEN_SHUTDOWN:-$FILE_POWER_OFF_WHEN_SHUTDOWN}"
fi

# ── Input Sanitization & Allowlist Validation ────────────────────────────────
if [ -n "$DASHBOARD" ]; then
    if [[ ! "$DASHBOARD" =~ ^(mainsail|fluidd|both)$ ]]; then
        log_warn "Invalid dashboard choice '$DASHBOARD'. Resetting to default."
        DASHBOARD="mainsail"
    fi
else
    DASHBOARD="mainsail"
fi

if [ -n "$CROWSNEST" ]; then
    if [[ ! "$CROWSNEST" =~ ^(true|false)$ ]]; then
        log_warn "Invalid crowsnest toggle '$CROWSNEST'. Resetting to default."
        CROWSNEST="false"
    fi
else
    CROWSNEST="false"
fi

if [ -n "$TIMEZONE" ]; then
    if [[ ! "$TIMEZONE" =~ ^[A-Za-z0-9/_+-]+$ ]]; then
        log_warn "Malformed timezone string '$TIMEZONE' rejected to prevent command injection."
        TIMEZONE=""
    fi
fi

if [ -n "$PREBAKED" ]; then
    if [[ ! "$PREBAKED" =~ ^(true|false)$ ]]; then
        PREBAKED="false"
    fi
else
    PREBAKED="false"
fi

if ! validate_power_relay_settings; then
    echo "=== KACE_BOOTSTRAP_ERROR: GPIO_RELAY_CONFIG ==="
    log_err "GPIO relay configuration is incomplete or invalid; bootstrap aborted."
    exit 1
fi

echo -e "${C_BOLD}"
echo "--------------------------------------------------------"
echo "  Target Configuration"
echo "  Dashboard UI : $DASHBOARD"
echo "  Webcam Stream: $CROWSNEST"
echo "  Timezone     : ${TIMEZONE:-'(Keep system default)'}"
echo "  Pre-baked OS : $PREBAKED"
if [ "$POWER_RELAY" = "true" ]; then
    echo "  GPIO relay   : ${POWER_DEVICE} on GPIO${POWER_GPIO}"
fi
echo "--------------------------------------------------------"
echo -e "${C_RESET}"

# ── Resolve Printer User Home Directory ─────────────────────────────────────
# By first boot, firstrun.sh has already renamed the pre-existing printer user
# (pi/mainsail/fluidd) to the target username and moved their home directory.
# The SUDO_USER/USER resolution below therefore finds the correct home directly.
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ] && [ -d "/home/$SUDO_USER" ]; then
    PRINTER_HOME="/home/$SUDO_USER"
elif [ -n "$USER" ] && [ "$USER" != "root" ] && [ -d "/home/$USER" ]; then
    PRINTER_HOME="/home/$USER"
else
    # Scan /home for the first valid user directory
    DETECTED_USER=""
    for udir in /home/mainsail /home/fluidd /home/pi /home/kace /home/*; do
        [ -d "$udir" ] || continue
        uname=$(basename "$udir")
        if [ "$uname" != "*" ] && [ "$uname" != "root" ] && id "$uname" &>/dev/null; then
            DETECTED_USER="$uname"
            PRINTER_HOME="$udir"
            break
        fi
    done
    if [ -z "$DETECTED_USER" ]; then
        PRINTER_HOME="$HOME"
    fi
fi
echo "Resolved printer home directory: $PRINTER_HOME"

# Get the owner and group of the printer user home directory
PRINTER_USER=$(stat -c '%U' "$PRINTER_HOME" 2>/dev/null || echo "$USER")
PRINTER_GROUP=$(stat -c '%G' "$PRINTER_HOME" 2>/dev/null || echo "$USER")

mkdir -p "$PRINTER_HOME/printer_data/config"
mkdir -p "$PRINTER_HOME/printer_data/gcodes"
mkdir -p "$PRINTER_HOME/printer_data/comms"
begin_power_reconciliation "$PRINTER_HOME/printer_data/config/moonraker.conf"
ensure_moonraker_config \
    "$PRINTER_HOME/printer_data/config/moonraker.conf" \
    "$PRINTER_HOME/printer_data/comms/klippy.sock"
MOONRAKER_CONFIG="$PRINTER_HOME/printer_data/config/moonraker.conf"

# ── 1. Timezone Configuration ────────────────────────────────────────────────
if [ -n "$TIMEZONE" ]; then
    log_stage "TIMEZONE" "Setting Timezone"
    if ! $SUDO timedatectl set-timezone "$TIMEZONE" 2>/dev/null; then
        log_warn "Timezone update skipped."
    else
        log_ok "Timezone set to $TIMEZONE."
    fi
fi

# ── 2. System Packages ───────────────────────────────────────────────────────
log_stage "PACKAGES" "Updating System Packages"
if [ "$PREBAKED" = "true" ]; then
    # We still need to install git and unzip if we are installing Fluidd on top of MainsailOS (both case)
    if [ "$DASHBOARD" = "both" ]; then
        wait_for_apt_locks
        $SUDO apt-get -o DPkg::Lock::Timeout=300 update -y
        $SUDO apt-get -o DPkg::Lock::Timeout=300 install -y git unzip file
    fi
    log_ok "System packages already pre-installed (skipped)."
else
    wait_for_apt_locks
    $SUDO apt-get -o DPkg::Lock::Timeout=300 update -y
    wait_for_apt_locks
    $SUDO apt-get -o DPkg::Lock::Timeout=300 install -y git curl unzip nginx file
    log_ok "System packages ready."
fi

# ── 3. Klipper ───────────────────────────────────────────────────────────────
log_stage "KLIPPER" "Installing Klipper"
if [ "$PREBAKED" = "true" ]; then
    log_ok "Klipper already pre-installed (skipped)."
    log_stage "KLIPPER_FIX" "Patching Klipper service paths"
    log_ok "Klipper service paths already configured (skipped)."
else
    ensure_pinned_git_checkout \
        "Klipper" "$KLIPPER_REPOSITORY" "$KLIPPER_REF" "$PRINTER_HOME/klipper"

    if [ ! -f "$PRINTER_HOME/klipper/scripts/install-debian.sh" ]; then
        log_err "Klipper Debian install script not found."
        exit 1
    fi

    # Patch for Python 3 support on modern Debian/Ubuntu
    sed -i 's/python-dev/python3-dev/g'               "$PRINTER_HOME/klipper/scripts/install-debian.sh"
    sed -i 's/virtualenv -p python2/virtualenv -p python3/g' "$PRINTER_HOME/klipper/scripts/install-debian.sh"

    if systemctl is-active --quiet klipper 2>/dev/null; then
        echo "Klipper service already active. Skipping reinstall."
    else
        wait_for_apt_locks
        sudo -u "$PRINTER_USER" "$PRINTER_HOME/klipper/scripts/install-debian.sh"
    fi
    log_ok "Klipper installed."

    log_stage "KLIPPER_FIX" "Patching Klipper service for printer_data layout"
    KLIPPER_DROPIN_DIR="/etc/systemd/system/klipper.service.d"
    $SUDO mkdir -p "$KLIPPER_DROPIN_DIR"
    $SUDO tee "$KLIPPER_DROPIN_DIR/kace-override.conf" > /dev/null <<EOF
# Generated by KACE Studio bootstrap — do not edit manually.
# Overrides the upstream klipper.service ExecStart to use the modern
# printer_data directory layout expected by Moonraker.
[Service]
ExecStart=
ExecStart=$PRINTER_HOME/klippy-env/bin/python $PRINTER_HOME/klipper/klippy/klippy.py \\
    $PRINTER_HOME/printer_data/config/printer.cfg \\
    -l $PRINTER_HOME/printer_data/logs/klippy.log \\
    -a $PRINTER_HOME/printer_data/comms/klippy.sock
EOF
    mkdir -p "$PRINTER_HOME/printer_data/logs" 2>/dev/null || $SUDO mkdir -p "$PRINTER_HOME/printer_data/logs"
    log_ok "Klipper service patched: using printer_data/config/printer.cfg and klippy.sock."
fi

# ── 4. Moonraker ─────────────────────────────────────────────────────────────
log_stage "MOONRAKER" "Installing Moonraker"
if [ "$PREBAKED" = "true" ]; then
    log_ok "Moonraker already pre-installed (skipped)."
else
    ensure_pinned_git_checkout \
        "Moonraker" "$MOONRAKER_REPOSITORY" "$MOONRAKER_REF" "$PRINTER_HOME/moonraker"

    if [ ! -f "$PRINTER_HOME/moonraker/scripts/install-moonraker.sh" ]; then
        log_err "Moonraker install script not found."
        exit 1
    fi
    wait_for_apt_locks
    sudo -u "$PRINTER_USER" "$PRINTER_HOME/moonraker/scripts/install-moonraker.sh"

    sleep 3
    if ! systemctl is-active --quiet moonraker 2>/dev/null; then
        log_warn "Moonraker service did not start. Check: journalctl -u moonraker"
    else
        log_ok "Moonraker installed and running."
    fi
fi

# Boot-order optimization: ensure Moonraker waits for Klipper to fully
# initialize before starting. On low-resource SBCs (Pi 3, 1 GB RAM) all
# services starting simultaneously pins CPU at 100% and delays the web
# interface by 10-15 minutes. The 5-second delay compensates for
# Type=simple services where After= only guarantees fork order, not
# readiness.
MOONRAKER_DROPIN_DIR="/etc/systemd/system/moonraker.service.d"
$SUDO mkdir -p "$MOONRAKER_DROPIN_DIR"
$SUDO tee "$MOONRAKER_DROPIN_DIR/kace-boot-order.conf" > /dev/null <<EOF
# Generated by KACE Studio bootstrap — do not edit manually.
# Staggers Moonraker startup after Klipper to reduce CPU contention
# during cold boot on low-resource SBCs.
[Unit]
After=klipper.service
Wants=klipper.service

[Service]
ExecStartPre=/bin/sleep 5
EOF
log_ok "Moonraker boot-order optimization applied (starts 5s after Klipper)."

# ── 5. Printer Data Directories & Config Files ───────────────────────────────
log_stage "CONFIGS" "Creating Printer Configuration"

# printer.cfg — [include] line written conditionally per selected dashboard
if [ "$PREBAKED" = "true" ]; then
    # MainsailOS and FluiddPi already ship with a safe printer.cfg
    # using kinematics: none — do not overwrite it, just ensure the
    # dashboard include line is present if missing.
    echo "Pre-baked image detected: preserving existing printer.cfg."
    INCLUDE_LINE=""
    if [ "$DASHBOARD" = "mainsail" ] || [ "$DASHBOARD" = "both" ]; then
        INCLUDE_LINE="[include mainsail.cfg]"
    elif [ "$DASHBOARD" = "fluidd" ]; then
        INCLUDE_LINE="[include fluidd.cfg]"
    fi
    if [ -n "$INCLUDE_LINE" ] && \
       ! grep -q "include.*mainsail.cfg" "$PRINTER_HOME/printer_data/config/printer.cfg" 2>/dev/null && \
       ! grep -q "include.*fluidd.cfg"   "$PRINTER_HOME/printer_data/config/printer.cfg" 2>/dev/null; then
        echo "Prepending $INCLUDE_LINE to existing printer.cfg..."
        echo -e "${INCLUDE_LINE}\n$(cat $PRINTER_HOME/printer_data/config/printer.cfg)" \
            > "$PRINTER_HOME/printer_data/config/printer.cfg"
    else
        echo "Dashboard include already present or not needed. Skipping."
    fi
else
    # Fresh RPi OS Lite install — write our baseline placeholder only if
    # no printer.cfg exists yet.
    if [ ! -f "$PRINTER_HOME/printer_data/config/printer.cfg" ]; then
        echo "Creating default printer.cfg..."

        INCLUDE_LINES=""
        if [ "$DASHBOARD" = "mainsail" ] || [ "$DASHBOARD" = "both" ]; then
            INCLUDE_LINES="[include mainsail.cfg]"
        elif [ "$DASHBOARD" = "fluidd" ]; then
            INCLUDE_LINES="[include fluidd.cfg]"
        fi

        cat <<EOF > "$PRINTER_HOME/printer_data/config/printer.cfg"
${INCLUDE_LINES}

[mcu]
# serial: /dev/serial/by-id/change-me-to-your-mcu-id

[printer]
kinematics: none
max_velocity: 300
max_accel: 3000
EOF
    else
        echo "printer.cfg already exists. Ensuring dashboard include is present..."
        INCLUDE_LINE=""
        if [ "$DASHBOARD" = "mainsail" ] || [ "$DASHBOARD" = "both" ]; then
            INCLUDE_LINE="[include mainsail.cfg]"
        elif [ "$DASHBOARD" = "fluidd" ]; then
            INCLUDE_LINE="[include fluidd.cfg]"
        fi

        if [ -n "$INCLUDE_LINE" ] && ! grep -q "include.*mainsail.cfg" "$PRINTER_HOME/printer_data/config/printer.cfg" && ! grep -q "include.*fluidd.cfg" "$PRINTER_HOME/printer_data/config/printer.cfg"; then
            echo "Prepending $INCLUDE_LINE to printer.cfg..."
            # Safely prepend include line to existing printer.cfg
            echo -e "${INCLUDE_LINE}\n$(cat $PRINTER_HOME/printer_data/config/printer.cfg)" > "$PRINTER_HOME/printer_data/config/printer.cfg"
        fi
    fi
fi

ensure_config_entry \
    "$PRINTER_HOME/printer_data/config/printer.cfg" \
    "exclude_object"
ensure_config_entry \
    "$PRINTER_HOME/printer_data/config/printer.cfg" \
    "force_move" "enable_force_move" "True"

# Make sure permissions are correct
$SUDO chown -R "${PRINTER_USER}:${PRINTER_GROUP}" "$PRINTER_HOME/printer_data"
log_ok "Printer configuration files ready."

# ── 6. Dashboard UI ──────────────────────────────────────────────────────────
$SUDO mkdir -p /var/www

setup_mainsail() {
    log_stage "MAINSAIL" "Installing Mainsail"
    install_verified_dashboard \
        "Mainsail" "$MAINSAIL_URL" "$MAINSAIL_SHA256" \
        "/tmp/mainsail.zip" "/var/www/mainsail"
    log_ok "Mainsail installed."
}

setup_fluidd() {
    log_stage "FLUIDD" "Installing Fluidd"
    install_verified_dashboard \
        "Fluidd" "$FLUIDD_URL" "$FLUIDD_SHA256" \
        "/tmp/fluidd.zip" "/var/www/fluidd"
    log_ok "Fluidd installed."
}

if [ "$PREBAKED" = "true" ]; then
    if [ "$DASHBOARD" = "mainsail" ]; then
        log_stage "MAINSAIL" "Installing Mainsail"
        log_ok "Mainsail already pre-installed (skipped)."
        DEFAULT_UI="mainsail"
    elif [ "$DASHBOARD" = "fluidd" ]; then
        log_stage "FLUIDD" "Installing Fluidd"
        log_ok "Fluidd already pre-installed (skipped)."
        DEFAULT_UI="fluidd"
    elif [ "$DASHBOARD" = "both" ]; then
        # MainsailOS is the base, so Mainsail is preinstalled.
        log_stage "MAINSAIL" "Installing Mainsail"
        log_ok "Mainsail already pre-installed (skipped)."
        
        # Fluidd needs to be installed.
        setup_fluidd
        DEFAULT_UI="both"
    fi
else
    if [ "$DASHBOARD" = "mainsail" ]; then
        setup_mainsail
        DEFAULT_UI="mainsail"
    elif [ "$DASHBOARD" = "fluidd" ]; then
        setup_fluidd
        DEFAULT_UI="fluidd"
    elif [ "$DASHBOARD" = "both" ]; then
        setup_mainsail
        setup_fluidd
        DEFAULT_UI="both"
    else
        setup_mainsail
        DEFAULT_UI="mainsail"
    fi
fi

# ── 7. UI Client Config Files ─────────────────────────────────────────────────
log_stage "CLIENT_CFG" "Downloading UI Client Config"
setup_client_config() {
    local dashboard="$1"
    local config_dir="$PRINTER_HOME/printer_data/config"

    # Helper: publish a verified client cfg file as a real file (no symlinks).
    _install_client_cfg() {
        local dest="$1"          # full destination path, e.g. .../config/mainsail.cfg
        local local_src="$2"     # preferred local source (may be a symlink)
        local url="$3"           # pinned download URL
        local expected_sha256="$4"
        local temporary=""
        local actual_sha256=""

        if [ -e "$local_src" ]; then
            if ! temporary=$(mktemp "${dest}.kace-config.XXXXXX"); then
                log_err "Could not create a temporary client config for $(basename "$dest")."
                return 1
            fi
            if ! cp --dereference "$local_src" "$temporary"; then
                log_err "Could not copy local $(basename "$dest") for verification."
                rm -f "$temporary"
                return 1
            fi
            actual_sha256=$(sha256sum "$temporary" | cut -d" " -f1)
            if [ "$actual_sha256" = "$expected_sha256" ]; then
                mv -f "$temporary" "$dest"
                $SUDO chown "${PRINTER_USER}:${PRINTER_GROUP}" "$dest"
                return 0
            fi
            log_warn "Local $(basename "$dest") does not match the pinned hash; downloading the verified version."
            rm -f "$temporary"
        fi

        download_verified_file "$(basename "$dest")" "$url" "$dest" "$expected_sha256" || return 1
        $SUDO chown "${PRINTER_USER}:${PRINTER_GROUP}" "$dest"
    }

    if [ "$dashboard" = "mainsail" ] || [ "$dashboard" = "both" ]; then
        # Always re-evaluate: even if the file exists it may be a broken symlink.
        if [ ! -f "$config_dir/mainsail.cfg" ] || [ -L "$config_dir/mainsail.cfg" ]; then
            _install_client_cfg \
                "$config_dir/mainsail.cfg" \
                "$PRINTER_HOME/mainsail-config/client.cfg" \
                "$MAINSAIL_CONFIG_URL" \
                "$MAINSAIL_CONFIG_SHA256"
        else
            echo "mainsail.cfg already present as a regular file. Skipping."
        fi
    fi

    if [ "$dashboard" = "fluidd" ] || [ "$dashboard" = "both" ]; then
        if [ ! -f "$config_dir/fluidd.cfg" ] || [ -L "$config_dir/fluidd.cfg" ]; then
            _install_client_cfg \
                "$config_dir/fluidd.cfg" \
                "$PRINTER_HOME/fluidd-config/client.cfg" \
                "$FLUIDD_CONFIG_URL" \
                "$FLUIDD_CONFIG_SHA256"
        else
            echo "fluidd.cfg already present as a regular file. Skipping."
        fi
    fi
}

setup_client_config "$DASHBOARD"
log_ok "UI client config ready."

# ── 8. Nginx ──────────────────────────────────────────────────────────────────
log_stage "NGINX" "Configuring Nginx"

if [ "$PREBAKED" = "true" ] && [ "$DASHBOARD" != "both" ]; then
    log_ok "Nginx already configured on pre-baked image (skipped)."
else
    NGINX_CONF="/etc/nginx/sites-available/kace-printer"

    # Check if IPv6 is supported by checking if /proc/net/if_inet6 exists
    listen_ipv6=""
    listen_ipv6_81=""
    if [ -f /proc/net/if_inet6 ]; then
        listen_ipv6="listen [::]:80 default_server;"
        listen_ipv6_81="listen [::]:81 default_server;"
    fi
    
    if [ "$DEFAULT_UI" = "both" ]; then
        # Configure Nginx for both: Mainsail on port 80, Fluidd on port 81
        $SUDO tee "$NGINX_CONF" > /dev/null <<EOF
server {
    listen 80 default_server;
    $listen_ipv6

    root /var/www/mainsail;
    index index.html;
    server_name _;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /websocket {
        proxy_pass http://kace_apiserver;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    location ~ ^/(printer|api|access|machine|server|files|history)(/.*)?$ {
        proxy_pass http://kace_apiserver;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }
}

server {
    listen 81 default_server;
    $listen_ipv6_81

    root /var/www/fluidd;
    index index.html;
    server_name _;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /websocket {
        proxy_pass http://kace_apiserver;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    location ~ ^/(printer|api|access|machine|server|files|history)(/.*)?$ {
        proxy_pass http://kace_apiserver;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }
}

upstream kace_apiserver {
    ip_hash;
    server 127.0.0.1:7125;
}
EOF
    else
        # Single UI config
        TEMP_UI="$DEFAULT_UI"
        if [[ ! "$TEMP_UI" =~ ^(mainsail|fluidd)$ ]]; then
            TEMP_UI="mainsail"
        fi

        $SUDO tee "$NGINX_CONF" > /dev/null <<EOF
server {
    listen 80 default_server;
    $listen_ipv6

    root /var/www/$TEMP_UI;
    index index.html;
    server_name _;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /websocket {
        proxy_pass http://kace_apiserver;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    location ~ ^/(printer|api|access|machine|server|files|history)(/.*)?$ {
        proxy_pass http://kace_apiserver;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }
}

upstream kace_apiserver {
    ip_hash;
    server 127.0.0.1:7125;
}
EOF
    fi

    # Stop potentially conflicting web servers to free up ports 80 and 81
    $SUDO systemctl stop apache2 2>/dev/null || true
    $SUDO systemctl disable apache2 2>/dev/null || true
    $SUDO systemctl stop lighttpd 2>/dev/null || true
    $SUDO systemctl disable lighttpd 2>/dev/null || true

    # Link the new configuration and remove the default/conflicting configurations
    $SUDO rm -f /etc/nginx/sites-enabled/default
    $SUDO rm -f /etc/nginx/sites-enabled/kace-printer
    $SUDO ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/

    # Test the final configuration (which now includes kace-printer and excludes default)
    if ! $SUDO nginx -t 2>&1; then
        log_err "Nginx configuration test failed. Aborting."
        # Rollback symlink on failure to avoid leaving nginx in a broken state
        $SUDO rm -f /etc/nginx/sites-enabled/kace-printer
        exit 1
    fi

    $SUDO systemctl restart nginx
    log_ok "Nginx configured and running."
fi

# ── 9. Patch Systemd Service Paths ───────────────────────────────────────────
log_stage "SYSTEMD_PATCH" "Patching Systemd Service Paths"

patch_systemd_services() {
    local target_home="$PRINTER_HOME"
    local target_user="$PRINTER_USER"
    local target_group="$PRINTER_GROUP"
    local patched=0

    local search_dirs=(
        "/etc/systemd/system"
        "/lib/systemd/system"
        "/usr/lib/systemd/system"
    )

    local services=("klipper" "moonraker" "crowsnest")

    for svc in "${services[@]}"; do
        for dir in "${search_dirs[@]}"; do
            local svc_file="$dir/${svc}.service"
            local dropin_dir="$dir/${svc}.service.d"

            for f in "$svc_file" "$dropin_dir"/*.conf; do
                [ -f "$f" ] || continue

                # Snapshot, apply all substitutions in a single sed pass,
                # then compare hashes to detect actual changes.
                local before_hash
                before_hash=$(md5sum "$f" 2>/dev/null | cut -d' ' -f1)

                $SUDO sed -i \
                    -e "s|/home/mainsail/|${target_home}/|g" \
                    -e "s|/home/pi/|${target_home}/|g" \
                    -e "s|/home/fluidd/|${target_home}/|g" \
                    -e "s|^User=mainsail$|User=${target_user}|" \
                    -e "s|^User=pi$|User=${target_user}|" \
                    -e "s|^User=fluidd$|User=${target_user}|" \
                    -e "s|^Group=mainsail$|Group=${target_group}|" \
                    -e "s|^Group=pi$|Group=${target_group}|" \
                    -e "s|^Group=fluidd$|Group=${target_group}|" \
                    "$f"

                local after_hash
                after_hash=$(md5sum "$f" 2>/dev/null | cut -d' ' -f1)

                if [ "$before_hash" != "$after_hash" ]; then
                    echo "  Patched $f"
                    patched=1
                fi
            done
        done
    done

    if [ "$patched" -eq 1 ]; then
        log_ok "Systemd service files patched."
    else
        log_ok "No systemd path mismatches found. Nothing to patch."
    fi
}

patch_systemd_services

# ── 10. Start Services ────────────────────────────────────────────────────────
log_stage "SERVICES" "Starting Klipper & Moonraker Services"
# Single daemon-reload for all preceding drop-in and unit file changes
# (Klipper override, Moonraker boot-order, systemd path patches).
verify_requested_power_relay "$MOONRAKER_CONFIG"
$SUDO systemctl daemon-reload
$SUDO systemctl restart klipper
$SUDO systemctl restart moonraker
if ! systemctl is-active --quiet klipper 2>/dev/null; then
    log_err "Klipper did not remain active after restart."
    exit 1
fi
if ! systemctl is-active --quiet moonraker 2>/dev/null; then
    log_err "Moonraker did not remain active after restart."
    exit 1
fi
if [ "$PREBAKED" = "false" ] || [ "$DASHBOARD" = "both" ]; then
    $SUDO systemctl restart nginx || true
fi
log_ok "Services restarted."

if [ "$POWER_RELAY" = "true" ]; then
    if ! prepare_power_relay_for_kace; then
        echo "=== KACE_BOOTSTRAP_ERROR: POWER_ON ==="
        log_err "Printer power-on verification failed; KACE will not start until the relay and MCU are ready."
        exit 1
    fi
fi

if ! verify_power_api_configuration "http://127.0.0.1:7125"; then
    echo "=== KACE_BOOTSTRAP_ERROR: GPIO_RELAY_API_VERIFY ==="
    log_err "Power reconciliation was not persisted because Moonraker verification failed."
    exit 1
fi
persist_power_controller_config
commit_power_reconciliation

# ── 10. Crowsnest (Optional) ──────────────────────────────────────────────────
if [ "$CROWSNEST" = "true" ]; then
    if [ "$PREBAKED" = "true" ]; then
        log_stage "CROWSNEST" "Configuring Crowsnest Webcam Streamer"
        mkdir -p "$PRINTER_HOME/printer_data/config"
        if [ ! -f "$PRINTER_HOME/printer_data/config/crowsnest.conf" ]; then
            echo "Creating default crowsnest.conf..."
            cat <<EOF > "$PRINTER_HOME/printer_data/config/crowsnest.conf"
[crowsnest]
log_path: ~/printer_data/logs/crowsnest.log
log_level: verbose
delete_log: false

[cam 1]
mode: ustreamer
enable_audio: false
port: 8080
device: /dev/video0
resolution: 640x480
max_fps: 15
EOF
            $SUDO chown "${PRINTER_USER}:${PRINTER_GROUP}" "$PRINTER_HOME/printer_data/config/crowsnest.conf"
        fi
        log_ok "Crowsnest configured."
    else
        log_stage "CROWSNEST" "Installing Crowsnest Webcam Streamer"
        ensure_pinned_git_checkout \
            "Crowsnest" "$CROWSNEST_REPOSITORY" "$CROWSNEST_REF" "$PRINTER_HOME/crowsnest"
        if [ ! -f "$PRINTER_HOME/crowsnest/tools/install.sh" ]; then
            log_err "Crowsnest install script not found."
            exit 1
        fi
        (
            cd "$PRINTER_HOME/crowsnest"
            wait_for_apt_locks
            if ! sudo -E env CROWSNEST_UNATTENDED=1 CROWSNEST_SKIP_REBOOT_PROMPT=1 ./tools/install.sh; then
                log_warn "Crowsnest upstream installer returned an error. Continuing..."
            fi
        )
        log_ok "Crowsnest installed."
    fi

    # Boot-order optimization: ensure Crowsnest (the heaviest service due to
    # USB video device probing and stream initialization) starts last, after
    # Moonraker is running. The 10-second delay gives Klipper and Moonraker
    # time to fully initialize, keeping the web interface responsive on
    # low-resource SBCs.
    CROWSNEST_DROPIN_DIR="/etc/systemd/system/crowsnest.service.d"
    $SUDO mkdir -p "$CROWSNEST_DROPIN_DIR"
    $SUDO tee "$CROWSNEST_DROPIN_DIR/kace-boot-order.conf" > /dev/null <<EOF
# Generated by KACE Studio bootstrap — do not edit manually.
# Staggers Crowsnest startup after Moonraker to reduce CPU contention
# during cold boot on low-resource SBCs.
[Unit]
After=moonraker.service

[Service]
ExecStartPre=/bin/sleep 10
EOF
    $SUDO systemctl daemon-reload
    log_ok "Crowsnest boot-order optimization applied (starts 10s after Moonraker)."

    # Hardware-aware service enablement: only activate crowsnest if a physical
    # camera (USB/UVC, modern CSI, or legacy CSI) is detected at bootstrap time.
    # This prevents the systemd fail-restart loop (Restart=on-failure + RestartSec=30
    # x StartLimitBurst=3) that wastes ~2 minutes of CPU on camera-less Pis.
    # Idempotent: re-running bootstrap with a webcam plugged in will re-enable.
    if detect_camera_hardware; then
        $SUDO systemctl enable crowsnest.service >/dev/null 2>&1 || true
        $SUDO systemctl restart crowsnest || true
        log_ok "Camera hardware detected. Crowsnest service enabled and started."
    else
        $SUDO systemctl stop crowsnest >/dev/null 2>&1 || true
        $SUDO systemctl disable crowsnest.service >/dev/null 2>&1 || true
        log_warn "No physical camera detected. Crowsnest is installed but has been disabled"
        log_warn "to prevent systemd restart loops and unnecessary boot-time CPU load."
        log_warn "Connect a webcam and run the following to activate it:"
        log_warn "  sudo systemctl enable --now crowsnest.service"
    fi
else
    log_stage "CROWSNEST" "Webcam Streamer"
    if systemctl is-active --quiet crowsnest 2>/dev/null || systemctl is-enabled --quiet crowsnest 2>/dev/null; then
        $SUDO systemctl stop crowsnest >/dev/null 2>&1 || true
        $SUDO systemctl disable crowsnest.service >/dev/null 2>&1 || true
        log_ok "Crowsnest not selected — disabled existing systemd service (saves ~30s boot time)."
    else
        log_ok "Crowsnest was not selected (skipped)."
    fi
fi

# ── 11. KACE Agent ────────────────────────────────────────────────────────────
log_stage "KACE" "Installing KACE Agent"
INSTALL_OK=0
INSTALL_EXIT=1

if [ "$(id -un)" != "$PRINTER_USER" ]; then
    # Running as a different user (e.g. root), switch to printer user context
    if sudo -u "$PRINTER_USER" -i env \
        KACE_INSTALL_URL="$KACE_INSTALL_URL" \
        KACE_INSTALL_SHA256="$KACE_INSTALL_SHA256" \
        KACE_SOURCE_REF="$KACE_INSTALL_REF" \
        sh -c '
        tmp_script="/tmp/kace-install.sh"
        rm -f "$tmp_script"
        if curl --fail --silent --show-error --location "$KACE_INSTALL_URL" -o "$tmp_script"; then
            actual_hash=$(sha256sum "$tmp_script" | cut -d" " -f1)
            if [ "$actual_hash" = "$KACE_INSTALL_SHA256" ]; then
                bash "$tmp_script"
                status=$?
                rm -f "$tmp_script"
                exit $status
            else
                echo "Error: KACE agent script integrity check failed." >&2
                echo "Expected: $KACE_INSTALL_SHA256" >&2
                echo "Got:      $actual_hash" >&2
                rm -f "$tmp_script"
                exit 1
            fi
        fi
        exit 1
    '; then
        log_ok "KACE agent installed."
        INSTALL_OK=1
        INSTALL_EXIT=0
    else
        INSTALL_EXIT=$?
    fi
else
    # Already running as printer user, run directly without sudo
    tmp_script="/tmp/kace-install.sh"
    rm -f "$tmp_script"
    if curl --fail --silent --show-error --location "$KACE_INSTALL_URL" -o "$tmp_script"; then
        actual_hash=$(sha256sum "$tmp_script" | cut -d" " -f1)
        if [ "$actual_hash" = "$KACE_INSTALL_SHA256" ]; then
            if KACE_SOURCE_REF="$KACE_INSTALL_REF" bash "$tmp_script"; then
                log_ok "KACE agent installed."
                INSTALL_OK=1
                INSTALL_EXIT=0
            else
                INSTALL_EXIT=$?
            fi
            rm -f "$tmp_script"
        else
            echo "Error: KACE agent script integrity check failed." >&2
            echo "Expected: $KACE_INSTALL_SHA256" >&2
            echo "Got:      $actual_hash" >&2
            rm -f "$tmp_script"
        fi
    fi
fi

if [ "$INSTALL_OK" -ne 1 ]; then
    echo "=== KACE_BOOTSTRAP_ERROR: KACE_INSTALL ==="
    log_err "KACE agent installation failed; the node is not fully provisioned."
    log_err "Pinned installer: $KACE_INSTALL_URL"
    case "$INSTALL_EXIT" in
        2)
            emit_bootstrap_terminal "workflow_cancelled" "CANCELLED" "$INSTALL_EXIT"
            ;;
        10)
            emit_bootstrap_terminal "workflow_failed" "PRECONDITION_FAILED" "$INSTALL_EXIT"
            ;;
        20)
            emit_bootstrap_terminal "workflow_failed" "GENERATION_FAILED" "$INSTALL_EXIT"
            ;;
        30)
            emit_bootstrap_terminal "workflow_failed" "FIRMWARE_FAILED" "$INSTALL_EXIT"
            ;;
        40)
            emit_bootstrap_terminal "workflow_failed" "DEPLOYMENT_FAILED" "$INSTALL_EXIT"
            ;;
        *)
            INSTALL_EXIT=1
            emit_bootstrap_terminal "workflow_failed" "KACE_INSTALL" "$INSTALL_EXIT"
            ;;
    esac
    exit "$INSTALL_EXIT"
fi

# ── 12. Disable cloud-init ────────────────────────────────────────────────────
# Cloud-init has finished its one-time provisioning job.  Disable it to prevent
# re-provisioning on future reboots, which can generate conflicting network
# profiles and break WiFi connectivity (especially on prebaked MainsailOS images
# that use NetworkManager instead of Netplan).
log_stage "CLOUDINIT" "Disabling cloud-init for future boots"
$SUDO touch /etc/cloud/cloud-init.disabled 2>/dev/null || true
# Also clean up cloud-init trigger files from the boot partition so they cannot
# accidentally re-enable provisioning if the disable marker is removed.
for _ci_file in /boot/firmware/user-data /boot/firmware/meta-data /boot/firmware/network-config \
                /boot/user-data /boot/meta-data /boot/network-config; do
    [ -f "$_ci_file" ] && $SUDO rm -f "$_ci_file" 2>/dev/null || true
done
log_ok "cloud-init disabled — will not re-provision on reboot."

# ── Done ──────────────────────────────────────────────────────────────────────
# The success marker is emitted only after the KACE installer returns from the
# interactive wizard and the requested relay configuration verifies exactly.
finalize_bootstrap_success "$MOONRAKER_CONFIG" "$BOOT_CFG"
