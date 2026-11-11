export function shiftMonth(month, delta) {
  const [year, index] = month.split('-').map(Number);
  const date = new Date(Date.UTC(year, index - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth()+1).padStart(2,'0')}`;
}
export function monthDays(month) {
  const [year, number] = month.split('-').map(Number);
  const first = new Date(Date.UTC(year, number-1, 1));
  const offset = (first.getUTCDay()+6)%7;
  const last = new Date(Date.UTC(year, number, 0)).getUTCDate();
  const count = Math.ceil((offset+last)/7)*7;
  return Array.from({length:count},(_,i)=>{
    const day=new Date(Date.UTC(year,number-1,1-offset+i));
    const date=day.toISOString().slice(0,10);
    return {date, day:day.getUTCDate(), current:date.startsWith(month)};
  });
}
export function paymentsByDay(occurrences) {
  const result=new Map();
  for(const o of occurrences) {
    if(['cancelled','skipped'].includes(o.status))continue;
    if(!result.has(o.due_date))result.set(o.due_date,[]);
    result.get(o.due_date).push(o);
  }
  return result;
}
