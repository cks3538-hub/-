import {Native} from './native';
import * as store from './store';
import {TRIGGERS, CONSTRAINTS, DAYS, cmp, matchAny, summary} from './catalog';

const pad = n => String(n).padStart(2, '0');
export const hhmm = d => pad(d.getHours()) + ':' + pad(d.getMinutes());
export const ymd = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const toMin = s => { const [h, m] = String(s || '0:0').split(':').map(Number); return (h || 0) * 60 + (m || 0); };
const digits = s => String(s || '').replace(/[^0-9]/g, '');
const sleep = ms => new Promise(r => setTimeout(r, ms));

export function log(lv, msg) {
  store.update(st => ({...st, log: [{t: Date.now(), lv, msg}].concat(st.log).slice(0, 300)}));
}

function enabledTriggers(type) {
  const out = [];
  store.get().macros.forEach(m => { if (m.enabled) m.triggers.forEach(t => { if (t.type === type) out.push([m, t]); }); });
  return out;
}

function matcher(ev) {
  switch (ev.type) {
    case 'notification':
      return p => (p.pkg === '*' || p.pkg === ev.pkg) && matchAny(ev.title, p.title) && matchAny(ev.text, p.text);
    case 'sms':
      return p => (!p.from || digits(ev.from).includes(digits(p.from)) || String(ev.from).includes(p.from)) && matchAny(ev.body, p.body);
    case 'call':
      return p => p.state === ev.state && (!p.number || digits(ev.number).includes(digits(p.number)));
    case 'battery':
      return p => (p.op === 'below' ? ev.prev > p.level && ev.level <= p.level : ev.prev < p.level && ev.level >= p.level);
    case 'power': case 'screen': case 'headset': case 'network':
      return p => p.state === ev.state;
    case 'boot':
      return () => true;
  }
  return () => false;
}

const EVENT_LABEL = {
  notification: ev => `${ev.app} 알림 (${ev.title})`,
  sms: ev => `문자 ${ev.from}`,
  call: ev => `전화 ${ev.number || ''} ${ev.state}`,
  battery: ev => `배터리 ${ev.level}%`,
};

/** Entry point for every device event (background headless task and UI). */
export async function handleEvent(ev) {
  await store.ready();
  if (ev.type === 'alarm') return onAlarm(ev.id);
  if (ev.type === 'boot') rescheduleAlarms();
  if (!TRIGGERS[ev.type]) return;
  const match = matcher(ev);
  const label = (EVENT_LABEL[ev.type] || (() => TRIGGERS[ev.type].label))(ev);
  const runs = enabledTriggers(ev.type).filter(([, t]) => match(t.params)).map(([m]) => fire(m.id, {ev, label}));
  await Promise.all(runs);
}

async function onAlarm(id) {
  const hit = store.get().macros.flatMap(m => m.triggers.map(t => [m, t])).find(([, t]) => t.id === id);
  if (hit && hit[0].enabled) {
    const [m, t] = hit;
    if (t.type === 'interval') {
      store.update(st => ({...st, intervalNext: {...(st.intervalNext || {}), [t.id]: Date.now() + Math.max(1, Number(t.params.every) || 1) * 60000}}));
    }
    rescheduleAlarms();
    await fire(m.id, {ev: {type: 'alarm'}, label: summary('trigger', t)});
  } else rescheduleAlarms();
}

function nextTime(time, days, now) {
  const [h, mi] = String(time || '08:00').split(':').map(Number);
  for (let d = 0; d <= 7; d++) {
    const c = new Date(now.getFullYear(), now.getMonth(), now.getDate() + d, h || 0, mi || 0, 0, 0);
    if (c.getTime() > now.getTime() + 30000 && (days || []).includes(c.getDay())) return c.getTime();
  }
  return null;
}

/** Keeps one Android alarm per enabled time/interval trigger. */
export function rescheduleAlarms() {
  const st = store.get();
  if (!st) return;
  const now = new Date(), want = {}, nextMap = {...(st.intervalNext || {})};
  st.macros.forEach(m => {
    if (!m.enabled) return;
    m.triggers.forEach(t => {
      if (t.type === 'time') { const at = nextTime(t.params.time, t.params.days, now); if (at) want[t.id] = at; }
      if (t.type === 'interval') {
        const per = Math.max(1, Number(t.params.every) || 1) * 60000;
        if (!nextMap[t.id] || nextMap[t.id] < now.getTime() - 60000 || nextMap[t.id] > now.getTime() + per) nextMap[t.id] = now.getTime() + per;
        want[t.id] = Math.max(nextMap[t.id], now.getTime() + 5000);
      }
    });
  });
  (st.alarms || []).forEach(id => { if (!want[id]) Native.cancelAlarm(id); });
  Object.entries(want).forEach(([id, at]) => Native.scheduleAlarm(id, at));
  Object.keys(nextMap).forEach(k => { if (!want[k]) delete nextMap[k]; });
  const ids = Object.keys(want);
  if (ids.join() !== (st.alarms || []).join() || JSON.stringify(nextMap) !== JSON.stringify(st.intervalNext || {})) {
    store.update(s => ({...s, alarms: ids, intervalNext: nextMap}));
  }
}

async function checkConstraints(m, dev) {
  const now = new Date(), vars = store.get().vars;
  for (const c of m.constraints) {
    const p = c.params;
    let ok = true;
    switch (c.type) {
      case 'time_between': { const n = now.getHours() * 60 + now.getMinutes(), a = toMin(p.from), b = toMin(p.to); ok = a <= b ? n >= a && n < b : n >= a || n < b; break; }
      case 'weekday': ok = (p.days || []).includes(now.getDay()); break;
      case 'battery_c': ok = dev.battery != null && (p.op === 'above' ? dev.battery >= Number(p.level) : dev.battery <= Number(p.level)); break;
      case 'charging_c': ok = (p.state === 'charging') === !!dev.charging; break;
      case 'screen_c': ok = (p.state === 'on') === !!dev.screenOn; break;
      case 'wifi_c': ok = (p.state === 'on') === !!dev.wifi; break;
      case 'var_c': ok = cmp(vars[p.name], p.op, p.value); break;
    }
    if (!ok) return CONSTRAINTS[c.type].label + ' (' + summary('constraint', c) + ')';
  }
  return null;
}

export async function fire(id, ctx = {}) {
  const m = store.get().macros.find(x => x.id === id);
  if (!m || (!m.enabled && !ctx.force)) return;
  const dev = await Native.getDevice().catch(() => ({}));
  if (!ctx.test) {
    const fail = await checkConstraints(m, dev);
    if (fail) { log('skip', `「${m.name}」 건너뜀 · 조건 불충족: ${fail}`); return; }
  }
  await run(m, {...ctx, dev, depth: ctx.depth || 0});
}

function fmt(str, ctx) {
  const d = new Date(), ev = ctx.ev || {}, vars = store.get().vars, dev = ctx.dev || {};
  return String(str ?? '').replace(/\{(\w+)(?:=([^}]+))?\}/g, (all, k, v) => {
    switch (k) {
      case 'time': return hhmm(d);
      case 'date': return ymd(d);
      case 'weekday': return DAYS[d.getDay()] + '요일';
      case 'battery': return dev.battery == null ? '?' : String(dev.battery);
      case 'not_app': return ev.app || '';
      case 'not_title': return ev.title || '';
      case 'not_text': return ev.text || '';
      case 'sms_from': return ev.from || '';
      case 'sms_body': return ev.body || '';
      case 'call_number': return ev.number || '';
      case 'macro': return ctx.macro || '';
      case 'trigger': return ctx.label || '';
      case 'v': return v in vars ? String(vars[v]) : '';
    }
    return all;
  });
}

export function setVar(name, op, value, depth = 0) {
  if (!name) return;
  let next;
  store.update(st => {
    const cur = st.vars[name];
    switch (op) {
      case 'add': next = String((Number(cur) || 0) + (Number(value) || 0)); break;
      case 'sub': next = String((Number(cur) || 0) - (Number(value) || 0)); break;
      case 'append': next = String(cur ?? '') + value; break;
      default: next = String(value);
    }
    return {...st, vars: {...st.vars, [name]: next}};
  });
  if (depth < 5) {
    enabledTriggers('variable')
      .filter(([, t]) => t.params.name === name && cmp(next, t.params.op, t.params.value))
      .forEach(([m]) => fire(m.id, {label: `변수 ${name} = ${next}`, depth: depth + 1}));
  }
}

export async function run(m, ctx) {
  ctx = {macro: m.name, label: '', depth: 0, ...ctx};
  if (ctx.depth > 5) { log('err', `「${m.name}」 중첩 실행이 너무 깊어 중단했습니다.`); return; }
  if (!ctx.dev) ctx.dev = await Native.getDevice().catch(() => ({}));
  const acts = m.actions, jump = {}, stack = [];
  acts.forEach((a, i) => {
    if (a.type === 'if') stack.push({i, e: null});
    else if (a.type === 'else' && stack.length) stack[stack.length - 1].e = i;
    else if (a.type === 'end_if' && stack.length) { const b = stack.pop(); jump[b.i] = {e: b.e, end: i}; if (b.e !== null) jump[b.e] = {end: i}; }
  });
  log('run', `「${m.name}」 실행${ctx.test ? ' (테스트)' : ''}${ctx.label ? ' · ' + ctx.label : ''}`);
  store.update(st => ({...st, macros: st.macros.map(x => (x.id === m.id ? {...x, lastRun: Date.now()} : x))}));
  const ev = ctx.ev || {};
  let i = 0;
  try {
    while (i < acts.length) {
      const a = acts[i], p = a.params, f = v => fmt(v, ctx);
      switch (a.type) {
        case 'reply_notif':
          if (!ev.key) log('err', '답장할 알림이 없습니다. 「알림 수신」 트리거와 함께 쓰세요.');
          else if (!(await Native.replyNotification(ev.key, f(p.text)))) log('err', `${ev.app || '앱'} 알림에는 답장 버튼이 없습니다.`);
          else log('ok', `${ev.title}에게 자동 답장을 보냈습니다.`);
          break;
        case 'dismiss_notif': if (ev.key) await Native.dismissNotification(ev.key); break;
        case 'send_sms': {
          const to = f(p.number).trim();
          if (!to) { log('err', '받는 번호가 비어 있습니다.'); break; }
          try { await Native.sendSms(to, f(p.text)); log('ok', `${to}에 문자를 보냈습니다.`); } catch (e) { log('err', '문자를 보내지 못했습니다. 설정에서 「문자 보내기」 권한을 허용하세요.'); }
          break;
        }
        case 'notify': Native.notify(f(p.title), f(p.text)); break;
        case 'toast': Native.toast(f(p.text), true); break;
        case 'speak': await Native.speak(f(p.text), Number(p.rate) || 1); break;
        case 'vibrate': Native.vibrate(String(p.pattern).split(',').map(x => Number(x.trim())).filter(x => !isNaN(x))); break;
        case 'beep': await Native.beep(Number(p.ms) || 300, Math.min(10, Number(p.times) || 1)); break;
        case 'launch_app': if (!(await Native.launchApp(p.pkg))) log('err', '앱을 열 수 없습니다: ' + p.pkg); break;
        case 'open_url': Native.openUrl(f(p.url)); break;
        case 'http': {
          const url = f(p.url);
          try {
            const r = await fetch(url, {method: p.method, body: p.method === 'POST' ? f(p.body) : undefined, headers: p.method === 'POST' ? {'Content-Type': 'application/json'} : undefined});
            const txt = (await r.text()).slice(0, 2000);
            log('ok', `HTTP ${r.status} · ${url}`);
            if (p.saveTo) setVar(p.saveTo, 'set', txt, ctx.depth);
          } catch (e) { log('err', 'HTTP 요청 실패: ' + url); }
          break;
        }
        case 'copy': Native.copy(f(p.text)); break;
        case 'set_var': setVar(p.name, p.op, f(p.value), ctx.depth); break;
        case 'if': {
          const ok = cmp(f(p.left), p.op, f(p.right)), j = jump[i];
          if (!ok && j) { i = (j.e !== null ? j.e : j.end) + 1; continue; }
          break;
        }
        case 'else': { const j = jump[i]; if (j) { i = j.end + 1; continue; } break; }
        case 'end_if': break;
        case 'wait': await sleep(Math.min(150, Math.max(0, Number(p.sec) || 0)) * 1000); break;
        case 'run_macro': {
          const t = store.get().macros.find(x => x.id === p.id);
          if (!t) { log('err', '실행할 매크로를 찾을 수 없습니다.'); break; }
          await run(t, {ev, dev: ctx.dev, label: `「${m.name}」에서 호출`, depth: ctx.depth + 1});
          break;
        }
        case 'enable_macro':
          store.update(st => ({...st, macros: st.macros.map(x => (x.id === p.id ? {...x, enabled: p.mode === 'on' ? true : p.mode === 'off' ? false : !x.enabled} : x))}));
          rescheduleAlarms();
          break;
        case 'stop': log('info', `「${m.name}」 중단 액션으로 종료`); i = acts.length; continue;
        case 'log': log('info', f(p.text)); break;
      }
      i++;
    }
    log('ok', `「${m.name}」 완료`);
  } catch (e) {
    log('err', `「${m.name}」 오류: ${e && e.message}`);
  }
}
