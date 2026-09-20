// ruflo MCP 서버(stdio)에 접속해 도구 목록과 핵심 도구의 입력 스키마를 저장합니다.
import { connectRuflo } from './ruflo-client.mjs';
import { writeFileSync } from 'node:fs';

const client = await connectRuflo('ruflo-test-client');
const info = client.getServerVersion();
console.log('connected:', JSON.stringify(info));

let tools = [];
let cursor;
do {
  const r = await client.listTools({ cursor });
  tools = tools.concat(r.tools);
  cursor = r.nextCursor;
} while (cursor);
console.log('total tools:', tools.length);

const names = tools.map((t) => t.name).sort();
writeFileSync('results/mcp-tools.json', JSON.stringify(names, null, 2));
const want = /^(swarm_init|swarm_status|agent_spawn|agent_list|task_orchestrate|task_status|task_results|memory_store|memory_search|memory_usage|hooks_pre_task|hooks_post_task)$/;
const picked = tools.filter((t) => want.test(t.name));
writeFileSync('results/mcp-tool-schemas.json', JSON.stringify(picked, null, 2));
for (const t of picked) {
  console.log(`\n## ${t.name}: ${(t.description || '').slice(0, 120)}`);
  console.log('   props:', Object.keys(t.inputSchema?.properties || {}).join(', '));
  console.log('   required:', (t.inputSchema?.required || []).join(', '));
}
// 이름 그룹 통계
const groups = {};
for (const n of names) { const g = n.split('_')[0]; groups[g] = (groups[g] || 0) + 1; }
console.log('\ngroups:', JSON.stringify(groups));
await client.close();
process.exit(0);
