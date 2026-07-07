# Diagnostic Report: Configuration Paths in KACE & Klipper

This report diagnoses why your configuration files (e.g., `printer.cfg`, `macros.cfg`) are placed in and read from `/home/kace/printer_data/config/` instead of `/home/kace/printer_data/`.

---

## 1. Summary of the Diagnostics
In modern Klipper/Moonraker installations (like the one set up by KACE and KACE-studio), `/home/kace/printer_data/config/` is the **correct and designated location** for all configuration files. 

If you place configuration files directly in `/home/kace/printer_data/`, Klipper will fail to find them, and Moonraker will not be able to manage them. The system automatically routes and reads files from `/home/kace/printer_data/config/` due to the following system configurations:

---

## 2. Technical Breakdown

### A. Klipper's systemd Service Configuration
During the bootstrapping process, KACE patches the systemd service for Klipper by creating a systemd override drop-in file located at `/etc/systemd/system/klipper.service.d/kace-override.conf`.

This file explicitly tells Klipper where to find its primary configuration file:
```ini
[Service]
ExecStart=
ExecStart=/home/kace/klippy-env/bin/python /home/kace/klipper/klippy/klippy.py \
    /home/kace/printer_data/config/printer.cfg \
    -l /home/kace/printer_data/logs/klippy.log \
    -a /home/kace/printer_data/comms/klippy.sock
```
* **Implication**: Klipper is hardcoded to start up using the configuration file at `/home/kace/printer_data/config/printer.cfg`.

### B. Moonraker's Virtual File Manager Structure
Moonraker acts as the API server interfacing with Klipper and web dashboards (Mainsail/Fluidd). It segregates virtual paths for security and organization. 

When you upload configurations via the **Moonraker API** deployment option, KACE sends a POST request to Moonraker's `/server/files/upload` endpoint:
* The upload payload is sent with the form field parameter `root="config"`.
* In Moonraker, the virtual `"config"` root maps directly to `/home/kace/printer_data/config/`.
* **Implication**: Moonraker automatically routes any config upload to the `config` subdirectory. It is not possible to upload a configuration file directly to `/home/kace/printer_data/` via Moonraker.

### C. KACE SSH/SFTP Default Target Paths
When deploying via **SSH/SFTP**, KACE prompts the user for the destination path:
* **The default path** is set to `~/printer_data/config/` (which resolves to `/home/kace/printer_data/config/`).
* If you input a directory path (e.g., `~/printer_data/config/` or `~/printer_data/config`), KACE's deployer automatically appends `/printer.cfg` to the path, placing the file at `/home/kace/printer_data/config/printer.cfg`.

---

## 3. Why the Modern Layout is Designed This Way
Historically, older Klipper setups placed `printer.cfg` directly in the user's home directory (`~/printer.cfg`). However, modern Klipper architectures use the `printer_data` layout to separate concerns:
1. **`/home/kace/printer_data/config/`**: Contains user-editable configuration files (`.cfg`, `.conf`).
2. **`/home/kace/printer_data/logs/`**: Keeps logging output separated to prevent cluttering directories.
3. **`/home/kace/printer_data/comms/`**: Contains sockets and runtime communication interfaces (like `klippy.sock`).
4. **`/home/kace/printer_data/gcodes/`**: Reserved for uploaded G-code files print jobs.

---

## 4. Conclusion & Recommendation
Everything is functioning exactly as designed. The configurations **must** remain in `/home/kace/printer_data/config/` for both Klipper and Moonraker to operate correctly.

* If you are manually copying files over to your printer, place them in `/home/kace/printer_data/config/`.
* If you are using the KACE CLI or KACE-studio app, let it use the default path: `~/printer_data/config/`.
