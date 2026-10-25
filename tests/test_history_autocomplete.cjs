"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web_interface/static/dashboard.js"), "utf8");
const html = fs.readFileSync(path.join(root, "web_interface/templates/index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "web_interface/static/dashboard.css"), "utf8");

function between(start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, "Missing source section: " + start);
  return source.slice(a,b);
}

function node(tag) {
  return {
    tag, id:"", className:"", textContent:"", children:[], dataset:{}, handlers:{},
    attrs:{}, classList:{add(){},remove(){},toggle(){}}, offsetWidth:5,
    append(...parts){this.children.push(...parts);},
    replaceChildren(...parts){this.children=[...parts];},
    addEventListener(type,callback){this.handlers[type]=callback;},
    setAttribute(key,value){this.attrs[key]=value;},
    removeAttribute(key){delete this.attrs[key];},
    scrollIntoView(){this.scrolled=true;},
    querySelectorAll(selector){
      const className=selector.slice(1);
      return this.children.filter(x=>x.className.split(/\s+/).includes(className));
    }
  };
}

function fixture(initial) {
  const list=node("div"),panel=node("div");panel.hidden=true;
  const viewer=node("input");viewer.value="";
  const history=node("input");
  history.value=initial;history.focus=()=>{};
  const viewerClear={hidden:true},historyClear={hidden:true};
  const count=node("span");
  const map={
    "history-search":history,"history-clear":historyClear,
    "history-suggestions":panel,"history-list":list,"history-count":count,
    "viewer-search":viewer,"viewer-clear":viewerClear
  };
  const state={sessions:[
    {session_id:"scan_pato", display_name:"Patinho de borracha", created_at:"2026-10-20 17:05",ply_available:true},
    {session_id:"scan_parafuso", display_name:"Parafuso", created_at:"2026-10-21 17:05",ply_available:true},
    {session_id:"scan_peca", display_name:"Peça azul", created_at:"2026-10-22 17:05",ply_available:true},
    {session_id:"scan_aberto", display_name:"Em processamento", created_at:"2026-10-23 17:05",ply_available:false}
  ],selected:"scan_pato"};
  const document={
    createElement:node,
    createTextNode:text=>({textContent:text})
  };
  const ctx={
    state,document,$:key=>map[key],
    displayName:s=>s.display_name,
    displayDate:s=>s.created_at,
    makeButton:(text)=>{const b=node("button");b.textContent=text;return b;}
  };
  vm.createContext(ctx);
  const helpers=between("  function normalizeSearch(value)", "  function safeDownload(sessionId)");
  const historyFunctions=between("  let activeHistorySuggestion = -1;", "  async function refreshHistory(){");
  const historyView=between("  function renderHistory(){", "  async function refreshSettings(){");
  vm.runInContext(helpers+"\n"+historyFunctions+"\n"+historyView,ctx);
  return {ctx,list,panel,state,history,count,historyClear};
}

test("history uses the same autocomplete DOM and suggestion styles as the PLY viewer", () => {
  assert.match(html,/id="history-search-box"/);
  assert.match(html,/id="history-suggestions" role="listbox"/);
  assert.match(html,/id="history-search"[^>]*role="combobox"/);
  assert.match(html,/id="history-clear"/);
  assert.match(css,/\.history-search-combo\s*\{/);
  assert.match(css,/\.search-suggestions\s*\{/);
  assert.match(source,/\$\("history-search"\)\.addEventListener\("keydown"/);
  assert.match(source,/\$\("history-search"\)\.addEventListener\("focus"/);
  assert.match(source,/\$\("history-search-box"\)\.contains\(event\.target\)/);
});

test("every typed character narrows history suggestions and list", () => {
  const {ctx,history,panel,count,state,list}=fixture("P");
  for(const [query,suggestions] of [["P",3],["Pa",2],["Pati",1]]){
    history.value=query;
    vm.runInContext("renderHistory(); renderHistorySuggestions()",ctx);
    assert.equal(panel.querySelectorAll(".suggestion-option").length,suggestions,query);
    assert.equal(list.querySelectorAll(".session-item").length,suggestions,query);
    assert.equal(history.attrs["aria-expanded"],"true");
    assert.equal(count.textContent,suggestions+" de 4 sessões");
    assert.equal(state.selected,"scan_pato");
  }
});

test("accent-independent search can select and highlight saved or unfinished sessions", () => {
  const {ctx,history,panel,list}=fixture("peca");
  vm.runInContext("renderHistorySuggestions()",ctx);
  const options=panel.querySelectorAll(".suggestion-option");
  assert.equal(options.length,1);
  options[0].handlers.click();
  assert.equal(history.value,"Peça azul");
  assert.equal(panel.hidden,true);
  assert.equal(list.querySelectorAll(".session-item").length,1);
  assert.equal(list.querySelectorAll(".session-item")[0].dataset.sessionId,"scan_peca");
  assert.equal(list.querySelectorAll(".session-item")[0].scrolled,true);
});

test("empty query closes suggestions, restores all sessions and hides X",()=>{
  const {ctx,history,panel,historyClear,list}=fixture("Pat");
  vm.runInContext("renderHistory(); renderHistorySuggestions()",ctx);
  assert.equal(historyClear.hidden,false);
  history.value="";
  vm.runInContext("renderHistory(); renderHistorySuggestions()",ctx);
  assert.equal(historyClear.hidden,true);
  assert.equal(panel.hidden,true);
  assert.equal(list.querySelectorAll(".session-item").length,4);
});

test("no results has a readable empty state",()=>{
  const {ctx,panel,list}=fixture("xyz não existe");
  vm.runInContext("renderHistory(); renderHistorySuggestions()",ctx);
  assert.equal(panel.querySelectorAll(".suggestion-option").length,0);
  assert.match(panel.children[0].textContent,/Nenhum escaneamento/);
  assert.match(list.children[0].textContent,/Nenhum escaneamento corresponde/);
});
