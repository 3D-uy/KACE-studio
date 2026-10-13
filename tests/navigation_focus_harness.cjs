// Execute production routing and the actual Next onclick with an observable DOM
// model. Browser/WebView2 checks own layout, native Tab order and dialog Escape.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
let document;
function node(id, parent = null) {
    const classes = new Set();
    const el = {id, parentElement: parent, style: {}, dataset: {}, children: [], attributes: {},
        value: '', disabled: false, hidden: false, textContent: '', tabIndex: 0,
        classList: {add(...names) {names.forEach(n => classes.add(n));},
            remove(...names) {names.forEach(n => classes.delete(n));},
            contains(name) {return classes.has(name);},
            toggle(name, value) {if (value) classes.add(name); else classes.delete(name);}},
        focus() {document.activeElement = this;},
        getClientRects() {return this.hidden || (this.parentElement && !this.parentElement.classList.contains('active')) ? [] : [{}];},
        matches(selector) {return selector === ':disabled' && this.disabled;},
        closest(selector) {return selector === '.tab-content' ? this.parentElement : null;},
        setAttribute(name, value) {this.attributes[name] = value;},
        getAttribute(name) {return this.attributes[name] || null;},
        removeAttribute(name) {delete this.attributes[name];},
        appendChild(child) {this.children.push(child);}, replaceChildren() {this.children = [];},
        querySelectorAll() {return this.children.filter(child => !child.heading);},
        querySelector(selector) {return selector === 'h2' ? this.children.find(child => child.heading) : node('child');},
        addEventListener() {}, dispatchEvent() {}, scrollIntoView() {},
    };
    nodes.set(id, el);
    if (parent) parent.children.push(el);
    return el;
}
const tabs = ['imager-tab', 'credentials-tab', 'discovery-tab', 'terminal-tab'].map(id => node(id));
const nav = ['imager-nav-btn', 'credentials-nav-btn', 'discovery-nav-btn', 'terminal-nav-btn'].map(id => node(id));
tabs[0].classList.add('active'); nav[0].classList.add('active');
const next = node('next', tabs[0]);
const username = node('ssh-username', tabs[1]);
const password = node('ssh-password', tabs[1]);
const wifi = node('wifi-ssid', tabs[1]);
const heading = node('credentials-heading', tabs[1]); heading.heading = true;
const terminal = node('terminal-input', tabs[3]);
document = {activeElement: next, documentElement: {}, addEventListener() {}, createElement: () => node('created'),
    getElementById(id) {return nodes.get(id) || node(id);},
    querySelectorAll(selector) {return selector === '.tab-content' ? tabs : selector === '.nav-item' ? nav : [];},
    querySelector(selector) {return selector === '.nav-item:nth-child(1)' ? nav[0] : nav[2];},
};
const context = {document, console, assert, next, username, password, wifi, heading, terminal, nav, tabs,
    navigator: {language: 'en'}, Event: class {}, getComputedStyle: el => ({visibility: el.style.visibility || 'visible'}),
    addEventListener() {}, setTimeout() {}, clearTimeout() {}, setInterval() {}, clearInterval() {},
    localStorage: {getItem() {return null;}, setItem() {}},
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const html = fs.readFileSync(process.argv[3], 'utf8');
const nextHandler = html.match(/<div class="imager-actions">\s*<button[^>]*onclick="([^"]+)"/)[1];
context.advance = () => vm.runInContext(nextHandler, context);
const cases = {
    next_moves_focus: `
        username.value = 'retained-user'; password.value = 'synthetic-secret'; wifi.value = 'retained-network';
        currentDeviceIp = 'fixture.local'; loginState = 'CONNECTED'; loginUsername = 'session-user';
        next.focus(); advance();
        assert.equal(document.activeElement, username);
        assert.equal(activeTab, 'credentials-tab');
        assert.equal(document.activeElement.getClientRects().length, 1);
        assert.equal(username.value, 'retained-user'); assert.equal(password.value, 'synthetic-secret');
        assert.equal(wifi.value, 'retained-network'); assert.equal(currentDeviceIp, 'fixture.local');
        assert.equal(loginState, 'CONNECTED'); assert.equal(loginUsername, 'session-user');
    `,
    skip_disabled_or_hidden: `
        username.disabled = true; password.hidden = true; next.focus(); advance();
        assert.equal(document.activeElement, wifi);
    `,
    skip_visibility_hidden: `
        username.style.visibility = 'hidden'; next.focus(); advance();
        assert.equal(document.activeElement, password);
    `,
    heading_fallback: `
        username.disabled = password.disabled = wifi.disabled = true; next.focus(); advance();
        assert.equal(document.activeElement, heading);
        assert.equal(heading.tabIndex, -1);
    `,
    sidebar_and_background_keep_focus: `
        nav[1].focus(); switchTab('credentials-tab'); assert.equal(document.activeElement, nav[1]);
        nav[0].focus(); switchTab('imager-tab'); assert.equal(document.activeElement, nav[0]);
        nav[3].focus(); switchTab('terminal-tab'); terminal.focus();
        refreshSftpBrowser(); setStudioLanguage('Español', false); setStudioLanguage('Português', false);
        assert.equal(document.activeElement, terminal); assert.equal(activeTab, 'terminal-tab');
    `,
    error_focus_wins: `
        next.focus(); advance();
        document.getElementById = id => id === 'wifi-ssid-trigger' ? null : nodesForErrors(id);
        focusInputError('wifi-ssid'); assert.equal(document.activeElement, wifi);
        setStudioLanguage('Español', false); assert.equal(document.activeElement, wifi);
    `,
};
context.nodesForErrors = document.getElementById.bind(document);
const scenario = cases[process.argv[4]];
assert.ok(scenario, 'Unknown navigation scenario');
vm.runInContext(scenario, context);
console.log(`${process.argv[4]} completed`);
