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
        elif ps aux 2>/dev/null | grep -v grep | grep -E "apt-get|dpkg|unattended-upgrades" >/dev/null 2>&1; then
            locked=1
        fi
        
        # Python-based fcntl lock check on all standard apt/dpkg lock files
        if [ $locked -eq 0 ] && command -v python3 >/dev/null 2>&1; then
            if ! $SUDO python3 -c '
import fcntl, sys, os
for fpath in ["/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock", "/var/lib/apt/lists/lock", "/var/cache/apt/archives/lock"]:
    if os.path.exists(fpath):
        try:
            f = open(fpath, "a")
            fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.close()
        except (BlockingIOError, PermissionError):
            sys.exit(1)
' >/dev/null 2>&1; then
                locked=1
            fi
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

# ── Privileges & Logging ─────────────────────────────────────────────────────
SUDO=""
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
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
    rm -f /tmp/mainsail.zip /tmp/fluidd.zip
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

    FILE_DASHBOARD=$(grep -E "^DASHBOARD=" "$BOOT_CFG" | cut -d'=' -f2 || true)
    FILE_CROWSNEST=$(grep -E "^CROWSNEST=" "$BOOT_CFG" | cut -d'=' -f2 || true)
    FILE_TIMEZONE=$(grep -E "^TIMEZONE="  "$BOOT_CFG" | cut -d'=' -f2 || true)
    FILE_PREBAKED=$(grep -E "^PREBAKED="  "$BOOT_CFG" | cut -d'=' -f2 || true)

    [ -z "$DASHBOARD" ] && DASHBOARD="$FILE_DASHBOARD"
    [ -z "$CROWSNEST" ] && CROWSNEST="$FILE_CROWSNEST"
    [ -z "$TIMEZONE"  ] && TIMEZONE="$FILE_TIMEZONE"
    [ -z "$PREBAKED"  ] && PREBAKED="$FILE_PREBAKED"
    
    # Securely remove sensitive credentials / file after reading
    # Overwrite first to prevent forensic recovery of clean-text parameters
    echo "CLEARED" > "$BOOT_CFG" 2>/dev/null || true
    rm -f "$BOOT_CFG" 2>/dev/null || true
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

# ── Resolve Active Printer User Home Directory ────────────────────────────────
PRINTER_HOME="$HOME"
for udir in "/home/mainsail" "/home/fluidd" "/home/pi"; do
    if [ -d "${udir}/printer_data" ]; then
        PRINTER_HOME="${udir}"
        break
    fi
done
echo "Resolved printer home directory: $PRINTER_HOME"

# Get the owner and group of the printer_data dir for permission updates
PRINTER_USER=$(stat -c '%U' "$PRINTER_HOME/printer_data" 2>/dev/null || echo "$USER")
PRINTER_GROUP=$(stat -c '%G' "$PRINTER_HOME/printer_data" 2>/dev/null || echo "$USER")

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
    if [ ! -d "$HOME/klipper" ]; then
        echo "Cloning Klipper repository..."
        git clone https://github.com/Klipper3d/klipper.git "$HOME/klipper"
    else
        echo "Klipper repository already exists. Updating..."
        pushd "$HOME/klipper" > /dev/null && git pull && popd > /dev/null
    fi

    if [ ! -f "$HOME/klipper/scripts/install-debian.sh" ]; then
        log_err "Klipper Debian install script not found."
        exit 1
    fi

    # Patch for Python 3 support on modern Debian/Ubuntu
    sed -i 's/python-dev/python3-dev/g'               "$HOME/klipper/scripts/install-debian.sh"
    sed -i 's/virtualenv -p python2/virtualenv -p python3/g' "$HOME/klipper/scripts/install-debian.sh"

    if systemctl is-active --quiet klipper 2>/dev/null; then
        echo "Klipper service already active. Skipping reinstall."
    else
        wait_for_apt_locks
        "$HOME/klipper/scripts/install-debian.sh"
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
ExecStart=$HOME/klippy-env/bin/python $HOME/klipper/klippy/klippy.py \\
    $HOME/printer_data/config/printer.cfg \\
    -l $HOME/printer_data/logs/klippy.log \\
    -a $HOME/printer_data/comms/klippy.sock
EOF
    mkdir -p "$HOME/printer_data/logs" 2>/dev/null || $SUDO mkdir -p "$HOME/printer_data/logs"
    $SUDO chown -R "$USER:$USER" "$HOME/printer_data"
    $SUDO systemctl daemon-reload
    log_ok "Klipper service patched: using printer_data/config/printer.cfg and klippy.sock."
fi

# ── 4. Moonraker ─────────────────────────────────────────────────────────────
log_stage "MOONRAKER" "Installing Moonraker"
if [ "$PREBAKED" = "true" ]; then
    log_ok "Moonraker already pre-installed (skipped)."
else
    if [ ! -d "$HOME/moonraker" ]; then
        echo "Cloning Moonraker repository..."
        git clone https://github.com/Arksine/moonraker.git "$HOME/moonraker"
    else
        echo "Moonraker repository already exists. Updating..."
        pushd "$HOME/moonraker" > /dev/null && git pull && popd > /dev/null
    fi

    if [ ! -f "$HOME/moonraker/scripts/install-moonraker.sh" ]; then
        log_err "Moonraker install script not found."
        exit 1
    fi
    wait_for_apt_locks
    "$HOME/moonraker/scripts/install-moonraker.sh"

    sleep 3
    if ! systemctl is-active --quiet moonraker 2>/dev/null; then
        log_warn "Moonraker service did not start. Check: journalctl -u moonraker"
    else
        log_ok "Moonraker installed and running."
    fi
fi

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

    if [ "$dashboard" = "mainsail" ] || [ "$dashboard" = "both" ]; then
        if [ ! -f "$config_dir/mainsail.cfg" ]; then
            echo "Downloading mainsail.cfg..."
            if ! curl -fsSL --retry 3 --retry-delay 5 \
                "https://raw.githubusercontent.com/mainsail-crew/mainsail-config/master/client.cfg" \
                -o "$config_dir/mainsail.cfg"; then
                log_warn "Could not download mainsail.cfg. Writing minimal placeholder."
                cat <<'MCFG' > "$config_dir/mainsail.cfg"
# mainsail.cfg placeholder — replace with the official file from:
# https://raw.githubusercontent.com/mainsail-crew/mainsail-config/master/client.cfg
[virtual_sdcard]
path: ~/printer_data/gcodes
on_error_gcode: CANCEL_PRINT

[pause_resume]

[display_status]

[respond]
MCFG
            fi
            $SUDO chown "${PRINTER_USER}:${PRINTER_GROUP}" "$config_dir/mainsail.cfg"
        else
            echo "mainsail.cfg already present. Skipping."
        fi
    fi

    if [ "$dashboard" = "fluidd" ] || [ "$dashboard" = "both" ]; then
        if [ ! -f "$config_dir/fluidd.cfg" ]; then
            echo "Downloading fluidd.cfg..."
            if ! curl -fsSL --retry 3 --retry-delay 5 \
                "https://raw.githubusercontent.com/fluidd-core/fluidd-config/master/client.cfg" \
                -o "$config_dir/fluidd.cfg"; then
                log_warn "Could not download fluidd.cfg. Writing minimal placeholder."
                cat <<'FCFG' > "$config_dir/fluidd.cfg"
# fluidd.cfg placeholder — replace with the official file from:
# https://raw.githubusercontent.com/fluidd-core/fluidd-config/master/client.cfg
[virtual_sdcard]
path: ~/printer_data/gcodes
on_error_gcode: CANCEL_PRINT

[pause_resume]

[display_status]

[respond]
FCFG
            fi
            $SUDO chown "${PRINTER_USER}:${PRINTER_GROUP}" "$config_dir/fluidd.cfg"
        else
            echo "fluidd.cfg already present. Skipping."
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
    
    if [ "$DEFAULT_UI" = "both" ]; then
        # Configure Nginx for both: Mainsail on port 80, Fluidd on port 81
        $SUDO tee "$NGINX_CONF" > /dev/null <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    root /var/www/mainsail;
    index index.html;
    server_name _;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /websocket {
        proxy_pass http://apiserver;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    location ~ ^/(printer|api|access|machine|server|files|history)(/.*)?$ {
        proxy_pass http://apiserver;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }
}

server {
    listen 81 default_server;
    listen [::]:81 default_server;

    root /var/www/fluidd;
    index index.html;
    server_name _;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /websocket {
        proxy_pass http://apiserver;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    location ~ ^/(printer|api|access|machine|server|files|history)(/.*)?$ {
        proxy_pass http://apiserver;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }
}

upstream apiserver {
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
    listen [::]:80 default_server;

    root /var/www/$TEMP_UI;
    index index.html;
    server_name _;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /websocket {
        proxy_pass http://apiserver;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    location ~ ^/(printer|api|access|machine|server|files|history)(/.*)?$ {
        proxy_pass http://apiserver;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }
}

upstream apiserver {
    ip_hash;
    server 127.0.0.1:7125;
}
EOF
    fi

    if ! $SUDO nginx -t 2>&1; then
        log_err "Nginx configuration test failed. Aborting."
        exit 1
    fi

    $SUDO ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
    $SUDO rm -f /etc/nginx/sites-enabled/default
    $SUDO systemctl restart nginx
    log_ok "Nginx configured and running."
fi

# ── 9. Start Services ─────────────────────────────────────────────────────────
log_stage "SERVICES" "Starting Klipper & Moonraker Services"
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
        $SUDO systemctl restart crowsnest || true
        log_ok "Crowsnest configured."
    else
        log_stage "CROWSNEST" "Installing Crowsnest Webcam Streamer"
        if [ ! -d "$HOME/crowsnest" ]; then
            git clone https://github.com/mainsail-crew/crowsnest.git "$HOME/crowsnest"
        else
            pushd "$HOME/crowsnest" > /dev/null && git pull && popd > /dev/null
        fi
        if [ ! -f "$HOME/crowsnest/tools/install.sh" ]; then
            log_err "Crowsnest install script not found."
            exit 1
        fi
        pushd "$HOME/crowsnest" > /dev/null
        wait_for_apt_locks
        sudo -E env CROWSNEST_UNATTENDED=1 CROWSNEST_SKIP_REBOOT_PROMPT=1 ./tools/install.sh
        popd > /dev/null
        log_ok "Crowsnest installed."
    fi
else
    log_stage "CROWSNEST" "Installing Crowsnest Webcam Streamer"
    log_ok "Crowsnest was not selected (skipped)."
fi

# ── 11. KACE Agent ────────────────────────────────────────────────────────────
log_stage "KACE" "Installing KACE Agent"
if bash <(curl -sSL https://raw.githubusercontent.com/3D-uy/KACE/main/install.sh); then
    log_ok "KACE agent installed."
else
    log_warn "KACE agent installation failed. Retry manually with:"
    log_warn "  bash <(curl -sSL https://raw.githubusercontent.com/3D-uy/KACE/main/install.sh)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo -e "\n${C_GREEN}${C_BOLD}"
echo "========================================================"
echo "      Bootstrap complete! KACE Node is fully ready.     "
echo "========================================================"
echo -e "${C_RESET}"
