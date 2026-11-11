import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cents, decimal, rubles, localToday, escapeHTML} from './backend/app/static/money.mjs';

test('копейки без ошибок дробных чисел',()=>{
  assert.equal(cents('0.10')+cents('0.20'),30n);
  assert.equal(decimal(cents('999999999999.99')),'999999999999.99');
  assert.equal(decimal('5200,05'),'5200.05');
  assert.equal(rubles(0n),'0 ₽');
});
test('отклоняются лишние знаки и некорректные суммы',()=>{
  for(const value of ['-1','1.001','NaN','Infinity','1e5','','0x10'])assert.throws(()=>cents(value));
});
test('дата Иркутска на границе UTC',()=>{
  assert.equal(localToday(new Date('2026-11-07T15:59:59Z')),'2026-11-07');
  assert.equal(localToday(new Date('2026-11-07T16:00:00Z')),'2026-11-08');
});
test('названия пользователя не превращаются в HTML',()=>{
  assert.equal(escapeHTML('<script>"&\'</script>'),'&lt;script&gt;&quot;&amp;&#39;&lt;/script&gt;');
});
import {shiftMonth, monthDays, paymentsByDay} from './backend/app/static/calendar.mjs';
import {serviceFor} from './backend/app/static/ui.mjs';
test('календарь начинается с понедельника и включает високосный день',()=>{
  const days=monthDays('2024-02');assert.equal(days[0].date,'2024-01-29');assert.equal(days.length,35);assert.ok(days.some(d=>d.date==='2024-02-29'&&d.current));
  assert.equal(monthDays('2026-11').length,42);assert.equal(shiftMonth('2026-12',1),'2027-01');assert.equal(shiftMonth('2027-01',-1),'2026-12');
});
test('отметки календаря используют перенесённый срок и исключают отмены',()=>{
 const rows=[{due_date:'2026-11-12',scheduled_date:'2026-11-10',status:'partial'},{due_date:'2026-11-12',status:'paid'},{due_date:'2026-11-13',status:'cancelled'},{due_date:'2026-11-14',status:'skipped'}];
 const days=paymentsByDay(rows);assert.equal(days.size,1);assert.equal(days.get('2026-11-12').length,2);assert.equal(days.has('2026-11-10'),false);
});
test('сервисы распознаются локально по полным словам',()=>{
 for(const [name,id] of [['ChatGPT Plus','openai'],['Сбер мобайл','sbermobile'],['Tele2 связь','t2'],['МТС домашний','mts']])assert.equal(serviceFor(name)?.id,id);
 assert.equal(serviceFor('notchatgpt сервис'),null);assert.equal(serviceFor('<script>'),null);
});
