// KACE Desktop Web App logic

let activeTab = 'imager-tab';
let term = null;
let fitAddon = null;
let searchAddon = null;
let sshConnected = false;
let currentDeviceIp = "";
let currentDeviceName = "";
let loginState = 'DISCONNECTED'; // 'DISCONNECTED', 'PROMPTING_USER', 'PROMPTING_PASS', 'CONNECTING'
let loginUsername = '';
let loginPassword = '';
let connectedUsername = 'kace';
let currentLoginInput = '';

document.addEventListener('DOMContentLoaded', () => {
    // Initialize blank xterm terminal
    initTerminal();
    
    // Initialize custom dropdown selectors
    initCustomDropdowns();
    
    // Initialize real-time validation clearing listeners
    initValidationListeners();
    
    // Restore saved form values from localStorage
    restoreFormState();
    
    // Auto-detect timezone if not overridden by saved state
    autoDetectTimezone();
    
    // Initialize form persistence listeners
    initFormPersistence();
    
    // Check saved theme preferences
    const savedTheme = localStorage.getItem('theme') || 'dark';
    if (savedTheme === 'light') {
        document.body.classList.add('light-mode');
        const themeIcon = document.getElementById('theme-icon');
        const themeText = document.getElementById('theme-text');
        if (themeIcon) themeIcon.className = 'fa-solid fa-moon';
        if (themeText) themeText.textContent = 'Dark Mode';
    }
});


// PyWebView communication initialization
window.addEventListener('pywebviewready', () => {
    console.log("PyWebView Python API connection established.");
    refreshDrives();
    triggerScan();
});

// Tab Switching Routing
function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.nav-item').forEach(btn => {
        btn.classList.remove('active');
    });
    
    document.getElementById(tabId).classList.add('active');
    
    // Highlight sidebar nav item
    if (tabId === 'imager-tab') {
        document.querySelector('.nav-item:nth-child(1)').classList.add('active');
    } else if (tabId === 'discovery-tab') {
        document.querySelector('.nav-item:nth-child(2)').classList.add('active');
    } else if (tabId === 'terminal-tab') {
        document.getElementById('terminal-nav-btn').classList.add('active');
        // Fit terminal viewport on window display
        setTimeout(() => {
            if (fitAddon) fitAddon.fit();
        }, 100);
    }
    
    activeTab = tabId;
}

// Drive Management (Stage A)
function refreshDrives() {
    const driveSelect = document.getElementById('drive-select');
    driveSelect.innerHTML = '<option value="">Scanning for removable drives...</option>';
    
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.get_drives().then((drives) => {
            driveSelect.innerHTML = '';
            if (drives.length === 0) {
                driveSelect.innerHTML = '<option value="">No removable drives found.</option>';
                return;
            }
            drives.forEach(drive => {
                const opt = document.createElement('option');
                opt.value = drive.id;
                opt.textContent = `${drive.name} (${drive.size}) [Drive ${drive.id}]`;
                driveSelect.appendChild(opt);
            });
        }).catch(err => {
            console.error("Failed to load drives: ", err);
            driveSelect.innerHTML = '<option value="">Error querying drives.</option>';
        });
    } else {
        // Mock drives in pure web debug mode
        setTimeout(() => {
            driveSelect.innerHTML = `
                <option value="1">Generic USB Flash Drive (14.9 GB) [Drive 1]</option>
                <option value="2">SanDisk Ultra Card Reader (29.8 GB) [Drive 2]</option>
            `;
        }, 500);
    }
}

function toggleImageSource(value) {
    const wrapper = document.getElementById('custom-image-wrapper');
    const pathInput = document.getElementById('custom-image-path');
    if (value === 'custom') {
        wrapper.style.display = 'flex';
    } else {
        wrapper.style.display = 'none';
        pathInput.value = '';
        clearInputError('custom-image-path');
    }
}

function browseLocalImage() {
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.browse_image().then((path) => {
            if (path) {
                document.getElementById('custom-image-path').value = path;
                clearInputError('custom-image-path');
            }
        });
    } else {
        document.getElementById('custom-image-path').value = "C:\\Downloads\\mainsailos-lite-bookworm.img";
        clearInputError('custom-image-path');
    }
}

function showInputError(inputId, message) {
    const inputEl = document.getElementById(inputId);
    if (!inputEl) return;
    
    inputEl.classList.add('input-error');
    
    const targetContainer = inputEl.closest('.password-wrapper') || inputEl;
    const parent = targetContainer.parentNode;
    
    // Create text label if not present, specific to this inputId
    let errorEl = parent.querySelector(`.error-message-label[data-for="${inputId}"]`);
    if (!errorEl) {
        errorEl = document.createElement('span');
        errorEl.className = 'error-message-label';
        errorEl.setAttribute('data-for', inputId);
        errorEl.style.color = 'var(--danger-color)';
        errorEl.style.fontSize = '11px';
        errorEl.style.marginTop = '4px';
        errorEl.style.display = 'block';
        // Insert right after the container/input element to prevent layout shifting of relative items (like icons)
        parent.insertBefore(errorEl, targetContainer.nextSibling);
    }
    errorEl.textContent = message;
}

function clearInputError(inputId) {
    const inputEl = document.getElementById(inputId);
    if (!inputEl) return;
    
    inputEl.classList.remove('input-error');
    const targetContainer = inputEl.closest('.password-wrapper') || inputEl;
    const parent = targetContainer.parentNode;
    const errorEl = parent.querySelector(`.error-message-label[data-for="${inputId}"]`);
    if (errorEl) {
        errorEl.remove();
    }
}

window.togglePasswordVisibility = function(inputId, iconEl) {
    const inputEl = document.getElementById(inputId);
    if (!inputEl) return;
    
    if (inputEl.type === 'password') {
        inputEl.type = 'text';
        iconEl.classList.remove('fa-eye');
        iconEl.classList.add('fa-eye-slash');
    } else {
        inputEl.type = 'password';
        iconEl.classList.remove('fa-eye-slash');
        iconEl.classList.add('fa-eye');
    }
};

function clearInputErrors() {
    document.querySelectorAll('.input-error').forEach(el => {
        el.classList.remove('input-error');
    });
    document.querySelectorAll('.error-message-label').forEach(el => {
        el.remove();
    });
}

function validateSshPasswords() {
    const password = document.getElementById('ssh-password').value;
    const passwordConfirm = document.getElementById('ssh-password-confirm').value;
    
    // Always clear the mismatch error on confirm first
    clearInputError('ssh-password-confirm');
    
    // If confirmation is introduced and does not match, show error immediately
    if (passwordConfirm && password !== passwordConfirm) {
        showInputError('ssh-password-confirm', "User credentials passwords do not match.");
        return false;
    }
    return true;
}

function validateWifiPasswords() {
    const wifiPassword = document.getElementById('wifi-password').value;
    const wifiPasswordConfirm = document.getElementById('wifi-password-confirm').value;
    
    // Always clear the mismatch error on confirm first
    clearInputError('wifi-password-confirm');
    
    // If confirmation is introduced and does not match, show error immediately
    if (wifiPasswordConfirm && wifiPassword !== wifiPasswordConfirm) {
        showInputError('wifi-password-confirm', "Wi-Fi passwords do not match.");
        return false;
    }
    return true;
}

function initValidationListeners() {
    const monitoredIds = [
        'drive-select',
        'hostname-input',
        'wifi-ssid'
    ];
    monitoredIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            const clearFn = () => clearInputError(id);
            el.addEventListener('input', clearFn);
            el.addEventListener('change', clearFn);
        }
    });

    // Wire up SSH Password real-time matching checks
    const sshPw = document.getElementById('ssh-password');
    const sshPwConfirm = document.getElementById('ssh-password-confirm');
    if (sshPw && sshPwConfirm) {
        const checkFn = () => {
            clearInputError('ssh-password');
            validateSshPasswords();
        };
        sshPw.addEventListener('input', checkFn);
        sshPw.addEventListener('change', checkFn);
        sshPwConfirm.addEventListener('input', checkFn);
        sshPwConfirm.addEventListener('change', checkFn);
    }

    // Wire up WiFi Password real-time matching checks
    const wifiPw = document.getElementById('wifi-password');
    const wifiPwConfirm = document.getElementById('wifi-password-confirm');
    if (wifiPw && wifiPwConfirm) {
        const checkFn = () => {
            clearInputError('wifi-password');
            validateWifiPasswords();
        };
        wifiPw.addEventListener('input', checkFn);
        wifiPw.addEventListener('change', checkFn);
        wifiPwConfirm.addEventListener('input', checkFn);
        wifiPwConfirm.addEventListener('change', checkFn);
    }
}

function openFormatModal() {
    clearInputErrors();
    let hasErrors = false;
    
    const imageSource = document.getElementById('image-source-select').value;
    if (imageSource === 'custom') {
        const customPath = document.getElementById('custom-image-path').value.trim();
        if (!customPath) {
            showInputError('custom-image-path', "Please browse and select a custom local OS image.");
            hasErrors = true;
        }
    }
    
    const driveSelect = document.getElementById('drive-select');
    const driveId = driveSelect.value;
    if (!driveId) {
        showInputError('drive-select', "Please select a target storage drive.");
        hasErrors = true;
    }
    
    const hostname = document.getElementById('hostname-input').value.trim();
    if (!hostname) {
        showInputError('hostname-input', "Please specify a Hostname.");
        hasErrors = true;
    }
    
    // Step 6: User Credentials check
    const password = document.getElementById('ssh-password').value;
    const passwordConfirm = document.getElementById('ssh-password-confirm').value;
    if (!password) {
        showInputError('ssh-password', "An SSH password must be specified for user 'kace'.");
        hasErrors = true;
    }
    if (password !== passwordConfirm) {
        showInputError('ssh-password-confirm', "User credentials passwords do not match.");
        hasErrors = true;
    }
    
    // Step 7: WiFi credentials check
    const wifiSsid = document.getElementById('wifi-ssid').value.trim();
    const wifiPassword = document.getElementById('wifi-password').value;
    const wifiPasswordConfirm = document.getElementById('wifi-password-confirm').value;
    
    if (wifiSsid || wifiPassword || wifiPasswordConfirm) {
        if (!wifiSsid) {
            showInputError('wifi-ssid', "SSID is required when Wi-Fi passwords are provided.");
            hasErrors = true;
        }
        if (wifiPassword !== wifiPasswordConfirm) {
            showInputError('wifi-password-confirm', "Wi-Fi passwords do not match.");
            hasErrors = true;
        }
    }
    
    if (hasErrors) {
        return;
    }
    
    // Get drive name for display in modal
    const selectedOption = driveSelect.options[driveSelect.selectedIndex];
    document.getElementById('modal-drive-name').innerHTML = `Target Drive: <strong>${selectedOption.textContent}</strong>`;
    
    // Show step 10 modal
    document.getElementById('format-modal').style.display = 'flex';
}

function closeFormatModal() {
    document.getElementById('format-modal').style.display = 'none';
}

function closeSuccessModal() {
    document.getElementById('success-modal').style.display = 'none';
}

function confirmAndFlash() {
    closeFormatModal();
    startFlashing();
}

function startFlashing() {
    const driveSelect = document.getElementById('drive-select');
    const driveId = driveSelect.value;
    
    // Hardware, OS & Arch
    const piModel = document.getElementById('pi-model-select').value;
    const dashboardUi = document.getElementById('bootstrap-ui-select-imager').value;
    const osArch = document.getElementById('os-arch-select').value;
    
    const hostname = document.getElementById('hostname-input').value.trim();
    const timezone = document.getElementById('timezone-select').value;
    
    // Credentials
    const sshPassword = document.getElementById('ssh-password').value;
    
    // WiFi setup
    const wifiSsid = document.getElementById('wifi-ssid').value;
    const wifiPassword = document.getElementById('wifi-password').value;
    
    // Services
    const sshEnabled = document.getElementById('ssh-enable').checked;
    const crowsnest = document.getElementById('crowsnest-enable').checked;
    
    // Image configuration
    const imageSource = document.getElementById('image-source-select').value;
    const imagePath = imageSource === 'custom' ? document.getElementById('custom-image-path').value : "default_lite";
    
    // Show progress panel
    const progressCard = document.getElementById('progress-card');
    const flashBtn = document.getElementById('flash-action-btn');
    const cancelBtn = document.getElementById('cancel-flash-btn');
    progressCard.style.display = 'block';
    flashBtn.disabled = true;
    if (cancelBtn) cancelBtn.style.display = 'block';
    
    updateProgress(0, "Requesting administrative privileges...");
    window.updateDeviceState("FLASHING", 0, "Requesting administrative privileges...");
    
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.start_flash(
            parseInt(driveId), 
            imagePath, 
            hostname, 
            wifiSsid, 
            wifiPassword, 
            sshPassword,
            dashboardUi,
            timezone,
            piModel,
            osArch,
            sshEnabled,
            crowsnest
        ).then(res => {
            if (!res) {
                window.updateDeviceState("ERROR", 0, "Flashing aborted or failed.");
            }
        }).catch(err => {
            window.updateDeviceState("ERROR", 0, "Error triggering flash process: " + err);
        });
    } else {
        // Mock progress in browser UI (Debug/Dev Mode)
        let progress = 0;
        const interval = setInterval(() => {
            progress += 10;
            window.updateDeviceState("FLASHING", progress, `Writing blocks: ${progress}%...`);
            if (progress >= 90) {
                clearInterval(interval);
                window.updateDeviceState("FLASHING", 95, "Injecting system configuration files...");
                setTimeout(() => {
                    window.updateDeviceState("FLASHED", 100, "SD Card successfully flashed and provisioned!");
                }, 1000);
            }
        }, 300);
    }
}

function cancelFlashing() {
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.cancel_flash().then(() => {
            console.log("Flash cancellation requested.");
        });
    }
    const cancelBtn = document.getElementById('cancel-flash-btn');
    if (cancelBtn) {
        cancelBtn.disabled = true;
        cancelBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Cancelling...';
    }
}

// Global functions exposed to Python flasher thread and state machine
let currentDeviceState = "UNKNOWN";

window.updateDeviceState = function(state, progress, message) {
    console.log(`State Transition: ${currentDeviceState} -> ${state} (${progress}%) - ${message}`);
    currentDeviceState = state;
    
    updateTrackerBar(state);
    
    const globalStatus = document.getElementById('global-connection-status');
    const flashBtn = document.getElementById('flash-action-btn');
    
    switch(state) {
        case 'UNKNOWN':
            globalStatus.className = 'status-indicator-offline';
            globalStatus.innerHTML = '<i class="fa-solid fa-circle-nodes"></i> Disconnected';
            break;
        case 'FLASHING':
            globalStatus.className = 'status-indicator-online';
            globalStatus.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Flashing: ${progress}%`;
            
            const progressCard = document.getElementById('progress-card');
            progressCard.style.display = 'block';
            flashBtn.disabled = true;
            updateProgress(progress, message);
            
            // Disable cancel once block writing starts (to prevent SD corruption)
            const cancelBtnFlashing = document.getElementById('cancel-flash-btn');
            if (cancelBtnFlashing && message && message.toLowerCase().startsWith('writing blocks')) {
                cancelBtnFlashing.style.display = 'none';
            }
            break;
        case 'FLASHED':
            globalStatus.className = 'status-indicator-online';
            globalStatus.innerHTML = '<i class="fa-solid fa-compact-disc"></i> Flipped & Flashed';
            flashBtn.disabled = false;
            updateProgress(100, message);
            document.getElementById('success-modal').style.display = 'flex';
            
            // Hide cancel button
            const cancelBtnDone = document.getElementById('cancel-flash-btn');
            if (cancelBtnDone) { cancelBtnDone.style.display = 'none'; cancelBtnDone.disabled = false; cancelBtnDone.innerHTML = '<i class="fa-solid fa-xmark"></i> Cancel'; }
            
            // Play completion notification sound
            playFlashCompletionSound();
            
            // Show desktop notification if window not focused
            showFlashNotification();
            break;
        case 'CONNECTING':
            globalStatus.className = 'status-indicator-online';
            globalStatus.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Connecting...';
            if (term) term.write(`\x1b[1;33m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'DISCOVERED':
            globalStatus.className = 'status-indicator-online';
            globalStatus.innerHTML = '<i class="fa-solid fa-satellite-dish"></i> Discovered';
            if (term) term.write(`\x1b[1;36m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'SSH_READY':
            globalStatus.className = 'status-indicator-online';
            globalStatus.innerHTML = '<i class="fa-solid fa-circle-check"></i> Connected (SSH)';
            updateConnectionStatus(true);
            if (term) term.write(`\x1b[1;32m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'BOOTSTRAPPED':
            globalStatus.className = 'status-indicator-online';
            globalStatus.innerHTML = '<i class="fa-solid fa-rocket"></i> Bootstrapped';
            updateConnectionStatus(true);
            if (term) term.write(`\x1b[1;32m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'ERROR':
            globalStatus.className = 'status-indicator-offline';
            globalStatus.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Error';
            flashBtn.disabled = false;
            
            // Reset cancel button state
            const cancelBtnErr = document.getElementById('cancel-flash-btn');
            if (cancelBtnErr) { cancelBtnErr.style.display = 'none'; cancelBtnErr.disabled = false; cancelBtnErr.innerHTML = '<i class="fa-solid fa-xmark"></i> Cancel'; }
            
            if (activeTab === 'imager-tab') {
                updateProgress(0, `Error: ${message}`);
            } else if (activeTab === 'terminal-tab') {
                updateConnectionStatus(false);
                if (term) term.write(`\x1b[1;31m[Error] ${message}\x1b[0m\r\n`);
                showTroubleshootingPrompt(message);
            }
            break;
    }
};

function showTroubleshootingPrompt(errorMsg) {
    if (!term) return;
    term.write("\r\n\x1b[1;35m[KACE Diagnostics] Troubleshooting Guidance:\x1b[0m\r\n");
    term.write(" 1. \x1b[1;37mPower Check:\x1b[0m Verify the Raspberry Pi is powered on and ACT green LED is blinking.\r\n");
    term.write(" 2. \x1b[1;37mNetwork Subnet:\x1b[0m Ensure your PC and the Pi are connected to the same WiFi SSID / Subnet.\r\n");
    term.write(" 3. \x1b[1;37mWiFi Band:\x1b[0m Confirm you didn't connect a 2.4GHz-only Pi model (like Zero W or 3B) to a 5GHz band.\r\n");
    term.write(" 4. \x1b[1;37mPassword Check:\x1b[0m Double-check the SSH password for user 'kace'.\r\n");
    term.write(" 5. \x1b[1;37mRouter isolation:\x1b[0m Confirm Client/AP Isolation is disabled on your router.\r\n\r\n");
}

function updateTrackerBar(state) {
    const steps = [
        { id: 'step-unknown', states: ['UNKNOWN'] },
        { id: 'step-flashing', states: ['FLASHING'] },
        { id: 'step-booting', states: ['FLASHED', 'BOOTING'] },
        { id: 'step-discovered', states: ['DISCOVERED', 'CONNECTING'] },
        { id: 'step-ssh', states: ['SSH_READY'] },
        { id: 'step-bootstrapped', states: ['BOOTSTRAPPED'] }
    ];
    
    let activeIndex = -1;
    steps.forEach((step, idx) => {
        if (step.states.includes(state)) {
            activeIndex = idx;
        }
    });
    
    if (state === 'ERROR' || activeIndex === -1) {
        return; 
    }
    
    steps.forEach((step, idx) => {
        const el = document.getElementById(step.id);
        if (!el) return;
        el.classList.remove('active', 'completed');
        if (idx < activeIndex) {
            el.classList.add('completed');
            const numEl = el.querySelector('.step-num');
            if (numEl) numEl.innerHTML = '<i class="fa-solid fa-check"></i>';
        } else if (idx === activeIndex) {
            el.classList.add('active');
            const numEl = el.querySelector('.step-num');
            if (numEl) numEl.textContent = idx + 1;
        } else {
            const numEl = el.querySelector('.step-num');
            if (numEl) numEl.textContent = idx + 1;
        }
    });
}

function updateProgress(percent, message) {
    const fill = document.getElementById('progress-fill');
    const msg = document.getElementById('flasher-status-msg');
    
    fill.style.width = `${percent}%`;
    fill.textContent = `${percent}%`;
    msg.textContent = message;
}


// Network Discovery & Scanning (Stage B)
function triggerScan() {
    const visual = document.getElementById('scanner-visual');
    const text = document.getElementById('scan-status-text');
    const list = document.getElementById('discovered-device-list');
    
    visual.classList.add('scanning');
    text.textContent = "Probing local network subnet...";
    list.innerHTML = `
        <div class="list-empty">
            <i class="fa-solid fa-spinner fa-spin"></i> Subnet port scanning active...
        </div>
    `;
    
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.scan_network().then(devices => {
            visual.classList.remove('scanning');
            text.textContent = "Scan completed.";
            populateDevices(devices);
        }).catch(err => {
            console.error(err);
            visual.classList.remove('scanning');
            text.textContent = "Scan failed.";
            list.innerHTML = '';
            const emptyDiv = document.createElement('div');
            emptyDiv.className = 'list-empty';
            emptyDiv.textContent = "Scan failed: " + err;
            list.appendChild(emptyDiv);
        });
    } else {
        // Mock nodes found
        setTimeout(() => {
            visual.classList.remove('scanning');
            text.textContent = "Scan complete (Debug Mock Mode).";
            populateDevices([
                { ip: "192.168.1.99", hostname: "kace.local", ssh: true, moonraker: false },
                { ip: "192.168.1.121", hostname: "mainsailos.local", ssh: true, moonraker: true }
            ]);
        }, 1500);
    }
}

function populateDevices(devices) {
    const list = document.getElementById('discovered-device-list');
    list.innerHTML = '';
    
    if (devices.length === 0) {
        const emptyDiv = document.createElement('div');
        emptyDiv.className = 'list-empty';
        emptyDiv.innerHTML = '<i class="fa-solid fa-circle-question"></i> No responsive KACE/SBC nodes found.';
        list.appendChild(emptyDiv);
        return;
    }
    
    devices.forEach(dev => {
        const item = document.createElement('div');
        item.className = 'device-item';
        
        const deviceMeta = document.createElement('div');
        deviceMeta.className = 'device-meta';
        
        const deviceName = document.createElement('span');
        deviceName.className = 'device-name';
        deviceName.textContent = dev.hostname || 'Unknown';
        
        const deviceIp = document.createElement('span');
        deviceIp.className = 'device-ip';
        deviceIp.textContent = dev.ip || '';
        
        const deviceTags = document.createElement('div');
        deviceTags.className = 'device-tags';
        
        if (dev.ssh) {
            const tagSsh = document.createElement('span');
            tagSsh.className = 'tag tag-ssh';
            tagSsh.textContent = 'SSH Enabled';
            deviceTags.appendChild(tagSsh);
        }
        if (dev.moonraker) {
            const tagMoon = document.createElement('span');
            tagMoon.className = 'tag tag-moonraker';
            tagMoon.textContent = 'Moonraker';
            deviceTags.appendChild(tagMoon);
        }
        
        deviceMeta.appendChild(deviceName);
        deviceMeta.appendChild(deviceIp);
        deviceMeta.appendChild(deviceTags);
        
        const connectBtn = document.createElement('button');
        connectBtn.className = 'btn btn-primary btn-sm';
        connectBtn.textContent = 'Connect';
        connectBtn.addEventListener('click', () => {
            connectToDevice(dev.ip, dev.hostname);
        });
        
        item.appendChild(deviceMeta);
        item.appendChild(connectBtn);
        list.appendChild(item);
    });
}

function connectManually() {
    const ip = document.getElementById('manual-ip').value.trim();
    if (!ip) {
        alert("Please enter a valid IP address or hostname.");
        return;
    }
    
    const list = document.getElementById('discovered-device-list');
    list.innerHTML = `
        <div class="list-empty">
            <i class="fa-solid fa-spinner fa-spin"></i> Probing manual IP address ${ip}...
        </div>
    `;
    
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.probe_device_ip(ip).then(device => {
            if (device) {
                populateDevices([device]);
            } else {
                list.innerHTML = `
                    <div class="list-empty text-danger">
                        <i class="fa-solid fa-triangle-exclamation"></i> Manual IP probe failed. Host unresponsive.
                    </div>
                `;
                alert("IP Probe failed. Target device is not listening on SSH (22) or Moonraker (7125) ports.");
            }
        }).catch(err => {
            list.innerHTML = '';
            const emptyDiv = document.createElement('div');
            emptyDiv.className = 'list-empty';
            emptyDiv.textContent = "Manual probe error: " + err;
            list.appendChild(emptyDiv);
        });
    } else {
        // Mock fallback in UI debug mode
        setTimeout(() => {
            populateDevices([{ ip: ip, hostname: "manual-node.local", ssh: true, moonraker: false }]);
        }, 1000);
    }
}

function promptTerminalLogin() {
    loginState = 'PROMPTING_USER';
    loginUsername = '';
    loginPassword = '';
    currentLoginInput = '';
    term.write('login: ');
}

function handleLoginInput(data) {
    if (data === '\r' || data === '\n' || data === '\r\n') {
        if (loginState === 'PROMPTING_USER') {
            loginUsername = currentLoginInput.trim();
            currentLoginInput = '';
            if (loginUsername === '') {
                term.write('\r\nlogin: ');
            } else {
                loginState = 'PROMPTING_PASS';
                term.write('\r\nPassword: ');
            }
        } else if (loginState === 'PROMPTING_PASS') {
            loginPassword = currentLoginInput;
            currentLoginInput = '';
            loginState = 'CONNECTING';
            term.write('\r\n[KACE Workspace] Establishing SSH connection...\r\n');
            performSshLogin(loginUsername, loginPassword);
        }
    } else if (data === '\x7f' || data === '\b') {
        if (currentLoginInput.length > 0) {
            currentLoginInput = currentLoginInput.slice(0, -1);
            if (loginState === 'PROMPTING_USER') {
                term.write('\b \b');
            }
        }
    } else if (data.charCodeAt(0) < 32 || data.startsWith('\x1b')) {
        // Ignore control codes/escape sequences
    } else {
        currentLoginInput += data;
        if (loginState === 'PROMPTING_USER') {
            term.write(data);
        }
    }
}

function performSshLogin(username, password) {
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.connect_ssh(currentDeviceIp, username, password).then(success => {
            if (success) {
                connectedUsername = username;
                term.write("\x1b[1;32m[KACE Workspace] SSH connection established successfully.\x1b[0m\r\n");
                updateConnectionStatus(true);
                loginState = 'DISCONNECTED';
            } else {
                term.write("\r\n\x1b[1;31m[Error] SSH connection failed. Verify user credentials or network path.\x1b[0m\r\n");
                updateConnectionStatus(false);
                promptTerminalLogin();
            }
        }).catch(err => {
            term.write(`\r\n\x1b[1;31m[Error] Connection error: ${err}\x1b[0m\r\n`);
            updateConnectionStatus(false);
            promptTerminalLogin();
        });
    } else {
        // Mock connection
        setTimeout(() => {
            if (username === 'kace' && password === 'kace') {
                connectedUsername = username;
                term.write("\x1b[1;32m[KACE Workspace] (DEBUG MOCK) SSH connection established.\x1b[0m\r\n");
                term.write("kace@kace:~ $ ");
                updateConnectionStatus(true);
                loginState = 'DISCONNECTED';
                window.updateDeviceState("SSH_READY", 100, "Connected to mock node.");
            } else {
                term.write("\r\n\x1b[1;31m[Error] (DEBUG MOCK) Login failed. Hint: use kace/kace.\x1b[0m\r\n");
                updateConnectionStatus(false);
                promptTerminalLogin();
            }
        }, 1000);
    }
}

function connectToDevice(ip, name) {
    currentDeviceIp = ip;
    currentDeviceName = name;
    
    const terminalNav = document.getElementById('terminal-nav-btn');
    terminalNav.click(); // Switch to terminal workspace tab
    
    term.clear();
    term.write(`\x1b[1;35m[KACE Workspace] Connecting to ${name} (${ip})...\x1b[0m\r\n`);
    
    promptTerminalLogin();
}


// Terminal Workspace (Stage C)
function initTerminal() {
    const container = document.getElementById('terminal-container');
    container.innerHTML = '';
    
    term = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: 'JetBrains Mono, monospace',
        theme: {
            background: '#000000',
            foreground: '#ffffff',
            cursor: '#5a52e5',
            magenta: '#a78bfa',
            green: '#10b981',
            red: '#f43f5e'
        }
    });
    
    fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    
    // Load search addon if available
    if (typeof SearchAddon !== 'undefined') {
        searchAddon = new SearchAddon.SearchAddon();
        term.loadAddon(searchAddon);
    }
    
    term.open(container);
    fitAddon.fit();
    
    // Bind window resize event
    window.addEventListener('resize', () => {
        if (fitAddon) fitAddon.fit();
    });
    
    // Ctrl+F to toggle terminal search bar
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'f' && activeTab === 'terminal-tab') {
            e.preventDefault();
            toggleTerminalSearch();
        }
    });
    
    // Enter key in search input triggers findNext
    const searchInput = document.getElementById('terminal-search-input');
    if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (e.shiftKey) {
                    terminalSearchPrev();
                } else {
                    terminalSearchNext();
                }
            } else if (e.key === 'Escape') {
                toggleTerminalSearch();
            }
        });
    }
    
    // Send terminal input to Python SSH bridge
    term.onData(data => {
        if (sshConnected && window.pywebview && window.pywebview.api) {
            window.pywebview.api.send_ssh_input(data);
        } else if (loginState === 'PROMPTING_USER' || loginState === 'PROMPTING_PASS') {
            handleLoginInput(data);
        } else if (!sshConnected) {
            // Echo locally if not connected in debug/mock
            term.write(data);
        }
    });
}

// Push data from Python SSH output stream into xterm terminal
window.writeTerminalData = function(data) {
    if (term) {
        term.write(data);
    }
};

function updateConnectionStatus(connected) {
    sshConnected = connected;
    const globalStatus = document.getElementById('global-connection-status');
    const bootstrapBtn = document.getElementById('bootstrap-btn');
    const disconnectBtn = document.getElementById('disconnect-btn');
    const connTitle = document.getElementById('connection-title');
    const connSubtitle = document.getElementById('connection-subtitle');
    
    if (connected) {
        globalStatus.className = 'status-indicator-online';
        globalStatus.innerHTML = '<i class="fa-solid fa-circle-check"></i> Connected';
        bootstrapBtn.disabled = false;
        disconnectBtn.style.display = 'block';
        connTitle.textContent = `SSH Workspace — Connected`;
        connSubtitle.textContent = `Active session: ${connectedUsername}@${currentDeviceName} (${currentDeviceIp})`;
    } else {
        globalStatus.className = 'status-indicator-offline';
        globalStatus.innerHTML = '<i class="fa-solid fa-circle-nodes"></i> Disconnected';
        bootstrapBtn.disabled = true;
        disconnectBtn.style.display = 'none';
        connTitle.textContent = `SSH Session: Disconnected`;
        connSubtitle.textContent = `No active session. Select a device in the Discovery tab to connect.`;
    }
}

function disconnectSSH() {
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.disconnect_ssh().then(() => {
            term.write("\r\n\x1b[1;31m[KACE Workspace] SSH session disconnected by user.\x1b[0m\r\n");
            updateConnectionStatus(false);
            loginState = 'DISCONNECTED';
        });
    } else {
        updateConnectionStatus(false);
        loginState = 'DISCONNECTED';
    }
}

function startBootstrap() {
    if (!sshConnected) return;
    
    const selectedUi = document.getElementById('bootstrap-ui-select-imager').value || 'mainsail';
    term.write(`\r\n\x1b[1;35m[KACE Workspace] Starting KACE bootstrap execution [UI selection: ${selectedUi}]... \x1b[0m\r\n`);
    const bootstrapCmd = `curl -sSL https://raw.githubusercontent.com/3D-uy/KACE-studio/main/bootstrap.sh | bash -s -- --dashboard ${selectedUi}\n`;
    
    if (window.pywebview && window.pywebview.api) {
        // Send the shell command to execute the bootstrap
        window.pywebview.api.send_ssh_input(bootstrapCmd);
    } else {
        // Mock terminal output for bootstrap process in pure UI debug mode
        let steps = [
            `Starting Klipper & Moonraker installers...`,
            `Downloading selected dashboard components for: ${selectedUi.toUpperCase()}`,
            "Installing system dependencies: python3-venv libjpeg-dev...",
            "Configuring service permissions...",
            `KACE Bootstrapper: Setup complete! ${selectedUi.toUpperCase()} interface ready.`
        ];
        
        term.write(`$ ${bootstrapCmd}`);
        let idx = 0;
        const interval = setInterval(() => {
            if (idx < steps.length) {
                term.write(`\r\n\x1b[1;32m[Progress]\x1b[0m ${steps[idx]}\r\n`);
                idx++;
            } else {
                clearInterval(interval);
                term.write("\r\nkace@kace:~ $ ");
            }
        }, 1200);
    }
}

// Custom Dropdowns Initialization & Event Handling
function initCustomDropdowns() {
    const dropdowns = document.querySelectorAll('.custom-select-container');
    
    dropdowns.forEach(container => {
        const trigger = container.querySelector('.custom-select-trigger');
        const optionsList = container.querySelector('.custom-options-list');
        const options = container.querySelectorAll('.custom-option');
        const hiddenInput = container.querySelector('input[type="hidden"]');
        
        // Toggle dropdown open state
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdowns.forEach(other => {
                if (other !== container) {
                    other.classList.remove('open');
                }
            });
            container.classList.toggle('open');
        });
        
        // Handle selection
        options.forEach(option => {
            option.addEventListener('click', (e) => {
                e.stopPropagation();
                
                const val = option.getAttribute('data-value');
                options.forEach(opt => opt.classList.remove('selected'));
                option.classList.add('selected');
                
                hiddenInput.value = val;
                
                // Update trigger visual values
                const optImg = option.querySelector('img');
                const optGroup = option.querySelector('.logo-group');
                const optTitle = option.querySelector('.option-title').textContent;
                const optDesc = option.querySelector('.option-desc').textContent;
                
                const triggerImg = trigger.querySelector('.trigger-icon');
                const triggerGroup = trigger.querySelector('.logo-group');
                const triggerTitle = trigger.querySelector('.trigger-title');
                const triggerDesc = trigger.querySelector('.trigger-desc');
                
                triggerTitle.textContent = optTitle;
                triggerDesc.textContent = optDesc;
                
                if (triggerImg && optImg) {
                    triggerImg.src = optImg.getAttribute('src');
                    triggerImg.style.display = 'block';
                } else if (triggerGroup) {
                    if (optGroup) {
                        triggerGroup.innerHTML = optGroup.innerHTML;
                    } else if (optImg) {
                        triggerGroup.innerHTML = `<img src="${optImg.getAttribute('src')}">`;
                    }
                    triggerGroup.style.display = 'flex';
                }
                
                container.classList.remove('open');
            });
        });
    });
    
    // Close dropdowns if clicking outside container
    document.addEventListener('click', () => {
        dropdowns.forEach(container => {
            container.classList.remove('open');
        });
    });
}

// Light / Dark Theme Toggler
function toggleTheme() {
    const body = document.body;
    const themeIcon = document.getElementById('theme-icon');
    const themeText = document.getElementById('theme-text');
    
    if (body.classList.contains('light-mode')) {
        body.classList.remove('light-mode');
        localStorage.setItem('theme', 'dark');
        if (themeIcon) themeIcon.className = 'fa-solid fa-sun';
        if (themeText) themeText.textContent = 'Light Mode';
    } else {
        body.classList.add('light-mode');
        localStorage.setItem('theme', 'light');
        if (themeIcon) themeIcon.className = 'fa-solid fa-moon';
        if (themeText) themeText.textContent = 'Dark Mode';
    }
}

// ── Form Persistence (localStorage) ──────────────────────────────────────

const FORM_PERSIST_KEY = 'kace_form_state';
const PERSISTED_FIELDS = [
    { id: 'hostname-input', type: 'value' },
    { id: 'wifi-ssid', type: 'value' },
    { id: 'timezone-select', type: 'value' },
    { id: 'os-arch-select', type: 'value' },
    { id: 'image-source-select', type: 'value' },
    { id: 'pi-model-select', type: 'hidden' },
    { id: 'bootstrap-ui-select-imager', type: 'hidden' },
    { id: 'ssh-enable', type: 'checked' },
    { id: 'crowsnest-enable', type: 'checked' },
];

function saveFormState() {
    const state = {};
    PERSISTED_FIELDS.forEach(field => {
        const el = document.getElementById(field.id);
        if (el) {
            state[field.id] = field.type === 'checked' ? el.checked : el.value;
        }
    });
    try {
        localStorage.setItem(FORM_PERSIST_KEY, JSON.stringify(state));
    } catch (e) {
        console.warn('Failed to persist form state:', e);
    }
}

function restoreFormState() {
    try {
        const raw = localStorage.getItem(FORM_PERSIST_KEY);
        if (!raw) return;
        const state = JSON.parse(raw);
        
        PERSISTED_FIELDS.forEach(field => {
            const el = document.getElementById(field.id);
            if (el && state[field.id] !== undefined) {
                if (field.type === 'checked') {
                    el.checked = state[field.id];
                } else {
                    el.value = state[field.id];
                }
            }
        });
        
        // Sync image source toggle visibility
        const imageSource = document.getElementById('image-source-select');
        if (imageSource) toggleImageSource(imageSource.value);
        
    } catch (e) {
        console.warn('Failed to restore form state:', e);
    }
}

function initFormPersistence() {
    PERSISTED_FIELDS.forEach(field => {
        const el = document.getElementById(field.id);
        if (el) {
            el.addEventListener('change', saveFormState);
            if (field.type === 'value') {
                el.addEventListener('input', saveFormState);
            }
        }
    });
    
    // Also save when custom dropdown hidden inputs change (via MutationObserver)
    ['pi-model-select', 'bootstrap-ui-select-imager'].forEach(hiddenId => {
        const hiddenEl = document.getElementById(hiddenId);
        if (hiddenEl) {
            const observer = new MutationObserver(saveFormState);
            observer.observe(hiddenEl, { attributes: true, attributeFilter: ['value'] });
            // Custom dropdowns set .value via JS, so also listen via a polling fallback
            let lastVal = hiddenEl.value;
            setInterval(() => {
                if (hiddenEl.value !== lastVal) {
                    lastVal = hiddenEl.value;
                    saveFormState();
                }
            }, 500);
        }
    });
}

// ── Timezone Auto-Detection ──────────────────────────────────────────────

function autoDetectTimezone() {
    // Skip if user previously saved a timezone preference
    try {
        const raw = localStorage.getItem(FORM_PERSIST_KEY);
        if (raw) {
            const state = JSON.parse(raw);
            if (state['timezone-select']) return; // User has a saved preference
        }
    } catch (e) {}
    
    try {
        const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (detected) {
            const tzSelect = document.getElementById('timezone-select');
            if (tzSelect) {
                const options = Array.from(tzSelect.options);
                const match = options.find(opt => opt.value === detected);
                if (!match) {
                    const newOpt = document.createElement('option');
                    newOpt.value = detected;
                    newOpt.textContent = `${detected} (Auto-detected)`;
                    tzSelect.appendChild(newOpt);
                    tzSelect.value = detected;
                } else {
                    tzSelect.value = detected;
                }
            }
        }
    } catch (e) {
        console.warn('Timezone auto-detection failed:', e);
    }
}

// ── Terminal Search (xterm-addon-search) ─────────────────────────────────

function toggleTerminalSearch() {
    const bar = document.getElementById('terminal-search-bar');
    if (!bar) return;
    
    if (bar.style.display === 'none' || !bar.style.display) {
        bar.style.display = 'flex';
        document.getElementById('terminal-search-input').focus();
    } else {
        bar.style.display = 'none';
    }
}

function terminalSearchNext() {
    if (!searchAddon) return;
    const query = document.getElementById('terminal-search-input').value;
    if (query) searchAddon.findNext(query);
}

function terminalSearchPrev() {
    if (!searchAddon) return;
    const query = document.getElementById('terminal-search-input').value;
    if (query) searchAddon.findPrevious(query);
}

// ── Flash Completion Notifications ───────────────────────────────────────

function playFlashCompletionSound() {
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        
        // Play two ascending tones for a pleasant chime
        const notes = [523.25, 659.25]; // C5, E5
        notes.forEach((freq, i) => {
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = 'sine';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.15, audioCtx.currentTime + i * 0.15);
            gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + i * 0.15 + 0.4);
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start(audioCtx.currentTime + i * 0.15);
            osc.stop(audioCtx.currentTime + i * 0.15 + 0.5);
        });
    } catch (e) {
        console.warn('Audio notification failed:', e);
    }
}

function showFlashNotification() {
    if (!document.hasFocus() && 'Notification' in window) {
        if (Notification.permission === 'granted') {
            new Notification('KACE Studio', {
                body: 'SD Card successfully flashed and provisioned!',
                icon: 'KACE-studio.ico'
            });
        } else if (Notification.permission !== 'denied') {
            Notification.requestPermission().then(permission => {
                if (permission === 'granted') {
                    new Notification('KACE Studio', {
                        body: 'SD Card successfully flashed and provisioned!',
                        icon: 'KACE-studio.ico'
                    });
                }
            });
        }
    }
}
