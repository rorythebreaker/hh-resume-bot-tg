const tg=window.Telegram?.WebApp;
const initData=tg?.initData||'';
const demo=['localhost','127.0.0.1'].includes(location.hostname)&&new URLSearchParams(location.search).get('preview')==='1';
const main=document.getElementById('main'),status=document.getElementById('status');
let current=null,busy=false,view='main',toastTimer,inputValue='',inputNonce='',scoringDraft='';
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
async function copyText(text){
 try{
  if(globalThis.navigator?.clipboard?.writeText){
   await globalThis.navigator.clipboard.writeText(text);
  }else{
   throw new Error('Clipboard API unavailable');
  }
  toast('\u0421\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u043d\u043e');
 }catch(error){
  const field=element('textarea');field.value=text;
  field.style.position='fixed';field.style.opacity='0';
  document.body.appendChild(field);
  try{
   field.focus();field.select();field.setSelectionRange(0,text.length);
   if(!document.execCommand('copy'))throw new Error('Copy failed');
   toast('\u0421\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u043d\u043e');
  }catch(fallbackError){toast('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c. \u0412\u044b\u0434\u0435\u043b\u0438\u0442\u0435 \u0442\u0435\u043a\u0441\u0442 \u0432\u0440\u0443\u0447\u043d\u0443\u044e.');}
  finally{field.remove();}
 }
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
 return {menu:{title,rows:buttons.map(([label,id])=>[{label,id}])},profile:{name:'Основной',account:'main'},paused:false,stats:{today:24,free:176},messages:[],input:null,scoring:{enabled:false,mode:'shadow',auto_threshold:90,review_threshold:50,rules:{}},scoring_queue:[],scoring_feedback_stats:{},notifications:'Сводки и уведомления продолжают приходить в чат бота.'};
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
 try{const next=await api(route,body);if(quiet&&JSON.stringify(next)===JSON.stringify(current))return;current=next;render(quiet);}
 catch(error){if(!quiet)toast(error.message);}
 finally{busy=false;main.classList.remove('is-loading');document.querySelectorAll('button').forEach(button=>button.disabled=false);}
}
function render(preserveScroll=false){
 const pageY=window.scrollY||0,feedY=main.querySelector?.('.messages')?.scrollTop||0;
 const data=current;if(!data)return;if(data.section)view=data.section;
 const scoringTab=document.getElementById('scoring-tab');if(scoringTab)scoringTab.hidden=!data.scoring?.enabled;
 if(view==='scoring'&&!data.scoring?.enabled)view='profile';
 main.textContent='';status.textContent=data.paused?'На паузе':'Готов к работе';status.className='status'+(data.paused?' paused':'');
 if(demo)main.appendChild(element('div','Предпросмотр · пример данных, действия не выполняются','demo-label'));
 const greeting=element('section',undefined,'greeting');
 greeting.appendChild(element('div','ВАША КАРЬЕРА · '+data.profile.account,'eyebrow'));
 greeting.appendChild(element('h1',data.profile.name));
 greeting.appendChild(element('p','Все настройки здесь. Важные события — в чате.'));
 main.appendChild(greeting);
 if(view==='profile'){
  const card=element('section',undefined,'card');
  card.appendChild(element('h2','Настройки скоринга'));
  const row=element('label',undefined,'scoring-switch');
  const toggle=element('input');toggle.type='checkbox';toggle.checked=!!data.scoring?.enabled;
  toggle.onchange=()=>update('/api/scoring/config',{enabled:toggle.checked});
  row.appendChild(toggle);row.appendChild(element('span','Включить скоринг'));
  card.appendChild(row);
  card.appendChild(element('p','При включении появится вкладка «Скоринг». Пока действует только теневой режим: решения не влияют на отклики.'));
  main.appendChild(card);
 }
 if(view==='scoring'){
  const config=data.scoring||{};
  const card=element('section',undefined,'card');
  card.appendChild(element('h2','Скоринг · настройки'));
  card.appendChild(element('p','Режим: теневой. Оценки 0–100 не влияют на отправку откликов.'));
  const thresholdForm=element('form');
  const inputs={};
  for(const [key,label] of [['review_threshold','Порог ручной проверки'],['auto_threshold','Порог высокого соответствия']]){
   const wrapper=element('label',label,'input-label');
   const input=element('input');input.type='number';input.min='0';input.max='100';input.value=String(config[key]??(key==='auto_threshold'?90:50));
   wrapper.appendChild(input);thresholdForm.appendChild(wrapper);inputs[key]=input;
  }
  const save=element('button','Сохранить пороги');save.type='submit';thresholdForm.appendChild(save);
  thresholdForm.onsubmit=event=>{event.preventDefault();update('/api/scoring/config',{review_threshold:Number(inputs.review_threshold.value),auto_threshold:Number(inputs.auto_threshold.value)});};
  card.appendChild(thresholdForm);
  const editor=element('details');editor.appendChild(element('summary','Словари правил (JSON)'));
  const help=element('p','Ключи: role, skills, duties, negative. Значения — массивы строк. Неуказанные категории используют стандартные правила.');
  editor.appendChild(help);
  const textarea=element('textarea');textarea.value=JSON.stringify(config.rules||{},null,2);textarea.rows=7;editor.appendChild(textarea);
  const saveRules=element('button','Сохранить словари');
  saveRules.onclick=()=>{try{const rules=JSON.parse(textarea.value);update('/api/scoring/config',{rules});}catch(error){toast('Неверный JSON');}};
  editor.appendChild(saveRules);
  const importer=element('details');importer.appendChild(element('summary','Импорт полного JSON · v1.0'));
  importer.appendChild(element('p','Выберите файл или вставьте JSON. Сначала проверьте изменения, затем сохраните. Поиск HH и автоматические отклики не меняются.'));
  const file=element('input');file.type='file';file.accept='.json,application/json';
  const jsonInput=element('textarea');jsonInput.rows=8;jsonInput.value=scoringDraft;jsonInput.oninput=()=>{scoringDraft=jsonInput.value;};jsonInput.placeholder='Вставьте полный JSON словаря v1.0';
  file.onchange=async()=>{if(file.files?.[0]){if(file.files[0].size>100000){toast('Файл превышает 100 КБ');return;}jsonInput.value=await file.files[0].text();scoringDraft=jsonInput.value;}};
  importer.appendChild(file);importer.appendChild(jsonInput);
  const preview=element('button','Проверить JSON');preview.type='button';
  preview.onclick=()=>{try{update('/api/scoring/preview',{dictionary:JSON.parse(jsonInput.value)});}catch(e){toast('Некорректный JSON: '+e.message);}};
  const apply=element('button','Импортировать словарь');apply.type='button';
  apply.onclick=()=>{try{if(!window.confirm('Заменить настройки скоринга текущего профиля?'))return;update('/api/scoring/import',{dictionary:JSON.parse(jsonInput.value)});}catch(e){toast('Некорректный JSON: '+e.message);}};
  importer.appendChild(preview);importer.appendChild(apply);
  if(data.scoring_import_preview){
   const result=data.scoring_import_preview;
   importer.open=true;
   importer.appendChild(element('pre',JSON.stringify(result,null,2)));
   if(result.saved)importer.appendChild(element('p','Настройки скоринга сохранены для текущего профиля.'));
  }
  card.appendChild(editor);card.appendChild(importer);main.appendChild(card);
  const queue=element('section',undefined,'card');queue.appendChild(element('h2','Дообучение · ручная разметка'));
  const feedback=data.scoring_feedback_stats||{};
  queue.appendChild(element('p','Размечено: подходит '+(feedback.positive||0)+' · не подходит '+(feedback.negative||0)+' · сомневаюсь '+(feedback.unsure||0)+'. Обучение ML пока не запускается.'));
  const load=element('button','Обновить вакансии');load.onclick=()=>update('/api/scoring/list',{});queue.appendChild(load);
  for(const item of data.scoring_queue||[]){
   const box=element('div',undefined,'scoring-vacancy');
   const name=element('strong',String(item.vacancy?.name||'Вакансия #'+item.vacancy_id));box.appendChild(name);
   box.appendChild(element('p','Оценка: '+item.score+'/100 · Решение: '+(item.decision||'не оценено')));
   const details=element('details');details.appendChild(element('summary','Объяснение оценки'));
   details.appendChild(element('pre',JSON.stringify({parts:item.features?.parts,skills:item.features?.matched_skills,negative:item.features?.negative_matches},null,2)));
   box.appendChild(details);
   const link=element('a','Открыть на HH');link.href='https://hh.ru/vacancy/'+encodeURIComponent(item.vacancy_id);link.target='_blank';link.rel='noopener noreferrer';box.appendChild(link);
   const reason=element('select');
   for(const [value,label] of [['','Без причины'],['duties','Обязанности'],['stack','Стек'],['experience','Опыт'],['conditions','Условия'],['other','Другое']]){
    const option=element('option',label);option.value=value;reason.appendChild(option);
   }
   box.appendChild(reason);
   const buttons=element('div',undefined,'row');
   for(const [decision,label] of [['positive','Подходит'],['negative','Не подходит'],['unsure','Сомневаюсь']]){
    const button=element('button',label);
    button.onclick=()=>update('/api/scoring/feedback',{vacancy_id:item.vacancy_id,decision,reason:reason.value||null});
    buttons.appendChild(button);
   }
   box.appendChild(buttons);queue.appendChild(box);
  }
  if(!(data.scoring_queue||[]).length)queue.appendChild(element('p','Вакансий пока нет. Включите скоринг и запустите обычный поиск/рассылку, затем обновите список.'));
  main.appendChild(queue);
 }
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
  const form=element('form',undefined,'card');form.appendChild(element('label',data.input.secret?'Введите код или секретное значение':'Введите значение','input-label'));
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
  const list=element('div',undefined,'messages');for(const message of data.messages.slice(-8)){
   const item=element('div',undefined,'message');
   const copy=element('button','\u041a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c','copy-message');copy.type='button';
   copy.onclick=()=>copyText(message.text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,''));
   item.appendChild(copy);item.appendChild(element('div',message.text));list.appendChild(item);
  }feed.appendChild(list);main.appendChild(feed);
 }
 if(preserveScroll){const feed=main.querySelector?.('.messages');if(feed)feed.scrollTop=feedY;window.scrollTo?.(0,pageY);}
 document.querySelectorAll('[data-view]').forEach(button=>button.classList.toggle('selected',button.dataset.view===view));
}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{view=button.dataset.view;update(view==='scoring'?'/api/scoring/list':'/api/view',view==='scoring'?{}:{name:view});});
if(tg){tg.ready();tg.expand();tg.onEvent('activated',()=>update('/api/state',undefined,true));}
if(!initData&&!demo){
 main.textContent='';const gate=element('div',undefined,'empty');gate.appendChild(element('h1','Откройте панель в Telegram'));gate.appendChild(element('p','В личном чате с ботом нажмите «Открыть панель». Доступ подтверждается Telegram.'));main.appendChild(gate);
 status.textContent='Нужен вход';document.getElementById('tabs').hidden=true;
}else{
 update();setInterval(()=>{if(!document.hidden&&!document.activeElement?.matches('input,textarea'))update('/api/state',undefined,true);},5000);
}
