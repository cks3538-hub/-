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
    case 'power': case 'screen': case 'headset': case 'network': case 'wifi': case 'airplane': case 'ringer': case 'app_install':
      return p => p.state === ev.state;
    case 'bluetooth':
      return p => p.state === ev.state && matchAny(ev.device, p.device);
    case 'shake':
      return () => true;
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
  bluetooth: ev => `블루투스 ${ev.device || ''} ${ev.state === 'connected' ? '연결' : '해제'}`,
  app_install: ev => `${ev.app} ${ev.state === 'installed' ? '설치' : '삭제'}`,
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

function nextMonthDay(day, time, now) {
  const [h, mi] = String(time || '09:00').split(':').map(Number);
  for (let k = 0; k <= 2; k++) {
    const last = new Date(now.getFullYear(), now.getMonth() + k + 1, 0).getDate();
    const c = new Date(now.getFullYear(), now.getMonth() + k, Math.min(Math.max(1, Number(day) || 1), last), h || 0, mi || 0, 0, 0);
    if (c.getTime() > now.getTime() + 30000) return c.getTime();
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
      if (t.type === 'month_day') { const at = nextMonthDay(t.params.day, t.params.time, now); if (at) want[t.id] = at; }
      if (t.type === 'interval') {
        const per = Math.max(1, Number(t.params.every) || 1) * 60000;
        if (!nextMap[t.id] || nextMap[t.id] < now.getTime() - 60000 || nextMap[t.id] > now.getTime() + per) nextMap[t.id] = now.getTime() + per;
        want[t.id] = Math.max(nextMap[t.id], now.getTime() + 5000);
      }
    });
  });
  Native.setShake(st.macros.some(m => m.enabled && m.triggers.some(t => t.type === 'shake')));
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
      case 'month_day_c': ok = String(p.days).split(',').map(x => Number(x.trim())).includes(now.getDate()); break;
      case 'month_c': ok = String(p.months).split(',').map(x => Number(x.trim())).includes(now.getMonth() + 1); break;
      case 'notif_present_c': ok = (await Native.hasNotification(p.pkg || '*', p.text || '')) === (p.state === 'yes'); break;
      case 'call_c': ok = dev.call === p.state; break;
      case 'headphones_c': ok = !!dev.headphones === (p.state === 'yes'); break;
      case 'music_c': ok = !!dev.musicActive === (p.state === 'yes'); break;
      case 'ringer_c': ok = dev.ringer === p.state; break;
      case 'locked_c': ok = !!dev.locked === (p.state === 'yes'); break;
      case 'airplane_c': ok = !!dev.airplane === (p.state === 'on'); break;
      case 'bluetooth_c': ok = !!dev.bluetoothOn === (p.state === 'on'); break;
      case 'macro_on_c': { const o = store.get().macros.find(x => x.id === p.id); ok = !!o && o.enabled === (p.state === 'on'); break; }
      case 'last_run_c': ok = !m.lastRun || Date.now() - m.lastRun >= (Number(p.min) || 0) * 60000; break;
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
      case 'bt_device': return ev.device || '';
      case 'app_name': return ev.app || '';
      case 'loop': return ctx.loop == null ? '' : String(ctx.loop);
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

/** Static block structure: If/Else If/Else/End If and Loop/End Loop pairs. */
export function blocks(acts) {
  const info = {}, stack = [];
  acts.forEach((a, i) => {
    switch (a.type) {
      case 'if': stack.push({kind: 'if', i, branches: []}); break;
      case 'else_if': case 'else': {
        const b = [...stack].reverse().find(x => x.kind === 'if');
        if (b) { b.branches.push(i); info[i] = {owner: b.i}; }
        break;
      }
      case 'end_if': {
        const k = stack.map(x => x.kind).lastIndexOf('if');
        if (k >= 0) { const b = stack.splice(k)[0]; info[b.i] = {branches: b.branches, end: i}; b.branches.forEach(j => { info[j].end = i; }); }
        break;
      }
      case 'loop': stack.push({kind: 'loop', i}); break;
      case 'end_loop': {
        const k = stack.map(x => x.kind).lastIndexOf('loop');
        if (k >= 0) { const b = stack.splice(k)[0]; info[b.i] = {end: i}; info[i] = {start: b.i}; }
        break;
      }
      case 'break_loop': case 'continue_loop': {
        const b = [...stack].reverse().find(x => x.kind === 'loop');
        if (b) info[i] = {loop: b.i};
        break;
      }
    }
  });
  return info;
}

function jsonPath(obj, path) {
  return String(path || '').split('.').filter(Boolean).reduce((o, part) => {
    if (o == null) return undefined;
    const m = part.match(/^([^[\]]*)((?:\[\d+\])*)$/);
    if (!m) return undefined;
    let v = m[1] ? o[m[1]] : o;
    (m[2].match(/\d+/g) || []).forEach(n => { v = v == null ? undefined : v[Number(n)]; });
    return v;
  }, obj);
}

export async function run(m, ctx) {
  ctx = {macro: m.name, label: '', depth: 0, ...ctx};
  if (ctx.depth > 5) { log('err', `「${m.name}」 중첩 실행이 너무 깊어 중단했습니다.`); return; }
  if (!ctx.dev) ctx.dev = await Native.getDevice().catch(() => ({}));
  const acts = m.actions, info = blocks(acts), counters = {};
  log('run', `「${m.name}」 실행${ctx.test ? ' (테스트)' : ''}${ctx.label ? ' · ' + ctx.label : ''}`);
  store.update(st => ({...st, macros: st.macros.map(x => (x.id === m.id ? {...x, lastRun: Date.now()} : x))}));
  const ev = ctx.ev || {};
  const started = Date.now();
  let i = 0, steps = 0;
  try {
    while (i < acts.length) {
      if (++steps > 5000) { log('err', `「${m.name}」 액션이 5000번을 넘어 중단했습니다. 반복 설정을 확인하세요.`); break; }
      const a = acts[i], p = a.params, f = v => fmt(v, ctx), b = info[i] || {};
      switch (a.type) {
        case 'reply_notif':
          if (!ev.key) log('err', '답장할 알림이 없습니다. 「알림 수신」 트리거와 함께 쓰세요.');
          else if (!(await Native.replyNotification(ev.key, f(p.text)))) log('err', `${ev.app || '앱'} 알림에는 답장 버튼이 없습니다.`);
          else log('ok', `${ev.title}에게 자동 답장을 보냈습니다.`);
          break;
        case 'press_notif_button':
          if (!ev.key) log('err', '누를 알림이 없습니다. 「알림 수신」 트리거와 함께 쓰세요.');
          else if (!(await Native.pressNotificationButton(ev.key, f(p.label)))) log('err', `알림에 「${p.label}」 버튼이 없습니다. 있는 버튼: ${ev.buttons || '없음'}`);
          break;
        case 'clear_notifs': await Native.clearNotifications(p.pkg || '*'); break;
        case 'dismiss_notif': if (ev.key) await Native.dismissNotification(ev.key); break;
        case 'send_sms': {
          const to = f(p.number).trim();
          if (!to) { log('err', '받는 번호가 비어 있습니다.'); break; }
          try { await Native.sendSms(to, f(p.text)); log('ok', `${to}에 문자를 보냈습니다.`); } catch (e) { log('err', '문자를 보내지 못했습니다. 설정에서 「문자 보내기」 권한을 허용하세요.'); }
          break;
        }
        case 'make_call': try { await Native.makeCall(f(p.number)); } catch (e) { log('err', '전화를 걸 수 없습니다. 설정에서 「전화 걸기·받기」 권한을 허용하세요.'); } break;
        case 'end_call': try { await Native.endCall(); } catch (e) { log('err', '전화를 끊을 수 없습니다. 설정에서 「전화 걸기·받기」 권한을 허용하세요.'); } break;
        case 'answer_call': try { await Native.answerCall(); } catch (e) { log('err', '전화를 받을 수 없습니다. 설정에서 「전화 걸기·받기」 권한을 허용하세요.'); } break;
        case 'notify': Native.notify(f(p.title), f(p.text)); break;
        case 'toast': Native.toast(f(p.text), true); break;
        case 'speak': await Native.speak(f(p.text), Number(p.rate) || 1); break;
        case 'say_time': { const d = new Date(); await Native.speak(`지금은 ${d.getHours()}시 ${d.getMinutes()}분입니다.`, 1); break; }
        case 'vibrate': Native.vibrate(String(p.pattern).split(',').map(x => Number(x.trim())).filter(x => !isNaN(x))); break;
        case 'beep': await Native.beep(Number(p.ms) || 300, Math.min(10, Number(p.times) || 1)); break;
        case 'flashlight': if (!(await Native.torch(p.state))) log('err', '손전등을 켤 수 없습니다.'); break;
        case 'set_volume': try { await Native.setVolume(p.stream, Number(p.pct) || 0); } catch (e) { log('err', '볼륨을 바꿀 수 없습니다. 방해금지 중에는 「방해금지 접근」 허용이 필요합니다.'); } break;
        case 'ringer_mode': try { await Native.setRinger(p.state); } catch (e) { log('err', '소리 모드를 바꿀 수 없습니다. 설정에서 「방해금지 접근」을 허용하세요.'); } break;
        case 'brightness': try { await Native.setBrightness(Number(p.pct) || 30); } catch (e) { log('err', '밝기를 바꿀 수 없습니다. 설정에서 「시스템 설정 변경」을 허용하세요.'); } break;
        case 'set_alarm': { const [h, mi] = String(p.time || '07:00').split(':').map(Number); Native.setAlarmClock(h || 0, mi || 0, f(p.label)); break; }
        case 'launch_app': if (!p.pkg || !(await Native.launchApp(p.pkg))) log('err', '앱을 열 수 없습니다: ' + (p.pkg || '앱 선택 안 됨')); break;
        case 'open_url': Native.openUrl(f(p.url)); break;
        case 'http': {
          const url = f(p.url);
          try {
            const r = await fetch(url, {method: p.method, body: p.method === 'POST' ? f(p.body) : undefined, headers: p.method === 'POST' ? {'Content-Type': 'application/json'} : undefined});
            const txt = (await r.text()).slice(0, 4000);
            log('ok', `HTTP ${r.status} · ${url}`);
            if (p.saveTo) setVar(p.saveTo, 'set', txt, ctx.depth);
          } catch (e) { log('err', 'HTTP 요청 실패: ' + url); }
          break;
        }
        case 'copy': Native.copy(f(p.text)); break;
        case 'set_var': setVar(p.name, p.op, f(p.value), ctx.depth); break;
        case 'text_extract': {
          let out = '';
          try { const r = String(f(p.input)).match(new RegExp(p.pattern)); out = r ? (r[1] ?? r[0]) : ''; } catch (e) { log('err', '패턴 형식이 잘못되었습니다: ' + p.pattern); }
          if (!out) log('info', `「${p.pattern}」에 맞는 글자를 찾지 못했습니다.`);
          setVar(p.saveTo, 'set', out, ctx.depth);
          break;
        }
        case 'text_replace': setVar(p.saveTo, 'set', String(f(p.input)).split(f(p.find)).join(f(p.replace)), ctx.depth); break;
        case 'json_parse': {
          let out = '';
          try { const v = jsonPath(JSON.parse(f(p.input)), p.path); out = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v); } catch (e) { log('err', 'JSON 형식이 아닙니다.'); }
          setVar(p.saveTo, 'set', out, ctx.depth);
          break;
        }
        case 'if': {
          if (cmp(f(p.left), p.op, f(p.right))) break;
          let next = b.end != null ? b.end + 1 : i + 1;
          for (const j of b.branches || []) {
            const bj = acts[j];
            if (bj.type === 'else' || cmp(f(bj.params.left), bj.params.op, f(bj.params.right))) { next = j + 1; break; }
          }
          i = next;
          continue;
        }
        case 'else_if': case 'else':
          // Reached by finishing the previous branch: skip to End If.
          if (b.end != null) { i = b.end + 1; continue; }
          break;
        case 'end_if': break;
        case 'loop': {
          const n = Math.min(1000, Math.max(0, Number(f(p.times)) || 0));
          counters[i] = (counters[i] || 0) + 1;
          if (counters[i] > n) { delete counters[i]; ctx.loop = null; if (b.end != null) { i = b.end + 1; continue; } }
          else ctx.loop = counters[i];
          break;
        }
        case 'end_loop': if (b.start != null) { i = b.start; continue; } break;
        case 'break_loop': if (b.loop != null) { delete counters[b.loop]; ctx.loop = null; i = info[b.loop].end + 1; continue; } break;
        case 'continue_loop': if (b.loop != null) { i = b.loop; continue; } break;
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
    log('ok', `「${m.name}」 완료 (${((Date.now() - started) / 1000).toFixed(1)}초)`);
  } catch (e) {
    log('err', `「${m.name}」 오류: ${e && e.message}`);
  }
  if (!ctx.test && ctx.depth < 5) {
    const next = enabledTriggers('macro_finished').filter(([, t]) => t.params.id === m.id && t.params.id);
    await Promise.all(next.map(([x]) => fire(x.id, {label: `「${m.name}」 완료`, depth: ctx.depth + 1})));
  }
}
