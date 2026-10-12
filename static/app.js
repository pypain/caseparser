let state;
const routes={
  '/prices':['compare','Сравнение цен','Сравнивай площадки по сумме, которую получишь после вывода.'],
  '/collection':['automatic','Автоматический сбор','Свежие котировки, состояние источников и настройки вывода.'],
  '/inventory':['inventory','Кейсы и выплаты','Партии, завершенные продажи и расчеты с владельцами.'],
  '/history':['history','История цен','Следи за изменением цены Danger Zone на разных площадках.'],
  '/markets':['markets','Площадки','Управляй площадками и исключай нежелательные места продажи.']
};
function navigate(path, push=false){
  if(!routes[path])path='/prices';if(push)history.pushState({},'',path);
  const [id,title,description]=routes[path];document.querySelectorAll('.page-panel').forEach(s=>s.hidden=s.id!==id);
  document.querySelectorAll('[data-route]').forEach(a=>{const active=a.getAttribute('href')===path;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  document.querySelector('#page-title').textContent=title;document.querySelector('#page-name').textContent=title;document.querySelector('#page-description').textContent=description;document.title=title+' · Caseparser';window.scrollTo(0,0);
}
document.addEventListener('click',e=>{const a=e.target.closest('a[data-route]');if(a&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&e.button===0){e.preventDefault();navigate(a.getAttribute('href'),true);}});
window.addEventListener('popstate',()=>navigate(location.pathname));
navigate(location.pathname);
document.documentElement.dataset.theme=localStorage.getItem('theme')||'light';
const $ = s => document.querySelector(s);
const rub = n => new Intl.NumberFormat('ru-RU', {style:'currency',currency:'RUB'}).format(n / 100);
const date = s => new Date(s).toLocaleString('ru-RU', {timeZone:'Europe/Moscow'}) + ' МСК';
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const option = (v,t) => `<option value="${esc(v)}">${esc(t)}</option>`;
const cash = (n,c) => new Intl.NumberFormat('ru-RU',{style:'currency',currency:c}).format(n/100);
function options(s, html){const value=s.value;s.innerHTML=html;if([...s.options].some(o=>o.value===value))s.value=value;}
async function api(path, data) {
  const r = await fetch('/api/' + path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.status===401){location.assign('/login');throw new Error('Требуется вход');}
  const body = await r.json(); if(!r.ok) throw new Error(body.error); return body;
}
function report(e){$('#error').textContent = e.message;}
async function load(){
  state = await api('state?quantity=' + encodeURIComponent($('#count').value));
  render();
}
function render(){
  const {markets,quotes,batches,sales,payments} = state;
  $('#price-count').textContent=new Set([...state.automatic,...quotes].map(q=>q.market_id)).size;
  $('#market-count').textContent=markets.length;
  $('#coverage-summary').textContent=`Цены получены: ${new Set(state.automatic.map(p=>p.market_id)).size} из ${markets.length} · Автосбор подключен: ${state.collectors.filter(s=>s.supported).length} · Остальные требуют подключения`;
  const statusText={pending:'Ожидание первого запроса',running:'Запрос выполняется',ok:'Цена получена',error:'Ошибка доступа или ответа',needs_key:'Требуется ключ API',not_connected:'Автосбор не подключен'};
  $('#stock-count').textContent=batches.reduce((a,b)=>a+b.remaining,0);
  $('#auto-prices').innerHTML = state.automatic.map(p=>`<tr><td>${esc(p.name)}<small>${p.kind==='estimate'?'Оценка продажи':p.kind==='instant'?'Заявка на выкуп':'Цена выставления'}</small>${p.stale?'<small>Цена устарела</small>':''}</td><td>${cash(p.price,p.currency)}</td><td>${date(p.observed_at)}</td><td>${esc(p.source_label)}<small>${esc(p.source)}</small></td></tr>`).join('') || '<tr><td colspan="4">Сбор еще не получил цены. Проверь статус источников.</td></tr>';
  $('#collector-status').innerHTML = state.collectors.map(s=>`<div class="card"><strong>${esc(s.name)}</strong><p>${statusText[s.state]||s.state}</p><p>${esc(s.error)}</p><p>Последний успех: ${s.last_success?date(s.last_success):'не было'}<br>Следующая попытка: ${s.next_attempt?date(new Date(s.next_attempt*1000).toISOString()):(s.supported?'при запуске':'не запланирована')}</p></div>`).join('');
  document.querySelectorAll('.auto-market-options').forEach(s=>options(s,markets.filter(m=>state.collectors.some(s=>s.name===m.name&&s.supported)).map(m=>option(m.id,m.name)).join('')));
  $('#saved-settings').innerHTML = state.payout_settings.map(s=>`<p>${esc(markets.find(m=>m.id===s.market_id).name)} · ${esc(s.method)} · продажа ${esc(s.sale_fee)}% · вывод ${esc(s.withdrawal_fee)}% + ${rub(s.fixed_fee)} · курс ${esc(s.rub_rate)} ₽/${esc(s.rate_currency)} · ${s.verified?'Подтверждено вручную':'Не подтверждено'}</p>`).join('');
  const kinds = {instant:'Заявка на выкуп', listing:'Цена выставления', estimate:'Оценка продажи'};
  const displayed=[...quotes,...state.automatic.filter(p=>!quotes.some(q=>q.automatic&&q.market_id===p.market_id)).map(p=>({...p,unknownFees:true}))];
  $('#quotes').innerHTML = displayed.length ? displayed.map(q => `<tr><td>${esc(q.name)}<small>${kinds[q.kind]}</small></td><td>${q.unknownFees?cash(q.price,q.currency):rub(q.price)}<small>${q.unknownFees?(q.rate_mismatch?'Измени курс для '+esc(q.currency):'Условия вывода не заданы'):`Продажа ${esc(q.sale_fee)}%; вывод ${esc(q.withdrawal_fee)}% + ${rub(q.fixed_fee)}`}</small></td><td>${q.unknownFees?'Настрой в разделе «Автосбор»':esc(q.method)}</td><td>${q.unknownFees?'—':rub(q.received)}</td><td>${q.unknownFees?'—':rub(q.own_share)}</td><td>${q.unknownFees?'—':rub(q.owner_share)}</td><td>${date(q.observed_at)}<small>${esc(q.source_label||'')}</small><small>${esc(q.source)}</small></td><td>${!q.cash?'Баланс Steam':q.excluded?'Исключена':q.stale?'Цена устарела':q.unknownFees?(q.rate_mismatch?'Другая валюта курса':'Нужны комиссии'):!q.verified?'Вывод не проверен':'Проверено вручную'}</td></tr>`).join('') : '';
  $('#quotes').innerHTML += markets.filter(m=>!displayed.some(q=>q.market_id===m.id)).map(m=>{const s=state.collectors.find(s=>s.name===m.name);return `<tr class="no-price"><td><a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">${esc(m.name)}</a></td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>${s&&s.last_attempt?date(s.last_attempt):'Цена еще не получена'}<small>${esc(s?.error||'')}</small></td><td>${m.excluded?'Исключена':statusText[s?.state]||'Нет данных'}</td></tr>`;}).join('');
  const instant = quotes.find(q => q.eligible && q.kind === 'instant');
  const listing = quotes.find(q => q.eligible && q.kind === 'listing');
  $('#best').textContent = instant ? `Лучшая актуальная заявка по введенным данным: ${instant.name}, ${rub(instant.received)} после вывода за ${$('#count').value} кейсов. Объем спроса не проверен.` : 'Для рекомендации нужны актуальная цена мгновенной продажи и проверенный способ вывода.';
  if(listing) $('#best').textContent += ` Лучшее предложение продавца: ${listing.name}, расчет ${rub(listing.received)}; продажа по этой цене не гарантирована.`;
  $('#batches').innerHTML = batches.map(b=>`<tr><td>#${b.id}</td><td>${esc(b.owner)}</td><td>${b.quantity}</td><td>${b.sold}</td><td>${b.remaining}</td><td>${rub(b.owed)}</td><td>${rub(b.paid)}</td><td>${rub(b.balance)}</td></tr>`).join('') || '<tr><td colspan="8">Партии еще не добавлены.</td></tr>';
  const own = sales.reduce((a,s)=>a+s.own_share,0), balance = batches.reduce((a,b)=>a+b.balance,0), stock = batches.reduce((a,b)=>a+b.remaining,0);
  $('#totals').innerHTML = [['Кейсов осталось',stock],['Твои 15% от полученных денег',rub(own)],['Осталось перевести владельцам',rub(balance)]].map(([k,v])=>`<div class="card"><strong>${k}</strong>${v}</div>`).join('');
  document.querySelectorAll('.market-names').forEach(s => options(s,markets.map(m=>option(m.name,m.name)).join('')));
  document.querySelectorAll('.sale-markets').forEach(s => options(s,markets.filter(m=>m.cash&&!m.excluded).map(m=>option(m.id,m.name)).join('')));
  document.querySelectorAll('.batch-options').forEach(s => options(s,batches.map(b=>option(b.id,`#${b.id} · ${b.owner} · осталось ${b.remaining}`)).join('')));
  $('#sales').innerHTML = sales.map(s=>`<div class="log">${date(s.created_at)} · #${s.batch_id} · ${esc(s.owner)} · ${esc(s.name)} · ${s.quantity} кейсов · Получено ${rub(s.received)} · Тебе ${rub(s.own_share)} · Владельцу ${rub(s.owner_share)}</div>`).join('') || '<p class="muted">Нет завершенных продаж.</p>';
  $('#payments').innerHTML = payments.map(p=>`<div class="log">${date(p.created_at)} · #${p.batch_id} · ${esc(p.owner)} · Переведено ${rub(p.amount)}</div>`).join('') || '<p class="muted">Нет переводов.</p>';
  $('#market-list').innerHTML = markets.map(m=>`<div class="card"><strong><a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">${esc(m.name)}</a></strong><p>${esc(m.note)}</p><button data-market="${m.id}" data-excluded="${m.excluded?0:1}">${m.excluded?'Вернуть в сравнение':'Исключить из продаж'}</button></div>`).join('');
  const selected = $('#history-market').value;
  $('#history-market').innerHTML = markets.map(m=>option(m.id,m.name)).join('');
  if(selected) $('#history-market').value = selected;
  chart();
}
function chart(){
  const currency=$('#history-currency').value;
  const rows = [...state.history.map(q=>({...q,currency:'RUB'})), ...state.auto_history.map(q=>({...q,method:'Автосбор'}))].filter(q=>q.market_id===Number($('#history-market').value)&&q.currency===currency).sort((a,b)=>Date.parse(a.observed_at)-Date.parse(b.observed_at));
  const canvas = $('#chart'), ctx = canvas.getContext('2d'), w=canvas.width,h=canvas.height;
  ctx.clearRect(0,0,w,h);ctx.font='14px sans-serif';ctx.fillStyle='#a2b0c7';
  $('#history-table').innerHTML = '<table><thead><tr><th>Время</th><th>Цена</th><th>Тип / вывод</th><th>Источник</th></tr></thead><tbody>' + rows.map(q=>`<tr><td>${date(q.observed_at)}</td><td>${cash(q.price,currency)}</td><td>${q.kind==='estimate'?'Оценка продажи':q.kind==='instant'?'Мгновенная':'Выставление'} / ${esc(q.method)}</td><td>${esc(q.source)}</td></tr>`).join('')+'</tbody></table>';
  if(!rows.length){ctx.fillText('История пока пуста',30,55);return;}
  const times=rows.map(q=>Date.parse(q.observed_at)),prices=rows.map(q=>q.price),tmin=Math.min(...times),tmax=Math.max(...times),pmin=Math.min(...prices),pmax=Math.max(...prices);
  const x=t=>80+(t-tmin)/(tmax-tmin||1)*(w-110),y=p=>h-65-(p-pmin)/(pmax-pmin||1)*(h-115);
  ctx.fillText(cash(pmax,currency),10,30);ctx.fillText(cash(pmin,currency),10,h-65);ctx.fillText(date(rows[0].observed_at),80,h-15);ctx.fillText(date(rows.at(-1).observed_at),w-280,h-15);
  const groups = new Map(); rows.forEach(q=>{const k=q.kind+' / '+q.method; if(!groups.has(k))groups.set(k,[]);groups.get(k).push(q);});
  const colors=['#69deb0','#91b9ff','#f5c478','#fa93b1','#cbadff'];let i=0;
  for(const [name,points] of groups){const color=colors[i%colors.length];ctx.strokeStyle=color;ctx.fillStyle=color;ctx.fillText(name,100+(i%3)*280,25+Math.floor(i/3)*18);ctx.beginPath();points.forEach((q,j)=>{const px=x(Date.parse(q.observed_at)),py=y(q.price);j?ctx.lineTo(px,py):ctx.moveTo(px,py);});ctx.stroke();points.forEach(q=>{ctx.beginPath();ctx.arc(x(Date.parse(q.observed_at)),y(q.price),4,0,Math.PI*2);ctx.fill();});i++;}
}
document.querySelectorAll('form[data-api]').forEach(form=>form.addEventListener('submit',async e=>{
  e.preventDefault();$('#error').textContent='';const button=form.querySelector('button');button.disabled=true;
  try{let data=Object.fromEntries(new FormData(form));if(form.dataset.api==='import')data={csv:await $('#csvfile').files[0].text()};await api(form.dataset.api,data);form.reset();await load();}catch(e){report(e);}finally{button.disabled=false;}
}));
document.querySelector('.auto-market-options').addEventListener('change',e=>{
  const price=state.automatic.find(p=>p.market_id===Number(e.target.value));
  if(price)e.target.form.elements.rate_currency.value=price.currency;
});
$('#market-list').addEventListener('click',async e=>{const b=e.target.closest('[data-market]');if(!b)return;b.disabled=true;try{await api('markets',{id:b.dataset.market,excluded:b.dataset.excluded});await load();}catch(e){report(e);b.disabled=false;}});
$('#count').addEventListener('change',()=>load().catch(report));$('#history-market').addEventListener('change',chart);load().catch(report);
$('#history-currency').addEventListener('change',chart);
$('#collect-now').addEventListener('click',async()=>{const b=$('#collect-now');b.disabled=true;try{await api('collect',{});$('#collect-message').textContent='Обновление запрошено. До следующей разрешенной попытки повторные запросы к API не отправляются.';await load();}catch(e){report(e);}finally{b.disabled=false;}});
setInterval(()=>{if(document.visibilityState==='visible')load().catch(report);},15000);
$('#logout').addEventListener('click',async()=>{try{await api('logout',{});location.assign('/login');}catch(e){report(e);}});
$('#theme-toggle').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;localStorage.setItem('theme',theme);});
