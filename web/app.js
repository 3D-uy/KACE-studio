// KACE Desktop Web App logic

let activeTab = 'imager-tab';
let lastFlashedDriveId = null;
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

let userPreferences = { theme: 'dark', kace_auto_scan: true, form_state: {} };

function applyTheme(theme) {
    const body = document.body;
    const themeIcon = document.getElementById('theme-icon');
    const themeText = document.getElementById('theme-text');
    
    if (theme === 'light') {
        body.classList.add('light-mode');
        if (themeIcon) themeIcon.className = 'fa-solid fa-moon';
        if (themeText) themeText.textContent = 'Dark Mode';
    } else {
        body.classList.remove('light-mode');
        if (themeIcon) themeIcon.className = 'fa-solid fa-sun';
        if (themeText) themeText.textContent = 'Light Mode';
    }
}

document.addEventListener('DOMContentLoaded', () => {
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
    
    // Check saved theme preferences (startup cache fallback)
    const savedTheme = localStorage.getItem('theme') || 'dark';
    applyTheme(savedTheme);

    // Initialize blank xterm terminal once local fonts are loaded
    // This prevents xterm.js from caching incorrect character widths from fallback fonts
    if (document.fonts) {
        document.fonts.ready.then(() => {
            initTerminal();
        });
    } else {
        initTerminal();
    }
});


// PyWebView communication initialization
window.addEventListener('pywebviewready', () => {
    console.log("PyWebView Python API connection established.");
    refreshDrives();
    
    const FORM_PERSIST_KEY = 'kace_form_state';
    
    if (window.pywebview && pywebview.api && pywebview.api.get_preferences) {
        pywebview.api.get_preferences().then(prefs => {
            if (prefs) {
                userPreferences = prefs;
                
                // Rule: prefs.json always overwrites localStorage
                localStorage.setItem('theme', prefs.theme || 'dark');
                localStorage.setItem(FORM_PERSIST_KEY, JSON.stringify(prefs.form_state || {}));
                localStorage.setItem('kace_auto_scan', prefs.kace_auto_scan !== false ? 'true' : 'false');
                
                // Apply theme from python preferences
                applyTheme(prefs.theme || 'dark');
                
                // Sync form elements with python preferences
                restoreFormState();
                
                // Trigger auto-scan based on python preferences
                const autoScanPref = prefs.kace_auto_scan !== false;
                if (autoScanPref) {
                    triggerScan();
                } else {
                    console.log("Auto-scan on startup disabled via user preference.");
                    const text = document.getElementById('scan-status-text');
                    if (text) text.textContent = "Auto-scan disabled. Click Scan Subnet below to discover nodes.";
                }
            } else {
                triggerScan();
            }
        }).catch(err => {
            console.error("Error loading preferences from python:", err);
            triggerScan();
        });
    } else {
        triggerScan();
    }
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
    } else if (tabId === 'credentials-tab') {
        document.getElementById('credentials-nav-btn').classList.add('active');
    } else if (tabId === 'discovery-tab') {
        document.querySelector('.nav-item:nth-child(3)').classList.add('active');
    } else if (tabId === 'terminal-tab') {
        document.getElementById('terminal-nav-btn').classList.add('active');
        // Fit terminal viewport on window display
        setTimeout(() => {
            if (fitAddon) fitAddon.fit();
        }, 100);
    }
    
    activeTab = tabId;
    
    // Refresh SFTP Panel status
    refreshSftpBrowser();
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
        'wifi-ssid',
        'ssh-username'
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
    let errTab = null;
    
    const imageSource = document.getElementById('image-source-select').value;
    if (imageSource === 'custom') {
        const customPath = document.getElementById('custom-image-path').value.trim();
        if (!customPath) {
            showInputError('custom-image-path', "Please browse and select a custom local OS image.");
            hasErrors = true;
            if (!errTab) errTab = 'imager-tab';
        }
    }
    
    const driveSelect = document.getElementById('drive-select');
    const driveId = driveSelect.value;
    if (!driveId) {
        showInputError('drive-select', "Please select a target storage drive.");
        hasErrors = true;
        if (!errTab) errTab = 'imager-tab';
    }
    
    const hostname = document.getElementById('hostname-input').value.trim();
    if (!hostname) {
        showInputError('hostname-input', "Please specify a Hostname.");
        hasErrors = true;
        if (!errTab) errTab = 'imager-tab';
    }
    
    // Step 7: User Credentials check
    const username = document.getElementById('ssh-username').value.trim();
    if (!username) {
        showInputError('ssh-username', "An SSH username must be specified.");
        hasErrors = true;
        if (!errTab) errTab = 'credentials-tab';
    } else if (!/^[a-z_][a-z0-9_-]*$/.test(username)) {
        showInputError('ssh-username', "Username must start with a lowercase letter or underscore, and only contain lowercase letters, numbers, hyphens, or underscores.");
        hasErrors = true;
        if (!errTab) errTab = 'credentials-tab';
    }

    const password = document.getElementById('ssh-password').value;
    const passwordConfirm = document.getElementById('ssh-password-confirm').value;
    if (!password) {
        showInputError('ssh-password', `An SSH password must be specified for user '${username || 'kace'}'.`);
        hasErrors = true;
        if (!errTab) errTab = 'credentials-tab';
    }
    if (password !== passwordConfirm) {
        showInputError('ssh-password-confirm', "User credentials passwords do not match.");
        hasErrors = true;
        if (!errTab) errTab = 'credentials-tab';
    }
    
    // Step 8: WiFi credentials check
    const wifiSsid = document.getElementById('wifi-ssid').value.trim();
    const wifiPassword = document.getElementById('wifi-password').value;
    const wifiPasswordConfirm = document.getElementById('wifi-password-confirm').value;
    
    if (wifiSsid || wifiPassword || wifiPasswordConfirm) {
        if (!wifiSsid) {
            showInputError('wifi-ssid', "SSID is required when Wi-Fi passwords are provided.");
            hasErrors = true;
            if (!errTab) errTab = 'credentials-tab';
        }
        if (wifiPassword !== wifiPasswordConfirm) {
            showInputError('wifi-password-confirm', "Wi-Fi passwords do not match.");
            hasErrors = true;
            if (!errTab) errTab = 'credentials-tab';
        }
    }
    
    if (hasErrors) {
        if (errTab) {
            switchTab(errTab);
        }
        return;
    }
    
    // Get drive name for display in modal
    const selectedOption = driveSelect.options[driveSelect.selectedIndex];
    // MED-02 FIX: Use createElement + textContent instead of innerHTML to neutralise
    // maliciously-named USB drives (e.g. a drive named "<script>...").
    const modalDriveName = document.getElementById('modal-drive-name');
    modalDriveName.textContent = '';
    const prefix = document.createTextNode('Target Drive: ');
    const strong = document.createElement('strong');
    strong.textContent = selectedOption.textContent;
    modalDriveName.appendChild(prefix);
    modalDriveName.appendChild(strong);
    
    // Show step 10 modal
    document.getElementById('format-modal').style.display = 'flex';
}

function closeFormatModal() {
    document.getElementById('format-modal').style.display = 'none';
}

function closeSuccessModal() {
    document.getElementById('success-modal').style.display = 'none';
}

function ejectFlashedDrive() {
    if (lastFlashedDriveId === null) {
        alert("No drive selection available to eject.");
        return;
    }
    const ejectBtn = document.getElementById('eject-btn');
    if (ejectBtn) {
        ejectBtn.disabled = true;
        ejectBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Ejecting...';
    }
    
    if (window.pywebview && pywebview.api && pywebview.api.eject_drive) {
        pywebview.api.eject_drive(parseInt(lastFlashedDriveId))
            .then(res => {
                if (res && res.success) {
                    if (ejectBtn) {
                        ejectBtn.className = "btn btn-success";
                        ejectBtn.innerHTML = '<i class="fa-solid fa-check"></i> Ejected Successfully';
                    }
                    refreshDrives();
                } else {
                    alert("Error ejecting drive: " + (res.error || "Unknown error"));
                    if (ejectBtn) {
                        ejectBtn.disabled = false;
                        ejectBtn.innerHTML = '<i class="fa-solid fa-eject"></i> Eject SD Card';
                    }
                }
            })
            .catch(err => {
                alert("Failed to eject drive: " + err);
                if (ejectBtn) {
                    ejectBtn.disabled = false;
                    ejectBtn.innerHTML = '<i class="fa-solid fa-eject"></i> Eject SD Card';
                }
            });
    } else {
        alert("Eject is only supported on Windows Desktop mode.");
        if (ejectBtn) {
            ejectBtn.disabled = false;
            ejectBtn.innerHTML = '<i class="fa-solid fa-eject"></i> Eject SD Card';
        }
    }
}

function confirmAndFlash() {
    closeFormatModal();
    startFlashing();
}

function startFlashing() {
    const driveSelect = document.getElementById('drive-select');
    const driveId = driveSelect.value;
    lastFlashedDriveId = driveId;
    
    // Hardware, OS & Arch
    const piModel = document.getElementById('pi-model-select').value;
    const dashboardUi = document.getElementById('bootstrap-ui-select-imager').value;
    const osArch = document.getElementById('os-arch-select').value;
    
    const hostname = document.getElementById('hostname-input').value.trim();
    const timezone = document.getElementById('timezone-select').value;
    
    // Credentials
    const sshUsername = document.getElementById('ssh-username').value.trim() || 'kace';
    const sshPassword = document.getElementById('ssh-password').value;
    
    // WiFi setup
    const wifiSsid = document.getElementById('wifi-ssid').value;
    const wifiPassword = document.getElementById('wifi-password').value;
    
    // Services
    const sshEnabled = document.getElementById('ssh-enable').checked;
    const crowsnest = document.getElementById('crowsnest-enable').checked;
    // L4 FIX: Wire the 'Enable Password Authentication' checkbox to the backend.
    // Previously this checkbox was a dead UI control — the backend always wrote
    // password_authentication = true regardless of its state.
    const passwordAuth = document.getElementById('ssh-password-auth').checked;
    
    // Image configuration
    const imageSource = document.getElementById('image-source-select').value;
    let imagePath = "default_prebaked";
    if (imageSource === 'custom') {
        imagePath = document.getElementById('custom-image-path').value;
    } else if (imageSource === 'raspios_lite') {
        imagePath = "default_lite";
    }
    
    // Show progress elements
    const flashBtn = document.getElementById('flash-action-btn');
    const cancelBtn = document.getElementById('cancel-flash-btn');
    const statusContainer = document.getElementById('flash-status-container');
    if (statusContainer) statusContainer.style.display = 'flex';
    flashBtn.disabled = true;
    if (cancelBtn) {
        cancelBtn.style.display = 'block';
        cancelBtn.disabled = false;
        cancelBtn.innerHTML = '<i class="fa-solid fa-xmark"></i> Cancel';
    }
    
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
            crowsnest,
            sshUsername,
            passwordAuth
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
    
    const flashBtn = document.getElementById('flash-action-btn');
    
    switch(state) {
        case 'UNKNOWN':
            break;
        case 'FLASHING':
            const statusContainer = document.getElementById('flash-status-container');
            if (statusContainer) statusContainer.style.display = 'flex';
            flashBtn.disabled = true;
            updateProgress(progress, message);
            
            // Disable cancel once block writing starts (to prevent SD corruption)
            const cancelBtnFlashing = document.getElementById('cancel-flash-btn');
            if (cancelBtnFlashing && message && message.toLowerCase().startsWith('writing blocks')) {
                cancelBtnFlashing.style.display = 'none';
            }
            break;
        case 'FLASHED':
            flashBtn.disabled = false;
            updateProgress(100, message);
            
            // Hide progress container and reset progress fill
            const statusContainerDone = document.getElementById('flash-status-container');
            if (statusContainerDone) statusContainerDone.style.display = 'none';
            const progressFillDone = document.getElementById('btn-progress-fill');
            if (progressFillDone) progressFillDone.style.width = '0%';
            
            const ejectBtn = document.getElementById('eject-btn');
            if (ejectBtn) {
                ejectBtn.disabled = false;
                ejectBtn.className = "btn btn-secondary";
                ejectBtn.innerHTML = '<i class="fa-solid fa-eject"></i> Eject SD Card';
            }
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
            if (term) term.write(`\x1b[1;33m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'DISCOVERED':
            if (term) term.write(`\x1b[1;36m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'SSH_READY':
            updateConnectionStatus(true);
            if (term) term.write(`\x1b[1;32m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'BOOTSTRAPPED':
            updateConnectionStatus(true);
            if (term) term.write(`\x1b[1;32m[Status] ${message}\x1b[0m\r\n`);
            break;
        case 'ERROR':
            flashBtn.disabled = false;
            
            // Hide progress container and reset progress fill
            const statusContainerErr = document.getElementById('flash-status-container');
            if (statusContainerErr) statusContainerErr.style.display = 'none';
            const progressFillErr = document.getElementById('btn-progress-fill');
            if (progressFillErr) progressFillErr.style.width = '0%';
            
            // Reset cancel button state
            const cancelBtnErr = document.getElementById('cancel-flash-btn');
            if (cancelBtnErr) { cancelBtnErr.style.display = 'none'; cancelBtnErr.disabled = false; cancelBtnErr.innerHTML = '<i class="fa-solid fa-xmark"></i> Cancel'; }
            
            if (activeTab === 'imager-tab' || activeTab === 'credentials-tab') {
                updateProgress(0, `Error: ${message}`);
            } else if (activeTab === 'terminal-tab') {
                updateConnectionStatus(false);
                if (term) term.write(`\x1b[1;31m[Error] ${message}\x1b[0m\r\n`);
                showTroubleshootingPrompt(message);
            }
    }
};

function showTroubleshootingPrompt(errorMsg) {
    if (!term) return;
    term.write("\r\n\x1b[1;36m[KACE Diagnostics] Troubleshooting Guidance:\x1b[0m\r\n");
    term.write(" 1. \x1b[1;37mPower Check:\x1b[0m Verify the Raspberry Pi is powered on and ACT green LED is blinking.\r\n");
    term.write(" 2. \x1b[1;37mNetwork Subnet:\x1b[0m Ensure your PC and the Pi are connected to the same WiFi SSID / Subnet.\r\n");
    term.write(" 3. \x1b[1;37mWiFi Band:\x1b[0m Confirm you didn't connect a 2.4GHz-only Pi model (like Zero W or 3B) to a 5GHz band.\r\n");
    term.write(" 4. \x1b[1;37mPassword Check:\x1b[0m Double-check the SSH password for the user.\r\n");
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
    const fill = document.getElementById('btn-progress-fill');
    const textContent = document.getElementById('btn-text-content');
    const msg = document.getElementById('flasher-status-msg');
    
    if (fill) {
        fill.style.width = `${percent}%`;
    }
    if (textContent) {
        // MED-03 FIX: Coerce percent to integer before innerHTML interpolation to
        // ensure it cannot carry embedded HTML from an unexpected string value.
        const safePct = parseInt(percent, 10);
        if (safePct > 0 && safePct < 100) {
            textContent.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Writing... ${safePct}%`;
        } else {
            textContent.innerHTML = `<i class="fa-solid fa-fire"></i> Write`;
        }
    }
    if (msg) {
        msg.textContent = message;
    }
}


// Network Discovery & Scanning (Stage B)
function triggerScan() {
    const visual = document.getElementById('scanner-visual');
    const text = document.getElementById('scan-status-text');
    const list = document.getElementById('discovered-device-list');
    
    if (visual) visual.classList.add('scanning');
    text.textContent = "Probing local network subnet...";
    list.innerHTML = `
        <div class="list-empty">
            <i class="fa-solid fa-spinner fa-spin"></i> Subnet port scanning active...
        </div>
    `;
    
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.scan_network().then(devices => {
            if (visual) visual.classList.remove('scanning');
            // Handle rate-limit signal returned by the backend
            if (devices && !Array.isArray(devices) && devices.status === 'rate_limited') {
                const wait = Math.ceil(devices.wait_seconds || 10);
                text.textContent = `Scan rate-limited. Please wait ${wait}s before scanning again.`;
                return;
            }
            text.textContent = "Scan completed.";
            populateDevices(devices);
        }).catch(err => {
            console.error(err);
            if (visual) visual.classList.remove('scanning');
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
            if (visual) visual.classList.remove('scanning');
            text.textContent = "Scan complete (Debug Mock Mode).";
            populateDevices([
                { ip: "192.168.1.99", hostname: "kace.local", ssh: true, moonraker: false, klipper: false, crowsnest: false },
                { ip: "192.168.1.121", hostname: "mainsailos.local", ssh: true, moonraker: true, klipper: true, crowsnest: true }
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
        if (dev.klipper) {
            const tagKlip = document.createElement('span');
            tagKlip.className = 'tag tag-klipper';
            tagKlip.textContent = 'Klipper';
            deviceTags.appendChild(tagKlip);
        }
        if (dev.crowsnest) {
            const tagCrows = document.createElement('span');
            tagCrows.className = 'tag tag-crowsnest';
            tagCrows.textContent = 'Crowsnest';
            deviceTags.appendChild(tagCrows);
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
    // Client-side format validation (mirrors backend LOW-02 FIX regex).
    // Accepts dotted-decimal IPs, hostnames, and optional :PORT suffix.
    // Rejects empty strings and inputs containing dangerous characters.
    const IP_HOST_RE = /^[\w.\-]{1,253}(:\d{1,5})?$/;
    if (!IP_HOST_RE.test(ip)) {
        alert("Invalid IP address or hostname format. Only letters, digits, dots, hyphens, and an optional :PORT are allowed.");
        return;
    }

    // Resolve the button by its ID (added to index.html as part of BUG-01 fix)
    const btn = document.getElementById('manual-connect-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Probing...';
    }

    const list = document.getElementById('discovered-device-list');
    // MED-01 FIX: Replace innerHTML template interpolation of the user-typed IP string
    // with safe DOM node construction. A crafted value like "<img onerror=alert(1)>"
    // would previously execute inside the PyWebView context and could invoke privileged
    // backend APIs via window.pywebview.api.
    list.innerHTML = '';
    const probeDiv = document.createElement('div');
    probeDiv.className = 'list-empty';
    const probeIcon = document.createElement('i');
    probeIcon.className = 'fa-solid fa-spinner fa-spin';
    probeDiv.appendChild(probeIcon);
    probeDiv.appendChild(document.createTextNode(` Probing manual IP address ${ip}...`));
    list.appendChild(probeDiv);

    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.probe_device_ip(ip).then(device => {
            if (btn) { btn.disabled = false; btn.innerHTML = 'Connect to Target'; }
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
            if (btn) { btn.disabled = false; btn.innerHTML = 'Connect to Target'; }
            list.innerHTML = '';
            const emptyDiv = document.createElement('div');
            emptyDiv.className = 'list-empty';
            emptyDiv.textContent = "Manual probe error: " + err;
            list.appendChild(emptyDiv);
        });
    } else {
        setTimeout(() => {
            if (btn) { btn.disabled = false; btn.innerHTML = 'Connect to Target'; }
            populateDevices([{ ip: ip, hostname: "manual-node.local", ssh: true, moonraker: false, klipper: false, crowsnest: false }]);
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
            // H4 FIX: Capture the password in a local variable and immediately
            // clear the module-scope loginPassword before any async work begins.
            // This minimises the window in which the plaintext password exists
            // in a JS heap-reachable module-scope variable.
            const capturedPassword = currentLoginInput;
            loginPassword = '';        // clear module-scope var immediately
            currentLoginInput = '';    // clear input buffer immediately
            loginState = 'CONNECTING';
            term.write('\r\n[KACE Workspace] Establishing SSH connection...\r\n');
            performSshLogin(loginUsername, capturedPassword);
            // capturedPassword goes out of scope after this frame, making it
            // eligible for GC as soon as the engine collects the closure.
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
        // H4 FIX: Pass password directly to the API call without re-storing in any
        // wider-scope variable. Clear it from the parameter before the Promise resolves.
        window.pywebview.api.connect_ssh(currentDeviceIp, username, password, term.cols, term.rows).then(res => {
            // Clear the local password reference on resolution
            password = '';
            loginPassword = '';
            currentLoginInput = '';
            
            const isSuccess = (res === true || (res && res.status === 'success'));
            const isHostKeyMismatch = (res && res.status === 'host_key_mismatch');
            
            if (isSuccess) {
                connectedUsername = username;
                term.write("\x1b[1;32m[KACE Workspace] SSH connection established successfully.\x1b[0m\r\n");
                updateConnectionStatus(true);
                loginState = 'DISCONNECTED';
                
                // Synchronize terminal dimensions with the remote PTY
                if (window.pywebview && window.pywebview.api) {
                    window.pywebview.api.resize_ssh_pty(term.cols, term.rows);
                }
            } else if (isHostKeyMismatch) {
                term.write(`\r\n\x1b[1;33m[SECURITY ALERT] SSH Host Key Mismatch for ${currentDeviceIp}!\x1b[0m\r\n`);
                term.write("This is normal if you have recently re-flashed the SD card.\r\n");
                term.write("Do you want to clear the old stored key and trust the new one? (y/n): ");
                loginState = 'PROMPTING_HOST_KEY_MISMATCH';
                updateConnectionStatus(false);
            } else {
                const errMsg = (res && res.message) ? res.message : "SSH connection failed. Verify user credentials or network path.";
                term.write(`\r\n\x1b[1;31m[Error] ${errMsg}\x1b[0m\r\n`);
                updateConnectionStatus(false);
                promptTerminalLogin();
            }
        }).catch(err => {
            // Clear password on error path too
            password = '';
            loginPassword = '';
            currentLoginInput = '';
            term.write(`\r\n\x1b[1;31m[Error] Connection error: ${err}\x1b[0m\r\n`);
            updateConnectionStatus(false);
            promptTerminalLogin();
        });
    } else {
        // Mock connection
        setTimeout(() => {
            loginPassword = '';
            currentLoginInput = '';
            const expectedUser = document.getElementById('ssh-username').value.trim() || 'kace';
            const expectedPass = document.getElementById('ssh-password').value || 'kace';
            if (username === expectedUser && password === expectedPass) {
                connectedUsername = username;
                term.write("\x1b[1;32m[KACE Workspace] (DEBUG MOCK) SSH connection established.\x1b[0m\r\n");
                term.write(`${username}@${currentDeviceName || 'kace'}:~ $ `);
                updateConnectionStatus(true);
                loginState = 'DISCONNECTED';
                window.updateDeviceState("SSH_READY", 100, "Connected to mock node.");
            } else {
                term.write(`\r\n\x1b[1;31m[Error] (DEBUG MOCK) Login failed. Hint: use ${expectedUser}/${expectedPass}.\x1b[0m\r\n`);
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
    term.write(`\x1b[1;36m[KACE Workspace] Connecting to ${name} (${ip})...\x1b[0m\r\n`);
    
    promptTerminalLogin();
}


// Terminal Workspace (Stage C)
function initTerminal() {
    const container = document.getElementById('terminal-container');
    container.innerHTML = '';
    
    term = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: 'JetBrains Mono, Consolas, "Courier New", monospace',
        customGlyphs: true,
        theme: {
            background: '#000000',
            foreground: '#ffffff',
            cursor: '#5a52e5',
            magenta: '#818cf8',
            green: '#10b981',
            red: '#ef4444'
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

    // Listen for terminal resize events and forward them to the backend SSH session
    term.onResize(size => {
        if (sshConnected && window.pywebview && window.pywebview.api) {
            window.pywebview.api.resize_ssh_pty(size.cols, size.rows);
        }
    });
    
    // Intercept right-click context menu inside the terminal container
    container.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const contextMenu = document.getElementById('terminal-context-menu');
        if (contextMenu) {
            contextMenu.style.left = `${e.clientX}px`;
            contextMenu.style.top = `${e.clientY}px`;
            contextMenu.style.display = 'flex';
        }
    });
    
    // Add keydown listener to support standard keyboard copy & paste (Ctrl+C / Ctrl+V)
    container.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'v') {
            navigator.clipboard.readText()
                .then(text => {
                    if (text) {
                        handleTerminalPaste(text);
                    }
                })
                .catch(err => console.error('Failed to read from clipboard: ', err));
            e.preventDefault();
            e.stopPropagation();
        }
        else if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
            if (term && term.hasSelection()) {
                const selection = term.getSelection();
                navigator.clipboard.writeText(selection)
                    .then(() => console.log('Copied selection to clipboard'))
                    .catch(err => console.error('Failed to copy selection: ', err));
                e.preventDefault();
                e.stopPropagation();
            }
        }
    }, true); // useCapture = true to intercept before xterm.js handlers
    
    // Dismiss custom context menu when clicking anywhere else
    document.addEventListener('click', (e) => {
        const contextMenu = document.getElementById('terminal-context-menu');
        if (contextMenu) {
            contextMenu.style.display = 'none';
        }
    });
    
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
        } else if (loginState === 'PROMPTING_HOST_KEY_MISMATCH') {
            const char = data.toLowerCase();
            if (char === 'y') {
                term.write("y\r\n[KACE Workspace] Clearing stored host key...\r\n");
                loginState = 'CONNECTING';
                if (window.pywebview && window.pywebview.api) {
                    window.pywebview.api.clear_stored_host_key(currentDeviceIp).then(() => {
                        promptTerminalLogin();
                    });
                } else {
                    promptTerminalLogin();
                }
            } else if (char === 'n') {
                term.write("n\r\n[KACE Workspace] Connection aborted.\r\n");
                promptTerminalLogin();
            }
        } else if (!sshConnected) {
            // Echo locally if not connected in debug/mock
            term.write(data);
        }
    });
}

let bootstrapBuffer = "";

// Stage definitions — ordered for the progress bar.
// Each entry maps a STAGE_ID (emitted by bootstrap.sh as "=== STAGE: ID ===")
// to a human-readable label shown in the UI status area.
const BOOTSTRAP_STAGES = [
    { id: 'PACKAGES',   label: 'Updating system packages'      },
    { id: 'KLIPPER',      label: 'Installing Klipper'              },
    { id: 'KLIPPER_FIX',  label: 'Patching Klipper service paths'   },
    { id: 'MOONRAKER',    label: 'Installing Moonraker'              },
    { id: 'CONFIGS',    label: 'Writing printer configuration' },
    { id: 'MAINSAIL',   label: 'Installing Mainsail'           },
    { id: 'FLUIDD',     label: 'Installing Fluidd'             },
    { id: 'CLIENT_CFG', label: 'Downloading UI client config'  },
    { id: 'NGINX',      label: 'Configuring Nginx'             },
    { id: 'SERVICES',   label: 'Starting services'             },
    { id: 'CROWSNEST',  label: 'Installing Crowsnest'          },
    { id: 'KACE',       label: 'Installing KACE agent'         },
];

let lastBootstrapStageIdx = -1;

function setBootstrapStage(stageId) {
    const idx = BOOTSTRAP_STAGES.findIndex(s => s.id === stageId);
    if (idx === -1 || idx <= lastBootstrapStageIdx) return;
    lastBootstrapStageIdx = idx;

    const label = document.getElementById('bootstrap-stage-label');
    if (label) {
        label.textContent = BOOTSTRAP_STAGES[idx].label + '...';
    }

    // Mark all previous stages as completed, current as active
    BOOTSTRAP_STAGES.forEach((stage, i) => {
        const el = document.getElementById('bstage-' + stage.id);
        if (!el) return;
        el.classList.remove('active', 'completed', 'done');
        if (i < idx) {
            el.classList.add('done');
        } else if (i === idx) {
            el.classList.add('active');
        }
    });

    // Also update the old connection-subtitle for backwards compatibility
    const connSubtitle = document.getElementById('connection-subtitle');
    if (connSubtitle) {
        connSubtitle.innerHTML = `<i class="fa-solid fa-spinner fa-spin" style="color:var(--primary-color)"></i> <span style="color:var(--primary-color);font-weight:600"> ${BOOTSTRAP_STAGES[idx].label}...</span>`;
    }
}

function parseBootstrapProgress(data) {
    bootstrapBuffer += data;
    if (bootstrapBuffer.length > 8000) {
        bootstrapBuffer = bootstrapBuffer.slice(-3000);
    }

    // Scan for === STAGE: ID === markers emitted by bootstrap.sh
    const markerRe = /=== STAGE: ([A-Z_]+) ===/g;
    let match;
    while ((match = markerRe.exec(bootstrapBuffer)) !== null) {
        setBootstrapStage(match[1]);
    }

    // Detect completion banner
    if (bootstrapBuffer.includes('Bootstrap complete! KACE Node is fully ready.')) {
        // Mark all stages done
        BOOTSTRAP_STAGES.forEach(stage => {
            const el = document.getElementById('bstage-' + stage.id);
            if (el) { el.classList.remove('active'); el.classList.add('done'); }
        });
        const label = document.getElementById('bootstrap-stage-label');
        if (label) label.textContent = '✔ Bootstrap complete!';
        const connSubtitle = document.getElementById('connection-subtitle');
        if (connSubtitle) {
            connSubtitle.innerHTML = '<i class="fa-solid fa-circle-check" style="color:var(--success-color)"></i> <span style="color:var(--success-color);font-weight:600"> Bootstrap complete! KACE Node is fully ready.</span>';
        }
        updateTrackerBar('BOOTSTRAPPED');
        
        const finishBtn = document.getElementById('finish-btn');
        if (finishBtn) {
            finishBtn.disabled = false;
            finishBtn.classList.add('active');
        }

        // Hide the progress tracker and resize terminal to the full height
        // This ensures the remote interactive TUI (like KACE logo/menus) sees a correct, standard-sized PTY
        setTimeout(() => {
            const tracker = document.getElementById('bootstrap-progress-tracker');
            if (tracker && tracker.style.display !== 'none') {
                tracker.style.display = 'none';
                setTimeout(() => {
                    if (fitAddon) {
                        fitAddon.fit();
                    }
                }, 50);
            }
        }, 1500);
    }
}

// Push data from Python SSH output stream into xterm terminal
window.writeTerminalData = function(data) {
    if (term) {
        term.write(data);
    }
    parseBootstrapProgress(data);
};

function updateConnectionStatus(connected) {
    sshConnected = connected;
    const bootstrapBtn = document.getElementById('bootstrap-btn');
    const disconnectBtn = document.getElementById('disconnect-btn');
    const connTitle = document.getElementById('connection-title');
    const connSubtitle = document.getElementById('connection-subtitle');
    
    if (connected) {
        bootstrapBtn.disabled = false;
        disconnectBtn.style.display = 'block';
        connTitle.textContent = `SSH Workspace — Connected`;
        connSubtitle.textContent = `Active session: ${connectedUsername}@${currentDeviceName} (${currentDeviceIp})`;
    } else {
        bootstrapBtn.disabled = true;
        disconnectBtn.style.display = 'none';
        connTitle.textContent = `SSH Session: Disconnected`;
        connSubtitle.textContent = `No active session. Select a device in the Discovery tab to connect.`;
        
        // Hide the progress tracker and resize terminal
        const tracker = document.getElementById('bootstrap-progress-tracker');
        if (tracker && tracker.style.display !== 'none') {
            tracker.style.display = 'none';
            setTimeout(() => {
                if (fitAddon) fitAddon.fit();
            }, 50);
        }
    }
    
    // Refresh SFTP Panel status
    refreshSftpBrowser();
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
    
    // Strict allowlist validation to prevent remote SSH command injection
    const validUis = ['mainsail', 'fluidd', 'both'];
    if (!validUis.includes(selectedUi)) {
        term.write(`\r\n\x1b[1;31m[Error] Invalid dashboard UI selection: ${selectedUi}\x1b[0m\r\n`);
        return;
    }
    
    // Show the stage progress tracker and reset state
    const tracker = document.getElementById('bootstrap-progress-tracker');
    if (tracker) {
        tracker.style.display = 'block';
        setTimeout(() => {
            if (fitAddon) fitAddon.fit();
        }, 50);
    }
    lastBootstrapStageIdx = -1;
    BOOTSTRAP_STAGES.forEach(stage => {
        const el = document.getElementById('bstage-' + stage.id);
        if (el) el.classList.remove('active', 'done');
    });
    const stageLabel = document.getElementById('bootstrap-stage-label');
    if (stageLabel) stageLabel.textContent = 'Starting bootstrap...';

    term.write(`\r\n\x1b[1;36m[KACE Workspace] Starting KACE bootstrap execution [UI selection: ${selectedUi}]... \x1b[0m\r\n`);
    const bootstrapCmd = `if [ -f /boot/firmware/bootstrap.sh ]; then bash /boot/firmware/bootstrap.sh --dashboard ${selectedUi}; elif [ -f /boot/bootstrap.sh ]; then bash /boot/bootstrap.sh --dashboard ${selectedUi}; else echo "ERROR: bootstrap.sh not found on boot partition. Please re-flash the SD card."; exit 1; fi\n`;
    // H3 FIX: The previous fallback included 'curl | bash' from a remote GitHub URL.
    // This has been removed: if the bootstrap script is not found on the SD card,
    // the command now exits with an error instead of silently fetching and executing
    // arbitrary code from the internet over the authenticated SSH session.
    
    // Reset buffer at start
    bootstrapBuffer = "";
    
    if (window.pywebview && window.pywebview.api) {
        // Send the shell command to execute the bootstrap
        window.pywebview.api.send_ssh_input(bootstrapCmd);
    } else {
        // Mock terminal output — stage markers match bootstrap.sh log_stage() output
        const mockSteps = [
            '=== STAGE: PACKAGES ===',
            '=== STAGE: KLIPPER ===',
            '=== STAGE: MOONRAKER ===',
            '=== STAGE: CONFIGS ===',
            `=== STAGE: ${selectedUi === 'fluidd' ? 'FLUIDD' : 'MAINSAIL'} ===`,
            '=== STAGE: CLIENT_CFG ===',
            '=== STAGE: NGINX ===',
            '=== STAGE: SERVICES ===',
            ...(selectedUi === 'both' || Math.random() > 0.5 ? ['=== STAGE: CROWSNEST ==='] : []),
            '=== STAGE: KACE ===',
            'Bootstrap complete! KACE Node is fully ready.',
        ];
        
        term.write(`$ ${bootstrapCmd}`);
        let idx = 0;
        const interval = setInterval(() => {
            if (idx < mockSteps.length) {
                const stepText = mockSteps[idx];
                term.write(`\r\n${stepText}\r\n`);
                parseBootstrapProgress(stepText);
                idx++;
            } else {
                clearInterval(interval);
                term.write(`\r\n${connectedUsername}@${currentDeviceName || 'kace'}:~ $ `);
            }
        }, 1500);
    }
}

// Synchronizes the visual state of a custom dropdown based on its hidden input's value
function syncCustomDropdown(container) {
    const hiddenInput = container.querySelector('input[type="hidden"]');
    if (!hiddenInput) return;
    const val = hiddenInput.value;
    const options = container.querySelectorAll('.custom-option');
    const trigger = container.querySelector('.custom-select-trigger');
    if (!trigger) return;
    
    let selectedOption = null;
    options.forEach(option => {
        if (option.getAttribute('data-value') === val) {
            selectedOption = option;
            option.classList.add('selected');
        } else {
            option.classList.remove('selected');
        }
    });
    
    if (selectedOption) {
        const optImg = selectedOption.querySelector('img');
        const optGroup = selectedOption.querySelector('.logo-group');
        const optTitle = selectedOption.querySelector('.option-title').textContent;
        const optDesc = selectedOption.querySelector('.option-desc').textContent;
        
        const triggerImg = trigger.querySelector('.trigger-icon');
        const triggerGroup = trigger.querySelector('.logo-group');
        const triggerTitle = trigger.querySelector('.trigger-title');
        const triggerDesc = trigger.querySelector('.trigger-desc');
        
        if (triggerTitle) triggerTitle.textContent = optTitle;
        if (triggerDesc) triggerDesc.textContent = optDesc;
        
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
    const isCurrentlyLight = document.body.classList.contains('light-mode');
    const newTheme = isCurrentlyLight ? 'dark' : 'light';
    applyTheme(newTheme);
    
    // Save state to python preferences and update localStorage cache
    userPreferences.theme = newTheme;
    localStorage.setItem('theme', newTheme);
    
    if (window.pywebview && pywebview.api && pywebview.api.set_preferences) {
        pywebview.api.set_preferences(userPreferences);
    }
}

// ── Form Persistence (localStorage + Python preferences) ──────────────────

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
    { id: 'ssh-username', type: 'value' },
];

function saveFormState() {
    const state = {};
    PERSISTED_FIELDS.forEach(field => {
        const el = document.getElementById(field.id);
        if (el) {
            state[field.id] = field.type === 'checked' ? el.checked : el.value;
        }
    });
    
    userPreferences.form_state = state;
    
    try {
        localStorage.setItem(FORM_PERSIST_KEY, JSON.stringify(state));
    } catch (e) {
        console.warn('Failed to persist form state to localStorage:', e);
    }
    
    if (window.pywebview && pywebview.api && pywebview.api.set_preferences) {
        pywebview.api.set_preferences(userPreferences);
    }
}

function restoreFormState() {
    try {
        let state = userPreferences.form_state;
        if (!state || Object.keys(state).length === 0) {
            const raw = localStorage.getItem(FORM_PERSIST_KEY);
            if (raw) {
                state = JSON.parse(raw);
            }
        }
        if (!state) return;
        
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
        
        // Sync custom dropdowns visual states
        document.querySelectorAll('.custom-select-container').forEach(container => {
            syncCustomDropdown(container);
        });
        
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

// ── SFTP File Browser & Context Menu Logic ─────────────────────────────────

let sftpCurrentPath = "/home/kace";
let sftpSelectedFile = null;

function clearSftpSelection() {
    sftpSelectedFile = null;
    const dlBtn = document.getElementById('sftp-download-btn');
    if (dlBtn) {
        dlBtn.disabled = true;
    }
    document.querySelectorAll('.sftp-item.file').forEach(el => {
        el.classList.remove('selected');
    });
}

window.refreshSftpBrowser = function() {
    const panel = document.getElementById('sftp-browser-panel');
    if (!panel) return;
    
    if (sshConnected && activeTab === 'terminal-tab') {
        panel.style.display = 'flex';
        loadSftpDirectory(sftpCurrentPath);
    } else {
        panel.style.display = 'none';
        clearSftpSelection();
        sftpCurrentPath = "/home/kace";
        const listContainer = document.getElementById('sftp-file-list');
        if (listContainer) listContainer.innerHTML = '';
    }
};

window.loadSftpDirectory = function(path) {
    sftpCurrentPath = path;
    const pathInput = document.getElementById('sftp-current-path');
    if (pathInput) pathInput.value = sftpCurrentPath;
    
    fetch(`/api/sftp/list?path=${encodeURIComponent(path)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            renderSftpList(data.items);
        })
        .catch(err => {
            console.error("Failed to load SFTP directory:", err);
            const listContainer = document.getElementById('sftp-file-list');
            if (listContainer) {
                listContainer.innerHTML = `
                    <div class="list-empty">
                        <i class="fa-solid fa-triangle-exclamation"></i> Error loading directory.
                    </div>
                `;
            }
        });
};

function renderSftpList(items) {
    const listContainer = document.getElementById('sftp-file-list');
    if (!listContainer) return;
    
    listContainer.innerHTML = '';
    
    if (!items || items.length === 0) {
        listContainer.innerHTML = `
            <div class="list-empty">
                <i class="fa-solid fa-folder-open"></i> This directory is empty.
            </div>
        `;
        return;
    }
    
    items.sort((a, b) => {
        if (a.is_dir && !b.is_dir) return -1;
        if (!a.is_dir && b.is_dir) return 1;
        return a.name.localeCompare(b.name);
    });
    
    items.forEach(item => {
        const itemEl = document.createElement('div');
        itemEl.className = `sftp-item ${item.is_dir ? 'folder' : 'file'}`;

        if (!item.is_dir && sftpSelectedFile === item.name) {
            itemEl.classList.add('selected');
        }

        const iconClass = item.is_dir
            ? 'fa-solid fa-folder'
            : (item.name.endsWith('.cfg') || item.name.endsWith('.conf') ? 'fa-solid fa-file-code' : 'fa-solid fa-file');

        // L7 FIX: Build the item DOM via createElement + textContent instead of innerHTML
        // string interpolation. The filename (item.name) comes from the remote SFTP server
        // and must be treated as untrusted. innerHTML interpolation would allow a filename
        // containing HTML/script tags to execute in the webview context (stored XSS).
        const leftDiv = document.createElement('div');
        leftDiv.className = 'sftp-item-left';
        const icon = document.createElement('i');
        icon.className = iconClass;
        const nameSpan = document.createElement('span');
        nameSpan.className = 'sftp-item-name';
        nameSpan.textContent = item.name; // safe — textContent never executes markup
        leftDiv.appendChild(icon);
        leftDiv.appendChild(nameSpan);
        itemEl.appendChild(leftDiv);

        if (item.is_dir) {
            itemEl.addEventListener('click', () => {
                navigateSftpInto(item.name);
            });
        } else {
            itemEl.addEventListener('click', (e) => {
                if (sftpSelectedFile === item.name) {
                    clearSftpSelection();
                } else {
                    document.querySelectorAll('.sftp-item.file').forEach(el => {
                        el.classList.remove('selected');
                    });
                    itemEl.classList.add('selected');
                    sftpSelectedFile = item.name;
                    const dlBtn = document.getElementById('sftp-download-btn');
                    if (dlBtn) dlBtn.disabled = false;
                }
            });
        }
        listContainer.appendChild(itemEl);
    });
}

// Real implementation using POST and triggers browser file download using blob URL
window.downloadSelectedSftpFile = function() {
    if (sftpSelectedFile) {
        downloadSftpFile(sftpSelectedFile);
    }
};

window.downloadSftpFile = function(fileName) {
    const fullPath = sftpCurrentPath === "/" ? "/" + fileName : sftpCurrentPath + "/" + fileName;
    console.log(`SFTP Native Download requested for file: ${fullPath}`);
    
    const dlBtn = document.getElementById('sftp-download-btn');
    if (dlBtn) dlBtn.disabled = true;
    
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.download_file(fullPath)
            .then(success => {
                if (success) {
                    console.log(`Successfully downloaded ${fileName} natively.`);
                } else {
                    console.log(`SFTP download cancelled or failed for ${fileName}.`);
                }
            })
            .catch(err => {
                console.error("Native SFTP Download error:", err);
                alert(`Error downloading file: ${err.message || err}`);
            })
            .finally(() => {
                if (dlBtn && sftpSelectedFile) {
                    dlBtn.disabled = false;
                }
            });
    } else {
        console.warn("Pywebview API not available for native download.");
        if (dlBtn && sftpSelectedFile) {
            dlBtn.disabled = false;
        }
    }
};

window.navigateSftpInto = function(folderName) {
    clearSftpSelection();
    if (sftpCurrentPath === "/") {
        sftpCurrentPath = "/" + folderName;
    } else {
        sftpCurrentPath = sftpCurrentPath + "/" + folderName;
    }
    loadSftpDirectory(sftpCurrentPath);
};

window.navigateSftpUp = function() {
    clearSftpSelection();
    if (sftpCurrentPath === "/") return;
    
    const parts = sftpCurrentPath.split('/');
    parts.pop();
    let parentPath = parts.join('/');
    if (parentPath === "") {
        parentPath = "/";
    }
    sftpCurrentPath = parentPath;
    loadSftpDirectory(sftpCurrentPath);
};

window.terminalContextMenuAction = function(action) {
    const contextMenu = document.getElementById('terminal-context-menu');
    if (contextMenu) contextMenu.style.display = 'none';
    
    if (action === 'copy') {
        if (term) {
            const selection = term.getSelection();
            if (selection) {
                navigator.clipboard.writeText(selection)
                    .then(() => console.log('Copied selection to clipboard'))
                    .catch(err => console.error('Failed to copy selection: ', err));
            }
        }
    } else if (action === 'paste') {
        navigator.clipboard.readText()
            .then(text => {
                if (text) {
                    handleTerminalPaste(text);
                }
            })
            .catch(err => console.error('Failed to read from clipboard: ', err));
    } else if (action === 'clear') {
        if (term) term.clear();
    }
};

function handleTerminalPaste(text) {
    if (sshConnected && window.pywebview && window.pywebview.api) {
        window.pywebview.api.send_ssh_input(text);
    } else if (loginState === 'PROMPTING_USER' || loginState === 'PROMPTING_PASS') {
        for (let i = 0; i < text.length; i++) {
            handleLoginInput(text[i]);
        }
    } else if (!sshConnected) {
        term.write(text);
    }
}

function finishSetup() {
    window.location.reload();
}
