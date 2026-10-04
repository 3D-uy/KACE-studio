const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map(), scans = [], intervals = new Map();
let clock = 100000, timerId = 0;
function node() {
    return {style: {}, dataset: {}, children: [], textContent: '', value: '', hidden: false,
        disabled: false, checked: false, open: false, innerHTML: '',
        classList: {add() {}, remove() {}, toggle() {}},
        appendChild(child) {this.children.push(child);}, addEventListener() {},
        setAttribute() {}, focus() {}};
}
const context = {console, assert, scans, nodes, intervals, node, addEventListener() {},
    document: {addEventListener() {}, querySelectorAll() {return [];}, createElement: node,
        getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);}},
    navigator: {language: process.argv[3] || 'es'},
    setInterval(fn) {const id=++timerId; intervals.set(id,fn); return id;},
    clearInterval(id) {intervals.delete(id);}, setTimeout() {}, clearTimeout() {},
    advance(ms) {clock+=ms; [...intervals.values()].forEach(fn=>fn());},
    Date: {now:()=>clock},
    pywebview: {api: {scan_network:()=>new Promise((resolve,reject)=>scans.push({resolve,reject}))}},
};
context.window=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
vm.runInContext(`(async()=>{
    const flush=async()=>{for(let i=0;i<12;i++) await Promise.resolve();};
    const a={ip:'192.0.2.1',hostname:'fixture-one',ssh:true};
    const b={ip:'192.0.2.2',hostname:'fixture-two',ssh:true};
    startFirstBootDiscovery();
    assert.equal(scans.length,1);
    assert.equal(document.getElementById('scan-now-btn').disabled,true);
    advance(20000); assert.equal(scans.length,1); // Serialized while pending.
    scans[0].resolve([a,a]); await flush();
    assert.equal(firstBootDiscovery.paused,true);
    assert.equal(firstBootDiscovery.devices.size,1);
    assert.equal(document.getElementById('continue-discovery-btn').hidden,false);
    advance(30000); assert.equal(scans.length,1); // Paused on a result.
    continueDiscovery(); assert.equal(scans.length,2);
    scans[1].resolve([a]); await flush();
    assert.equal(firstBootDiscovery.paused,false); // Known device does not pause again.
    advance(16000); assert.equal(scans.length,3);
    scans[2].resolve([a,b]); await flush();
    assert.equal(firstBootDiscovery.paused,true);
    assert.equal(firstBootDiscovery.devices.size,2);
    stopFirstBootDiscovery();
    assert.equal(firstBootDiscovery,null);
    assert.equal(document.getElementById('first-boot-scan-progress').textContent,'');
    triggerScan();
    stopFirstBootDiscovery();
    const stopped=document.getElementById('scan-status-text').textContent;
    assert.equal(discoveryScanTimer,null);
    assert.equal(discoveryScanInFlight,true);
    assert.equal(document.getElementById('discovered-device-list').innerHTML,'');
    triggerScan(); assert.equal(scans.length,4); // Cannot overlap cancelled pending work.
    scans[3].resolve([a]); await flush();
    assert.equal(document.getElementById('scan-status-text').textContent,stopped);
    assert.equal(document.getElementById('scan-now-btn').disabled,false);
    triggerScan(); scans[4].resolve([a]); await flush();
    assert.equal(document.getElementById('continue-discovery-btn').hidden,false);
    continueDiscovery(); scans[5].resolve([a]); await flush();
    assert.equal(firstBootDiscovery.paused,false); // Continue also works after manual scan.
    advance(600000); assert.equal(firstBootDiscovery,null);
    assert.equal(scans.length,6); // Deadline cannot trigger another probe.
    triggerScan(); scans[6].resolve({status:'rate_limited',wait_seconds:8}); await flush();
    assert(document.getElementById('scan-status-text').textContent.includes('8'));
    triggerScan(); scans[7].reject(new Error('simulated')); await flush();
    assert.equal(document.getElementById('scan-status-text').textContent,studioText('scanFailed'));
    const relay=document.getElementById('power-relay-enable');
    const settings=document.getElementById('power-relay-settings');
    relay.checked=true; togglePowerRelaySettings();
    assert.equal(settings.hidden,false); assert.equal(settings.open,true);
    relay.checked=false; togglePowerRelaySettings();
    assert.equal(settings.hidden,true); assert.equal(settings.open,false);
    relay.checked=true; togglePowerRelaySettings(); assert.equal(settings.open,true);
    for (const lang of Object.keys(STUDIO_INSTALLATION_TEXT)) {
        assert.deepEqual(Object.keys(STUDIO_INSTALLATION_TEXT[lang]).sort(),Object.keys(STUDIO_INSTALLATION_TEXT.English).sort());
    }
    console.log('Discovery and relay controls completed');
})().catch(error=>{console.error(error); throw error;});`,context);
