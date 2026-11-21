// This scenario is evaluated in the existing frontend harness with the actual
// source or bundled app.js. The API only records requests and never touches disks.
const el = id => document.getElementById(id);
const A = {id:3, number:3, name:'Identical reader', size:'16 GB', size_bytes:16000000000,
    serial_number:'A', path:'A', unique_id:'A', high_risk:false};
const B = {...A, id:4, number:4, serial_number:'B', path:'B', unique_id:'B'};
const drives = el('drive-select');
drives.options = [];
Object.defineProperty(drives, 'innerHTML', {set() {this.options=[]; this.value='';}});
Object.defineProperty(drives, 'selectedIndex', {get() {
    return this.options.findIndex(option => String(option.value) === this.value);
}});
drives.appendChild = option => {
    drives.options.push(option);
    if (drives.options.length === 1) drives.value = String(option.value);
};
document.createTextNode = textContent => ({textContent});
for (const id of ['format-modal','success-modal']) {
    el(id).showModal = function() {this.open=true;};
    el(id).close = function() {this.open=false;};
}
const errors = [], writes = [], ejections = [];
showInputError = (id, message) => errors.push([id,message]);
updateInputErrorSummary = () => {};
startFirstBootDiscovery = () => {};
playFlashCompletionSound = () => {};
showFlashNotification = () => {};
activeTab = 'imager-tab';
let sequence = 1;
crypto = {randomUUID:() => String(sequence++).padStart(32,'0')};
requests.drives = [];
pywebview.api.get_drives = () => deferred('drives',[]);
pywebview.api.start_flash = (...args) => { writes.push(args); return Promise.resolve(true); };
pywebview.api.eject_drive = (...args) => { ejections.push(args); return Promise.resolve({success:false}); };
alert = () => {};
for (const [id,value] of Object.entries({'hostname-input':'kace','ssh-username':'kace',
    'ssh-password':'user-test','ssh-password-confirm':'user-test','wifi-security-select':'open'})) {
    el(id).value=value;
}
// A delayed inventory cannot replace the latest inventory after a review starts.
refreshDrives(); refreshDrives();
requests.drives[1].resolve([B,A]); await tick();
drives.value='3'; openFormatModal();
assert.match(el('modal-drive-name').children.at(-1).textContent, /3/);
requests.drives[0].resolve([B]); await tick();
confirmAndFlash(); await tick();
assert.equal(writes.length,1);
assert.equal(writes[0][0],3);
assert.equal(writes[0][14].serial_number,'A');
const request = window.activeFlashOperation;
drives.value='4';
window.updateDeviceState('FLASHED',100,'done',{...request,schema:'kace-studio-readback/v1',status:'verified'});
assert.equal(el('success-modal').open,true);
assert.equal(el('flash-action-btn').disabled,false);
ejectFlashedDrive(); await tick();
assert.deepEqual(ejections,[[3,request.operation_id]]);
// A replacement with the same number, model and capacity invalidates approval.
drives.value='3'; openFormatModal();
refreshDrives(); requests.drives.at(-1).resolve([{...B,id:3,number:3}]); await tick();
confirmAndFlash(); await tick();
assert.equal(writes.length,1);
assert.equal(errors.at(-1)[0],'drive-select');
// The registered operation still owns its diagnostic after the selector changes.
drives.value='3'; openFormatModal(); confirmAndFlash(); await tick();
const next=window.activeFlashOperation;
assert.equal(next.disk_identity.serial_number,'B');
drives.value='4';
window.updateDeviceState('ERROR',95,'Injection failed',next);
assert.match(el('flash-error').textContent,/Injection failed/);
assert.equal(el('flash-action-btn').disabled,false);
window.updateDeviceState('FLASHED',100,'stale',{...request,schema:'kace-studio-readback/v1',status:'verified'});
assert.equal(currentDeviceState,'ERROR');
// Architecture consent is specific to the custom image and Pi reviewed in the modal.
el('image-source-select').value='custom';
el('custom-image-path').value='first.img';
el('custom-image-type').value='custom_vanilla';
el('os-arch-select').value='32bit';
el('pi-model-select').value='pizerow';
drives.value='3'; openFormatModal();
assert.equal(el('custom-arch-ack').checked,false);
el('custom-arch-ack').checked=true;
el('custom-image-path').value='second.img';
confirmAndFlash(); await tick();
assert.equal(writes.length,2);
assert.equal(errors.at(-1)[0],'custom-image-path');
openFormatModal(); el('custom-arch-ack').checked=true; confirmAndFlash(); await tick();
assert.equal(writes.at(-1)[1],'second.img');
assert.equal(writes.at(-1)[25],true);
for (const language of ['English','Español','Português']) {
    selectedStudioLanguage=language;
    assert.ok(driveOptionText({...A,high_risk:true}).includes(studioText('driveRisk')));
    const messages = [];
    alert = value => messages.push(value);
    el('manual-ip').value='';
    connectManually();
    assert.equal(messages.pop(),studioText('ipRequired'));
    el('manual-ip').value='invalid host/?';
    connectManually();
    assert.equal(messages.pop(),studioText('ipInvalid'));
    pywebview.api.probe_device_ip=() => Promise.resolve(null);
    el('manual-ip').value='192.0.2.15';
    connectManually(); await tick();
    assert.equal(messages.pop(),studioText('ipProbeFailed'));
    assert.ok(el('discovered-device-list').innerHTML.includes(studioText('ipUnresponsive')));
}
