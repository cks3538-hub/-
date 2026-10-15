jest.mock('../src/native', () => {
  const calls = [];
  return {
    __calls: calls,
    Native: new Proxy({}, {
      get: (_, k) => (...a) => {
        calls.push([k, ...a]);
        if (k === 'loadState') return Promise.resolve(null);
        if (k === 'getDevice') return Promise.resolve({battery: 50, charging: false, screenOn: true});
        if (k === 'replyNotification') return Promise.resolve(true);
        return Promise.resolve(true);
      },
    }),
  };
});
const {__calls: calls} = require('../src/native');
const store = require('../src/store');
const {handleEvent} = require('../src/engine');
const {TEMPLATES, fromTemplate} = require('../src/catalog');

const on = name => { const m = fromTemplate(TEMPLATES.find(t => t.name === name)); m.enabled = true; return m; };

beforeEach(async () => {
  calls.length = 0;
  await store.ready();
});

test('카톡 키워드 알림 → 음성, 키워드 없으면 무시', async () => {
  const m = on('카톡 키워드 알림');
  store.replaceAll({...store.get(), macros: [m]});
  await handleEvent({type: 'notification', pkg: 'com.kakao.talk', app: '카카오톡', title: '홍길동', text: '긴급 연락 바람', key: 'k1'});
  expect(calls.some(c => c[0] === 'speak' && c[1].includes('홍길동님의 긴급 메시지'))).toBe(true);
  calls.length = 0;
  await handleEvent({type: 'notification', pkg: 'com.kakao.talk', app: '카카오톡', title: '홍길동', text: '점심 뭐 먹지', key: 'k2'});
  expect(calls.some(c => c[0] === 'speak')).toBe(false);
});

test('입금 문자 → 알림 내용에 번호와 본문', async () => {
  store.replaceAll({...store.get(), macros: [on('입금 문자 읽어주기')]});
  await handleEvent({type: 'sms', from: '15881688', body: '[KB] 100,000원 입금'});
  expect(calls.find(c => c[0] === 'notify')).toEqual(['notify', '입금 알림', '15881688: [KB] 100,000원 입금']);
});

test('자동응답 매크로가 알림 key 로 답장', async () => {
  const m = on('카톡 부재중 자동응답');
  m.constraints = [];
  store.replaceAll({...store.get(), macros: [m]});
  await handleEvent({type: 'notification', pkg: 'com.kakao.talk', app: '카카오톡', title: '엄마', text: '어디야', key: 'k9'});
  expect(calls.find(c => c[0] === 'replyNotification')[1]).toBe('k9');
});

test('If/Else 카운터', async () => {
  const m = on('실행 횟수 카운터');
  store.replaceAll({...store.get(), vars: {counter: '3'}, macros: [m]});
  const {fire} = require('../src/engine');
  await fire(m.id, {});
  expect(store.get().vars.counter).toBe('4');
  await fire(m.id, {});
  expect(store.get().vars.counter).toBe('0');
  expect(calls.filter(c => c[0] === 'toast').map(c => c[1])).toEqual(['현재 4회', '5회 달성! 카운터를 초기화합니다.']);
});
