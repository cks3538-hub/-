// ruflo MCP 서버에 직접 접속해 "실제 작업 흐름"을 도구 호출로 수행합니다.
//   swarm_init → agent_spawn×3 → task_create×3(+assign) → task_update/complete
//   → memory_store/search → workflow_create/execute/status → task_list/summary → swarm_status → swarm_shutdown
// 출력: results/mcp-job.json (모든 호출의 입력/출력), 콘솔 요약
import { connectRuflo } from './ruflo-client.mjs';
import { writeFileSync } from 'node:fs';

const client = await connectRuflo('ruflo-job-runner');

const log = [];
function parse(res) {
  const txt = (res.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('\n');
  try { return JSON.parse(txt); } catch { return txt; }
}
async function call(name, args = {}) {
  const t0 = Date.now();
  let out, ok = true;
  try { out = parse(await client.callTool({ name, arguments: args })); }
  catch (e) { ok = false; out = { error: String(e.message || e) }; }
  const ms = Date.now() - t0;
  log.push({ step: log.length + 1, tool: name, args, ok, ms, result: out });
  const brief = typeof out === 'string' ? out.slice(0, 100) : JSON.stringify(out).slice(0, 160);
  console.log(`${ok ? 'OK ' : 'ERR'} ${String(log.length).padStart(2)} ${name.padEnd(18)} ${ms}ms  ${brief}`);
  return out;
}
const pick = (o, ...keys) => { for (const k of keys) { const v = k.split('.').reduce((a, p) => (a == null ? a : a[p]), o); if (v) return v; } return undefined; };

// 1) 스웜
const swarm = await call('swarm_init', { topology: 'hierarchical', maxAgents: 5, strategy: 'specialized' });
const swarmId = pick(swarm, 'swarmId', 'id', 'swarm.id', 'data.swarmId');

// 2) 에이전트 3개
const agents = {};
for (const type of ['researcher', 'coder', 'tester']) {
  const r = await call('agent_spawn', { agentType: type, swarmId, model: 'haiku', task: `${type} for crypto PnL fee feature` });
  agents[type] = pick(r, 'agentId', 'id', 'agent.id', 'data.agentId');
}

// 3) 작업 3건 (리서치 → 구현 → 테스트), 담당 에이전트 배정
const specs = [
  ['research', 'high',   '수수료 반영 손익 계산 요구사항 정리', 'researcher'],
  ['feature',  'high',   'price-calc.js 에 netPnL / breakEvenPrice 추가', 'coder'],
  ['feature',  'normal', '수수료 반영 단위 테스트 작성', 'tester'],
];
const taskIds = [];
for (const [type, priority, description, who] of specs) {
  const r = await call('task_create', { type, priority, description, tags: ['crypto', 'fee'], assignTo: agents[who] ? [agents[who]] : [] });
  taskIds.push(pick(r, 'taskId', 'id', 'task.id', 'data.taskId'));
}
await call('task_list', { limit: 20 });

// 4) 작업 진행 → 완료 처리
await call('task_update',   { taskId: taskIds[0], status: 'in_progress', progress: 50 });
await call('task_complete', { taskId: taskIds[0], result: { summary: '요구사항 정리 완료: feeRate × 거래대금 합계를 손익에서 차감' } });
await call('task_update',   { taskId: taskIds[1], status: 'in_progress', progress: 30 });
await call('task_status',   { taskId: taskIds[1] });

// 5) 메모리 저장 + 의미 검색
await call('memory_store',  { namespace: 'crypto', key: 'decision/fee-formula', value: '수수료 = feeRate × Σ(qty×price), 매수·매도 모두 부과. 0.0005 = 0.05%', tags: ['fee', 'decision'] });
// 기본 임계값은 한국어 다단어 질의의 낮은 유사도(0.1~0.3)를 걸러내므로 threshold 를 낮춰서 검색
await call('memory_search', { namespace: 'crypto', query: '수수료 계산 방식', limit: 3, threshold: 0.1 });

// 6) 워크플로 (의존성 있는 3단계)
const wf = await call('workflow_create', {
  name: 'fee-feature-pipeline', description: '리서치 → 구현 → 테스트',
  // task 단계는 config.agentId(또는 variables.defaultAgentId)가 필수
  steps: [
    { id: 'research',  name: '요구사항 정리', type: 'task', config: { agentId: agents.researcher, description: '수수료 요구사항 정리' } },
    { id: 'implement', name: '구현',         type: 'task', config: { agentId: agents.coder,      description: 'netPnL/breakEvenPrice 구현' }, dependsOn: ['research'] },
    { id: 'test',      name: '테스트',       type: 'task', config: { agentId: agents.tester,     description: '단위 테스트' },               dependsOn: ['implement'] },
  ],
  variables: { defaultAgentId: agents.coder },
});
const wfId = pick(wf, 'workflowId', 'id', 'workflow.id', 'data.workflowId');
if (wfId) { await call('workflow_execute', { workflowId: wfId }); await call('workflow_status', { workflowId: wfId, verbose: true }); }

// 7) 집계 + 상태
await call('task_summary');
await call('agent_list', {});
await call('swarm_status', swarmId ? { swarmId } : {});
await call('swarm_shutdown', swarmId ? { swarmId, graceful: true } : { graceful: true });

writeFileSync('results/mcp-job.json', JSON.stringify({ ranAt: new Date().toISOString(), swarmId, agents, taskIds, workflowId: wfId, calls: log }, null, 2));
const okN = log.filter((l) => l.ok).length;
console.log(`\n총 ${log.length}회 호출, 성공 ${okN}, 실패 ${log.length - okN}  →  results/mcp-job.json`);
await client.close();
process.exit(0);
