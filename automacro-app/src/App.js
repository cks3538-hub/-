import React, {useEffect, useMemo, useState, useCallback, createContext, useContext} from 'react';
import {
  AppState, BackHandler, FlatList, Modal, PermissionsAndroid, Platform, Pressable, SafeAreaView, ScrollView,
  StatusBar, StyleSheet, Switch, Text, TextInput, View, useColorScheme,
} from 'react-native';
import * as store from './store';
import {Native} from './native';
import {fire, run, setVar, rescheduleAlarms, hhmm} from './engine';
import {
  KINDS, TRIGGERS, TEMPLATES, DAYS, MAGIC, clone, mk, summary, fromTemplate, uid, appText,
} from './catalog';

/* ---------- 테마: 트리거=빨강, 액션=파랑, 제약=초록 ---------- */
const LIGHT = {bg: '#EDEFF3', surface: '#FFFFFF', surface2: '#F5F6F9', line: '#D9DEE7', fg: '#141922', muted: '#5B6474', trig: '#CF3F37', act: '#2A64CC', cons: '#21895A', warn: '#B07A07', on: '#FFFFFF'};
const DARK = {bg: '#0D1015', surface: '#161A21', surface2: '#1D222B', line: '#2A303B', fg: '#E7EAF0', muted: '#97A0AF', trig: '#EF645B', act: '#5D92F2', cons: '#47C07F', warn: '#E0AE3C', on: '#0D1015'};
const KC = c => ({trigger: c.trig, action: c.act, constraint: c.cons});
const Theme = createContext({c: LIGHT, s: {}});
const useT = () => useContext(Theme);

function makeStyles(c) {
  return StyleSheet.create({
    root: {flex: 1, backgroundColor: c.bg},
    header: {flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderColor: c.line, backgroundColor: c.bg, gap: 10},
    h1: {flex: 1, fontSize: 19, fontWeight: '700', color: c.fg},
    content: {padding: 16, paddingBottom: 40, gap: 16},
    card: {backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, borderRadius: 14, overflow: 'hidden'},
    row: {flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 12, borderTopWidth: 1, borderColor: c.line},
    first: {borderTopWidth: 0},
    grow: {flex: 1, minWidth: 0},
    t: {fontWeight: '600', color: c.fg, fontSize: 15},
    sub: {fontSize: 13, color: c.muted, marginTop: 1},
    txt: {color: c.fg, fontSize: 15},
    muted: {color: c.muted, fontSize: 13},
    secH: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center'},
    secT: {fontSize: 12, fontWeight: '700', letterSpacing: 1, color: c.muted},
    mono: {width: 36, height: 36, borderRadius: 10, alignItems: 'center', justifyContent: 'center'},
    monoT: {fontWeight: '700', fontSize: 14, color: c.on},
    btn: {borderWidth: 1, borderColor: c.line, backgroundColor: c.surface, borderRadius: 10, paddingVertical: 9, paddingHorizontal: 14, alignItems: 'center', justifyContent: 'center'},
    btnT: {fontWeight: '600', fontSize: 14, color: c.fg},
    primary: {backgroundColor: c.act, borderColor: c.act},
    primaryT: {color: c.on},
    danger: {color: c.trig},
    btns: {flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center'},
    tiles: {flexDirection: 'row', flexWrap: 'wrap', gap: 10},
    tile: {width: '31.5%', minHeight: 88, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, borderRadius: 14, padding: 12, gap: 8},
    bar: {width: 26, height: 6, borderRadius: 3},
    notice: {borderWidth: 1, borderStyle: 'dashed', borderColor: c.line, borderRadius: 12, padding: 12},
    warnBox: {borderWidth: 1, borderColor: c.warn, borderRadius: 12, padding: 12, gap: 8, backgroundColor: c.surface},
    blockH: {flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, paddingVertical: 11, gap: 8},
    blockHT: {color: c.on, fontWeight: '700', fontSize: 15, flex: 1},
    add: {paddingHorizontal: 14, paddingVertical: 12, borderTopWidth: 1, borderColor: c.line},
    addT: {color: c.act, fontWeight: '700'},
    input: {borderWidth: 1, borderColor: c.line, backgroundColor: c.surface2, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, color: c.fg, fontSize: 15},
    label: {fontSize: 13, fontWeight: '600', color: c.muted, marginBottom: 6},
    chips: {flexDirection: 'row', flexWrap: 'wrap', gap: 6},
    chip: {borderWidth: 1, borderColor: c.line, backgroundColor: c.surface2, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7},
    chipOn: {backgroundColor: c.act, borderColor: c.act},
    chipT: {fontSize: 13, fontWeight: '600', color: c.fg},
    overlay: {flex: 1, backgroundColor: 'rgba(8,10,14,0.45)', justifyContent: 'flex-end'},
    sheet: {backgroundColor: c.surface, borderTopLeftRadius: 18, borderTopRightRadius: 18, maxHeight: '88%'},
    sheetIn: {padding: 16, gap: 14, paddingBottom: 28},
    grab: {width: 40, height: 4, borderRadius: 2, backgroundColor: c.line, alignSelf: 'center', marginTop: 8},
    h3: {fontSize: 18, fontWeight: '700', color: c.fg},
    pick: {flexDirection: 'row', flexWrap: 'wrap', gap: 8},
    pickB: {width: '48.5%', flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: 1, borderColor: c.line, backgroundColor: c.surface2, borderRadius: 12, padding: 9},
    tabs: {flexDirection: 'row', borderTopWidth: 1, borderColor: c.line, backgroundColor: c.surface},
    tab: {flex: 1, alignItems: 'center', paddingTop: 8, paddingBottom: 10, gap: 4},
    tabBar: {width: 22, height: 4, borderRadius: 2},
    tabT: {fontSize: 12, color: c.muted},
    logT: {fontFamily: 'monospace', fontSize: 12, color: c.fg, flex: 1},
    pill: {borderRadius: 999, paddingHorizontal: 9, paddingVertical: 2},
    pillT: {color: c.on, fontSize: 12, fontWeight: '600'},
  });
}

/* ---------- 공통 조각 ---------- */
function Btn({title, onPress, primary, danger, disabled, small}) {
  const {s} = useT();
  return (
    <Pressable disabled={disabled} onPress={onPress} style={({pressed}) => [s.btn, primary && s.primary, small && {paddingVertical: 6, paddingHorizontal: 10}, (pressed || disabled) && {opacity: disabled ? 0.4 : 0.7}]}>
      <Text style={[s.btnT, primary && s.primaryT, danger && s.danger, small && {fontSize: 13}]}>{title}</Text>
    </Pressable>
  );
}
function ConfirmBtn({title, armedTitle, onConfirm, small}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => { if (!armed) return; const t = setTimeout(() => setArmed(false), 3000); return () => clearTimeout(t); }, [armed]);
  return <Btn small={small} danger title={armed ? armedTitle : title} onPress={() => (armed ? onConfirm() : setArmed(true))} />;
}
function Mono({kind, def, off, size = 36}) {
  const {c, s} = useT();
  return (
    <View style={[s.mono, {width: size, height: size, backgroundColor: off ? c.line : KC(c)[kind]}]}>
      <Text style={[s.monoT, off && {color: c.muted}, size < 34 && {fontSize: 12}]}>{def ? def.mono : '?'}</Text>
    </View>
  );
}
function Sheet({visible, onClose, children}) {
  const {s} = useT();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={s.overlay} onPress={onClose}>
        <Pressable style={s.sheet} onPress={() => {}}>
          <View style={s.grab} />
          <ScrollView contentContainerStyle={s.sheetIn} keyboardShouldPersistTaps="handled">{children}</ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
function Section({title, right, children}) {
  const {s} = useT();
  return (
    <View style={{gap: 10}}>
      <View style={s.secH}><Text style={s.secT}>{title}</Text>{right}</View>
      {children}
    </View>
  );
}
function timeAgo(t) {
  if (!t) return '실행 기록 없음';
  const x = Math.floor((Date.now() - t) / 1000);
  if (x < 60) return '방금 실행';
  if (x < 3600) return Math.floor(x / 60) + '분 전 실행';
  if (x < 86400) return Math.floor(x / 3600) + '시간 전 실행';
  return Math.floor(x / 86400) + '일 전 실행';
}
function LogRow({l, first}) {
  const {c, s} = useT();
  const d = new Date(l.t);
  const lv = {run: ['실행', c.act], ok: ['완료', c.cons], skip: ['건너뜀', c.warn], err: ['오류', c.trig], info: ['정보', c.muted]}[l.lv] || [l.lv, c.muted];
  return (
    <View style={[s.row, first && s.first, {alignItems: 'flex-start', paddingVertical: 8, gap: 8}]}>
      <Text style={[s.logT, {flex: 0, color: c.muted}]}>{`${d.getMonth() + 1}/${d.getDate()} ${hhmm(d)}`}</Text>
      <Text style={{fontSize: 11, fontWeight: '700', color: lv[1]}}>{lv[0]}</Text>
      <Text style={s.logT}>{l.msg}</Text>
    </View>
  );
}

/* ---------- 권한 상태 ---------- */
const P = PermissionsAndroid.PERMISSIONS;
async function checkPerms() {
  const st = await Native.status().catch(() => ({}));
  const has = async p => (p ? PermissionsAndroid.check(p).catch(() => false) : true);
  return {
    ...st,
    sms: await has(P.RECEIVE_SMS),
    sendSms: await has(P.SEND_SMS),
    phone: (await has(P.READ_PHONE_STATE)) && (await has(P.READ_CALL_LOG)),
    post: Platform.Version >= 33 ? await has(P.POST_NOTIFICATIONS) : true,
  };
}
function needs(st) {
  const types = new Set();
  st.macros.filter(m => m.enabled).forEach(m => { m.triggers.forEach(t => types.add('t:' + t.type)); m.actions.forEach(a => types.add('a:' + a.type)); });
  return types;
}

/* ---------- 홈 ---------- */
function Home({st, perms, go, onNew, refreshPerms}) {
  const {c, s} = useT();
  const [dev, setDev] = useState({});
  useEffect(() => { Native.getDevice().then(setDev).catch(() => {}); }, [st.log.length]);
  const enabled = st.macros.filter(m => m.enabled).length;
  const shortcuts = st.macros.filter(m => m.enabled && m.triggers.some(t => t.type === 'manual'));
  const n = needs(st);
  const warns = [];
  if ((n.has('t:notification') || n.has('a:reply_notif')) && !perms.notificationAccess) warns.push(['카톡 등 알림 감지가 꺼져 있습니다', '알림 접근 허용', () => Native.openNotificationAccess()]);
  if (n.has('t:sms') && !perms.sms) warns.push(['문자 수신 권한이 없습니다', '권한 허용', () => PermissionsAndroid.request(P.RECEIVE_SMS).then(refreshPerms)]);
  if (n.has('t:call') && !perms.phone) warns.push(['전화 상태 권한이 없습니다', '권한 허용', () => PermissionsAndroid.requestMultiple([P.READ_PHONE_STATE, P.READ_CALL_LOG]).then(refreshPerms)]);
  if (enabled && !perms.keepAlive) warns.push(['앱을 닫아도 계속 감지하려면 「항상 실행」을 켜세요', '항상 실행 켜기', () => { Native.setKeepAlive(true); setTimeout(refreshPerms, 800); }]);
  const tiles = [
    ['매크로 추가', '트리거·액션 만들기', c.trig, onNew],
    ['매크로', st.macros.length + '개', c.act, () => go('macros')],
    ['템플릿', TEMPLATES.length + '개 준비됨', c.cons, () => go('templates')],
    ['변수', Object.keys(st.vars).length + '개', c.warn, () => go('vars')],
    ['시스템 로그', st.log.length + '줄', c.muted, () => go('log')],
    ['설정·권한', '알림 접근·백업', c.line, () => go('settings')],
  ];
  return (
    <>
      <View style={[s.card, {padding: 14, gap: 12}]}>
        <View style={{flexDirection: 'row', alignItems: 'center', gap: 10}}>
          <View style={{width: 10, height: 10, borderRadius: 5, backgroundColor: enabled ? c.cons : c.muted}} />
          <View style={s.grow}>
            <Text style={s.t}>{enabled ? (perms.keepAlive ? '매크로 엔진 상시 실행 중' : '매크로 엔진 작동 중') : '켜진 매크로 없음'}</Text>
            <Text style={s.sub}>켜진 매크로 {enabled}개 / 전체 {st.macros.length}개</Text>
          </View>
        </View>
        <View style={{flexDirection: 'row', gap: 12, borderTopWidth: 1, borderColor: c.line, paddingTop: 12}}>
          <View style={s.grow}><Text style={s.muted}>배터리</Text><Text style={[s.t, {fontSize: 18}]}>{dev.battery == null ? '–' : dev.battery + '%'}</Text><Text style={s.muted}>{dev.charging ? '충전 중' : '충전 안 함'}</Text></View>
          <View style={s.grow}><Text style={s.muted}>인터넷</Text><Text style={[s.t, {fontSize: 18}]}>{dev.online ? (dev.wifi ? '와이파이' : '모바일') : '끊김'}</Text><Text style={s.muted}>알림 감지 {perms.notificationAccess ? '켜짐' : '꺼짐'}</Text></View>
        </View>
      </View>
      {warns.map(([msg, b, fn]) => (
        <View key={msg} style={s.warnBox}><Text style={s.txt}>{msg}</Text><View style={s.btns}><Btn small primary title={b} onPress={fn} /></View></View>
      ))}
      <View style={s.tiles}>
        {tiles.map(([l, sub, col, fn]) => (
          <Pressable key={l} style={({pressed}) => [s.tile, pressed && {opacity: 0.7}]} onPress={fn}>
            <View style={[s.bar, {backgroundColor: col}]} />
            <Text style={[s.t, {fontSize: 14}]}>{l}</Text>
            <Text style={[s.muted, {fontSize: 12}]}>{sub}</Text>
          </Pressable>
        ))}
      </View>
      <Section title="바로가기">
        <View style={s.card}>
          {shortcuts.length ? shortcuts.map((m, i) => (
            <View key={m.id} style={[s.row, i === 0 && s.first]}>
              <Mono kind="trigger" def={TRIGGERS.manual} />
              <View style={s.grow}><Text style={s.t}>{m.name}</Text><Text style={s.sub}>{timeAgo(m.lastRun)}</Text></View>
              <Btn small primary title="실행" onPress={() => fire(m.id, {label: '바로가기'})} />
            </View>
          )) : <View style={{padding: 14}}><Text style={s.muted}>「바로가기 (수동 실행)」 트리거를 넣은 매크로가 여기에 버튼으로 나타납니다.</Text></View>}
        </View>
      </Section>
      <Section title="최근 기록" right={<Pressable onPress={() => go('log')}><Text style={[s.muted, {fontWeight: '600'}]}>전체 보기</Text></Pressable>}>
        <View style={s.card}>{st.log.slice(0, 5).map((l, i) => <LogRow key={l.t + '-' + i} l={l} first={i === 0} />)}</View>
      </Section>
    </>
  );
}

/* ---------- 매크로 목록 ---------- */
function MacroList({st, onOpen, onNew}) {
  const {s} = useT();
  const [q, setQ] = useState('');
  const list = st.macros.filter(m => !q || (m.name + m.category).toLowerCase().includes(q.toLowerCase()));
  const cats = [...new Set(list.map(m => m.category || '미분류'))];
  const toggle = (id, v) => { store.update(x => ({...x, macros: x.macros.map(m => (m.id === id ? {...m, enabled: v} : m))})); rescheduleAlarms(); };
  return (
    <>
      <View style={{flexDirection: 'row', gap: 8}}>
        <TextInput style={[s.input, s.grow]} placeholder="매크로 검색" placeholderTextColor="#8A93A3" value={q} onChangeText={setQ} />
        <Btn primary title="추가" onPress={onNew} />
      </View>
      {cats.map(cat => (
        <Section key={cat} title={cat}>
          <View style={s.card}>
            {list.filter(m => (m.category || '미분류') === cat).map((m, i) => (
              <Pressable key={m.id} style={[s.row, i === 0 && s.first]} onPress={() => onOpen(m.id)}>
                <Mono kind="trigger" def={TRIGGERS[(m.triggers[0] || {}).type]} off={!m.enabled} />
                <View style={s.grow}>
                  <Text style={s.t}>{m.name}</Text>
                  <Text style={s.sub} numberOfLines={2}>{m.triggers.length ? summary('trigger', m.triggers[0], st) : '트리거 없음'} · 액션 {m.actions.length}개{m.constraints.length ? ` · 조건 ${m.constraints.length}개` : ''}</Text>
                </View>
                <Switch value={m.enabled} onValueChange={v => toggle(m.id, v)} />
              </Pressable>
            ))}
          </View>
        </Section>
      ))}
      {!list.length && <Text style={s.muted}>{q ? '검색 결과가 없습니다.' : '매크로가 없습니다. 추가 버튼이나 템플릿으로 시작하세요.'}</Text>}
    </>
  );
}

/* ---------- 편집기 ---------- */
function AppPicker({visible, onClose, onPick}) {
  const {s} = useT();
  const [apps, setApps] = useState(null);
  const [q, setQ] = useState('');
  useEffect(() => { if (visible && !apps) Native.listApps().then(setApps).catch(() => setApps([])); }, [visible, apps]);
  const list = [{pkg: '*', label: '모든 앱'}].concat(apps || []).filter(a => !q || (a.label + a.pkg).toLowerCase().includes(q.toLowerCase()));
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={s.root}>
        <View style={s.header}><Pressable onPress={onClose}><Text style={[s.muted, {fontWeight: '600'}]}>닫기</Text></Pressable><Text style={s.h1}>앱 선택</Text></View>
        <View style={{padding: 16}}><TextInput style={s.input} placeholder="앱 이름 검색 (예: 카카오톡)" placeholderTextColor="#8A93A3" value={q} onChangeText={setQ} /></View>
        <FlatList data={list} keyExtractor={a => a.pkg} renderItem={({item, index}) => (
          <Pressable style={[s.row, index === 0 && s.first]} onPress={() => onPick(item)}>
            <View style={s.grow}><Text style={s.t}>{item.label}</Text><Text style={s.sub}>{item.pkg === '*' ? '어떤 앱이든' : item.pkg}</Text></View>
          </Pressable>
        )} ListEmptyComponent={<Text style={[s.muted, {padding: 16}]}>{apps ? '앱이 없습니다.' : '앱 목록을 불러오는 중…'}</Text>} />
      </SafeAreaView>
    </Modal>
  );
}
function Field({f, params, setParams, st}) {
  const {s} = useT();
  const [appOpen, setAppOpen] = useState(false);
  const v = params[f.k];
  const set = val => setParams({...params, [f.k]: val});
  const Label = <Text style={s.label}>{f.l}</Text>;
  switch (f.t) {
    case 'select':
      return <View>{Label}<View style={s.chips}>{f.o.map(([k, l]) => (
        <Pressable key={k} style={[s.chip, v === k && s.chipOn]} onPress={() => set(k)}><Text style={[s.chipT, v === k && s.primaryT]}>{l}</Text></Pressable>
      ))}</View></View>;
    case 'days':
      return <View>{Label}<View style={s.chips}>{DAYS.map((d, i) => {
        const on = (v || []).includes(i);
        return <Pressable key={i} style={[s.chip, on && s.chipOn]} onPress={() => set(on ? v.filter(x => x !== i) : (v || []).concat(i))}><Text style={[s.chipT, on && s.primaryT]}>{d}</Text></Pressable>;
      })}</View></View>;
    case 'macro':
      return <View>{Label}<View style={s.chips}>{st.macros.map(m => (
        <Pressable key={m.id} style={[s.chip, v === m.id && s.chipOn]} onPress={() => set(m.id)}><Text style={[s.chipT, v === m.id && s.primaryT]}>{m.name}</Text></Pressable>
      ))}</View></View>;
    case 'app':
      return <View>{Label}
        <View style={s.chips}>{['com.kakao.talk', '*'].map(k => (
          <Pressable key={k} style={[s.chip, v === k && s.chipOn]} onPress={() => setParams({...params, [f.k]: k, pkgLabel: ''})}><Text style={[s.chipT, v === k && s.primaryT]}>{appText(k)}</Text></Pressable>
        ))}
          <Pressable style={s.chip} onPress={() => setAppOpen(true)}><Text style={s.chipT}>다른 앱 고르기…</Text></Pressable>
        </View>
        <Text style={[s.muted, {marginTop: 6}]}>선택됨: {appText(v, params.pkgLabel)}</Text>
        <AppPicker visible={appOpen} onClose={() => setAppOpen(false)} onPick={a => { setParams({...params, [f.k]: a.pkg, pkgLabel: a.pkg === '*' ? '' : a.label}); setAppOpen(false); }} />
      </View>;
    case 'number':
      return <View>{Label}<TextInput style={s.input} keyboardType="numeric" value={v === '' || v == null ? '' : String(v)} onChangeText={x => set(x === '' ? '' : Number(x.replace(/[^0-9.]/g, '')))} /></View>;
    case 'textarea':
      return <View>{Label}<TextInput style={[s.input, {minHeight: 84, textAlignVertical: 'top'}]} multiline value={String(v ?? '')} onChangeText={set} />
        <Text style={[s.muted, {fontSize: 12, marginTop: 6}]}>매직 텍스트: {MAGIC}</Text></View>;
    case 'var':
      return <View>{Label}<TextInput style={s.input} autoCapitalize="none" value={String(v ?? '')} onChangeText={x => set(x.trim())} />
        {!!Object.keys(st.vars).length && <View style={[s.chips, {marginTop: 6}]}>{Object.keys(st.vars).map(k => (
          <Pressable key={k} style={s.chip} onPress={() => set(k)}><Text style={s.chipT}>{k}</Text></Pressable>
        ))}</View>}</View>;
    case 'time':
      return <View>{Label}<TextInput style={s.input} keyboardType="numbers-and-punctuation" placeholder="07:30" value={String(v ?? '')} onChangeText={set} /></View>;
    default:
      return <View>{Label}<TextInput style={s.input} autoCapitalize="none" value={String(v ?? '')} onChangeText={set} /></View>;
  }
}
function ItemForm({kind, item, st, onSave, onCancel}) {
  const {s} = useT();
  const def = KINDS[kind].cat[item.type];
  const [params, setParams] = useState(clone(item.params));
  return (
    <Sheet visible onClose={onCancel}>
      <View style={{flexDirection: 'row', alignItems: 'center', gap: 10}}><Mono kind={kind} def={def} /><Text style={s.h3}>{def.label}</Text></View>
      {def.fields.length ? def.fields.map(f => <Field key={f.k} f={f} params={params} setParams={setParams} st={st} />) : <Text style={s.muted}>설정할 항목이 없습니다.</Text>}
      <View style={[s.btns, {justifyContent: 'flex-end'}]}><Btn title="취소" onPress={onCancel} /><Btn primary title="확인" onPress={() => onSave({...item, params})} /></View>
    </Sheet>
  );
}
function Picker({kind, onPick, onClose}) {
  const {s} = useT();
  const K = KINDS[kind], groups = {};
  Object.entries(K.cat).forEach(([k, d]) => { (groups[d.group] = groups[d.group] || []).push([k, d]); });
  return (
    <Sheet visible onClose={onClose}>
      <Text style={s.h3}>{K.title} 추가</Text>
      <Text style={s.muted}>{K.q}</Text>
      {Object.entries(groups).map(([g, items]) => (
        <View key={g} style={{gap: 8}}>
          <Text style={s.secT}>{g}</Text>
          <View style={s.pick}>{items.map(([k, d]) => (
            <Pressable key={k} style={({pressed}) => [s.pickB, pressed && {opacity: 0.7}]} onPress={() => onPick(k)}>
              <Mono kind={kind} def={d} size={30} /><Text style={[s.t, {fontSize: 13, flex: 1}]}>{d.label}</Text>
            </Pressable>
          ))}</View>
        </View>
      ))}
    </Sheet>
  );
}
function Block({kind, items, st, onAdd, onEdit, onDel, onMove}) {
  const {c, s} = useT();
  const K = KINDS[kind];
  let depth = 0;
  return (
    <View style={s.card}>
      <View style={[s.blockH, {backgroundColor: KC(c)[kind]}]}><Text style={s.blockHT}>{K.title} {items.length}</Text><Text style={{color: c.on, fontSize: 12}}>{K.q}</Text></View>
      {!items.length && <Text style={[s.muted, {padding: 14}]}>{kind === 'constraint' ? '제약 조건이 없으면 트리거가 감지될 때마다 실행됩니다.' : '아직 없습니다.'}</Text>}
      {items.map((it, i) => {
        if (it.type === 'else' || it.type === 'end_if') depth = Math.max(0, depth - 1);
        const ind = depth;
        if (it.type === 'if' || it.type === 'else') depth++;
        const def = K.cat[it.type];
        return (
          <View key={it.id} style={[s.row, i === 0 && s.first, {paddingLeft: 14 + ind * 16, paddingVertical: 10, gap: 10}]}>
            <Mono kind={kind} def={def} size={30} />
            <Pressable style={s.grow} onPress={() => onEdit(i)}>
              <Text style={[s.t, {fontSize: 14}]}>{def ? def.label : it.type}</Text>
              <Text style={s.sub} numberOfLines={3}>{summary(kind, it, st)}</Text>
            </Pressable>
            {kind === 'action' && <>
              <Pressable disabled={i === 0} onPress={() => onMove(i, -1)} hitSlop={6}><Text style={[s.muted, {fontWeight: '700', opacity: i === 0 ? 0.3 : 1}]}>위</Text></Pressable>
              <Pressable disabled={i === items.length - 1} onPress={() => onMove(i, 1)} hitSlop={6}><Text style={[s.muted, {fontWeight: '700', opacity: i === items.length - 1 ? 0.3 : 1}]}>아래</Text></Pressable>
            </>}
            <Pressable onPress={() => onDel(i)} hitSlop={6}><Text style={[s.muted, {fontWeight: '700'}]}>삭제</Text></Pressable>
          </View>
        );
      })}
      <Pressable style={s.add} onPress={onAdd}><Text style={s.addT}>+ {K.title} 추가</Text></Pressable>
    </View>
  );
}
function Editor({macro, st, onSave, onDelete, onClose}) {
  const {s} = useT();
  const [d, setD] = useState(clone(macro));
  const [sheet, setSheet] = useState(null);
  const setList = (kind, list) => setD(x => ({...x, [KINDS[kind].key]: list}));
  const addItem = (kind, it) => {
    const list = d[KINDS[kind].key].slice();
    list.push(it);
    if (kind === 'action' && it.type === 'if') list.push(mk('action', 'end_if'));
    setList(kind, list);
  };
  const pick = (kind, type) => {
    const it = mk(kind, type);
    if (KINDS[kind].cat[type].fields.length) setSheet({mode: 'form', kind, item: it, index: -1});
    else { addItem(kind, it); setSheet(null); }
  };
  const saveItem = it => {
    const {kind, index} = sheet;
    if (index < 0) addItem(kind, it);
    else { const l = d[KINDS[kind].key].slice(); l[index] = it; setList(kind, l); }
    setSheet(null);
  };
  const valid = d.name.trim() && d.triggers.length && d.actions.length;
  const exists = st.macros.some(m => m.id === d.id);
  const block = kind => (
    <Block kind={kind} items={d[KINDS[kind].key]} st={st}
      onAdd={() => setSheet({mode: 'pick', kind})}
      onEdit={i => setSheet({mode: 'form', kind, item: d[KINDS[kind].key][i], index: i})}
      onDel={i => setList(kind, d[KINDS[kind].key].filter((_, j) => j !== i))}
      onMove={(i, dir) => { const l = d[KINDS[kind].key].slice(); [l[i], l[i + dir]] = [l[i + dir], l[i]]; setList(kind, l); }} />
  );
  return (
    <>
      <View style={[s.card, {padding: 14, gap: 12}]}>
        <View><Text style={s.label}>매크로 이름</Text><TextInput style={s.input} value={d.name} placeholder="예: 카톡 자동응답" placeholderTextColor="#8A93A3" onChangeText={x => setD({...d, name: x})} /></View>
        <View><Text style={s.label}>분류</Text><TextInput style={s.input} value={d.category} placeholder="예: 메시지" placeholderTextColor="#8A93A3" onChangeText={x => setD({...d, category: x})} /></View>
        <View style={{flexDirection: 'row', alignItems: 'center', gap: 10}}><Switch value={d.enabled} onValueChange={v => setD({...d, enabled: v})} /><Text style={s.txt}>{d.enabled ? '켜짐' : '꺼짐'}</Text></View>
      </View>
      {block('trigger')}{block('action')}{block('constraint')}
      {!valid && <View style={s.notice}><Text style={s.muted}>저장하려면 이름, 트리거 1개 이상, 액션 1개 이상이 필요합니다.</Text></View>}
      <View style={s.btns}>
        <Btn primary disabled={!valid} title="저장" onPress={() => onSave({...d, name: d.name.trim(), category: (d.category || '').trim() || '미분류'})} />
        <Btn disabled={!d.actions.length} title="테스트 실행" onPress={() => run(d, {label: '테스트 실행', test: true})} />
        <Btn title="취소" onPress={onClose} />
        <View style={{flex: 1}} />
        {exists && <ConfirmBtn title="삭제" armedTitle="한 번 더 누르면 삭제" onConfirm={() => onDelete(d.id)} />}
      </View>
      {sheet && sheet.mode === 'pick' && <Picker kind={sheet.kind} onClose={() => setSheet(null)} onPick={t => pick(sheet.kind, t)} />}
      {sheet && sheet.mode === 'form' && <ItemForm kind={sheet.kind} item={sheet.item} st={st} onCancel={() => setSheet(null)} onSave={saveItem} />}
    </>
  );
}

/* ---------- 템플릿 / 변수 / 로그 / 설정 ---------- */
function Templates({onUse}) {
  const {c, s} = useT();
  return TEMPLATES.map(tp => (
    <View key={tp.name} style={[s.card, {padding: 14, gap: 10}]}>
      <View><Text style={s.muted}>{tp.category}</Text><Text style={[s.t, {fontSize: 16}]}>{tp.name}</Text></View>
      <Text style={s.txt}>{tp.desc}</Text>
      <View style={s.chips}>
        {tp.t.map(([ty], i) => <View key={'t' + i} style={[s.pill, {backgroundColor: c.trig}]}><Text style={s.pillT}>{KINDS.trigger.cat[ty].label}</Text></View>)}
        {tp.a.map(([ty], i) => <View key={'a' + i} style={[s.pill, {backgroundColor: c.act}]}><Text style={s.pillT}>{KINDS.action.cat[ty].label}</Text></View>)}
        {tp.c.map(([ty], i) => <View key={'c' + i} style={[s.pill, {backgroundColor: c.cons}]}><Text style={s.pillT}>{KINDS.constraint.cat[ty].label}</Text></View>)}
      </View>
      <View style={s.btns}><Btn small primary title="내 매크로로 추가" onPress={() => onUse(tp)} /></View>
    </View>
  ));
}
function Vars({st}) {
  const {s} = useT();
  const [n, setN] = useState('');
  const [v, setV] = useState('0');
  const names = Object.keys(st.vars).sort();
  return (
    <>
      <View style={s.card}>
        {names.map((k, i) => (
          <View key={k} style={[s.row, i === 0 && s.first]}>
            <View style={s.grow}><Text style={[s.t, {fontFamily: 'monospace'}]}>{k}</Text>
              <TextInput style={[s.input, {marginTop: 6}]} defaultValue={String(st.vars[k])} onEndEditing={e => setVar(k, 'set', e.nativeEvent.text)} /></View>
            <ConfirmBtn small title="삭제" armedTitle="확인" onConfirm={() => store.update(x => { const vs = {...x.vars}; delete vs[k]; return {...x, vars: vs}; })} />
          </View>
        ))}
        <View style={[s.row, !names.length && s.first]}>
          <TextInput style={[s.input, s.grow]} placeholder="새 변수 이름" placeholderTextColor="#8A93A3" autoCapitalize="none" value={n} onChangeText={setN} />
          <TextInput style={[s.input, {width: 80}]} placeholder="초기값" placeholderTextColor="#8A93A3" value={v} onChangeText={setV} />
          <Btn primary small title="추가" onPress={() => { if (n.trim()) { setVar(n.trim(), 'set', v); setN(''); setV('0'); } }} />
        </View>
      </View>
      <View style={s.notice}><Text style={s.muted}>변수는 모든 매크로가 함께 씁니다. 문장에 {'{v=변수이름}'}을 쓰면 값으로 바뀌고, If 액션과 「변수 값 변경」 트리거에서 비교할 수 있습니다.</Text></View>
    </>
  );
}
function LogView({st}) {
  const {s} = useT();
  const [f, setF] = useState('all');
  const list = st.log.filter(l => f === 'all' || l.lv === f);
  return (
    <>
      <View style={s.btns}>
        {[['all', '전체'], ['run', '실행'], ['skip', '건너뜀'], ['err', '오류']].map(([k, l]) => (
          <Pressable key={k} style={[s.chip, f === k && s.chipOn]} onPress={() => setF(k)}><Text style={[s.chipT, f === k && s.primaryT]}>{l}</Text></Pressable>
        ))}
        <View style={{flex: 1}} />
        <ConfirmBtn small title="기록 지우기" armedTitle="한 번 더 누르면 지움" onConfirm={() => store.update(x => ({...x, log: []}))} />
      </View>
      <View style={s.card}>{list.map((l, i) => <LogRow key={l.t + '-' + i} l={l} first={i === 0} />)}{!list.length && <Text style={[s.muted, {padding: 14}]}>기록이 없습니다.</Text>}</View>
    </>
  );
}
function Settings({st, perms, refreshPerms}) {
  const {c, s} = useT();
  const [imp, setImp] = useState('');
  const [msg, setMsg] = useState('');
  const req = list => PermissionsAndroid.requestMultiple(list).then(refreshPerms);
  const rows = [
    ['알림 접근 (카톡·모든 앱 알림 감지, 자동 답장)', perms.notificationAccess, '설정 열기', () => Native.openNotificationAccess()],
    ['문자 수신 감지', perms.sms, '허용', () => req([P.RECEIVE_SMS])],
    ['문자 보내기', perms.sendSms, '허용', () => req([P.SEND_SMS])],
    ['전화 수신 감지 (번호 포함)', perms.phone, '허용', () => req([P.READ_PHONE_STATE, P.READ_CALL_LOG])],
    ['알림 표시', perms.post, '허용', () => req([P.POST_NOTIFICATIONS])],
    ['배터리 최적화 제외 (백그라운드 안정성)', perms.batteryUnrestricted, '허용', () => Native.requestBatteryUnrestricted()],
  ];
  return (
    <>
      <Section title="권한">
        <View style={s.card}>
          {rows.map(([t, ok, b, fn], i) => (
            <View key={t} style={[s.row, i === 0 && s.first]}>
              <View style={s.grow}><Text style={[s.t, {fontSize: 14}]}>{t}</Text><Text style={[s.sub, {color: ok ? c.cons : c.trig}]}>{ok ? '허용됨' : '꺼져 있음'}</Text></View>
              {!ok && <Btn small primary title={b} onPress={fn} />}
            </View>
          ))}
          <View style={s.row}>
            <View style={s.grow}><Text style={[s.t, {fontSize: 14}]}>항상 실행</Text><Text style={s.sub}>앱을 닫아도 충전·화면·배터리·인터넷 이벤트를 감지합니다. 상단에 작은 알림이 표시됩니다.</Text></View>
            <Switch value={!!perms.keepAlive} onValueChange={v => { Native.setKeepAlive(v); setTimeout(refreshPerms, 800); }} />
          </View>
        </View>
        <View style={s.notice}><Text style={s.muted}>버튼을 눌러도 회색으로 막혀 있으면: 휴대폰 설정 → 애플리케이션 → 오토매크로 → 오른쪽 위 ⋮ → 「제한된 설정 허용」을 누른 뒤 다시 시도하세요. (Play 스토어 밖에서 설치한 앱에 안드로이드가 거는 잠금입니다.)</Text>
          <View style={[s.btns, {marginTop: 8}]}><Btn small title="앱 정보 열기" onPress={() => Native.openAppSettings()} /></View></View>
      </Section>
      <Section title="백업 · 복원">
        <View style={[s.card, {padding: 14, gap: 10}]}>
          <View style={s.btns}>
            <Btn small title="백업 내용 복사" onPress={() => { Native.copy(JSON.stringify({macros: st.macros, vars: st.vars})); setMsg('백업 내용을 클립보드에 복사했습니다. 메모장이나 카톡 나에게 보내기에 붙여넣어 보관하세요.'); }} />
            <Btn small title="공유로 보내기" onPress={() => Native.share(JSON.stringify({macros: st.macros, vars: st.vars}))} />
          </View>
          <TextInput style={[s.input, {minHeight: 90, textAlignVertical: 'top', fontFamily: 'monospace', fontSize: 12}]} multiline placeholder="복원하려면 백업 내용을 여기에 붙여넣으세요" placeholderTextColor="#8A93A3" value={imp} onChangeText={setImp} />
          <View style={s.btns}>
            <Btn small disabled={!imp.trim()} title="가져오기" onPress={() => {
              try { const j = JSON.parse(imp); if (!Array.isArray(j.macros)) throw new Error(); store.update(x => ({...x, macros: j.macros, vars: j.vars || {}})); rescheduleAlarms(); setImp(''); setMsg(`매크로 ${j.macros.length}개를 가져왔습니다.`); }
              catch (e) { setMsg('형식이 맞지 않습니다. 백업 내용을 통째로 붙여넣어 주세요.'); }
            }} />
            <View style={{flex: 1}} />
            <ConfirmBtn small title="전체 초기화" armedTitle="한 번 더 누르면 초기화" onConfirm={() => { store.replaceAll(store.freshSeed()); rescheduleAlarms(); setMsg('처음 상태로 되돌렸습니다.'); }} />
          </View>
          {!!msg && <Text style={s.muted}>{msg}</Text>}
        </View>
      </Section>
    </>
  );
}

/* ---------- 앱 ---------- */
export default function App() {
  const dark = useColorScheme() === 'dark';
  const c = dark ? DARK : LIGHT;
  const s = useMemo(() => makeStyles(c), [c]);
  const [st, setSt] = useState(store.get());
  const [tab, setTab] = useState('home');
  const [editing, setEditing] = useState(null);
  const [perms, setPerms] = useState({});
  const refreshPerms = useCallback(() => { checkPerms().then(setPerms); }, []);

  useEffect(() => {
    const un = store.subscribe(setSt);
    store.ready().then(x => { setSt(x); rescheduleAlarms(); });
    refreshPerms();
    const sub = AppState.addEventListener('change', a => { if (a === 'active') refreshPerms(); });
    return () => { un(); sub.remove(); };
  }, [refreshPerms]);
  useEffect(() => {
    const h = BackHandler.addEventListener('hardwareBackPress', () => {
      if (editing) { setEditing(null); return true; }
      if (tab !== 'home') { setTab('home'); return true; }
      return false;
    });
    return () => h.remove();
  }, [editing, tab]);

  if (!st) return <View style={{flex: 1, backgroundColor: c.bg}} />;

  const go = t => { setEditing(null); setTab(t); };
  const newMacro = () => setEditing({id: uid(), name: '', category: '', enabled: true, lastRun: null, triggers: [], actions: [], constraints: []});
  const openMacro = id => setEditing(clone(st.macros.find(m => m.id === id)));
  const saveMacro = m => {
    store.update(x => ({...x, macros: x.macros.some(y => y.id === m.id) ? x.macros.map(y => (y.id === m.id ? m : y)) : x.macros.concat(m)}));
    rescheduleAlarms();
    Native.toast(`「${m.name}」 저장됨`);
    setEditing(null); setTab('macros');
  };
  const titles = {home: '오토매크로', macros: '매크로', templates: '템플릿', vars: '변수', log: '시스템 로그', settings: '설정·권한'};
  let body;
  if (editing) body = <Editor key={editing.id} macro={editing} st={st} onSave={saveMacro} onClose={() => setEditing(null)} onDelete={id => { store.update(x => ({...x, macros: x.macros.filter(y => y.id !== id)})); rescheduleAlarms(); setEditing(null); setTab('macros'); }} />;
  else if (tab === 'home') body = <Home st={st} perms={perms} go={go} onNew={newMacro} refreshPerms={refreshPerms} />;
  else if (tab === 'macros') body = <MacroList st={st} onOpen={openMacro} onNew={newMacro} />;
  else if (tab === 'templates') body = <Templates onUse={tp => { const m = fromTemplate(tp); store.update(x => ({...x, macros: x.macros.concat(m)})); Native.toast(`「${m.name}」 추가됨 · 꺼진 상태입니다. 내용을 확인하고 켜세요.`, true); go('macros'); }} />;
  else if (tab === 'vars') body = <Vars st={st} />;
  else if (tab === 'log') body = <LogView st={st} />;
  else body = <Settings st={st} perms={perms} refreshPerms={refreshPerms} />;
  const TABS = [['home', '홈'], ['macros', '매크로'], ['templates', '템플릿'], ['vars', '변수'], ['log', '로그']];

  return (
    <Theme.Provider value={{c, s}}>
      <SafeAreaView style={s.root}>
        <StatusBar barStyle={dark ? 'light-content' : 'dark-content'} backgroundColor={c.bg} />
        <View style={s.header}>
          {editing ? <>
            <Pressable onPress={() => setEditing(null)} hitSlop={8}><Text style={[s.muted, {fontWeight: '700'}]}>뒤로</Text></Pressable>
            <Text style={s.h1}>{st.macros.some(m => m.id === editing.id) ? '매크로 편집' : '새 매크로'}</Text>
          </> : <>
            {tab === 'home' && <View style={{flexDirection: 'row', gap: 2}}>{[c.trig, c.act, c.cons].map(x => <View key={x} style={{width: 6, height: 14, borderRadius: 2, backgroundColor: x}} />)}</View>}
            <Text style={s.h1}>{titles[tab]}</Text>
            {tab !== 'settings' && <Pressable onPress={() => go('settings')} hitSlop={8}><Text style={[s.muted, {fontWeight: '700'}]}>설정</Text></Pressable>}
          </>}
        </View>
        <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">{body}</ScrollView>
        <View style={s.tabs}>
          {TABS.map(([k, l]) => {
            const on = !editing && tab === k;
            return (
              <Pressable key={k} style={s.tab} onPress={() => go(k)}>
                <View style={[s.tabBar, on && {backgroundColor: c.act}]} />
                <Text style={[s.tabT, on && {color: c.fg, fontWeight: '700'}]}>{l}</Text>
              </Pressable>
            );
          })}
        </View>
      </SafeAreaView>
    </Theme.Provider>
  );
}
