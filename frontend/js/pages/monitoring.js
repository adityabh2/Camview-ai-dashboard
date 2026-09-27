// pages/monitoring.js — MONITORING: is the camera working, is it recording, which centre has problems?
import { hub } from './hub.js';

export default hub('Monitoring', 'Centres, cameras and health', [
  { key: 'centres', icon: 'building', label: 'Centres', module: 'monitor', perms: ['live.view', 'alarm.view'], any: true },
  { key: 'cameras', icon: 'camera', label: 'Cameras', module: 'cameras', perms: ['camera.view'] },
  { key: 'health', icon: 'live', label: 'Camera health', module: 'health', perms: ['alarm.view'] },
  { key: 'map', icon: 'map', label: 'Map', module: 'map', perms: ['alarm.view', 'camera.view'], any: true, flag: 'ENABLE_MAP' },
]);
