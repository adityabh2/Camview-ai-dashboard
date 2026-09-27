// pages/management.js — MANAGEMENT: the mapping the automation relies on
// (Client → Exam → Project → TC → Centre → … → Camera) and its data quality.
import { hub } from './hub.js';

export default hub('Management', 'Clients, exams and mappings', [
  { key: 'clients', label: 'Clients', module: 'clients', perms: ['client.view'] },
  { key: 'exams', label: 'Exams', module: 'exams', perms: ['alarm.view', 'client.view'], any: true },
  { key: 'mappings', label: 'Nomenclature', module: 'context', perms: ['nomenclature.view'] },
  { key: 'quality', label: 'Data quality', module: 'data-quality', perms: ['nomenclature.view'] },
]);
