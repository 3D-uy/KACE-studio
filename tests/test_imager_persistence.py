"""Exercise production form restoration with native-select value semantics."""
from pathlib import Path
import json
import re
import shutil
import subprocess

import pytest

APP = (Path(__file__).resolve().parents[1] / "web/app.js").read_text(encoding="utf8")
RESTORE = re.search(r"function restoreFormState\(\) \{[\s\S]*?^}", APP, re.M)[0]


@pytest.mark.parametrize("saved,expected", [
    ({"timezone-select": "America/Buenos_Aires"}, {"timezone-select": "America/Buenos_Aires", "pi-model-select": "pi4"}),
    ({"pi-model-select": "removed-option"}, {"timezone-select": "UTC", "pi-model-select": "pi4"}),
    ({"pi-model-select": "pi3"}, {"timezone-select": "UTC", "pi-model-select": "pi3"}),
])
def test_saved_timezone_and_model_remain_visible(saved, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")
    harness = r'''
const assert = require('node:assert/strict');
class Option {constructor(text, value) {this.textContent=text;this.value=value;}}
class Select {
    constructor(values, initial) {this.tagName='SELECT';this.options=values.map(v=>new Option(v,v));this.value=initial;this.events=[];}
    get value() {return this.selected;}
    set value(value) {this.selected=this.options.some(o=>o.value===value)?value:'';}
    add(option) {this.options.push(option);}
    dispatchEvent(event) {this.events.push(event.type);}
}
const nodes = new Map([
    ['timezone-select',new Select(['UTC'],'UTC')],
    ['pi-model-select',new Select(['pi3','pi4'],'pi4')],
]);
const document = {getElementById:id=>nodes.get(id)};
const PERSISTED_FIELDS = [...nodes.keys()].map(id=>({id,type:'value'}));
const userPreferences = {form_state:saved};
const FORM_PERSIST_KEY='test';
const localStorage = {getItem:()=>null};
function toggleImageSource() {}
function toggleWifiSecurity() {}
function togglePowerRelaySettings() {}
function updateSelectDescription() {}
'''
    assertions = r'''
restoreFormState();
for(const [id,value] of Object.entries(expected)) assert.equal(nodes.get(id).value,value);
const timezone=nodes.get('timezone-select');
if(saved['timezone-select']) {
    assert.equal(timezone.options.filter(option=>option.value===saved['timezone-select']).length,1);
    assert.ok(timezone.events.includes('presentationchange'));
    restoreFormState();
    assert.equal(timezone.options.filter(option=>option.value===saved['timezone-select']).length,1);
}
console.log('completed');
'''
    script = "const saved=" + json.dumps(saved) + ";const expected=" + json.dumps(expected) + ";\n" + harness + RESTORE + assertions
    result = subprocess.run([node,"-e",script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "completed"
