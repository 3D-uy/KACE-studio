// Used only by the offline Chromium fixture. No API can access a device.
window.addEventListener('load', async () => {
    const el = id => document.getElementById(id);
    const check = (condition, message) => { if (!condition) throw Error(message); };
    const tick = async () => { await Promise.resolve(); await Promise.resolve(); };
    try {
        const a = {id:3, number:3, name:'Same model', size:'16 GB', size_bytes:16000000000,
            serial_number:'serial-A', path:'A', unique_id:'A', high_risk:false};
        const b = {...a, id:4, number:4, serial_number:'serial-B', path:'B', unique_id:'B'};
        const inventories = [], writes = [];
        window.pywebview = {api:{
            get_drives:() => new Promise(resolve => inventories.push(resolve)),
            start_flash:(...args) => {writes.push(args); return Promise.resolve(true);},
        }};
        startFirstBootDiscovery = () => {};
        playFlashCompletionSound = () => {};
        showFlashNotification = () => {};
        for (const [id, value] of Object.entries({'hostname-input':'kace','ssh-username':'kace',
            'ssh-password':'test-only','ssh-password-confirm':'test-only','wifi-security-select':'open'})) {
            el(id).value=value;
        }
        refreshDrives(); refreshDrives();
        inventories[1]([b,a]); await tick(); el('drive-select').value='3';
        el('flash-action-btn').click();
        check(el('format-modal').open && el('modal-drive-name').textContent.includes('serial-A'), 'modal identity');
        inventories[0]([b]); await tick();
        el('modal-agree-btn').click(); await tick();
        check(writes.length === 1 && writes[0][14].serial_number === 'serial-A','unexpected writer target');
        const active = window.activeFlashOperation;
        el('drive-select').value='4';
        window.updateDeviceState('FLASHED',100,'mock',{...active,schema:'kace-studio-readback/v1',status:'verified'});
        check(el('success-modal').open && !el('flash-action-btn').disabled, 'lost completion');
        closeSuccessModal(); el('drive-select').value='3'; el('flash-action-btn').click();
        refreshDrives(); inventories.at(-1)([{...b,id:3,number:3}]); await tick();
        el('modal-agree-btn').click(); await tick();
        check(writes.length === 1 && !el('format-modal').open, 'replacement accepted');
        el('flash-action-btn').click(); el('modal-agree-btn').click(); await tick();
        window.updateDeviceState('ERROR',95,'mock injection failed',window.activeFlashOperation);
        check(el('flash-error').getClientRects().length && el('flash-error').textContent.includes('mock injection failed'),
            'injection error hidden');
        document.body.dataset.kaceMediaTest = 'passed';
    } catch (error) {
        document.body.dataset.kaceMediaTest = String(error);
    }
});
