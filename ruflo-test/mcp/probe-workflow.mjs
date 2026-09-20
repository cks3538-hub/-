import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
const transport = new StdioClientTransport({ command: 'npx', args: ['ruflo', 'mcp', 'start'], env: { ...process.env, NO_COLOR: '1' }, stderr: 'pipe' });
const client = new Client({ name: 'probe-wf', version: '1.0.0' }); await client.connect(transport);
const call = async (n, a) => { const r = await client.callTool({ name: n, arguments: a }); const t = r.content.map(c => c.text).join(''); let j; try { j = JSON.parse(t); } catch { j = t; } return j; };
const show = (label, j) => console.log(`\n## ${label}\n${JSON.stringify(j).slice(0, 900)}`);
show('workflow_template list', await call('workflow_template', { action: 'list' }));
for (const type of ['command', 'shell', 'script', 'exec']) {
  const wf = await call('workflow_create', { name: `probe-${type}`, steps: [
    { id: 's1', name: 'run tests', type, config: { command: 'node --test sample/test/*.test.js', script: 'node --test sample/test/*.test.js', cmd: 'node --test sample/test/*.test.js' } },
  ] });
  const ex = await call('workflow_execute', { workflowId: wf.workflowId });
  show(`step type=${type}`, ex);
}
await client.close(); process.exit(0);
