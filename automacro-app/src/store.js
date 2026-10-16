import {Native} from './native';
import {TEMPLATES, fromTemplate} from './catalog';

// One in-memory state shared by the UI and the background (headless) task.
let state = null;
let loading = null;
const subs = new Set();

function seed() {
  const pick = n => TEMPLATES.find(t => t.name === n);
  const m = [pick('실행 횟수 카운터'), pick('카톡 부재중 자동응답'), pick('입금 문자 읽어주기')].map(fromTemplate);
  m[0].enabled = true;
  m.forEach(x => { x.category = '예시'; });
  return {
    macros: m,
    vars: {counter: '0'},
    log: [{t: Date.now(), lv: 'info', msg: '예시 매크로 3개를 불러왔습니다. 문자·카톡 예시는 꺼져 있으니 확인 후 켜세요.'}],
    alarms: [],
  };
}

export function ready() {
  if (state) return Promise.resolve(state);
  if (!loading) {
    loading = Native.loadState().then(json => {
      try {
        const j = json ? JSON.parse(json) : null;
        state = j && Array.isArray(j.macros) ? Object.assign({vars: {}, log: [], alarms: []}, j) : seed();
      } catch (e) { state = seed(); }
      if (!json) persist();
      return state;
    });
  }
  return loading;
}
export const get = () => state;
export function update(fn) {
  state = fn(state);
  persist();
  subs.forEach(f => f(state));
  return state;
}
export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
function persist() { try { Native.saveState(JSON.stringify(state)); } catch (e) {} }
export function replaceAll(next) { update(() => next); }
export const freshSeed = seed;
