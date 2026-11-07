const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../mini_app/app.mjs'),'utf8').replace('export function actionStyle','function actionStyle');
class Element{
 constructor(tag='div'){this.tagName=tag;this.children=[];this.value='';this.dataset={};this.className='';this.hidden=false;this.disabled=false;this._text='';this.classList={add:()=>{},toggle:()=>{}};}
 set textContent(text){this._text=String(text);this.children=[];}get textContent(){return this._text;}
 appendChild(child){this.children.push(child);return child;}click(){if(this.onclick)return this.onclick();}
 matches(selector){return selector.split(',').includes(this.tagName);}
}
const flatten=el=>[el,...el.children.flatMap(flatten)];
const sample={menu:{title:'Main',rows:[[{id:'safe-action',label:'Start'}]]},profile:{name:'Owner',account:'main'},paused:false,stats:{today:1,free:199},messages:[],input:null,notifications:'Reports in chat'};
async function boot({demo=false,initData='',response=sample}={}){
 const main=new Element(),status=new Element(),tabs=new Element(),toast=new Element();
 const buttons=['main','profile','filters','ai','ctl'].map(view=>{const el=new Element('button');el.dataset.view=view;tabs.appendChild(el);return el;});
 const elements={main,status,tabs,toast},calls=[];
 const document={hidden:false,activeElement:null,getElementById:id=>elements[id],createElement:tag=>new Element(tag),querySelectorAll:selector=>selector==='[data-view]'?buttons:flatten(main).concat(buttons).filter(el=>el.tagName==='button')};
 const sdk={initData,ready(){},expand(){},onEvent(){}};
 vm.runInNewContext(source,{window:{Telegram:{WebApp:sdk}},location:{hostname:demo?'127.0.0.1':'hh.example.com',search:demo?'?preview=1':''},URLSearchParams,document,setInterval:()=>0,setTimeout:()=>0,clearTimeout:()=>{},fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>response};}});
 for(let i=0;i<6;i++)await Promise.resolve();
 return {main,status,tabs,buttons,calls};
}
test('production shell requires Telegram identity',async()=>{
 const ui=await boot();assert.equal(ui.tabs.hidden,true);assert.equal(ui.calls.length,0);assert.match(flatten(ui.main).map(x=>x.textContent).join(' '),/Telegram/);
});
test('local preview has sample data and never calls the live API',async()=>{
 const ui=await boot({demo:true});assert.equal(ui.calls.length,0);assert.ok(flatten(ui.main).some(x=>x.className==='demo-label'));
});
test('real API receives initData in authorization header, never the URL',async()=>{
 const ui=await boot({initData:'signed-context'});assert.equal(ui.calls[0].url,'/api/state');assert.equal(ui.calls[0].options.headers.Authorization,'tma signed-context');assert.ok(flatten(ui.main).some(x=>x.textContent==='Owner'));
});
test('menu click sends only the server-issued action token',async()=>{
 const ui=await boot({initData:'signed-context'});const action=flatten(ui.main).find(el=>el.tagName==='button'&&el.textContent==='Start');action.click();await Promise.resolve();assert.equal(ui.calls[1].url,'/api/action');assert.equal(JSON.parse(ui.calls[1].options.body).id,'safe-action');
});
test('server menu markup is displayed as text',async()=>{
 const payload={...sample,menu:{title:'<img src=x onerror=alert(1)>',rows:[]}};
 const ui=await boot({initData:'signed-context',response:payload});assert.ok(flatten(ui.main).some(x=>x.textContent===payload.menu.title));assert.equal(flatten(ui.main).filter(x=>x.tagName==='img').length,0);
});
