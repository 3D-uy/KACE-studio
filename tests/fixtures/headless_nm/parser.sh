#!/usr/bin/env bash
# headless_nm
#
# Written by Stephan Wendel aka KwadFan <me@stephanwe.de>
# refactored by Stefan Dej aka meteyou <meteyou@mainsail.xyz>
#
# Copyright 2024 - till today
# https://github.com/mainsail-crew/MainsailOS
#
# This file is distributed under GPLv3
#
# Description:
# This is a wrapper for NetworkManager to set up WiFi connections on a headless
# SBC. It reads the configuration from a file in /boot and recreates the
# Raspberry 'preconfigured.nmconnection'.
#
# Copyright hint:
# This file contains code snippets from Raspberry raspi-config (see LICENSE) and
# reuses parts of raspberry-sys-mods.
#
# https://github.com/RPi-Distro/raspi-config/blob/bookworm/LICENSE
# https://github.com/RPi-Distro/raspberrypi-sys-mods/blob/bookworm/usr/lib/raspberrypi-sys-mods/imager_custom

get_value() {
    local key="$1" line key_part val_part

    while IFS= read -r line; do
        # Skip comments, empty lines or lines without '='
        if [[ "$line" =~ ^#.*$ || -z "$line" || "$line" != *"="* ]]; then
            continue
        fi

        # extract key and trim whitespace
        key_part="$(trim_whitespace "${line%%=*}")"

        # Continue with next line if key does not match
        if [[ "$key_part" != "$key" ]]; then
            continue
        fi

        # extract value and trim whitespace
        val_part="$(trim_whitespace "${line#*=}")"

        # Remove quotes from value if present
        val_part="$(remove_quotes "$val_part")" || {
            log "ERROR: Could not remove quotes from value for key '$key'"
            exit 1
        }

        echo "$val_part"
        return 0
    done < <(tac -- "$SETUPFILE")
}

trim_whitespace() {
    sed -e 's/^[[:space:]]\+//' -e 's/[[:space:]]\+$//' <<<"$1"
}

remove_quotes() {
    local str=$1
    local quotes=('"'
                "'"
                '`'
                '´')

    for quote in "${quotes[@]}"; do
        if [[ "${str:0:1}" != "$quote" ]]; then
            continue
        fi

        # Remove first quote
        str="${str:1}"

        # Return an error if no closing quote is found
        if [[ "$str" != *"$quote"* ]]; then
            return 1
        fi

        # Extract until next quote
        str="${str%%"$quote"*}"
        break
    done

    echo "$str"
    return 0
}

