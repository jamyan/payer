// Рубли считаем целыми копейками: дробные Number здесь не используются.
export function cents(value) {
  const raw = String(value).trim().replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) throw new Error('Введи сумму с точностью до копейки');
  const [rub, kop = ''] = raw.split('.');
  return BigInt(rub) * 100n + BigInt(kop.padEnd(2, '0'));
}
export function decimal(value) {
  const n = typeof value === 'bigint' ? value : cents(value);
  if (n < 0n) throw new Error('Сумма не может быть отрицательной');
  return `${n / 100n}.${String(n % 100n).padStart(2, '0')}`;
}
export function rubles(value) {
  const n = typeof value === 'bigint' ? value : cents(value);
  const fraction = n % 100n;
  return `${(n / 100n).toLocaleString('ru-RU')}${fraction ? ',' + String(fraction).padStart(2, '0') : ''} ₽`;
}
export function localToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Irkutsk', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(now);
  const get = type => parts.find(p => p.type === type).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}
export function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
}
