"""Run the production SFTP UI against delayed responses, without a browser."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_out_of_order_directory_and_disconnect_responses_cannot_change_selection():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend execution")
    app = (Path(__file__).resolve().parents[1] / "web/app.js").read_text(encoding="utf-8")
    source = app[app.index('let sftpCurrentPath ='):app.index('function renderSftpList')]
    source += app[app.index('window.downloadSftpFile ='):app.index('window.navigateSftpInto =')]
    harness = r'''
const assert = require('node:assert/strict');
const window = globalThis;
let sshConnected = true, activeTab = 'terminal-tab';
const nodes = new Map();
const document = {querySelectorAll() {return [];}, getElementById(id) {
  if (!nodes.has(id)) nodes.set(id, {style:{}, value:'', innerHTML:'', disabled:false});
  return nodes.get(id);
}};
const requests = [], rendered = [], downloads = [];
function fetch(url) {return new Promise(resolve => requests.push({url, resolve}));}
function renderSftpList(items) {rendered.push(items);}
window.pywebview = {token:'token', api:{download_file(...args) {downloads.push(args); return Promise.resolve(true);}}};
const tick = async () => {for (let n=0;n<12;n++) await Promise.resolve();};
function response(path, name, generation) {return {ok:true,json:async()=>({path,items:[name],generation})};}
'''
    scenario = r'''
(async () => {
  loadSftpDirectory('/old');
  loadSftpDirectory('/new');
  requests[1].resolve(response('/new','new.cfg',3)); await tick();
  requests[0].resolve(response('/old','old.cfg',3)); await tick();
  assert.deepEqual(rendered,[['new.cfg']]);
  assert.equal(sftpCurrentPath,'/new');
  downloadSftpFile('new.cfg'); await tick();
  assert.deepEqual(downloads,[['/new/new.cfg',3]]);
  loadSftpDirectory('/delayed');
  downloadSftpFile('stale.cfg');
  assert.equal(downloads.length,1);
  sshConnected=false; refreshSftpBrowser();
  requests[2].resolve(response('/delayed','wrong-host.cfg',3)); await tick();
  assert.deepEqual(rendered,[['new.cfg']]);
  assert.equal(sftpGeneration,null);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness + source + scenario], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
