#!/bin/bash
# KACE Studio Bootstrapper Script
# Auto-installs Klipper, Moonraker, Mainsail/Fluidd, and Crowsnest based on user selections.

set -e

# Determine secure privileges prefix
SUDO=""
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
fi

# Define logging target
LOG_FILE=""
if [ "$EUID" -eq 0 ]; then
    LOG_FILE="/var/log/kace-bootstrap.log"
else
    LOG_FILE="$HOME/kace-bootstrap.log"
fi

# Redirect stdout and stderr to the log file while preserving console output
exec > >(tee -i "$LOG_FILE") 2>&1

echo -e "\033[1;36m"
echo "========================================================"
echo "    KACE Studio: Klipper Automated Setup Bootstrapper   "
echo "========================================================"
echo -e "\033[0m"
echo "Logging execution output to: $LOG_FILE"

# 1. Traps & Cleanup Handlers
cleanup() {
    echo "Cleaning up temporary download archives..."
    rm -f /tmp/mainsail.zip /tmp/fluidd.zip
}
trap cleanup EXIT

failure_handler() {
    local exit_status=$?
    local line_num=$1
    echo -e "\033[1;31m"
    echo "========================================================"
    echo " ERROR: KACE Bootstrap failed at line $line_num (Exit code: $exit_status)."
    echo " For details, inspect the log file: $LOG_FILE"
    echo "========================================================"
    echo -e "\033[0m"
    exit $exit_status
}
trap 'failure_handler $LINENO' ERR

# 2. Parse Arguments (Fallback/Override options)
DASHBOARD=""
CROWSNEST=""
TIMEZONE=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --dashboard) DASHBOARD="$2"; shift ;;
        --crowsnest) CROWSNEST="$2"; shift ;;
        --timezone)  TIMEZONE="$2";  shift ;;
    esac
    shift
done

# 3. Read Injected Configurations from Boot Partition (First step settings)
BOOT_CFG=""
if [ -f "/boot/firmware/kace-bootstrap.txt" ]; then
    BOOT_CFG="/boot/firmware/kace-bootstrap.txt"
elif [ -f "/boot/kace-bootstrap.txt" ]; then
    BOOT_CFG="/boot/kace-bootstrap.txt"
fi

if [ -n "$BOOT_CFG" ]; then
    echo "Found injected KACE configurations at $BOOT_CFG:"
    grep -E "^(DASHBOARD|CROWSNEST|TIMEZONE)=" "$BOOT_CFG" || true

    # Parse file variables manually to avoid malicious shell execution
    FILE_DASHBOARD=$(grep -E "^DASHBOARD=" "$BOOT_CFG" | cut -d'=' -f2 || true)
    FILE_CROWSNEST=$(grep -E "^CROWSNEST=" "$BOOT_CFG" | cut -d'=' -f2 || true)
    FILE_TIMEZONE=$(grep -E "^TIMEZONE="  "$BOOT_CFG" | cut -d'=' -f2 || true)

    # Overwrite only if arguments were not explicitly passed
    [ -z "$DASHBOARD" ] && DASHBOARD="$FILE_DASHBOARD"
    [ -z "$CROWSNEST" ] && CROWSNEST="$FILE_CROWSNEST"
    [ -z "$TIMEZONE"  ] && TIMEZONE="$FILE_TIMEZONE"
fi

# 4. Input Sanitization & Allowlist Validation
if [ -n "$DASHBOARD" ]; then
    if [[ ! "$DASHBOARD" =~ ^(mainsail|fluidd|both)$ ]]; then
        echo "Warning: Invalid dashboard choice '$DASHBOARD'. Resetting to default."
        DASHBOARD="mainsail"
    fi
else
    DASHBOARD="mainsail"
fi

if [ -n "$CROWSNEST" ]; then
    if [[ ! "$CROWSNEST" =~ ^(true|false)$ ]]; then
        echo "Warning: Invalid crowsnest toggle '$CROWSNEST'. Resetting to default."
        CROWSNEST="false"
    fi
else
    CROWSNEST="false"
fi

if [ -n "$TIMEZONE" ]; then
    # Strict regex check for Timezone format (alphanumeric, slash, dash, underscore, plus, minus)
    if [[ ! "$TIMEZONE" =~ ^[A-Za-z0-9/_+-]+$ ]]; then
        echo "Warning: Malformed timezone string '$TIMEZONE' rejected to prevent command injection."
        TIMEZONE=""
    fi
fi

echo "--------------------------------------------------------"
echo "Target Configuration:"
echo "  Dashboard UI : $DASHBOARD"
echo "  Webcam Stream: $CROWSNEST"
echo "  Timezone     : ${TIMEZONE:-'(Keep system default)'}"
echo "--------------------------------------------------------"

# 5. Timezone Configuration
if [ -n "$TIMEZONE" ]; then
    echo "Setting system timezone to $TIMEZONE..."
    if ! $SUDO timedatectl set-timezone "$TIMEZONE" 2>/dev/null; then
        echo "Warning: timezone update skipped."
    fi
fi

# 6. System Package Updates & Dependencies
echo "Updating apt repositories..."
$SUDO apt-get update -y

echo "Installing core dependencies..."
$SUDO apt-get install -y git curl unzip nginx file

# 7. Installing Klipper (Firmware Engine)
if [ ! -d "$HOME/klipper" ]; then
    echo "Cloning Klipper repository..."
    git clone https://github.com/Klipper3d/klipper.git "$HOME/klipper"
else
    echo "Klipper repository already exists. Updating..."
    pushd "$HOME/klipper" > /dev/null && git pull && popd > /dev/null
fi

echo "Installing Klipper dependencies and systemd service..."
# Verify installer script exists
if [ ! -f "$HOME/klipper/scripts/install-debian.sh" ]; then
    echo "ERROR: Klipper Debian install script not found at $HOME/klipper/scripts/install-debian.sh" >&2
    exit 1
fi
# Patch Klipper installer script to use Python 3 on modern Debian/Ubuntu distributions
echo "Patching Klipper installer script for Python 3 support..."
sed -i 's/python-dev/python3-dev/g'               "$HOME/klipper/scripts/install-debian.sh"
sed -i 's/virtualenv -p python2/virtualenv -p python3/g' "$HOME/klipper/scripts/install-debian.sh"

# Idempotency guard: skip full reinstall if Klipper service is already active
if systemctl is-active --quiet klipper 2>/dev/null; then
    echo "Klipper service already active. Skipping reinstall."
else
    "$HOME/klipper/scripts/install-debian.sh"
fi

# 8. Installing Moonraker (API Web Server Backend)
if [ ! -d "$HOME/moonraker" ]; then
    echo "Cloning Moonraker repository..."
    git clone https://github.com/Arksine/moonraker.git "$HOME/moonraker"
else
    echo "Moonraker repository already exists. Updating..."
    pushd "$HOME/moonraker" > /dev/null && git pull && popd > /dev/null
fi

echo "Installing Moonraker dependencies and systemd service..."
# Verify installer script exists
if [ ! -f "$HOME/moonraker/scripts/install-moonraker.sh" ]; then
    echo "ERROR: Moonraker install script not found at $HOME/moonraker/scripts/install-moonraker.sh" >&2
    exit 1
fi
"$HOME/moonraker/scripts/install-moonraker.sh"

echo "Verifying Moonraker service is active..."
sleep 3
if ! systemctl is-active --quiet moonraker 2>/dev/null; then
    echo "WARNING: Moonraker service did not start after install. Check journalctl -u moonraker for details." >&2
fi

# Create printer_data directories if they don't exist
echo "Creating printer data configuration directories..."
mkdir -p "$HOME/printer_data/config"
mkdir -p "$HOME/printer_data/gcodes"
mkdir -p "$HOME/printer_data/comms"    # Required: Moonraker klippy.sock socket lives here

# Create a default moonraker.conf if not present.
# Note: heredoc is intentionally unquoted (<<EOF) so $HOME expands to the real user home path.
if [ ! -f "$HOME/printer_data/config/moonraker.conf" ] || ! grep -q "\[authorization\]" "$HOME/printer_data/config/moonraker.conf"; then
    echo "Creating default moonraker.conf..."
    cat <<EOF > "$HOME/printer_data/config/moonraker.conf"
[server]
host: 0.0.0.0
port: 7125
klippy_uds_address: $HOME/printer_data/comms/klippy.sock

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

# Create a default printer.cfg if not present
if [ ! -f "$HOME/printer_data/config/printer.cfg" ]; then
    echo "Creating default printer.cfg..."
    cat <<EOF > "$HOME/printer_data/config/printer.cfg"
[mcu]
serial: /dev/serial/by-id/change-me-to-your-mcu-id

[printer]
kinematics: none
max_velocity: 300
max_accel: 3000
EOF
fi

# 9. Installing Mainsail/Fluidd Dashboard
echo "Setting up Nginx and downloading selected web interfaces..."

# Make webroot parent directory
$SUDO mkdir -p /var/www

setup_mainsail() {
    echo "Installing Mainsail control interface..."
    $SUDO mkdir -p /var/www/mainsail
    # Download with retry and fail-on-error to catch HTTP error responses
    if ! curl -fsSL --retry 3 --retry-delay 5 \
        "https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip" \
        -o /tmp/mainsail.zip; then
        echo "ERROR: Failed to download Mainsail release archive from GitHub." >&2
        exit 1
    fi
    # Validate zip file magic bytes (PK header) before attempting extraction
    if ! file /tmp/mainsail.zip 2>/dev/null | grep -q 'Zip archive'; then
        echo "ERROR: Downloaded Mainsail archive is not a valid ZIP file (possible redirect or rate-limit error)." >&2
        rm -f /tmp/mainsail.zip
        exit 1
    fi
    $SUDO unzip -o /tmp/mainsail.zip -d /var/www/mainsail
    rm -f /tmp/mainsail.zip
    # Verify critical entry point was extracted
    if [ ! -f "/var/www/mainsail/index.html" ]; then
        echo "ERROR: Mainsail extraction failed â€” index.html not found in /var/www/mainsail." >&2
        exit 1
    fi
    echo "Mainsail installed successfully."
}

setup_fluidd() {
    echo "Installing Fluidd control interface..."
    $SUDO mkdir -p /var/www/fluidd
    # Download with retry and fail-on-error to catch HTTP error responses
    if ! curl -fsSL --retry 3 --retry-delay 5 \
        "https://github.com/fluidd-core/fluidd/releases/latest/download/fluidd.zip" \
        -o /tmp/fluidd.zip; then
        echo "ERROR: Failed to download Fluidd release archive from GitHub." >&2
        exit 1
    fi
    # Validate zip file magic bytes (PK header) before attempting extraction
    if ! file /tmp/fluidd.zip 2>/dev/null | grep -q 'Zip archive'; then
        echo "ERROR: Downloaded Fluidd archive is not a valid ZIP file (possible redirect or rate-limit error)." >&2
        rm -f /tmp/fluidd.zip
        exit 1
    fi
    $SUDO unzip -o /tmp/fluidd.zip -d /var/www/fluidd
    rm -f /tmp/fluidd.zip
    # Verify critical entry point was extracted
    if [ ! -f "/var/www/fluidd/index.html" ]; then
        echo "ERROR: Fluidd extraction failed â€” index.html not found in /var/www/fluidd." >&2
        exit 1
    fi
    echo "Fluidd installed successfully."
}

if [ "$DASHBOARD" = "mainsail" ]; then
    setup_mainsail
    DEFAULT_UI="mainsail"
elif [ "$DASHBOARD" = "fluidd" ]; then
    setup_fluidd
    DEFAULT_UI="fluidd"
elif [ "$DASHBOARD" = "both" ]; then
    setup_mainsail
    setup_fluidd
    DEFAULT_UI="mainsail"   # Default entry point for double setup
else
    setup_mainsail
    DEFAULT_UI="mainsail"
fi

# Double check that DEFAULT_UI remains strictly sanitized
if [[ ! "$DEFAULT_UI" =~ ^(mainsail|fluidd)$ ]]; then
    DEFAULT_UI="mainsail"
fi

# 10. Configuring Nginx Server Blocks
echo "Configuring Nginx web server..."
NGINX_CONF="/etc/nginx/sites-available/kace-printer"

$SUDO tee "$NGINX_CONF" > /dev/null <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    root /var/www/$DEFAULT_UI;
    index index.html;
    server_name _;

    # Client-side routing helper
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # Proxy Moonraker WebSocket connection
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

    # Proxy all Moonraker HTTP API routes
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

# Validate generated Nginx config before applying it
echo "Validating Nginx configuration..."
if ! $SUDO nginx -t 2>&1; then
    echo "ERROR: Nginx configuration test failed. Aborting to prevent breaking the web server." >&2
    exit 1
fi

# Enable configuration and reload Nginx
$SUDO ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
$SUDO rm -f /etc/nginx/sites-enabled/default
$SUDO systemctl restart nginx

# 11. Starting Klipper and Moonraker Services
# Services are (re)started after Nginx is fully configured so the full stack
# becomes available atomically â€” Moonraker is not exposed before the reverse proxy is live.
echo "Restarting Klipper and Moonraker services..."
$SUDO systemctl restart klipper   || true
$SUDO systemctl restart moonraker || true

# 12. Installing Crowsnest (Webcam Streamer)
if [ "$CROWSNEST" = "true" ]; then
    echo "Installing Crowsnest webcam streaming engine..."
    if [ ! -d "$HOME/crowsnest" ]; then
        git clone https://github.com/mainsail-crew/crowsnest.git "$HOME/crowsnest"
    else
        pushd "$HOME/crowsnest" > /dev/null && git pull && popd > /dev/null
    fi
    # Verify installer script exists
    if [ ! -f "$HOME/crowsnest/tools/install.sh" ]; then
        echo "ERROR: Crowsnest install script not found at $HOME/crowsnest/tools/install.sh" >&2
        exit 1
    fi
    # Use sudo -E to preserve the caller's HOME and environment so Crowsnest installs
    # its systemd service files pointing at the correct user home, not /root.
    pushd "$HOME/crowsnest" > /dev/null
    sudo -E env CROWSNEST_UNATTENDED=1 CROWSNEST_SKIP_REBOOT_PROMPT=1 ./tools/install.sh
    popd > /dev/null
else
    echo "Crowsnest was not selected. Skipping webcam setup."
fi

echo -e "\033[1;32m"
echo "========================================================"
echo "      Bootstrap complete! KACE Node is fully ready.     "
echo "========================================================"
echo -e "\033[0m"
