const tg=window.Telegram?.WebApp;
const initData=tg?.initData||'';
const demo=['localhost','127.0.0.1'].includes(location.hostname)&&new URLSearchParams(location.search).get('preview')==='1';
const main=document.getElementById('main'),status=document.getElementById('status');
let current=null,busy=false,view='main',toastTimer,inputValue='',inputNonce='';
export function actionStyle(label){
  if(/запуст|отправить|подтверд/i.test(label))return 'primary';
  if(/удал|отказ|пауза/i.test(label))return 'danger';
  return '';
}
function element(tag,text,className){
  const el=document.createElement(tag);if(text!==undefined)el.textContent=text;
  if(className)el.className=className;return el;
}
function toast(text){
  const target=document.getElementById('toast');target.textContent=text;target.className='show';
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>target.className='',4500);
}
function sample(name='main'){
 const sections={
 main:['Панель управления',[['Профиль и резюме','profile'],['Фильтры поиска','filters'],['Настройки AI','ai'],['Расписание','schedule'],['Запустить рассылку','ctl'],['История и статистика','view']]],
 profile:['Профиль: Основной',[['Выбрать резюме','profile'],['Сменить аккаунт','profile'],['Сопроводительное письмо','profile'],['Авторизация HH','profile']]],
 filters:['Фильтры поиска',[['Опыт: 3–6 лет','filters'],['Формат: удалённо','filters'],['Зарплата от: 150 000 ₽','filters'],['Лимит за запуск: 50','filters']]],
 ai:['Настройки AI',[['Сопроводительные письма: вкл','ai'],['Фильтрация вакансий','ai'],['Модель и API','ai'],['Промпты','ai']]],
 ctl:['Управление откликами',[['Запустить рассылку','ctl'],['Пауза','ctl'],['Отклик по ссылке','ctl'],['Расписание','schedule']]],
 schedule:['Расписание · МСК',[['Часы: 8–21','schedule'],['Изменить расписание','schedule']]],
 view:['Статистика',[['Последние отклики','view'],['История запусков','view'],['Приглашения','view'],['Состояние сервисов','view']]]};
 const [title,buttons]=sections[name]||sections.main;
 return {menu:{title,rows:buttons.map(([label,id])=>[{label,id}])},profile:{name:'Основной',account:'main'},paused:false,stats:{today:24,free:176},messages:[],input:null,notifications:'Сводки и уведомления продолжают приходить в чат бота.'};
}
async function api(route,body){
 if(demo)return sample(body?.name||view);
 const response=await fetch(route,{method:body?'POST':'GET',headers:{Authorization:'tma '+initData,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store'});
 const result=await response.json();
 if(!response.ok)throw new Error(result.error||'Не удалось связаться с ботом.');
 return result;
}
async function update(route='/api/state',body,quiet=false){
 if(busy)return;
 busy=true;
 if(!quiet){document.querySelectorAll('button').forEach(b=>b.disabled=true);main.classList.add('is-loading');}
 try{current=await api(route,body);render();}
 catch(error){if(!quiet)toast(error.message);}
 finally{busy=false;main.classList.remove('is-loading');document.querySelectorAll('button').forEach(button=>button.disabled=false);}
}
function render(){
 const data=current;if(!data)return;if(data.section)view=data.section;
 main.textContent='';status.textContent=data.paused?'На паузе':'Готов к работе';status.className='status'+(data.paused?' paused':'');
 if(demo)main.appendChild(element('div','Предпросмотр · пример данных, действия не выполняются','demo-label'));
 const greeting=element('section',undefined,'greeting');
 greeting.appendChild(element('div','ВАША КАРЬЕРА · '+data.profile.account,'eyebrow'));
 greeting.appendChild(element('h1',data.profile.name));
 greeting.appendChild(element('p','Все настройки здесь. Важные события — в чате.'));
 main.appendChild(greeting);
 const stats=element('div',undefined,'stats');
 for(const [label,value,note]of [['Отправлено сегодня',data.stats.today,'откликов за текущий день'],['Доступно сейчас',data.stats.free,'в суточном окне HH']]){
  const card=element('div',undefined,'stat');card.appendChild(element('span',label));card.appendChild(element('strong',value??'—',label.startsWith('Доступно')?'accent':''));card.appendChild(element('small',note));stats.appendChild(card);
 }main.appendChild(stats);
 const panel=element('section',undefined,'card'),heading=element('div',undefined,'section-title');
 heading.appendChild(element('h2','Управление'));
 const refresh=element('button','Обновить');refresh.onclick=()=>update();heading.appendChild(refresh);panel.appendChild(heading);
 panel.appendChild(element('p',data.menu.title,'menu-title'));
 const actions=element('div',undefined,'actions');
 for(const row of data.menu.rows)for(const action of row){
  const button=element('button',action.label,'action '+actionStyle(action.label));
  if(action.label.length>34)button.classList.add('wide');
  button.onclick=()=>{button.disabled=true;if(demo){view=action.id;update('/api/view',{name:view});}else update('/api/action',{id:action.id});};
  actions.appendChild(button);
 }panel.appendChild(actions);main.appendChild(panel);
 if(data.input){
  const form=element('form',undefined,'card');if(data.messages.length)form.appendChild(element('p',data.messages[data.messages.length-1].text,'menu-title'));form.appendChild(element('label',data.input.secret?'Введите код или секретное значение':'Введите значение','input-label'));
  if(data.captcha){const image=element('img',undefined,'captcha');image.src='data:image/png;base64,'+data.captcha;image.alt='CAPTCHA HH';form.appendChild(image);}
  if(inputNonce!==data.input.nonce){inputNonce=data.input.nonce;inputValue='';}
  const input=element(data.input.secret?'input':'textarea');if(data.input.secret)input.type='password';
  input.autocomplete='off';input.value=inputValue;input.oninput=()=>inputValue=input.value;input.maxLength=12000;form.appendChild(input);
  const row=element('div',undefined,'row'),submit=element('button','Сохранить'),cancel=element('button','Отмена','secondary');
  submit.type='submit';cancel.type='button';cancel.onclick=()=>{inputValue='';update('/api/cancel',{});};row.appendChild(submit);row.appendChild(cancel);form.appendChild(row);
  form.onsubmit=event=>{event.preventDefault();if(!input.value.trim())return;submit.disabled=true;const text=input.value;inputValue='';input.value='';update('/api/input',{text,nonce:data.input.nonce});};
  main.appendChild(form);
 }
 if(data.messages.length){
  const feed=element('section',undefined,'card');const heading=element('div',undefined,'section-title');heading.appendChild(element('h2','Ответы бота'));const clear=element('button','Очистить');clear.onclick=()=>update('/api/messages/clear',{});heading.appendChild(clear);feed.appendChild(heading);
  const list=element('div',undefined,'messages');for(const message of data.messages.slice(-8))list.appendChild(element('div',message.text,'message'));feed.appendChild(list);main.appendChild(feed);
 }
 document.querySelectorAll('[data-view]').forEach(button=>button.classList.toggle('selected',button.dataset.view===view));
}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{view=button.dataset.view;update('/api/view',{name:view});});
if(tg){tg.ready();tg.expand();tg.onEvent('activated',()=>update('/api/state',undefined,true));}
if(!initData&&!demo){
 main.textContent='';const gate=element('div',undefined,'empty');gate.appendChild(element('h1','Откройте панель в Telegram'));gate.appendChild(element('p','В личном чате с ботом нажмите «Открыть панель». Доступ подтверждается Telegram.'));main.appendChild(gate);
 status.textContent='Нужен вход';document.getElementById('tabs').hidden=true;
}else{
 update();setInterval(()=>{if(!document.hidden&&!document.activeElement?.matches('input,textarea'))update('/api/state',undefined,true);},5000);
}
