import {cents, decimal, rubles, localToday, escapeHTML as e} from './money.mjs';

import {shiftMonth, monthDays, paymentsByDay} from './calendar.mjs';
import {glyph, serviceFor} from './ui.mjs';

const $ = selector => document.querySelector(selector);
const state = {auth: '', view: 'payments', filter: 'open', archived:false, summary:null, month:localToday().slice(0,7), selectedDate:null, recurring: [], occurrences: [], history: [], loaded: false, busy: false};
const names = {pending:'Ожидает оплаты', partial:'Частично', paid:'Оплачено', skipped:'Пропущено', cancelled:'Отменено'};
const actionNames = {recurring_created:'Создано обязательство', recurring_edited:'Обязательство изменено', recurring_deleted:'Обязательство удалено из списка', tariff_edited:'Тариф изменён', expected_changed:'Уточнена сумма', due_date_changed:'Перенесён срок', status_changed:'Изменён статус', payment_registered:'Записана оплата', tariff_created:'Добавлен тариф', tariff_applied:'Применён тариф', recurring_stopped:'Обязательство остановлено'};
const frequencyNames = {days:'дн.', weeks:'нед.', months:'мес.', years:'г.'};
actionNames.payment_corrected='Оплата исправлена';actionNames.recurring_restored='Обязательство восстановлено';
actionNames.tariff_start_changed = 'Изменена дата начала тарифа';
const closed = o => ['paid', 'skipped', 'cancelled'].includes(o.status);
const recurring = id => state.recurring.find(r => r.id === id);
const title = o => recurring(o.recurring_id)?.title || 'Платёж';
const dateText = value => new Intl.DateTimeFormat('ru-RU', {day:'numeric', month:'short', year:'numeric'}).format(new Date(value + 'T12:00:00+08:00'));
const timeText = value => new Intl.DateTimeFormat('ru-RU', {timeZone:'Asia/Irkutsk', day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit'}).format(new Date(value));
function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('#toast').hidden=true, 4000); }
function errorText(detail) {
  if (Array.isArray(detail)) return detail.map(x => `${x.loc?.slice(1).join('.') || 'Поле'}: ${x.msg}`).join('; ');
  return typeof detail === 'string' ? detail : 'Не удалось выполнить запрос. Попробуй ещё раз.';
}
async function api(path, options={}) {
  let response;
  try { response=await fetch(path, {...options, credentials:'same-origin', headers:{Authorization:state.auth, 'Content-Type':'application/json', 'X-Platezhnik-Client':'web', ...options.headers}, cache:'no-store'}); }
  catch { $('#connection').hidden=false; throw new Error('Нет соединения. Сохрани введённые данные и повтори запрос.'); }
  if (response.status === 401) { showAuth('Сессия завершена. Получи новый код командой /login в боте или открой Mini App заново.'); throw new Error('Нужно войти заново'); }
  let data;
  try { data=await response.json(); } catch { throw new Error('Сервер временно недоступен. Попробуй ещё раз.'); }
  if (!response.ok) throw new Error(errorText(data.detail));
  $('#connection').hidden=true;
  return data;
}
async function all(path) {
  const rows=[];
  for (let offset=0;;offset+=500) {
    const page=await api(`${path}${path.includes('?')?'&':'?'}limit=500&offset=${offset}`);
    rows.push(...page);
    if (page.length<500) return rows;
  }
}
function showAuth(message) {
  document.body.classList.add('auth-screen');
  $('#browser-session-bar').hidden=true; $('#mobile-nav').hidden=true;
  $('#workspace').hidden=true; $('#auth-panel').hidden=false; $('#create-button').hidden=true;
  if(message) $('#auth-text').textContent=message;
}
async function refresh() {
  if(state.busy) return;
  state.busy=true; $('#refresh').disabled=true;
  try {
    await api('/settings');
    const [rs, os, summary]=await Promise.all([all('/recurring?include_deleted=true'), all('/occurrences'), api('/summary')]);
    state.summary=summary;
    state.recurring=rs; state.occurrences=os;
    if(state.view==='history') state.history=await all('/audit');
    document.body.classList.remove('auth-screen'); $('#mobile-nav').hidden=false; state.loaded=true; $('#workspace').hidden=false; $('#auth-panel').hidden=true; $('#create-button').hidden=false;
    render();
  } finally { state.busy=false; $('#refresh').disabled=false; }
}
function badge(o) {
  const overdue=o.due_date<localToday() && !closed(o);
  return `<span class="badge ${overdue?'overdue':e(o.status)}">${overdue?'Просрочен':names[o.status]}</span>`;
}
function icon(r) {
  const service=serviceFor(r?.title||'');
  const mark=service?.mark||r?.title?.slice(0,2).toUpperCase()||'₽';
  return `<div class="category-icon ${service?'brand-icon':'icon-missing'}">${service?`<img src="/assets/brands/${service.file}" alt="" loading="lazy">`:''}<span class="brand-fallback">${e(mark)}</span></div>`;
}
function bindIcons(root=document){root.querySelectorAll('.brand-icon img').forEach(img=>{const fail=()=>img.parentElement.classList.add('icon-missing');img.addEventListener('error',fail);if(img.complete&&!img.naturalWidth)fail();});}
function management(r){return r&&!r.deleted_at?`<button class="button compact" data-edit="${r.id}">${glyph('edit')}Изменить</button><button class="button compact danger" data-delete="${r.id}">${glyph('trash')}В архив</button>`:'';}
async function openRecurringEdit(r){
  modal('Изменить обязательство','<p class="skeleton">Загружаю настройки…</p>');
  try{const tariffs=await api(`/recurring/${r.id}/tariffs`);if($('#modal').open)editRecurringDialog(r,tariffs);}catch(error){toast(error.message);$('#modal').close();}
}
function bindManagement(root){
  root.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>openRecurringEdit(recurring(b.dataset.edit)));
  root.querySelectorAll('[data-restore]').forEach(b=>b.onclick=()=>restoreDialog(recurring(b.dataset.restore)));
  root.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>deleteRecurringDialog(recurring(b.dataset.delete)));
  bindIcons(root);
}
function render() {
  const today=localToday();
  const outstanding=state.occurrences.filter(o=>!closed(o));
  const totals=state.summary;
  $('#remaining-total').textContent=rubles(totals.remaining_due);
  $('#month-total').textContent=rubles(totals.expected_due);$('#month-count').textContent=`Начислений: ${totals.count}`;
  $('#remaining-caption').textContent=`По срокам месяца · оплачено ${rubles(totals.paid_for_due)}`;
  $('#paid-total').textContent=rubles(totals.cash_paid);
  $('#overdue-total').textContent=rubles(totals.old_debt);
  $('#overdue-total').classList.toggle('has-debt',cents(totals.old_debt)>0n);
  $('#overdue-caption').textContent=`Срок до начала месяца · просрочено всего ${rubles(totals.overdue)}`;
  const nearest=[...outstanding].sort((a,b)=>a.due_date.localeCompare(b.due_date))[0];
  $('#next-panel').hidden=!nearest || state.view!=='payments';
  if(nearest) {
    $('#next-title').textContent=title(nearest);$('#next-icon').innerHTML=icon(recurring(nearest.recurring_id));
    $('#next-date').textContent=`${nearest.due_date<today?'Срок прошёл':'Срок оплаты'} · ${dateText(nearest.due_date)}`;
    $('#next-amount').textContent=rubles(nearest.remaining_amount);$('#next-pay').onclick=()=>paymentDialog(nearest);
  }
  for(const id of ['stats','quick-actions','payment-filters'])$('#'+id).hidden=state.view!=='payments';
  $('#calendar-panel').hidden=state.view!=='calendar';$('#obligation-filters').hidden=state.view!=='subscriptions';
  $('#page-title').textContent={payments:'Мои платежи',calendar:'Календарь платежей',subscriptions:'Обязательства',history:'История'}[state.view];
  $('#page-subtitle').textContent={payments:'Подписки, счета и важные даты.',calendar:'Все сроки — в одном месяце.',subscriptions:'Названия, суммы и расписания.',history:'Изменения и оплаты в одном месте.'}[state.view];
  $('#list-heading').textContent={payments:'Платежи месяца',calendar:state.selectedDate?dateText(state.selectedDate):'Платежи месяца',subscriptions:state.archived?'Архив обязательств':'Твои обязательства',history:'Лента событий'}[state.view];
  document.querySelectorAll('.nav, .mobile-nav [data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
  if(state.view==='calendar')renderCalendar();
  renderList();bindIcons();
}
function renderCalendar(){
  const days=paymentsByDay(state.occurrences),today=localToday();
  $('#calendar-month').textContent=new Intl.DateTimeFormat('ru-RU',{month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(state.month+'-01T12:00:00Z'));
  $('#calendar-grid').innerHTML=monthDays(state.month).map(d=>{
    const rows=days.get(d.date)||[],overdue=rows.some(o=>!closed(o)&&d.date<today),paid=rows.length&&rows.every(o=>o.status==='paid');
    return `<button class="calendar-day ${d.current?'':'outside'} ${d.date===today?'today':''} ${d.date===state.selectedDate?'selected':''} ${rows.length?'has-events':''}" data-day="${d.date}" aria-pressed="${d.date===state.selectedDate}" aria-label="${dateText(d.date)}, платежей: ${rows.length}"><span>${d.day}</span>${rows.length?`<span class="day-marker ${overdue?'overdue-marker':paid?'paid-marker':''}">${rows.length}</span>`:''}</button>`;
  }).join('');
  $('#calendar-grid').querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{state.selectedDate=b.dataset.day;state.month=state.selectedDate.slice(0,7);render();});
  const rows=state.occurrences.filter(o=>o.due_date.startsWith(state.month)&&!['cancelled','skipped'].includes(o.status));
  $('#calendar-summary').textContent=`${rows.length} платежей · осталось ${rubles(rows.reduce((a,o)=>a+cents(o.remaining_amount),0n))}`;
}
function renderList() {
  const search=$('#search').value.toLocaleLowerCase('ru-RU').trim(),list=$('#list');list.className='list';
  if(state.view==='subscriptions') {
    const rows=state.recurring.filter(r=>Boolean(r.deleted_at)===state.archived&&r.title.toLocaleLowerCase('ru-RU').includes(search));
    $('#list-count').textContent=rows.length;list.classList.add('sub-list');
    list.innerHTML=rows.map(r=>`<article class="payment-row"><div class="payment-name">${icon(r)}<div><button class="card-title-button" data-sub="${r.id}">${e(r.title)}</button><small>${r.variable?'Переменная сумма':'Фиксированная сумма'}</small></div></div><div class="card-price"><span class="badge">${r.deleted_at?'В архиве':r.active?'Активно':'Остановлено'}</span></div><div class="card-meta"><span>${glyph('calendar')}Раз в ${r.interval} ${frequencyNames[r.frequency]}</span><span>С ${dateText(r.anchor_date)}</span></div><div class="card-actions">${r.deleted_at?`<button class="button compact" data-restore="${r.id}">${glyph('refresh')}Восстановить</button>`:management(r)}<button class="detail-button" data-sub="${r.id}">Тарифы ${glyph('chevron')}</button></div></article>`).join('');
    list.querySelectorAll('[data-sub]').forEach(b=>b.onclick=()=>subscriptionDialog(recurring(b.dataset.sub)));
    bindManagement(list);empty(rows.length,state.archived?'Архив пуст':'Нет обязательств',state.archived?'Здесь будут обязательства, убранные из основного списка. Их можно восстановить.':'Добавь подписку или регулярный платёж.');return;
  }
  if(state.view==='history') {
    const rows=[...state.history].reverse().filter(x=>`${actionNames[x.action]||x.action} ${entityTitle(x.entity_id)}`.toLocaleLowerCase('ru-RU').includes(search));
    list.classList.add('history-list');list.innerHTML=rows.map(historyItem).join('');$('#list-count').textContent=rows.length;
    empty(rows.length,'История пока пуста','Изменения и записанные оплаты появятся здесь.');return;
  }
  const now=localToday(),period=$('#period').value,month=period==='next'?shiftMonth(now.slice(0,7),1):now.slice(0,7);
  const rows=state.occurrences.filter(o=>{
    if(!title(o).toLocaleLowerCase('ru-RU').includes(search))return false;
    if(state.view==='calendar')return !['cancelled','skipped'].includes(o.status)&&(state.selectedDate?o.due_date===state.selectedDate:o.due_date.startsWith(state.month));
    if(period!=='all'&&!o.due_date.startsWith(month))return false;
    if(state.filter==='open')return !closed(o);
    if(state.filter==='overdue')return !closed(o)&&o.due_date<now;
    if(state.filter==='paid')return o.status==='paid';return true;
  }).sort((a,b)=>a.due_date.localeCompare(b.due_date));
  $('#list-count').textContent=rows.length;
  list.innerHTML=rows.map(o=>`<article class="payment-row"><div class="payment-name">${icon(recurring(o.recurring_id))}<div><button class="card-title-button" data-detail="${o.id}">${e(title(o))}</button><small>${recurring(o.recurring_id)?.variable?'Переменная сумма':'Регулярный платёж'}</small></div></div><div class="card-price"><strong>${rubles(o.status==='paid'?o.expected_amount:o.remaining_amount)}</strong><small>${o.status==='paid'?'Оплачено':'К оплате'}</small></div><div class="card-meta"><span>${glyph('calendar')}${dateText(o.due_date)}</span>${badge(o)}${cents(o.paid_amount)>0n&&o.status!=='paid'?`<span>Внесено ${rubles(o.paid_amount)}</span>`:''}</div><div class="card-actions">${!closed(o)?`<button class="button subtle compact" data-pay="${o.id}">Оплатить</button>`:''}<button class="detail-button" data-detail="${o.id}">Детали ${glyph('chevron')}</button></div></article>`).join('');
  list.querySelectorAll('[data-pay]').forEach(b=>b.onclick=()=>paymentDialog(state.occurrences.find(o=>o.id===b.dataset.pay)));
  list.querySelectorAll('[data-detail]').forEach(b=>b.onclick=()=>detailDialog(state.occurrences.find(o=>o.id===b.dataset.detail)));
  bindManagement(list);empty(rows.length,'На эти даты платежей нет','Выбери другой день, период или добавь обязательство.');
}
function empty(count, heading, message) { $('#empty').hidden=!!count; $('#empty h3').textContent=heading; $('#empty p').textContent=message; }
function entityTitle(id) { const o=state.occurrences.find(o=>o.id===id); return o?title(o):recurring(id)?.title||'Платёж'; }
function historyItem(x) {
  const d=x.details; let info='';
  if(x.action==='payment_corrected')info=`${rubles(d.old_amount)} → ${rubles(d.new_amount)} · ${dateText(d.old_paid_on)} → ${dateText(d.new_paid_on)} · ${d.reason}`;
  else if(x.action==='recurring_restored')info=`Возобновлено с ${dateText(d.first_date)}`;
  else if(x.action==='recurring_edited') info=`${d.new.title} · с ${dateText(d.new.anchor_date)} · каждые ${d.new.interval} ${frequencyNames[d.new.frequency]}`;
  else if(x.action==='tariff_start_changed') info=`${dateText(d.old)} → ${dateText(d.new)}`;
  else if(x.action==='expected_changed'||x.action==='tariff_applied') info=`${rubles(d.old)} → ${rubles(d.new)}`;
  else if(x.action==='due_date_changed') info=`${dateText(d.old)} → ${dateText(d.new)}`;
  else if(d.amount) info=rubles(d.amount)+(d.effective_from?` с ${dateText(d.effective_from)}`:'');
  else if(d.new) info=names[d.new]||d.new;
  return `<article class="history-item"><b>${e(actionNames[x.action]||x.action)}</b> · ${e(entityTitle(x.entity_id))}${info?`<p>${e(info)}</p>`:''}<small>${timeText(x.created_at)} · Иркутск</small></article>`;
}
function modal(title, body) {
  $('#dialog-title').textContent=title; $('#dialog-body').innerHTML=body;
  if(!$('#modal').open) $('#modal').showModal();
  $('#dialog-body').scrollTop=0;
  const input=$('#dialog-body input[name=title]');
  if(input){const hint=document.createElement('div');hint.className='brand-hint';input.closest('label').after(hint);const update=()=>{const service=serviceFor(input.value);hint.innerHTML=icon({title:input.value})+`<span>${service?e(service.label):'Значок по первым буквам'}<small>Определяется по названию · без внешних запросов</small></span>`;bindIcons(hint);};input.addEventListener('input',update);update();}
}
function bindForm(fn) {
  const form=$('#dialog-body form');
  form.addEventListener('submit', async event=>{
    event.preventDefault(); const button=form.querySelector('[type=submit]'); if(button.disabled) return;
    button.disabled=true; form.querySelector('.error')?.remove();
    try { await fn(new FormData(form)); }
    catch(error) { const p=document.createElement('p'); p.className='error'; p.setAttribute('role','alert'); p.textContent=error.message; form.append(p); }
    finally {button.disabled=false;}
  });
}
const moneyInput=(name,value='')=>`<input name="${name}" type="text" inputmode="decimal" pattern="[0-9]+([.,][0-9]{1,2})?" value="${e(value)}" placeholder="0,00" required autocomplete="off">`;
async function saved(message) { $('#modal').close(); toast(message); await refresh(); }
function createDialog() {
  modal('Новое обязательство',`<form><div class="form-grid"><label class="wide">Название<input name="title" maxlength="200" placeholder="Например, ChatGPT Plus или МТС" required autofocus></label><label>Сумма, ₽${moneyInput('amount')}</label><label>Первая дата<input name="anchor_date" type="date" value="${localToday()}" min="2000-01-01" max="2200-12-31" required></label><label>Повторять каждые<input name="interval" type="number" value="1" min="1" max="3660" required></label><label>Период<select name="frequency"><option value="months">Месяц</option><option value="weeks">Неделю</option><option value="years">Год</option><option value="days">День</option></select></label></div><label class="check-label"><input type="checkbox" name="variable">Сумма может меняться от платежа к платежу</label><p class="help">Если начальная дата в прошлом, начисления с неё тоже будут созданы. Напоминания: за 3 дня, за день и в день оплаты, в 11:00 по Иркутску.</p><div class="form-actions"><button type="submit" class="button primary">Добавить платёж</button></div></form>`);
  bindForm(async f=>{
    const amount=decimal(f.get('amount')); if(cents(amount)===0n) throw new Error('Сумма должна быть больше нуля');
    await api('/recurring',{method:'POST',body:JSON.stringify({title:f.get('title').trim(),amount,anchor_date:f.get('anchor_date'),frequency:f.get('frequency'),interval:Number(f.get('interval')),variable:f.has('variable')})});
    await saved('Платёж добавлен');
  });
}
function paymentDialog(o) {
  const r=recurring(o.recurring_id);
  modal('Записать оплату',`<form><div class="notice"><strong>${e(title(o))}</strong><br>Срок: ${dateText(o.due_date)} · Уже внесено ${rubles(o.paid_amount)}<br>Осталось <strong>${rubles(o.remaining_amount)}</strong></div><div class="form-grid"><label>Внесённая сумма, ₽${moneyInput('amount',decimal(o.remaining_amount))}</label><label>Дата фактической оплаты<input name="paid_on" type="date" min="2000-01-01" max="${localToday()}" value="${localToday()}" required></label></div>${r?.variable?`<label class="check-label"><input type="checkbox" id="revise-toggle">Уточнить общую сумму начисления</label><label id="revised-field" hidden>Новая общая сумма, ₽${moneyInput('revised',decimal(o.expected_amount))}</label>`:''}<p class="help">Это отметка об уже совершённой оплате. Деньги не списываются. Можно внести часть суммы; переплата не допускается.</p><div class="form-summary"><span>Остаток после оплаты</span><strong id="payment-remaining">—</strong></div><div class="form-actions"><button class="button primary" type="submit">Подтвердить оплату</button></div></form>`);
  let key=crypto.randomUUID(), fingerprint='';
  if($('[name=revised]')) $('[name=revised]').disabled=true;
  const update=()=>{try{const revised=$('#revise-toggle')?.checked; const expected=revised?cents($('[name=revised]').value):cents(o.expected_amount); const left=expected-cents(o.paid_amount)-cents($('[name=amount]').value); $('#payment-remaining').textContent=left<0n?'Переплата запрещена':rubles(left);}catch{$('#payment-remaining').textContent='Проверь суммы';}};
  $('#revise-toggle')?.addEventListener('change',event=>{ $('#revised-field').hidden=!event.target.checked; $('[name=revised]').disabled=!event.target.checked; update(); });
  $('#dialog-body form').addEventListener('input',update); update();
  bindForm(async f=>{
    const amount=decimal(f.get('amount')), revised=$('#revise-toggle')?.checked?decimal(f.get('revised')):null;
    if(cents(amount)===0n) throw new Error('Оплата должна быть больше нуля');
    const body={amount,paid_on:f.get('paid_on'),...(revised?{revised_expected_amount:revised}:{})};
    if(fingerprint && fingerprint!==JSON.stringify(body)) key=crypto.randomUUID();
    fingerprint=JSON.stringify(body);
    await api(`/occurrences/${o.id}/payments`,{method:'POST',body:JSON.stringify({...body,idempotency_key:key})});
    await saved('Оплата записана');
  });
}
async function detailDialog(o) {
  modal(title(o),'<p class="skeleton">Загружаю историю…</p>');
  try {
    const [history,payments,jobs]=await Promise.all([api(`/audit?entity_id=${o.id}&limit=500`),api(`/occurrences/${o.id}/payments`),api(`/occurrences/${o.id}/notifications`)]);
    if(!$('#modal').open) return;
    modal(title(o),`<div class="detail-top"><strong>${rubles(o.remaining_amount)}</strong>${badge(o)}</div><div class="notice">Начислено: <strong>${rubles(o.expected_amount)}</strong><br>Оплачено: <strong>${rubles(o.paid_amount)}</strong><br>Срок: ${dateText(o.due_date)}<br>Исходная дата: ${dateText(o.scheduled_date)}</div>${!closed(o)?`<div class="detail-actions"><button id="detail-pay" class="button primary">Записать оплату</button><button id="edit-occurrence" class="button">Изменить начисление</button></div>`:''}<p class="section-label">ОПЛАТЫ</p>${payments.map(p=>`<div class="history-item"><b>${p.cancelled?'Отменена · ':''}${rubles(p.amount)}</b><small>Дата оплаты: ${dateText(p.paid_on)} · записано ${timeText(p.created_at)}</small>${p.revision?`<small>Первоначально: ${rubles(p.original_amount)} · ${dateText(p.original_paid_on)} · исправлений: ${p.revision}</small>`:''}<button class="button compact" data-correct="${p.id}">Исправить оплату</button></div>`).join('')||'<p class="help">Оплат пока нет</p>'}<p class="section-label">ИСТОРИЯ ИЗМЕНЕНИЙ</p>${history.map(historyItem).join('')||'<p class="help">Изменений пока нет</p>'}<p class="section-label">НАПОМИНАНИЯ</p><p class="help">${jobs.filter(j=>j.status==='sent').length} отправлено · ${jobs.filter(j=>j.status==='pending'&&j.due_snapshot===o.due_date).length} в очереди. Очередь обновляется фоновым обработчиком.</p>`);
    $('#dialog-body').querySelectorAll('[data-correct]').forEach(b=>b.onclick=()=>correctionDialog(payments.find(p=>p.id===b.dataset.correct),o));
    const r=recurring(o.recurring_id);if(r&&!r.deleted_at){const actions=document.createElement('div');actions.className='management-actions';actions.innerHTML='<p class="help">Название и расписание всего обязательства</p>'+management(r);$('#dialog-body').prepend(actions);bindManagement(actions);}
    $('#detail-pay')?.addEventListener('click',()=>paymentDialog(o)); $('#edit-occurrence')?.addEventListener('click',()=>editDialog(o));
  } catch(error) { modal('Не удалось загрузить',`<p class="error">${e(error.message)}</p>`); }
}
function editDialog(o) {
  modal('Изменить начисление',`<form><div class="notice">${e(title(o))} · оплачено ${rubles(o.paid_amount)}</div><div class="form-grid"><label ${recurring(o.recurring_id)?.variable?'':'class="wide"'}>Срок оплаты<input name="due_date" type="date" value="${o.due_date}" min="2000-01-01" max="2200-12-31" required></label>${recurring(o.recurring_id)?.variable?`<label>Общая сумма, ₽${moneyInput('expected_amount',decimal(o.expected_amount))}</label>`:''}</div>${cents(o.paid_amount)===0n?'<label class="wide">Действие<select name="status"><option value="">Оставить открытым</option value="skipped">Пропустить этот платёж</option><option value="cancelled">Отменить это начисление</option></select></label>':''}<p class="help">Изменения относятся только к этому начислению. Они сохранятся в истории.</p><div class="form-actions"><button class="button primary" type="submit">Сохранить</button></div></form>`);
  bindForm(async f=>{
    const body={due_date:f.get('due_date')};
    if(f.has('expected_amount')&&decimal(f.get('expected_amount'))!==decimal(o.expected_amount)) body.expected_amount=decimal(f.get('expected_amount'));
    if(f.get('status')) body.status=f.get('status');
    await api(`/occurrences/${o.id}`,{method:'PATCH',body:JSON.stringify(body)}); await saved('Начисление обновлено');
  });
}
async function subscriptionDialog(r) {
  modal(r.title,'<p class="skeleton">Загружаю тарифы…</p>');
  try {
    const tariffs=await api(`/recurring/${r.id}/tariffs`);
    if(!$('#modal').open) return;
    modal(r.title,`<div class="notice">Раз в ${r.interval} ${frequencyNames[r.frequency]} · с ${dateText(r.anchor_date)}<br>${r.variable?'Переменная':'Фиксированная'} сумма · ${r.active?'Обязательство активно':'Обязательство остановлено'}</div><p class="section-label">ТАРИФЫ</p>${tariffs.map(t=>`<article class="history-item"><b>${rubles(t.amount)}</b><small>С ${dateText(t.effective_from)}</small></article>`).join('')}${r.active&&!r.deleted_at?'<div class="detail-actions"><button class="button primary" id="add-tariff">Новый тариф</button><button class="button danger" id="stop-recurring">Остановить</button></div><p class="help">Новый тариф не меняет уже оплаченные или вручную уточнённые начисления. Расписание и название можно изменить кнопкой «Редактировать обязательство».</p>':''}`);
    $('#add-tariff')?.addEventListener('click',()=>tariffDialog(r)); $('#stop-recurring')?.addEventListener('click',()=>stopDialog(r));
    const actions=document.createElement('div'); actions.className='detail-actions';
    if(r.deleted_at){const restore=document.createElement('button');restore.className='button primary';restore.textContent='Восстановить из архива';restore.onclick=()=>restoreDialog(r);actions.append(restore);$('#dialog-body').append(actions);return;}
    const edit=document.createElement('button'); edit.className='button'; edit.textContent='Редактировать обязательство'; edit.onclick=()=>editRecurringDialog(r,tariffs);
    const remove=document.createElement('button'); remove.className='button danger'; remove.textContent='Удалить обязательство'; remove.onclick=()=>deleteRecurringDialog(r);
    actions.append(edit,remove); $('#dialog-body').append(actions);
  } catch(error) { modal('Не удалось загрузить',`<p class="error">${e(error.message)}</p>`); }
}
function tariffDialog(r) {
  modal('Новый тариф',`<form><div class="notice">${e(r.title)}<br>Изменится сумма только нетронутых будущих начислений с выбранной даты. Две версии на одну дату не допускаются.</div><div class="form-grid"><label>Сумма, ₽${moneyInput('amount')}</label><label>Действует с<input type="date" name="effective_from" min="${localToday()>r.anchor_date?localToday():r.anchor_date}" required></label></div><div class="form-actions"><button class="button primary" type="submit">Добавить тариф</button></div></form>`);
  bindForm(async f=>{await api(`/recurring/${r.id}/tariffs`,{method:'POST',body:JSON.stringify({amount:decimal(f.get('amount')),effective_from:f.get('effective_from')})}); await saved('Тариф добавлен');});
}
function stopDialog(r) {
  modal('Остановить обязательство?',`<form><div class="notice"><strong>${e(r.title)}</strong><br>Новые начисления больше не будут создаваться. Оплаты и история сохранятся, старые долги можно будет доплатить.</div><label class="check-label"><input type="checkbox" name="cancel" checked>Отменить будущие начисления без оплат</label><div class="form-actions"><button class="button danger" type="submit">Остановить обязательство</button></div></form>`);
  bindForm(async f=>{await api(`/recurring/${r.id}/stop`,{method:'POST',body:JSON.stringify({cancel_future_unpaid:f.has('cancel')})}); await saved('Обязательство остановлено');});
}

function editRecurringDialog(r, tariffs) {
  const effective=localToday()>r.anchor_date?localToday():r.anchor_date;
  const current=[...tariffs].reverse().find(t=>t.effective_from<=effective);
  modal('Изменить обязательство',`<form><div class="form-grid"><label class="wide">Название<input name="title" value="${e(r.title)}" maxlength="200" required></label><label>Текущий тариф, ₽${moneyInput('amount',current?.amount||'')}</label><label>Первая дата расписания<input type="date" name="anchor_date" value="${r.anchor_date}" min="2000-01-01" max="2200-12-31" required></label><label>Повторять каждые<input name="interval" type="number" min="1" max="3660" value="${r.interval}" required></label><label>Период<select name="frequency">${Object.entries({months:'Месяц',weeks:'Неделю',years:'Год',days:'День'}).map(([k,v])=>`<option value="${k}" ${r.frequency===k?'selected':''}>${v}</option>`).join('')}</select></label></div><label class="check-label"><input type="checkbox" name="variable" ${r.variable?'checked':''}>Сумма может меняться</label><p class="help">Тариф применяется с сегодняшнего дня или первой даты будущего обязательства. Оплаченные, частичные и вручную уточнённые суммы сохраняются. Если меняешь дату или период, укажи новую первую дату не раньше сегодня: будущие неоплаченные начисления будут пересозданы, их отдельные корректировки останутся только в истории.</p><div class="form-actions"><button class="button primary" type="submit">Сохранить изменения</button></div></form>`);
  bindForm(async f=>{
    const body={title:f.get('title').trim(),variable:f.has('variable')};
    if(decimal(f.get('amount'))!==decimal(current.amount))body.amount=decimal(f.get('amount'));
    for(const k of ['anchor_date','frequency'])if(f.get(k)!==r[k])body[k]=f.get(k);
    if(Number(f.get('interval'))!==r.interval)body.interval=Number(f.get('interval'));
    await api(`/recurring/${r.id}`,{method:'PATCH',body:JSON.stringify(body)});await saved('Обязательство обновлено');
  });
}

function deleteRecurringDialog(r) {
  modal('Перенести в архив?',`<form><div class="notice"><strong>${e(r.title)}</strong><br>Обязательство будет перенесено в архив. Его можно восстановить с выбранной даты. Неоплаченные начисления будут отменены. Оплаты и история сохранятся; частично оплаченные начисления останутся в платежах, чтобы их можно было доплатить.</div><div class="form-actions"><button class="button danger" type="submit">Перенести в архив</button></div></form>`);
  bindForm(async()=>{await api(`/recurring/${r.id}`,{method:'DELETE'});await saved('Обязательство перенесено в архив');});
}
function restoreDialog(r){
  const first=localToday()>r.anchor_date?localToday():r.anchor_date;
  modal('Восстановить обязательство',`<form><div class="notice"><strong>${e(r.title)}</strong><br>Расписание возобновится с выбранной даты. Ранее отменённые начисления останутся в истории. Сохранённые частичные оплаты можно доплатить.</div><label>Первая дата нового расписания<input name="first_date" type="date" min="${first}" max="2200-12-31" value="${first}" required></label><div class="form-actions"><button class="button primary" type="submit">Восстановить</button></div></form>`);
  bindForm(async f=>{await api(`/recurring/${r.id}/restore`,{method:'POST',body:JSON.stringify({first_date:f.get('first_date')})});state.archived=false;document.querySelectorAll('[data-archive]').forEach(b=>b.classList.toggle('active',b.dataset.archive==='active'));await saved('Обязательство восстановлено');});
}
function correctionDialog(p,o){
  modal('Исправить оплату',`<form><div class="notice"><strong>${e(title(o))}</strong><br>Исходная запись и все исправления сохранятся. Это изменение журнала, возврат денег не выполняется.<br>Другие оплаты этого начисления: ${rubles(cents(o.paid_amount)-cents(p.amount))}</div><div class="form-grid"><label>Правильная сумма, ₽<input name="amount" type="text" inputmode="decimal" pattern="[0-9]+([.,][0-9]{1,2})?" value="${e(decimal(p.amount))}" required></label><label>Дата фактической оплаты<input name="paid_on" type="date" min="2000-01-01" max="${localToday()}" value="${p.paid_on}" required></label><label class="wide">Причина исправления<input name="reason" maxlength="500" placeholder="Например, ошибся в сумме" required></label></div><p class="help">Нулевая сумма отменяет ошибочную отметку оплаты. Остаток и месячные итоги будут пересчитаны. Переплата запрещена.</p><div class="form-actions"><button id="void-payment" class="button danger" type="button">Отменить отметку</button><button class="button primary" type="submit">Сохранить исправление</button></div></form>`);
  $('#void-payment').onclick=()=>{$('#dialog-body [name=amount]').value='0.00';if(!$('#dialog-body [name=reason]').value)$('#dialog-body [name=reason]').value='Ошибочная отметка оплаты';toast('Проверь причину и нажми «Сохранить исправление»');};
  let key=crypto.randomUUID(),fingerprint='';
  bindForm(async f=>{const body={amount:decimal(f.get('amount')),paid_on:f.get('paid_on'),reason:f.get('reason').trim(),expected_revision:p.revision};const next=JSON.stringify(body);if(fingerprint&&fingerprint!==next)key=crypto.randomUUID();fingerprint=next;await api(`/payments/${p.id}/corrections`,{method:'POST',body:JSON.stringify({...body,idempotency_key:key})});await saved('Оплата исправлена, остатки пересчитаны');});
}
document.querySelectorAll('[data-archive]').forEach(b=>b.onclick=()=>{state.archived=b.dataset.archive==='archived';document.querySelectorAll('[data-archive]').forEach(el=>el.classList.toggle('active',el===b));render();});
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',async()=>{
  state.view=button.dataset.view; window.scrollTo({top:0,behavior:'instant'});
  if(!state.loaded) return;
  try { if(state.view==='history') state.history=await all('/audit'); render(); } catch(error){toast(error.message);}
}));
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{state.filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b===button));renderList();}));
$('#search').addEventListener('input',()=>{if(state.loaded)renderList();}); $('#period').addEventListener('change',renderList);
for(const id of ['create-button','quick-add','mobile-add'])$('#'+id).addEventListener('click',createDialog); $('#close-modal').addEventListener('click',()=>$('#modal').close());
$('#modal').addEventListener('click',event=>{if(event.target===$('#modal')){const r=$('#modal').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('#modal').close();}});
$('#refresh').addEventListener('click',()=>refresh().catch(error=>toast(error.message)));
async function logout(){
  try{await api('/auth/logout',{method:'POST'});}catch(error){toast(error.message);return;}
  state.auth='';state.loaded=false;state.recurring=[];state.occurrences=[];state.history=[];showAuth();
}
$('#logout').addEventListener('click',logout); $('#browser-logout').addEventListener('click',logout);
$('#browser-login-form').addEventListener('submit',async event=>{
  event.preventDefault(); const button=event.currentTarget.querySelector('button');button.disabled=true;
  try{
    const response=await fetch('/auth/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Platezhnik-Client':'web'},body:JSON.stringify({code:$('#browser-code').value})});
    const data=await response.json();if(!response.ok)throw new Error(errorText(data.detail));
    $('#browser-code').value='';state.auth='';await refresh();$('#browser-session-bar').hidden=false;
  }catch(error){toast(error.message);}finally{button.disabled=false;}
});
$('#login-form').addEventListener('submit',async event=>{event.preventDefault();state.auth='Bearer '+$('#local-token').value;$('#local-token').value='';try{await refresh();}catch(error){toast(error.message);}});
window.addEventListener('online',()=>{if(state.loaded) refresh().catch(()=>{});}); window.addEventListener('offline',()=>$('#connection').hidden=false);
for(const el of document.querySelectorAll('[data-icon]'))el.innerHTML=glyph(el.dataset.icon);
for(const [id,delta] of [['calendar-prev',-1],['calendar-next',1]])$('#'+id).onclick=()=>{state.month=shiftMonth(state.month,delta);state.selectedDate=null;render();};
$('#calendar-today').onclick=()=>{state.selectedDate=localToday();state.month=state.selectedDate.slice(0,7);render();};
$('#calendar-all').onclick=()=>{state.selectedDate=null;render();};
async function telegramWebApp() {
  if(window.Telegram?.WebApp?.initData)return window.Telegram.WebApp;
  // Ordinary browsers never need the external SDK. Only a Telegram launch loads it.
  if(!new URLSearchParams(location.hash.slice(1)).has('tgWebAppData'))return null;
  return new Promise(resolve=>{
    const script=document.createElement('script');script.src='https://telegram.org/js/telegram-web-app.js';script.async=true;
    const timer=setTimeout(()=>resolve(null),5000);
    script.onload=()=>{clearTimeout(timer);resolve(window.Telegram?.WebApp||null);};
    script.onerror=()=>{clearTimeout(timer);resolve(null);};document.head.append(script);
  });
}
async function init() {
  $('#create-button').hidden=true;
  $('#today-label').textContent=new Intl.DateTimeFormat('ru-RU',{timeZone:'Asia/Irkutsk',weekday:'long',day:'numeric',month:'long'}).format(new Date()).toLocaleUpperCase('ru-RU')+' · ИРКУТСК';
  const telegramReady=telegramWebApp();
  const response=await fetch('/client-config',{cache:'no-store'}); if(!response.ok)throw new Error('Не удалось подключиться к приложению');
  const config=await response.json();
  const tg=config.auth_mode==='telegram'?await telegramReady:null;
  if(config.auth_mode==='telegram' && tg?.initData) {tg.ready();tg.expand();tg.setHeaderColor('#101012');tg.setBackgroundColor('#0c0c0e');state.auth='tma '+tg.initData;await refresh();}
  else {
    if(config.auth_mode==='local') {$('#browser-login-form').hidden=true;$('#auth-title').textContent='Твоё личное пространство';$('#auth-text').textContent='Введи локальный токен из .env. Он останется только в памяти этой вкладки.';$('#login-form').hidden=false;$('#logout').hidden=false;}
    else {$('#auth-title').textContent='Вход в Платёжник';$('#auth-text').textContent='Войди по одноразовому коду из Telegram.';$('#browser-login-form').hidden=false;}
    showAuth();
    const session=await fetch('/settings',{credentials:'same-origin',cache:'no-store'});
    if(session.ok){await refresh();$('#browser-session-bar').hidden=false;return;}
    showAuth();
  }
}
init().catch(error=>{showAuth(error.message);});
