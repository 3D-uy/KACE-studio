"""Execute the production language switch without connecting to a device."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest


def test_imager_markup_uses_translated_labels_hints_and_decorative_icons():
    html = (Path(__file__).resolve().parents[1] / "web/index.html").read_text(encoding="utf-8")
    for field, key in (("pi-model-select", "piModel"), ("bootstrap-ui-select-imager", "osDashboard")):
        label = re.search(rf'<label for="{field}">(.*?)</label>', html, re.S).group(1)
        assert f'data-ui-text="{key}"' in label
        select = re.search(rf'<select id="{field}"([^>]*)>(.*?)</select>', html, re.S)
        assert 'aria-label=' not in select.group(1)
        options = re.findall(r'<option\b([^>]*)>', select.group(2))
        assert options and all('data-ui-title=' in option for option in options)
    assert re.search(r'<input[^>]+id="manual-ip"[^>]+data-ui-placeholder="hostExample"', html)
    for field in ("flash-action-btn", "eject-btn"):
        body = re.search(rf'<button[^>]+id="{field}"[^>]*>(.*?)</button>', html, re.S).group(1)
        icons = re.findall(r'<i\b([^>]*)>', body)
        assert icons and all('aria-hidden="true"' in icon for icon in icons)


def test_language_switch_updates_ui_and_preserves_form_and_session():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    app = Path(__file__).resolve().parents[1] / "web/app.js"
    program = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const saved = new Map(), writes = [];
function element(dataset = {}) {
    return {dataset, textContent: '', value: 'keep-my-form-value', attributes: {},
        setAttribute(key, value) {this.attributes[key] = value;},
        dispatchEvent(event) {this.lastEvent = event.type;}};
}
const heading = element({uiText: 'imagerTitle'}), label = element({uiLabel: 'theme'});
const placeholder = element({uiPlaceholder: 'ssidPlaceholder'}), select = element();
const address = element({uiPlaceholder: 'hostExample'});
const model = element({uiTitle: 'pi4Hint'});
select.id = 'pi-model-select';
select.selectedOptions = [model];
model.parentElement = select;
model.selected = true;
const nodes = new Map([['language-toggle-btn', element()], ['language-flag', element()],
    ['start-first-boot-scan', element()], ['stop-first-boot-scan', element()],
    ['scan-status-text', element()], [select.id, select], [select.id + '-description', element()]]);
const context = {assert, nodes, heading, label, placeholder, select, model, address, writes, console,
    navigator: {language: 'en-US'}, Event: class {constructor(type) {this.type = type;}},
    localStorage: {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value)},
    document: {documentElement: {}, addEventListener() {}, getElementById: id => nodes.get(id) || null,
        querySelectorAll: selector => ({'[data-ui-text]': [heading], '[data-ui-label]': [label],
            'option[data-ui-title]': [model], '[data-ui-placeholder]': [placeholder,address],
            '#imager-tab select': [select]})[selector] || []},
    addEventListener() {}, pywebview: {api: {set_preferences(prefs) {
        writes.push(JSON.parse(JSON.stringify(prefs))); return Promise.resolve(true);
    }}}};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
vm.runInContext(`
sshConnected = true;
activeTab = 'credentials-tab';
studioPreferencesLoaded = true;
nodes.get('scan-status-text').textContent = studioText('discoveryAmbiguous');
assert.equal(studioLanguage(), 'English');
assert.equal(setStudioLanguage('Español'), true);
assert.equal(document.documentElement.lang, 'es');
assert.equal(heading.textContent, 'Grabador de tarjetas SD');
assert.equal(nodes.get('language-flag').src, 'flags/es.svg');
assert.equal(nodes.get('language-toggle-btn').attributes['aria-label'], 'Cambiar idioma: Español');
assert.equal(nodes.get('scan-status-text').textContent, studioText('discoveryAmbiguous'));
assert.equal(select.lastEvent, 'presentationchange');
assert.ok(model.title.includes('400 y Compute Module'));
assert.equal(nodes.get(select.id + '-description').textContent, model.title);
assert.equal(address.placeholder, 'p. ej., 192.168.1.50 o kace.local');
assert.equal(select.value, 'keep-my-form-value');
assert.equal(placeholder.placeholder, studioText('ssidPlaceholder'));
assert.equal(label.title, studioText('theme'));
cycleStudioLanguage();
assert.equal(studioLanguage(), 'English');
assert.equal(document.documentElement.lang, 'en');
assert.ok(model.title.includes('400, and Compute Module'));
assert.equal(address.placeholder, 'e.g. 192.168.1.50 or kace.local');
assert.equal(nodes.get('language-flag').src, 'flags/en.svg');
cycleStudioLanguage();
assert.equal(studioLanguage(), 'Português');
assert.equal(document.documentElement.lang, 'pt');
assert.ok(model.title.includes('400 e Compute Module'));
assert.equal(nodes.get(select.id + '-description').textContent, model.title);
assert.equal(address.placeholder, 'por exemplo, 192.168.1.50 ou kace.local');
assert.equal(nodes.get('language-flag').src, 'flags/pt.svg');
assert.equal(nodes.get('language-toggle-btn').attributes['aria-label'], 'Alterar idioma: Português');
assert.equal(localStorage.getItem('kace_language'), 'Português');
assert.equal(writes.length, 3);
assert.equal(writes[2].language, 'Português');
assert.equal(setStudioLanguage('__proto__'), false);
assert.equal(studioLanguage(), 'Português');
assert.equal(writes.length, 3);
assert.equal(sshConnected, true);
assert.equal(activeTab, 'credentials-tab');
assert.equal(select.value, 'keep-my-form-value');
nodes.set('kace-workflow-tracker', {dataset: {workflowId: 'currently-visible'}});
kaceWorkflowViews.set('currently-visible', {workflowId: 'currently-visible'});
kaceWorkflowViews.set('stale-workflow', {workflowId: 'stale-workflow'});
const repainted = [];
renderKaceWorkflow = view => repainted.push(view.workflowId);
setStudioLanguage('Português', false);
assert.deepEqual(repainted, ['currently-visible']);
`, context);
const restarted = {...context, navigator: {language: 'en-US'}};
restarted.window = restarted;
vm.createContext(restarted);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), restarted);
vm.runInContext("assert.equal(studioLanguage(), 'Português');", restarted);
'''
    result = subprocess.run([node, "-", str(app)], input=program, text=True,
                            encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("preference_result", ["loaded", "failed", "missing"])
def test_startup_language_change_waits_for_preferences(preference_result):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    app = Path(__file__).resolve().parents[1] / "web/app.js"
    program = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const callbacks = {}, writes = [], saved = new Map();
let resolvePreferences, rejectPreferences;
const context = {assert, console, navigator: {language: 'en-US'}, Event: class {},
    localStorage: {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value)},
    document: {documentElement: {}, addEventListener() {}, querySelectorAll() {return [];},
        getElementById() {return null;}},
    addEventListener(name, callback) {callbacks[name] = callback;},
    pywebview: {api: {
        get_preferences() {return new Promise((resolve, reject) => {
            resolvePreferences = resolve; rejectPreferences = reject;
        });},
        set_preferences(prefs) {writes.push(JSON.parse(JSON.stringify(prefs))); return Promise.resolve(true);}
    }}};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
vm.runInContext('refreshDrives = () => {}; applyTheme = () => {}; restoreFormState = () => {}; triggerScan = () => {};', context);
callbacks.pywebviewready();
vm.runInContext("setStudioLanguage('Español'); setStudioLanguage('Português');", context);
assert.equal(context.document.documentElement.lang, 'pt');
assert.equal(saved.get('kace_language'), 'Português');
assert.equal(writes.length, 0);
const existing = {theme: 'light', kace_auto_scan: false,
    form_state: {'hostname-input': 'saved-printer'}, language: 'English'};
const outcome = process.argv[3];
if (outcome === 'loaded') resolvePreferences(existing);
else if (outcome === 'failed') rejectPreferences(new Error('Simulated preference read failure'));
else resolvePreferences(null);
setImmediate(() => {
    assert.equal(context.document.documentElement.lang, 'pt');
    if (outcome === 'loaded') {
        assert.deepEqual(writes, [{...existing, language: 'Português'}]);
        vm.runInContext("setStudioLanguage('English');", context);
        assert.deepEqual(writes[1], {...existing, language: 'English'});
    } else {
        vm.runInContext("setStudioLanguage('English');", context);
        assert.equal(writes.length, 0);
    }
});
'''
    result = subprocess.run([node, "-", str(app), preference_result], input=program,
                            text=True, encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
