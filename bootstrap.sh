#!/bin/bash
# KACE Studio Bootstrapper Script
# Auto-installs Klipper, Moonraker, Mainsail/Fluidd, and Crowsnest based on user selections.

set -e

echo -e "\033[1;35m"
echo "========================================================"
echo "    KACE Studio: Klipper Automated Setup Bootstrapper   "
echo "========================================================"
echo -e "\033[0m"

# 1. Parse Arguments (Fallback/Override options)
DASHBOARD=""
CROWSNEST=""
TIMEZONE=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --dashboard) DASHBOARD="$2"; shift ;;
        --crowsnest) CROWSNEST="$2"; shift ;;
        --timezone) TIMEZONE="$2"; shift ;;
    esac
    shift
done

# 2. Read Injected Configurations from Boot Partition (First step settings)
BOOT_CFG=""
if [ -f "/boot/firmware/kace-bootstrap.txt" ]; then
    BOOT_CFG="/boot/firmware/kace-bootstrap.txt"
elif [ -f "/boot/kace-bootstrap.txt" ]; then
    BOOT_CFG="/boot/kace-bootstrap.txt"
fi

if [ -n "$BOOT_CFG" ]; then
    echo "Found injected KACE configurations at $BOOT_CFG:"
    cat "$BOOT_CFG"
    
    # Parse file variables manually to avoid malicious shell execution
    FILE_DASHBOARD=$(grep -E "^DASHBOARD=" "$BOOT_CFG" | cut -d'=' -f2 || true)
    FILE_CROWSNEST=$(grep -E "^CROWSNEST=" "$BOOT_CFG" | cut -d'=' -f2 || true)
    FILE_TIMEZONE=$(grep -E "^TIMEZONE=" "$BOOT_CFG" | cut -d'=' -f2 || true)
    
    # Overwrite only if arguments were not explicitly passed
    [ -z "$DASHBOARD" ] && DASHBOARD="$FILE_DASHBOARD"
    [ -z "$CROWSNEST" ] && CROWSNEST="$FILE_CROWSNEST"
    [ -z "$TIMEZONE" ] && TIMEZONE="$FILE_TIMEZONE"
fi

# Apply safe defaults if empty
[ -z "$DASHBOARD" ] && DASHBOARD="mainsail"
[ -z "$CROWSNEST" ] && CROWSNEST="false"

echo "--------------------------------------------------------"
echo "Target Configuration:"
echo "  Dashboard UI : $DASHBOARD"
echo "  Webcam Stream: $CROWSNEST"
echo "  Timezone     : ${TIMEZONE:-'(Keep system default)'}"
echo "--------------------------------------------------------"

# 3. System Timezone Configuration
if [ -n "$TIMEZONE" ]; then
    echo "Setting system timezone to $TIMEZONE..."
    sudo timedatectl set-timezone "$TIMEZONE" || echo "Warning: timezone update skipped (requires root/sudo privileges)."
fi

# 4. System Package Updates & Dependencies
echo "Updating apt repositories..."
sudo apt-get update -y

echo "Installing core dependencies..."
sudo apt-get install -y git curl python3-pip python3-virtualenv unzip nginx

# 5. Installing Klipper (Firmware Engine)
if [ ! -d "$HOME/klipper" ]; then
    echo "Cloning Klipper repository..."
    git clone https://github.com/Klipper3d/klipper.git "$HOME/klipper"
else
    echo "Klipper repository already exists. Updating..."
    cd "$HOME/klipper" && git pull && cd "$HOME"
fi
echo "Installing Klipper dependencies and systemd service..."
# Run the official Klipper installer script
"$HOME/klipper/scripts/install-octopi.sh"

# 6. Installing Moonraker (API Web Server Backend)
if [ ! -d "$HOME/moonraker" ]; then
    echo "Cloning Moonraker repository..."
    git clone https://github.com/Arksine/moonraker.git "$HOME/moonraker"
else
    echo "Moonraker repository already exists. Updating..."
    cd "$HOME/moonraker" && git pull && cd "$HOME"
fi
echo "Installing Moonraker dependencies and systemd service..."
"$HOME/moonraker/scripts/install-moonraker.sh"

# 7. Installing Mainsail/Fluidd Dashboard
echo "Setting up Nginx and downloading selected web interfaces..."

# Make webroot directories
sudo mkdir -p /var/www

setup_mainsail() {
    echo "Installing Mainsail control interface..."
    sudo mkdir -p /var/www/mainsail
    curl -L https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip -o /tmp/mainsail.zip
    sudo unzip -o /tmp/mainsail.zip -d /var/www/mainsail
    rm -f /tmp/mainsail.zip
}

setup_fluidd() {
    echo "Installing Fluidd control interface..."
    sudo mkdir -p /var/www/fluidd
    curl -L https://github.com/fluidd-core/fluidd/releases/latest/download/fluidd.zip -o /tmp/fluidd.zip
    sudo unzip -o /tmp/fluidd.zip -d /var/www/fluidd
    rm -f /tmp/fluidd.zip
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
    DEFAULT_UI="mainsail" # Default entry point for double setup
else
    echo "Invalid dashboard choice: $DASHBOARD. Defaulting to Mainsail."
    setup_mainsail
    DEFAULT_UI="mainsail"
fi

# 8. Configuring Nginx Server Blocks
echo "Configuring Nginx web server..."
NGINX_CONF="/etc/nginx/sites-available/kace-printer"

sudo tee "$NGINX_CONF" > /dev/null <<EOF
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

    # Proxy Moonraker API requests
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

    location /server {
        proxy_pass http://apiserver;
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

# Enable configuration
sudo ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

# 9. Installing Crowsnest (Webcam Streamer)
if [ "$CROWSNEST" = "true" ]; then
    echo "Installing Crowsnest webcam streaming engine..."
    if [ ! -d "$HOME/crowsnest" ]; then
        git clone https://github.com/mainsail-crew/crowsnest.git "$HOME/crowsnest"
    else
        cd "$HOME/crowsnest" && git pull && cd "$HOME"
    fi
    cd "$HOME/crowsnest"
    sudo ./install.sh --non-interactive
    cd "$HOME"
else
    echo "Crowsnest was not selected. Skipping webcam setup."
fi

echo -e "\033[1;32m"
echo "========================================================"
echo "      Bootstrap complete! KACE Node is fully ready.     "
echo "========================================================"
echo -e "\033[0m"
