// DOM integration checks, not a substitute for real-browser visual verification.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const {parseHTML} = require(path.resolve(process.argv[2] || 'dist/ui-test/node_modules/linkedom'));
const root = path.resolve(__dirname, '..', 'course_notes', 'web');
const {document, window} = parseHTML(fs.readFileSync(path.join(root, 'index.html'), 'utf8'));
Object.defineProperty(document, 'compatMode', {value:'CSS1Compat'});
window.HTMLElement.prototype.scrollIntoView = function() {};
window.HTMLElement.prototype.focus = function() {};
for (const select of document.querySelectorAll('select')) Object.defineProperty(select, 'value', {
  get(){return this._value ?? this.querySelector('option')?.getAttribute('value') ?? '';},
  set(value){this._value=String(value);}, configurable:true
});
document.querySelector('video').pause = () => {};
document.querySelector('video').load = () => {};
const dialog = document.querySelector('#settings-drawer');
dialog.showModal = function(){this.open=true;};
dialog.close = function(){this.open=false;this.dispatchEvent(new window.Event('close'));};
const status = {ok:true,version:'test',course:{title:'课程',profile:'general',path:'fixture'},
  environment:{api_key_present:true,api_key_status:'valid',downloader_exists:false,detected_materials:0},
  lectures:[{name:'第一讲',sources:1,status:'draft',has_draft:true,has_qa:true,updated_at:123,validation:{status:'passed'}}],
  job:{running:false,error:'',can_retry:false},watcher:{running:false},recorder:{running:false,logged_in:false,courses:[]}};
const mocks = {
  '/api/status':status, '/api/events':{ok:true,events:[]},
  '/api/settings':{ok:true,models:{writer:'fixture',reviewer:'fixture',transcriber:'fixture'},has_key:true,remembered:false,ffmpeg:true,ffprobe:true},
  '/api/runtime':{ok:true,version:'test',native:false,ffmpeg:true,ffprobe:true,chromium:false,pdf:true,pptx:true},
  '/api/courses/local':{ok:true,courses:[{name:'fixture',title:'课程',active:true}]},
  '/api/course/options':{ok:true,profile:'general',terms:[],prompt:'',exists:true},
  '/api/pick-files':{ok:true,paths:['C:\\private\\lecture.mp4','C:\\private\\slides.pdf']},
  '/api/simple/start':{ok:true},
  '/api/note':{ok:true,content:'# 测试笔记\n\n公式 $y=wx+b$。〔来源：slides.txt｜第 1 页〕',path:'fixture'},
  '/api/materials':{ok:true,files:[{id:0,name:'slides.txt',suffix:'.txt',kind:'document'}],evidence:[{source:'slides.txt',locator:'第 1 页',text:'原始证据'}]},
};
const requests = [];
const context = vm.createContext({document,window:{markdownit:require(path.join(root,'vendor','markdown-it.min.js'))},
  NodeFilter:{SHOW_TEXT:4},console,Blob,URL,confirm:()=>true,
  setInterval:()=>0,setTimeout:(fn,ms)=>ms < 100 ? setTimeout(fn,ms) : 0,clearTimeout,
  fetch:async (url,options={})=>{
    const endpoint = String(url).split('?')[0];
    requests.push({endpoint,body:options.body ? JSON.parse(options.body) : null});
    if (!(endpoint in mocks)) throw Error('Unexpected request: '+endpoint);
    return {ok:true,json:async()=>mocks[endpoint]};
  }});
for(const filename of ['vendor/temml.min.js','app.js','study-desk.js','optional-features.js','first-run.js','simple.js']) {
  vm.runInContext(fs.readFileSync(path.join(root,filename),'utf8'),context,{filename});
}
(async()=>{
  await new Promise(resolve=>setTimeout(resolve,50));
  assert(document.body.classList.contains('simple-mode'));
  assert(dialog.contains(document.querySelector('#connections')),'connections must live in settings');
  assert(dialog.contains(document.querySelector('#request-limit')),'usage tools must remain accessible in settings');
  assert(dialog.contains(document.querySelector('#toggle-watch')),'automation is not a home-page default');
  assert(!dialog.contains(document.querySelector('#simple-start')));
  assert(document.querySelector('#simple-books').textContent.includes('阅读笔记'));
  assert.strictEqual(document.querySelector('#simple-books .simple-actions').querySelectorAll('button').length,1,'one primary action per notebook');
  vm.runInContext("openSimpleSettings('setting-key')",context);
  assert(dialog.open);
  dialog.close();
  vm.runInContext("chooseSimpleSource('campus')",context);
  assert(document.body.classList.contains('campus-mode'));
  vm.runInContext("chooseSimpleSource('local')",context);
  assert(!document.body.classList.contains('campus-mode'));
  await vm.runInContext('pickSimpleFiles()',context);
  assert.strictEqual(document.querySelector('#simple-title').value,'lecture');
  assert(document.querySelector('#simple-selected').textContent.includes('2'));
  assert(!document.querySelector('#simple-selected').textContent.includes('private'),'home must show filenames, not paths');
  context.confirm=()=>false;
  await vm.runInContext('startSimpleTask(false)',context);
  assert(!requests.some(item=>item.endpoint==='/api/simple/start'),'cancelled confirmation must not start AI');
  context.confirm=()=>true;
  await vm.runInContext('startSimpleTask(false)',context);
  const paid = requests.find(item=>item.endpoint==='/api/simple/start');
  assert(paid.body.confirmed && paid.body.request_limit===100);
  assert.strictEqual(paid.body.paths.length,2);
  status.environment.api_key_present=false;
  const before=requests.filter(item=>item.endpoint==='/api/simple/start').length;
  await vm.runInContext('startSimpleTask(false)',context);
  assert(dialog.open,'missing key opens connection settings');
  assert.strictEqual(requests.filter(item=>item.endpoint==='/api/simple/start').length,before);
  dialog.close();
  await vm.runInContext('startSimpleTask(true)',context);
  assert(requests.filter(item=>item.endpoint==='/api/simple/start').at(-1).body.offline);
  vm.runInContext("openNote('第一讲','draft')",context);
  await new Promise(resolve=>setTimeout(resolve,50));
  assert(document.querySelector('#note-preview math'),'existing formulas must still render');
  assert(document.querySelector('#note-preview .source-cite'),'citations remain clickable');
  assert(!requests.some(item=>item.endpoint==='/api/check-key'),'no automatic AI connection requests');
  console.log('PASS: simplified home, settings drawer, source modes, one-action workflow, consent, offline fallback and note reading');
})().catch(error=>{console.error(error);process.exitCode=1;});
