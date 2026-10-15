import {NativeModules, Share} from 'react-native';

// Native bridge (android/app/src/main/java/com/automacro/AutoMacroModule.kt).
const N = NativeModules.AutoMacro || {};
const call = (name, ...args) => (N[name] ? N[name](...args) : undefined);

export const Native = {
  loadState: () => call('loadState') || Promise.resolve(null),
  saveState: json => call('saveState', json),
  status: () => call('status') || Promise.resolve({}),
  getDevice: () => call('getDevice') || Promise.resolve({}),
  listApps: () => call('listApps') || Promise.resolve([]),
  openNotificationAccess: () => call('openNotificationAccess'),
  openAppSettings: () => call('openAppSettings'),
  requestBatteryUnrestricted: () => call('requestBatteryUnrestricted'),
  setKeepAlive: on => call('setKeepAlive', on),
  notify: (t, x) => call('notify', t, x),
  toast: (x, long) => call('toast', x, !!long),
  vibrate: arr => call('vibrate', arr),
  beep: (ms, times) => call('beep', ms, times) || Promise.resolve(),
  speak: (x, rate) => call('speak', x, rate) || Promise.resolve(),
  sendSms: (n, x) => call('sendSms', n, x),
  replyNotification: (k, x) => call('replyNotification', k, x) || Promise.resolve(false),
  dismissNotification: k => call('dismissNotification', k) || Promise.resolve(false),
  launchApp: pkg => call('launchApp', pkg) || Promise.resolve(false),
  openUrl: url => call('openUrl', url),
  copy: x => call('copy', x),
  share: x => Share.share({message: x}),
  scheduleAlarm: (id, at) => call('scheduleAlarm', id, at),
  cancelAlarm: id => call('cancelAlarm', id),
};
