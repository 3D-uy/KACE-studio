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

log_stage() {
    # Usage: log_stage "STAGE_ID" "Human readable label"
    local id="$1"
    local label="$2"
    echo -e "\n${C_CYAN}=== ${label} ===${C_RESET}"
    echo -e "=== STAGE: ${id} ==="   # Machine-parseable marker (no color codes)
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
trap cleanup EXIT

failure_handler() {
    local exit_status=$?
    local line_num=$1
    echo -e "\n${C_RED}"
    echo "========================================================"
    echo " ERROR: KACE Bootstrap failed at line $line_num (Exit code: $exit_status)."
    echo " For details, inspect the log file: $LOG_FILE"
    echo "========================================================"
    echo -e "${C_RESET}"
    exit $exit_status
}
trap 'failure_handler $LINENO' ERR

# ── Parse Arguments ──────────────────────────────────────────────────────────
DASHBOARD=""
CROWSNEST=""
TIMEZONE=""
PREBAKED=""

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
    while IFS='=' read -r key value; do
        case "$key" in
            DASHBOARD) FILE_DASHBOARD="$value" ;;
            CROWSNEST) FILE_CROWSNEST="$value" ;;
            TIMEZONE)  FILE_TIMEZONE="$value"  ;;
            PREBAKED)  FILE_PREBAKED="$value"   ;;
        esac
    done < "$BOOT_CFG"

    DASHBOARD="${DASHBOARD:-$FILE_DASHBOARD}"
    CROWSNEST="${CROWSNEST:-$FILE_CROWSNEST}"
    TIMEZONE="${TIMEZONE:-$FILE_TIMEZONE}"
    PREBAKED="${PREBAKED:-$FILE_PREBAKED}"
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

echo -e "${C_BOLD}"
echo "--------------------------------------------------------"
echo "  Target Configuration"
echo "  Dashboard UI : $DASHBOARD"
echo "  Webcam Stream: $CROWSNEST"
echo "  Timezone     : ${TIMEZONE:-'(Keep system default)'}"
echo "  Pre-baked OS : $PREBAKED"
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
    if [ ! -d "$PRINTER_HOME/klipper" ]; then
        echo "Cloning Klipper repository..."
        sudo -u "$PRINTER_USER" git clone https://github.com/Klipper3d/klipper.git "$PRINTER_HOME/klipper"
    else
        echo "Klipper repository already exists. Updating..."
        (cd "$PRINTER_HOME/klipper" && sudo -u "$PRINTER_USER" git pull)
    fi

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
    if [ ! -d "$PRINTER_HOME/moonraker" ]; then
        echo "Cloning Moonraker repository..."
        sudo -u "$PRINTER_USER" git clone https://github.com/Arksine/moonraker.git "$PRINTER_HOME/moonraker"
    else
        echo "Moonraker repository already exists. Updating..."
        (cd "$PRINTER_HOME/moonraker" && sudo -u "$PRINTER_USER" git pull)
    fi

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
mkdir -p "$PRINTER_HOME/printer_data/config"
mkdir -p "$PRINTER_HOME/printer_data/gcodes"
mkdir -p "$PRINTER_HOME/printer_data/comms"

# moonraker.conf
# NOTE: If moonraker.conf exists but lacks the [authorization] section,
# it will be completely overwritten with our default template.
if [ ! -f "$PRINTER_HOME/printer_data/config/moonraker.conf" ] || ! grep -q "\[authorization\]" "$PRINTER_HOME/printer_data/config/moonraker.conf"; then
    echo "Creating default moonraker.conf..."
    cat <<EOF > "$PRINTER_HOME/printer_data/config/moonraker.conf"
[server]
host: 0.0.0.0
port: 7125
klippy_uds_address: $PRINTER_HOME/printer_data/comms/klippy.sock

[authorization]
trusted_clients:
    127.0.0.1
    10.0.0.0/8
    127.0.0.0/8
    162.254.206.0/24
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
fi

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

# Make sure permissions are correct
$SUDO chown -R "${PRINTER_USER}:${PRINTER_GROUP}" "$PRINTER_HOME/printer_data"
log_ok "Printer configuration files ready."

# ── 6. Dashboard UI ──────────────────────────────────────────────────────────
$SUDO mkdir -p /var/www

setup_mainsail() {
    log_stage "MAINSAIL" "Installing Mainsail"
    $SUDO mkdir -p /var/www/mainsail
    if ! curl -fsSL --retry 3 --retry-delay 5 \
        "https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip" \
        -o /tmp/mainsail.zip; then
        log_err "Failed to download Mainsail release archive."
        exit 1
    fi
    if ! file /tmp/mainsail.zip 2>/dev/null | grep -q 'Zip archive'; then
        log_err "Downloaded Mainsail archive is not a valid ZIP file."
        rm -f /tmp/mainsail.zip
        exit 1
    fi
    $SUDO unzip -o /tmp/mainsail.zip -d /var/www/mainsail
    rm -f /tmp/mainsail.zip
    if [ ! -f "/var/www/mainsail/index.html" ]; then
        log_err "Mainsail extraction failed — index.html not found."
        exit 1
    fi
    $SUDO chown -R www-data:www-data /var/www/mainsail
    $SUDO chmod -R 755 /var/www/mainsail
    log_ok "Mainsail installed."
}

setup_fluidd() {
    log_stage "FLUIDD" "Installing Fluidd"
    $SUDO mkdir -p /var/www/fluidd
    if ! curl -fsSL --retry 3 --retry-delay 5 \
        "https://github.com/fluidd-core/fluidd/releases/latest/download/fluidd.zip" \
        -o /tmp/fluidd.zip; then
        log_err "Failed to download Fluidd release archive."
        exit 1
    fi
    if ! file /tmp/fluidd.zip 2>/dev/null | grep -q 'Zip archive'; then
        log_err "Downloaded Fluidd archive is not a valid ZIP file."
        rm -f /tmp/fluidd.zip
        exit 1
    fi
    $SUDO unzip -o /tmp/fluidd.zip -d /var/www/fluidd
    rm -f /tmp/fluidd.zip
    if [ ! -f "/var/www/fluidd/index.html" ]; then
        log_err "Fluidd extraction failed — index.html not found."
        exit 1
    fi
    $SUDO chown -R www-data:www-data /var/www/fluidd
    $SUDO chmod -R 755 /var/www/fluidd
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

    # Helper: copy a client cfg file as a real file (no symlinks).
    # Tries local git checkout first (dereferencing any symlinks), then falls
    # back to curl download, then writes a minimal placeholder.
    _install_client_cfg() {
        local dest="$1"          # full destination path, e.g. .../config/mainsail.cfg
        local local_src="$2"     # preferred local source (may be a symlink)
        local curl_url="$3"      # fallback download URL
        local placeholder="$4"   # fallback inline content label for log

        # Remove any existing symlink so we always end up with a regular file.
        if [ -L "$dest" ]; then
            echo "Removing existing symlink at $dest to replace with regular file..."
            rm -f "$dest"
        fi

        if [ -e "$local_src" ]; then
            echo "Copying $(basename "$dest") from local checkout (dereferencing symlinks)..."
            cp --dereference "$local_src" "$dest"
        elif ! curl -fsSL --retry 3 --retry-delay 5 "$curl_url" -o "$dest"; then
            log_warn "Could not download $(basename "$dest"). Writing minimal placeholder."
            cat > "$dest" <<PLACEHOLDER
# $(basename "$dest") placeholder — replace with the official file from:
# $curl_url
[virtual_sdcard]
path: ~/printer_data/gcodes
on_error_gcode: CANCEL_PRINT

[pause_resume]

[display_status]

[respond]
PLACEHOLDER
        fi
        $SUDO chown "${PRINTER_USER}:${PRINTER_GROUP}" "$dest"
    }

    if [ "$dashboard" = "mainsail" ] || [ "$dashboard" = "both" ]; then
        # Always re-evaluate: even if the file exists it may be a broken symlink.
        if [ ! -f "$config_dir/mainsail.cfg" ] || [ -L "$config_dir/mainsail.cfg" ]; then
            _install_client_cfg \
                "$config_dir/mainsail.cfg" \
                "$PRINTER_HOME/mainsail-config/client.cfg" \
                "https://raw.githubusercontent.com/mainsail-crew/mainsail-config/master/client.cfg" \
                "mainsail.cfg"
        else
            echo "mainsail.cfg already present as a regular file. Skipping."
        fi
    fi

    if [ "$dashboard" = "fluidd" ] || [ "$dashboard" = "both" ]; then
        if [ ! -f "$config_dir/fluidd.cfg" ] || [ -L "$config_dir/fluidd.cfg" ]; then
            _install_client_cfg \
                "$config_dir/fluidd.cfg" \
                "$PRINTER_HOME/fluidd-config/client.cfg" \
                "https://raw.githubusercontent.com/fluidd-core/fluidd-config/master/client.cfg" \
                "fluidd.cfg"
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
                    -e "s|^User=mainsail|User=${target_user}|" \
                    -e "s|^Group=mainsail|Group=${target_group}|" \
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
$SUDO systemctl daemon-reload
$SUDO systemctl restart klipper   || true
$SUDO systemctl restart moonraker || true
if [ "$PREBAKED" = "false" ] || [ "$DASHBOARD" = "both" ]; then
    $SUDO systemctl restart nginx || true
fi
log_ok "Services restarted."

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
        if [ ! -d "$PRINTER_HOME/crowsnest" ]; then
            sudo -u "$PRINTER_USER" git clone https://github.com/mainsail-crew/crowsnest.git "$PRINTER_HOME/crowsnest"
        else
            (cd "$PRINTER_HOME/crowsnest" && sudo -u "$PRINTER_USER" git pull)
        fi
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
    log_stage "CROWSNEST" "Installing Crowsnest Webcam Streamer"
    log_ok "Crowsnest was not selected (skipped)."
fi

# ── 11. KACE Agent ────────────────────────────────────────────────────────────
log_stage "KACE" "Installing KACE Agent"
INSTALL_OK=0
EXPECTED_HASH="281ae79ac40a324adc5ab8d276567289b56f8683bfed69fc15146d33212e2619"

if [ "$(id -un)" != "$PRINTER_USER" ]; then
    # Running as a different user (e.g. root), switch to printer user context
    if sudo -u "$PRINTER_USER" -i env EXPECTED_HASH="$EXPECTED_HASH" sh -c '
        tmp_script="/tmp/kace-install.sh"
        rm -f "$tmp_script"
        if curl -sSL https://raw.githubusercontent.com/3D-uy/KACE/main/install.sh -o "$tmp_script"; then
            actual_hash=$(sha256sum "$tmp_script" | cut -d" " -f1)
            if [ "$actual_hash" = "$EXPECTED_HASH" ]; then
                bash "$tmp_script"
                status=$?
                rm -f "$tmp_script"
                exit $status
            else
                echo "Error: KACE agent script integrity check failed." >&2
                echo "Expected: $EXPECTED_HASH" >&2
                echo "Got:      $actual_hash" >&2
                rm -f "$tmp_script"
                exit 1
            fi
        fi
        exit 1
    '; then
        log_ok "KACE agent installed."
        INSTALL_OK=1
    fi
else
    # Already running as printer user, run directly without sudo
    tmp_script="/tmp/kace-install.sh"
    rm -f "$tmp_script"
    if curl -sSL https://raw.githubusercontent.com/3D-uy/KACE/main/install.sh -o "$tmp_script"; then
        actual_hash=$(sha256sum "$tmp_script" | cut -d" " -f1)
        if [ "$actual_hash" = "$EXPECTED_HASH" ]; then
            if bash "$tmp_script"; then
                log_ok "KACE agent installed."
                INSTALL_OK=1
            fi
            rm -f "$tmp_script"
        else
            echo "Error: KACE agent script integrity check failed." >&2
            echo "Expected: $EXPECTED_HASH" >&2
            echo "Got:      $actual_hash" >&2
            rm -f "$tmp_script"
        fi
    fi
fi

if [ "$INSTALL_OK" -ne 1 ]; then
    log_warn "KACE agent installation failed. Retry manually with:"
    log_warn "  curl -sSL https://raw.githubusercontent.com/3D-uy/KACE/main/install.sh -o /tmp/kace-install.sh && bash /tmp/kace-install.sh"
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
echo -e "\n${C_GREEN}${C_BOLD}"
echo "========================================================"
echo "      Bootstrap complete! KACE Node is fully ready.     "
echo "========================================================"
echo -e "${C_RESET}"

# ── Clean up bootstrap config ────────────────────────────────────────────────
if [ -n "$BOOT_CFG" ] && [ -f "$BOOT_CFG" ]; then
    if [ -n "$SUDO" ]; then
        echo "CLEARED" | $SUDO tee "$BOOT_CFG" >/dev/null 2>/dev/null || true
        $SUDO rm -f "$BOOT_CFG" 2>/dev/null || true
    else
        echo "CLEARED" > "$BOOT_CFG" 2>/dev/null || true
        rm -f "$BOOT_CFG" 2>/dev/null || true
    fi
fi