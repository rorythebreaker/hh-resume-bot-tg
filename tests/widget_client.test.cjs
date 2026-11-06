const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../ui/menu_widget.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
class Element {
  constructor() { this.children = []; this.listeners = {}; this.value = ''; }
  set textContent(value) { this.value = value; this.children = []; }
  get textContent() { return this.value; }
  appendChild(child) { this.children.push(child); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  click() { this.listeners.click(); }
}
function render(state) {
  const elements = Object.fromEntries(['title','actions','nav','feedback'].map(id => [id,new Element()]));
  const requests = [], events = {};
  const widget = {chatData: JSON.stringify(state), readyCalled: false,
    onEvent: (name, callback) => { events[name] = callback; },
    ready() { this.readyCalled = true; },
    request(data) { requests.push(data); return Promise.resolve({result:'ok'}); }};
  vm.runInNewContext(source, {window: {TelegramWidget:widget}, document: {
    getElementById: id => elements[id], createElement: () => new Element()}});
  return {elements,widget,events,requests};
}
const state = {n:'abc123',t:'Main',p:0,c:2,b:[{i:0,t:'Open'}]};
test('widget renders data using text nodes and emits the documented bridge request', async () => {
  const ui = render(state);
  assert.equal(ui.elements.title.textContent,'Main');
  assert.equal(ui.widget.readyCalled,true);
  ui.elements.actions.children[0].click();
  assert.deepEqual(ui.requests,['hb:abc123:a0']);
  await Promise.resolve();
  assert.equal(ui.elements.feedback.textContent,'ok');
});
test('duplicate clicks while a request is in flight do not send twice', () => {
  const ui = render(state),button=ui.elements.actions.children[0];
  button.click();button.click();assert.equal(ui.requests.length,1);
});
test('server state changes redraw in place without executing user markup', () => {
  const ui = render(state);
  ui.widget.chatData=JSON.stringify({...state,t:'<img src=x onerror=alert(1)>',b:[{i:4,t:'New'}]});
  ui.events.chatDataChanged();
  assert.equal(ui.elements.title.textContent,'<img src=x onerror=alert(1)>');
  assert.equal(ui.elements.actions.children.length,1);
  assert.equal(ui.elements.actions.children[0].textContent,'New');
});
test('forwarded copy with no chat state has no actions', () => {
  const ui=render(null);assert.equal(ui.elements.actions.children.length,0);
});
test('next-page navigation uses server-owned pagination', () => {
  const ui=render(state);ui.elements.nav.children[0].click();
  assert.deepEqual(ui.requests,['hb:abc123:p1']);
});
test('malformed state fails closed without interactive buttons', () => {
  const ui=render(null);ui.widget.chatData='{bad';ui.events.chatDataChanged();
  assert.equal(ui.elements.actions.children.length,0);
});
