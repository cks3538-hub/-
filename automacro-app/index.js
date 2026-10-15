import {AppRegistry} from 'react-native';
import App from './src/App';
import {handleEvent} from './src/engine';
import {name as appName} from './app.json';

AppRegistry.registerComponent(appName, () => App);
// Native receivers (SMS, notifications, calls, alarms, power...) hand events here,
// also while the app UI is closed.
AppRegistry.registerHeadlessTask('AutoMacroEvent', () => handleEvent);
