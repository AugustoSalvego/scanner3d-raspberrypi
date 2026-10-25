"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(
  path.join(root, "web_interface/static/dashboard.js"), "utf8"
);
const html = fs.readFileSync(
  path.join(root, "web_interface/templates/index.html"), "utf8"
);
const css = fs.readFileSync(
  path.join(root, "web_interface/static/dashboard.css"), "utf8"
);
function section(start, next) {
  const a = source.indexOf(start);
  const b = source.indexOf(next, a);
  assert.ok(a >= 0 && b > a, "Expected dashboard functions are present");
  return source.slice(a, b);
}
const helpers = section("  function normalizeSearch(value)", "  function safeDownload(sessionId)");
const picker = section("  function refreshOptions(){", "  async function refreshHistory(){");

function fixture(searchText = "") {
  const sessions = [
    { session_id: "scan_duck", display_name: "Patinho de borracha", created_at: "2026-10-20 17:05", ply_available: true },
    { session_id: "scan_gear", display_name: "Engrenagem", created_at: "2026-10-21 10:30", ply_available: true },
    { session_id: "scan_part", display_name: "Peça de aço", created_at: "2026-10-22 12:00", ply_available: true },
    { session_id: "scan_wait", display_name: "Em andamento", created_at: "2026-10-23 09:00", ply_available: false }
  ];
  const select = {
    value: "scan_duck", options: [],
    replaceChildren(...options) {
      this.options = [...options];
      this.value = options.length ? options[0].value : "";
    },
    add(option) { this.options.push(option); }
  };
  const search = { value: searchText };
  const state = { selected: "scan_duck", sessions };
  class Option {
    constructor(text, value) {
      this.text = text;
      this.value = value;
      this.disabled = false;
    }
  }
  const ctx = {
    state, Option,
    $: name => name === "viewer-source" ? select : search,
    displayName: s => s.display_name,
    displayDate: s => s.created_at
  };
  vm.createContext(ctx);
  vm.runInContext(helpers + "\n" + picker, ctx);
  return { ctx, select, search, state };
}

test("loaded PLY placeholder must be hidden even with flex styling", () => {
  assert.match(css, /[.]viewer-placeholder\[hidden\]\s*\{\s*display:\s*none\s*!important\s*\}/);
  assert.match(html, /id="viewer-empty"/);
  assert.match(source, /[$]\("viewer-empty"\)[.]hidden=true/);
});
test("both searchable areas exist and reset button is removed", () => {
  assert.match(html, /id="viewer-search"/);
  assert.match(html, /id="history-search"/);
  assert.doesNotMatch(html, /id="viewer-reset"/);
  assert.doesNotMatch(source, /[$]\("viewer-reset"\)/);
  assert.match(source, /[$]\("viewer-search"\)[.]addEventListener\("input",refreshOptions\)/);
  assert.match(source, /[$]\("history-search"\)[.]addEventListener\("input",renderHistory\)/);
});
test("filter ignores case and accents; keeps selected PLY", () => {
  const { ctx, select, state } = fixture("ENGRENAGEM");
  vm.runInContext("refreshOptions()", ctx);
  assert.equal(state.selected, "scan_duck");
  assert.equal(select.value, "scan_duck");
  assert.deepEqual(select.options.map(x => x.value), ["current", "scan_duck", "scan_gear"]);
  assert.equal(select.options.find(x => x.value === "scan_gear").disabled, false);
  assert.equal(vm.runInContext('matchesSession(state.sessions[2], normalizeSearch("PECA"))', ctx), true);
});
test("no match reports an empty search without hiding current model", () => {
  const { ctx, select, state } = fixture("não existe");
  vm.runInContext("refreshOptions()", ctx);
  assert.equal(state.selected, "scan_duck");
  assert.equal(select.value, "scan_duck");
  assert.deepEqual(select.options.map(x=>x.value), ["current", "scan_duck", ""]);
  assert.equal(select.options[2].disabled, true);
});
test("empty search shows only completed PLYs and current scan", () => {
  const { ctx, select } = fixture();
  vm.runInContext("refreshOptions()", ctx);
  assert.deepEqual(select.options.map(x=>x.value), ["current", "scan_duck", "scan_gear", "scan_part"]);
});
