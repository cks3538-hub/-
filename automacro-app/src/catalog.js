// 트리거 · 액션 · 제약 조건 카탈로그 (MacroDroid 구조를 따름)
export const DAYS = ['일', '월', '화', '수', '목', '금', '토'];
export const uid = () => Math.random().toString(36).slice(2, 10);
export const clone = o => JSON.parse(JSON.stringify(o));

export function daysText(d) {
  d = (d || []).slice().sort();
  if (d.length === 7) return '매일';
  if (d.join() === '1,2,3,4,5') return '평일';
  if (d.join() === '0,6') return '주말';
  if (!d.length) return '요일 없음';
  return d.map(i => DAYS[i]).join('·');
}

export const OPS = [['eq', '='], ['ne', '≠'], ['gt', '>'], ['ge', '≥'], ['lt', '<'], ['le', '≤'], ['contains', '포함'], ['not_contains', '미포함']];
export const opText = o => (OPS.find(x => x[0] === o) || ['', o])[1];
const isNum = v => v !== '' && v != null && !isNaN(Number(v));
export function cmp(a, op, b) {
  const n = isNum(a) && isNum(b);
  const x = n ? Number(a) : String(a ?? ''), y = n ? Number(b) : String(b ?? '');
  switch (op) {
    case 'eq': return x === y;
    case 'ne': return x !== y;
    case 'gt': return x > y;
    case 'ge': return x >= y;
    case 'lt': return x < y;
    case 'le': return x <= y;
    case 'contains': return String(a ?? '').includes(String(b ?? ''));
    case 'not_contains': return !String(a ?? '').includes(String(b ?? ''));
  }
  return false;
}
// "입금, 출금" → 둘 중 하나라도 포함하면 참. 빈 칸이면 항상 참.
export function matchAny(text, pattern) {
  const keys = String(pattern || '').split(',').map(s => s.trim()).filter(Boolean);
  if (!keys.length) return true;
  const t = String(text || '');
  return keys.some(k => t.includes(k));
}

export const KNOWN_APPS = {
  '*': '모든 앱',
  'com.kakao.talk': '카카오톡',
  'com.samsung.android.messaging': '삼성 메시지',
  'com.google.android.apps.messaging': '구글 메시지',
  'com.kakaobank.channel': '카카오뱅크',
  'viva.republica.toss': '토스',
  'org.telegram.messenger': '텔레그램',
  'jp.naver.line.android': '라인',
};
export const appText = (pkg, label) => label || KNOWN_APPS[pkg] || pkg || '앱 선택 안 됨';

const state = (o, d) => ({k: 'state', l: '상태', t: 'select', o, d});
const text = (k, l, d, extra) => Object.assign({k, l, t: 'text', d}, extra);
const area = (k, l, d) => ({k, l, t: 'textarea', d});

export const TRIGGERS = {
  notification: {
    label: '알림 수신 (카톡 등)', mono: '알', group: '메시지·알림',
    fields: [
      {k: 'pkg', l: '앱', t: 'app', d: 'com.kakao.talk'},
      text('title', '보낸 사람/방 이름에 포함 (쉼표로 여러 개, 비우면 전체)', ''),
      text('text', '내용에 포함 (쉼표로 여러 개, 비우면 전체)', ''),
    ],
    sum: p => `${appText(p.pkg, p.pkgLabel)}${p.title ? ' · 이름 「' + p.title + '」' : ''}${p.text ? ' · 내용 「' + p.text + '」' : ''}`,
  },
  sms: {
    label: '문자 수신', mono: '문', group: '메시지·알림',
    fields: [text('from', '보낸 번호에 포함 (비우면 전체)', ''), text('body', '내용에 포함 (쉼표로 여러 개, 비우면 전체)', '')],
    sum: p => `${p.from ? '번호 ' + p.from : '모든 번호'}${p.body ? ' · 내용 「' + p.body + '」' : ''}`,
  },
  call: {
    label: '전화 수신/종료', mono: '전', group: '메시지·알림',
    fields: [state([['ringing', '전화가 올 때'], ['answered', '받았을 때'], ['missed', '부재중일 때'], ['ended', '통화가 끝났을 때']], 'ringing'), text('number', '번호에 포함 (비우면 전체)', '')],
    sum: p => ({ringing: '전화 수신', answered: '전화 받음', missed: '부재중 전화', ended: '통화 종료'}[p.state] + (p.number ? ' · ' + p.number : '')),
  },
  time: {
    label: '시간/요일', mono: '시', group: '날짜·시간',
    fields: [{k: 'time', l: '시각 (24시간, 예 07:30)', t: 'time', d: '08:00'}, {k: 'days', l: '요일', t: 'days', d: [1, 2, 3, 4, 5]}],
    sum: p => p.time + ' · ' + daysText(p.days),
  },
  interval: {
    label: '반복 간격', mono: '반', group: '날짜·시간',
    fields: [{k: 'every', l: '간격 (분, 최소 1)', t: 'number', d: 60}],
    sum: p => p.every + '분마다',
  },
  battery: {
    label: '배터리 잔량', mono: '배', group: '배터리·전원',
    fields: [{k: 'op', l: '조건', t: 'select', o: [['below', '이하로 떨어질 때'], ['above', '이상으로 오를 때']], d: 'below'}, {k: 'level', l: '잔량 (%)', t: 'number', d: 20}],
    sum: p => `${p.level}% ${p.op === 'below' ? '이하로 떨어짐' : '이상으로 오름'}`,
  },
  power: {
    label: '충전기 연결/해제', mono: '충', group: '배터리·전원',
    fields: [state([['connected', '연결됨'], ['disconnected', '해제됨']], 'connected')],
    sum: p => '충전기 ' + (p.state === 'connected' ? '연결됨' : '해제됨'),
  },
  screen: {
    label: '화면 켜짐/꺼짐/잠금해제', mono: '화', group: '기기',
    fields: [state([['on', '화면 켜짐'], ['off', '화면 꺼짐'], ['unlock', '잠금 해제']], 'unlock')],
    sum: p => ({on: '화면 켜짐', off: '화면 꺼짐', unlock: '잠금 해제'}[p.state]),
  },
  headset: {
    label: '이어폰 연결/해제', mono: '이', group: '기기',
    fields: [state([['plugged', '연결됨'], ['unplugged', '해제됨']], 'plugged')],
    sum: p => '유선 이어폰 ' + (p.state === 'plugged' ? '연결' : '해제'),
  },
  network: {
    label: '인터넷 연결 변경', mono: '망', group: '기기',
    fields: [state([['online', '연결됨'], ['offline', '끊김']], 'offline')],
    sum: p => '인터넷 ' + (p.state === 'online' ? '연결됨' : '끊김'),
  },
  wifi: {
    label: '와이파이 연결/해제', mono: '와', group: '연결',
    fields: [state([['connected', '와이파이 연결됨'], ['disconnected', '와이파이 끊김']], 'connected')],
    sum: p => '와이파이 ' + (p.state === 'connected' ? '연결됨' : '끊김'),
  },
  bluetooth: {
    label: '블루투스 기기 연결/해제', mono: '블', group: '연결',
    fields: [state([['connected', '연결됨'], ['disconnected', '해제됨']], 'connected'), text('device', '기기 이름에 포함 (예: 차량, Buds / 비우면 전체)', '')],
    sum: p => `${p.device || '모든 기기'} ${p.state === 'connected' ? '연결' : '해제'}`,
  },
  airplane: {label: '비행기 모드 켜짐/꺼짐', mono: '비', group: '연결', fields: [state([['on', '켜짐'], ['off', '꺼짐']], 'on')], sum: p => '비행기 모드 ' + (p.state === 'on' ? '켜짐' : '꺼짐')},
  ringer: {
    label: '소리/진동/무음 변경', mono: '벨', group: '기기',
    fields: [state([['normal', '소리'], ['vibrate', '진동'], ['silent', '무음']], 'silent')],
    sum: p => ({normal: '소리', vibrate: '진동', silent: '무음'}[p.state]) + ' 모드로 바뀜',
  },
  shake: {label: '기기 흔들기', mono: '흔', group: '센서', fields: [], sum: () => '휴대폰을 흔들 때 (항상 실행 필요)'},
  app_install: {
    label: '앱 설치/삭제', mono: '설', group: '기기',
    fields: [state([['installed', '설치됨'], ['removed', '삭제됨']], 'installed')],
    sum: p => '앱이 ' + (p.state === 'installed' ? '설치될 때' : '삭제될 때'),
  },
  month_day: {
    label: '매월 특정 날짜', mono: '월', group: '날짜·시간',
    fields: [{k: 'day', l: '날짜 (1~31, 말일은 31)', t: 'number', d: 25}, {k: 'time', l: '시각', t: 'time', d: '09:00'}],
    sum: p => `매월 ${p.day}일 ${p.time}`,
  },
  macro_finished: {label: '다른 매크로 완료', mono: '완', group: '기타', fields: [{k: 'id', l: '매크로', t: 'macro', d: ''}], sum: (p, s) => `「${macroName(s, p.id)}」 끝났을 때`},
  boot: {label: '휴대폰 재시작', mono: '부', group: '기기', fields: [], sum: () => '휴대폰이 켜질 때'},
  manual: {label: '바로가기 (수동 실행)', mono: '수', group: '기타', fields: [], sum: () => '홈 화면 실행 버튼'},
  variable: {
    label: '변수 값 변경', mono: '변', group: '기타',
    fields: [text('name', '변수 이름', 'counter', {t: 'var'}), {k: 'op', l: '비교', t: 'select', o: OPS, d: 'ge'}, text('value', '값', '10')],
    sum: p => `${p.name} ${opText(p.op)} ${p.value}`,
  },
};

export const ACTIONS = {
  reply_notif: {
    label: '알림에 답장 (카톡 자동응답)', mono: '답', group: '메시지',
    fields: [area('text', '답장 내용', '지금은 바빠서 나중에 연락드리겠습니다.')],
    sum: p => '「' + p.text + '」',
  },
  send_sms: {
    label: '문자 보내기', mono: '문', group: '메시지',
    fields: [text('number', '받는 번호 ({sms_from}=문자 보낸 사람)', '{sms_from}'), area('text', '내용', '')],
    sum: p => `${p.number} ← 「${p.text}」`,
  },
  press_notif_button: {
    label: '알림 버튼 누르기 (읽음 등)', mono: '버', group: '메시지',
    fields: [text('label', '버튼 글자에 포함 (예: 읽음, 답장, 확인)', '읽음')],
    sum: p => `「${p.label}」 버튼 누르기`,
  },
  clear_notifs: {label: '알림 모두 지우기', mono: '청', group: '메시지', fields: [{k: 'pkg', l: '앱 (모든 앱 가능)', t: 'app', d: '*'}], sum: p => appText(p.pkg, p.pkgLabel) + ' 알림 지우기'},
  dismiss_notif: {label: '받은 알림 지우기', mono: '지', group: '메시지', fields: [], sum: () => '트리거가 된 알림을 지움'},
  notify: {label: '알림 표시', mono: '알', group: '알림·소리', fields: [text('title', '제목', '오토매크로'), area('text', '내용', '')], sum: p => p.title + ' — ' + p.text},
  toast: {label: '팝업 메시지', mono: '팝', group: '알림·소리', fields: [area('text', '메시지', '')], sum: p => p.text},
  speak: {
    label: '텍스트 읽기 (TTS)', mono: '말', group: '알림·소리',
    fields: [area('text', '읽을 문장', ''), {k: 'rate', l: '속도', t: 'select', o: [['0.8', '느리게'], ['1', '보통'], ['1.3', '빠르게']], d: '1'}],
    sum: p => '“' + p.text + '”',
  },
  vibrate: {label: '진동', mono: '진', group: '알림·소리', fields: [text('pattern', '패턴 (ms, 쉼표로 구분)', '300,100,300')], sum: p => p.pattern + ' ms'},
  beep: {label: '소리 재생', mono: '음', group: '알림·소리', fields: [{k: 'ms', l: '길이 (ms)', t: 'number', d: 300}, {k: 'times', l: '횟수', t: 'number', d: 2}], sum: p => `${p.ms}ms × ${p.times}`},
  make_call: {label: '전화 걸기', mono: '콜', group: '전화', fields: [text('number', '번호', '')], sum: p => p.number},
  end_call: {label: '전화 끊기/거절', mono: '끊', group: '전화', fields: [], sum: () => '현재 통화 종료 또는 수신 거절'},
  answer_call: {label: '전화 받기', mono: '받', group: '전화', fields: [], sum: () => '울리는 전화 자동으로 받기'},
  flashlight: {label: '손전등', mono: '등', group: '기기 설정', fields: [state([['toggle', '켜기/끄기 전환'], ['on', '켜기'], ['off', '끄기']], 'toggle')], sum: p => '손전등 ' + {toggle: '전환', on: '켜기', off: '끄기'}[p.state]},
  set_volume: {
    label: '볼륨 설정', mono: '볼', group: '기기 설정',
    fields: [{k: 'stream', l: '종류', t: 'select', o: [['music', '미디어'], ['ring', '벨소리'], ['notification', '알림'], ['alarm', '알람']], d: 'music'}, {k: 'pct', l: '크기 (0~100%)', t: 'number', d: 50}],
    sum: p => `${{music: '미디어', ring: '벨소리', notification: '알림', alarm: '알람'}[p.stream]} ${p.pct}%`,
  },
  ringer_mode: {label: '소리/진동/무음', mono: '벨', group: '기기 설정', fields: [state([['normal', '소리'], ['vibrate', '진동'], ['silent', '무음']], 'vibrate')], sum: p => ({normal: '소리', vibrate: '진동', silent: '무음'}[p.state]) + ' 모드'},
  brightness: {label: '화면 밝기', mono: '밝', group: '기기 설정', fields: [{k: 'pct', l: '밝기 (1~100%)', t: 'number', d: 30}], sum: p => `밝기 ${p.pct}%`},
  set_alarm: {label: '알람 맞추기', mono: '람', group: '기기 설정', fields: [{k: 'time', l: '시각', t: 'time', d: '07:00'}, text('label', '알람 이름', '오토매크로')], sum: p => `${p.time} 알람`},
  launch_app: {label: '앱 실행', mono: '앱', group: '앱·웹', fields: [{k: 'pkg', l: '앱', t: 'app', d: ''}], sum: p => appText(p.pkg, p.pkgLabel)},
  open_url: {label: '웹사이트 열기', mono: '링', group: '앱·웹', fields: [text('url', 'URL', 'https://')], sum: p => p.url},
  http: {
    label: 'HTTP 요청 (웹훅)', mono: '웹', group: '앱·웹',
    fields: [{k: 'method', l: '방식', t: 'select', o: [['GET', 'GET'], ['POST', 'POST']], d: 'POST'}, text('url', 'URL', 'https://'), area('body', 'POST 본문 (JSON 등)', '{"text":"{not_text}"}'), text('saveTo', '응답 저장 변수 (선택)', '', {t: 'var'})],
    sum: p => p.method + ' ' + p.url,
  },
  copy: {label: '클립보드에 복사', mono: '복', group: '앱·웹', fields: [area('text', '복사할 내용', '{sms_body}')], sum: p => p.text},
  set_var: {
    label: '변수 설정', mono: '변', group: '변수·흐름',
    fields: [text('name', '변수 이름', 'counter', {t: 'var'}), {k: 'op', l: '동작', t: 'select', o: [['set', '값으로 설정'], ['add', '더하기'], ['sub', '빼기'], ['append', '글자 이어붙이기']], d: 'set'}, text('value', '값 (매직 텍스트 가능)', '1')],
    sum: p => `${p.name} ${{set: '=', add: '+=', sub: '-=', append: '+='}[p.op]} ${p.value}`,
  },
  text_extract: {
    label: '글자 추출 (인증번호 등)', mono: '추', group: '글자·데이터',
    fields: [text('input', '원본 (예: {sms_body})', '{sms_body}'), text('pattern', '찾을 패턴 (정규식, 예: \\d{4,8} = 숫자 4~8자리)', '\\d{4,8}'), text('saveTo', '저장할 변수', 'code', {t: 'var'})],
    sum: p => `${p.input} 에서 /${p.pattern}/ → ${p.saveTo}`,
  },
  text_replace: {
    label: '글자 바꾸기', mono: '환', group: '글자·데이터',
    fields: [text('input', '원본', '{not_text}'), text('find', '찾을 글자', ''), text('replace', '바꿀 글자', ''), text('saveTo', '저장할 변수', 'text', {t: 'var'})],
    sum: p => `「${p.find}」→「${p.replace}」 → ${p.saveTo}`,
  },
  json_parse: {
    label: 'JSON 값 꺼내기', mono: 'J', group: '글자·데이터',
    fields: [text('input', 'JSON (예: {v=response})', '{v=response}'), text('path', '경로 (예: data.price, items[0].name)', 'data'), text('saveTo', '저장할 변수', 'value', {t: 'var'})],
    sum: p => `${p.path} → ${p.saveTo}`,
  },
  say_time: {label: '현재 시각 말하기', mono: '각', group: '알림·소리', fields: [], sum: () => '“지금은 ○시 ○분입니다”'},
  if: {
    label: 'If (조건 시작)', mono: 'If', group: '변수·흐름',
    fields: [text('left', '비교할 값 (예: {not_text}, {v=counter})', '{v=counter}'), {k: 'op', l: '비교', t: 'select', o: OPS, d: 'ge'}, text('right', '기준 값', '10')],
    sum: p => `만약 ${p.left} ${opText(p.op)} ${p.right}`,
  },
  else_if: {
    label: 'Else If (아니면 만약)', mono: '또', group: '변수·흐름',
    fields: [text('left', '비교할 값', '{v=counter}'), {k: 'op', l: '비교', t: 'select', o: OPS, d: 'ge'}, text('right', '기준 값', '5')],
    sum: p => `아니면 만약 ${p.left} ${opText(p.op)} ${p.right}`,
  },
  else: {label: 'Else (아니면)', mono: '외', group: '변수·흐름', fields: [], sum: () => '아니면'},
  end_if: {label: 'End If (조건 끝)', mono: '끝', group: '변수·흐름', fields: [], sum: () => '조건 끝'},
  loop: {label: 'Loop (반복 시작)', mono: '반', group: '변수·흐름', fields: [{k: 'times', l: '반복 횟수', t: 'number', d: 3}], sum: p => `${p.times}번 반복 ({loop}=현재 회차)`},
  end_loop: {label: 'End Loop (반복 끝)', mono: '끝', group: '변수·흐름', fields: [], sum: () => '반복 끝'},
  break_loop: {label: '반복 빠져나가기', mono: '탈', group: '변수·흐름', fields: [], sum: () => '반복을 즉시 끝냄'},
  continue_loop: {label: '다음 회차로', mono: '다', group: '변수·흐름', fields: [], sum: () => '이번 회차의 나머지를 건너뜀'},
  wait: {label: '대기', mono: '대', group: '변수·흐름', fields: [{k: 'sec', l: '대기 시간 (초, 최대 150)', t: 'number', d: 5}], sum: p => p.sec + '초 대기'},
  run_macro: {label: '다른 매크로 실행', mono: '실', group: '매크로', fields: [{k: 'id', l: '매크로', t: 'macro', d: ''}], sum: (p, s) => macroName(s, p.id)},
  enable_macro: {
    label: '매크로 켜기/끄기', mono: '켜', group: '매크로',
    fields: [{k: 'id', l: '매크로', t: 'macro', d: ''}, {k: 'mode', l: '동작', t: 'select', o: [['on', '켜기'], ['off', '끄기'], ['toggle', '전환']], d: 'toggle'}],
    sum: (p, s) => macroName(s, p.id) + ' ' + {on: '켜기', off: '끄기', toggle: '전환'}[p.mode],
  },
  stop: {label: '매크로 중단', mono: '중', group: '매크로', fields: [], sum: () => '여기서 실행을 멈춤'},
  log: {label: '로그 기록', mono: '록', group: '매크로', fields: [area('text', '기록할 내용', '')], sum: p => p.text},
};

export const CONSTRAINTS = {
  time_between: {label: '시간대', mono: '시', group: '날짜·시간', fields: [{k: 'from', l: '시작', t: 'time', d: '09:00'}, {k: 'to', l: '끝', t: 'time', d: '18:00'}], sum: p => p.from + ' ~ ' + p.to},
  weekday: {label: '요일', mono: '요', group: '날짜·시간', fields: [{k: 'days', l: '요일', t: 'days', d: [1, 2, 3, 4, 5]}], sum: p => daysText(p.days)},
  battery_c: {label: '배터리 잔량', mono: '배', group: '기기', fields: [{k: 'op', l: '조건', t: 'select', o: [['above', '이상'], ['below', '이하']], d: 'above'}, {k: 'level', l: '잔량 (%)', t: 'number', d: 30}], sum: p => p.level + '% ' + (p.op === 'above' ? '이상' : '이하')},
  charging_c: {label: '충전 상태', mono: '충', group: '기기', fields: [state([['charging', '충전 중'], ['not', '충전 안 함']], 'charging')], sum: p => (p.state === 'charging' ? '충전 중' : '충전 안 함')},
  screen_c: {label: '화면 상태', mono: '화', group: '기기', fields: [state([['on', '켜져 있음'], ['off', '꺼져 있음']], 'off')], sum: p => '화면 ' + (p.state === 'on' ? '켜짐' : '꺼짐')},
  wifi_c: {label: '와이파이 연결', mono: '와', group: '기기', fields: [state([['on', '와이파이 사용 중'], ['off', '와이파이 아님']], 'on')], sum: p => (p.state === 'on' ? '와이파이 사용 중' : '와이파이 아님')},
  month_day_c: {label: '날짜 (매월)', mono: '일', group: '날짜·시간', fields: [text('days', '날짜 (쉼표로 여러 개, 예: 1,15,25)', '25')], sum: p => `매월 ${p.days}일`},
  month_c: {label: '월', mono: '월', group: '날짜·시간', fields: [text('months', '월 (쉼표로 여러 개, 예: 6,7,8)', '12')], sum: p => `${p.months}월`},
  notif_present_c: {
    label: '알림이 남아 있음', mono: '알', group: '메시지',
    fields: [{k: 'pkg', l: '앱', t: 'app', d: 'com.kakao.talk'}, text('text', '내용에 포함 (비우면 전체)', ''), state([['yes', '있을 때'], ['no', '없을 때']], 'yes')],
    sum: p => `${appText(p.pkg, p.pkgLabel)} 알림이 ${p.state === 'yes' ? '있을 때' : '없을 때'}`,
  },
  call_c: {label: '통화 상태', mono: '통', group: '전화', fields: [state([['idle', '통화 중 아님'], ['offhook', '통화 중'], ['ringing', '전화 울리는 중']], 'idle')], sum: p => ({idle: '통화 중 아님', offhook: '통화 중', ringing: '전화 울리는 중'}[p.state])},
  headphones_c: {label: '이어폰 연결', mono: '이', group: '기기', fields: [state([['yes', '연결됨'], ['no', '연결 안 됨']], 'yes')], sum: p => '이어폰 ' + (p.state === 'yes' ? '연결됨' : '연결 안 됨')},
  music_c: {label: '음악 재생 중', mono: '음', group: '기기', fields: [state([['yes', '재생 중'], ['no', '재생 안 함']], 'yes')], sum: p => '음악 ' + (p.state === 'yes' ? '재생 중' : '재생 안 함')},
  ringer_c: {label: '소리 모드', mono: '벨', group: '기기', fields: [state([['normal', '소리'], ['vibrate', '진동'], ['silent', '무음']], 'normal')], sum: p => ({normal: '소리', vibrate: '진동', silent: '무음'}[p.state]) + ' 모드일 때'},
  locked_c: {label: '화면 잠김', mono: '잠', group: '기기', fields: [state([['yes', '잠겨 있음'], ['no', '잠금 해제됨']], 'yes')], sum: p => (p.state === 'yes' ? '잠겨 있음' : '잠금 해제됨')},
  airplane_c: {label: '비행기 모드', mono: '비', group: '기기', fields: [state([['on', '켜짐'], ['off', '꺼짐']], 'off')], sum: p => '비행기 모드 ' + (p.state === 'on' ? '켜짐' : '꺼짐')},
  bluetooth_c: {label: '블루투스 켜짐', mono: '블', group: '기기', fields: [state([['on', '켜짐'], ['off', '꺼짐']], 'on')], sum: p => '블루투스 ' + (p.state === 'on' ? '켜짐' : '꺼짐')},
  macro_on_c: {label: '다른 매크로 켜짐', mono: '켜', group: '변수', fields: [{k: 'id', l: '매크로', t: 'macro', d: ''}, state([['on', '켜져 있음'], ['off', '꺼져 있음']], 'on')], sum: (p, s) => `「${macroName(s, p.id)}」 ${p.state === 'on' ? '켜짐' : '꺼짐'}`},
  last_run_c: {label: '마지막 실행 후 시간', mono: '간', group: '변수', fields: [{k: 'min', l: '최소 몇 분 지나야 실행 (중복 방지)', t: 'number', d: 10}], sum: p => `마지막 실행 후 ${p.min}분 이상`},
  var_c: {label: '변수 값', mono: '변', group: '변수', fields: [text('name', '변수 이름', 'counter', {t: 'var'}), {k: 'op', l: '비교', t: 'select', o: OPS, d: 'eq'}, text('value', '값', '0')], sum: p => `${p.name} ${opText(p.op)} ${p.value}`},
};

export const KINDS = {
  trigger: {title: '트리거', cat: TRIGGERS, key: 'triggers', q: '언제 실행할까요?'},
  action: {title: '액션', cat: ACTIONS, key: 'actions', q: '무엇을 할까요?'},
  constraint: {title: '제약 조건', cat: CONSTRAINTS, key: 'constraints', q: '어떤 조건일 때만?'},
};

export function macroName(s, id) {
  const m = s && s.macros.find(x => x.id === id);
  return m ? m.name : '(매크로 선택 안 됨)';
}
export function defaults(def) {
  const p = {};
  def.fields.forEach(f => { p[f.k] = clone(f.d === undefined ? '' : f.d); });
  return p;
}
export function mk(kind, type, params) {
  return {id: uid(), type, params: Object.assign(defaults(KINDS[kind].cat[type]), params || {})};
}
export function summary(kind, it, s) {
  const def = KINDS[kind].cat[it.type];
  if (!def) return it.type;
  try { return def.sum(it.params, s); } catch (e) { return def.label; }
}

export const MAGIC = '{time} {date} {weekday} {battery} {not_app} {not_title} {not_text} {sms_from} {sms_body} {call_number} {bt_device} {app_name} {loop} {macro} {v=변수}';

export const TEMPLATES = [
  {name: '카톡 부재중 자동응답', category: '메시지', desc: '밤 10시~아침 7시에 카카오톡이 오면 정해진 문장으로 자동 답장합니다.',
    t: [['notification', {pkg: 'com.kakao.talk'}]], a: [['reply_notif', {text: '[자동응답] 지금은 쉬는 시간입니다. 아침에 답장드릴게요.'}]], c: [['time_between', {from: '22:00', to: '07:00'}]]},
  {name: '입금 문자 읽어주기', category: '메시지', desc: '문자에 "입금"이 들어 있으면 내용을 음성으로 읽고 알림을 띄웁니다.',
    t: [['sms', {body: '입금'}]], a: [['speak', {text: '입금 문자가 왔습니다. {sms_body}'}], ['notify', {title: '입금 알림', text: '{sms_from}: {sms_body}'}]], c: []},
  {name: '인증번호 복사', category: '메시지', desc: '"인증번호"가 들어간 문자가 오면 문자 전체를 클립보드에 복사합니다.',
    t: [['sms', {body: '인증번호,인증 번호,승인번호'}]], a: [['copy', {text: '{sms_body}'}], ['toast', {text: '인증 문자를 복사했습니다'}]], c: []},
  {name: '카톡 키워드 알림', category: '메시지', desc: '카톡 내용에 "급함"이나 "긴급"이 있으면 진동과 음성으로 크게 알려줍니다.',
    t: [['notification', {pkg: 'com.kakao.talk', text: '급함,긴급'}]], a: [['vibrate', {pattern: '600,200,600,200,600'}], ['speak', {text: '{not_title}님의 긴급 메시지. {not_text}'}]], c: []},
  {name: '카톡을 웹훅으로 전달', category: '메시지', desc: '받은 카톡을 지정한 주소(구글 시트, 텔레그램 봇 등)로 보냅니다. URL을 바꿔서 쓰세요.',
    t: [['notification', {pkg: 'com.kakao.talk'}]], a: [['http', {method: 'POST', url: 'https://example.com/webhook', body: '{"from":"{not_title}","text":"{not_text}","time":"{date} {time}"}'}]], c: []},
  {name: '부재중 전화 문자 답장', category: '전화', desc: '전화를 못 받으면 상대방에게 문자를 보냅니다.',
    t: [['call', {state: 'missed'}]], a: [['send_sms', {number: '{call_number}', text: '지금 통화가 어렵습니다. 곧 다시 연락드리겠습니다.'}]], c: []},
  {name: '인증번호만 복사', category: '메시지', desc: '인증 문자에서 숫자(4~8자리)만 뽑아 클립보드에 복사합니다.',
    t: [['sms', {body: '인증'}]], a: [['text_extract', {input: '{sms_body}', pattern: '\\d{4,8}', saveTo: 'code'}], ['copy', {text: '{v=code}'}], ['toast', {text: '인증번호 {v=code} 복사됨'}]], c: []},
  {name: '문자 전달', category: '메시지', desc: '은행 문자를 다른 번호로 그대로 전달합니다. 받는 번호를 꼭 바꿔서 쓰세요.',
    t: [['sms', {body: '입금,출금'}]], a: [['send_sms', {number: '010-0000-0000', text: '[전달] {sms_from}: {sms_body}'}]], c: []},
  {name: '카톡 자동 읽음 처리', category: '메시지', desc: '특정 단톡방(이름에 "공지" 포함) 카톡을 받으면 읽음 버튼을 눌러 숫자를 없앱니다.',
    t: [['notification', {pkg: 'com.kakao.talk', title: '공지'}]], a: [['press_notif_button', {label: '읽음'}]], c: []},
  {name: '흔들어서 손전등', category: '기기', desc: '휴대폰을 흔들면 손전등을 켜고 끕니다. 화면이 꺼져 있을 때만 동작합니다.',
    t: [['shake', {}]], a: [['flashlight', {state: 'toggle'}], ['vibrate', {pattern: '80'}]], c: [['screen_c', {state: 'off'}]]},
  {name: '차량 블루투스 연결 시 내비', category: '기기', desc: '이름에 "차량"이 들어간 블루투스 기기에 연결되면 미디어 볼륨을 올리고 앱을 엽니다. 앱은 편집에서 고르세요.',
    t: [['bluetooth', {state: 'connected', device: '차량'}]], a: [['set_volume', {stream: 'music', pct: 70}], ['launch_app', {pkg: ''}]], c: []},
  {name: '밤에는 진동 모드', category: '일상', desc: '매일 밤 23:00에 진동 모드로 바꿉니다. 아침에 되돌리려면 같은 매크로를 복사해 시각과 모드만 바꾸세요.',
    t: [['time', {time: '23:00', days: [0, 1, 2, 3, 4, 5, 6]}]], a: [['ringer_mode', {state: 'vibrate'}]], c: []},
  {name: '월급날 알림', category: '일상', desc: '매월 25일 09:00에 알려줍니다.',
    t: [['month_day', {day: 25, time: '09:00'}]], a: [['notify', {title: '월급날', text: '오늘은 {date} 월급날입니다.'}]], c: []},
  {name: '배터리 부족 경고', category: '배터리', desc: '배터리가 20% 이하로 떨어지면 알려줍니다.',
    t: [['battery', {op: 'below', level: 20}]], a: [['notify', {title: '배터리 부족', text: '배터리가 {battery}% 남았습니다.'}], ['speak', {text: '배터리가 {battery}퍼센트 남았습니다.'}]], c: [['charging_c', {state: 'not'}]]},
  {name: '아침 브리핑', category: '일상', desc: '평일 07:30에 날짜와 배터리를 읽어줍니다.',
    t: [['time', {time: '07:30', days: [1, 2, 3, 4, 5]}]], a: [['speak', {text: '좋은 아침입니다. 오늘은 {date} {weekday}입니다. 배터리는 {battery}퍼센트입니다.'}]], c: []},
  {name: '실행 횟수 카운터', category: '예제', desc: '실행할 때마다 counter를 1 올리고, 5회가 되면 축하 후 0으로 되돌립니다. If/Else 예제입니다.',
    t: [['manual', {}]], a: [['set_var', {name: 'counter', op: 'add', value: '1'}], ['if', {left: '{v=counter}', op: 'ge', right: '5'}], ['toast', {text: '5회 달성! 카운터를 초기화합니다.'}], ['set_var', {name: 'counter', op: 'set', value: '0'}], ['else', {}], ['toast', {text: '현재 {v=counter}회'}], ['end_if', {}]], c: []},
];

export function fromTemplate(tp) {
  return {
    id: uid(), name: tp.name, category: tp.category, enabled: false, lastRun: null,
    triggers: tp.t.map(([ty, p]) => mk('trigger', ty, p)),
    actions: tp.a.map(([ty, p]) => mk('action', ty, p)),
    constraints: tp.c.map(([ty, p]) => mk('constraint', ty, p)),
  };
}
