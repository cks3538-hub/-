import { connectRuflo } from './ruflo-client.mjs';
const client = await connectRuflo('schema-dump');
let tools = [], cursor;
do { const r = await client.listTools({ cursor }); tools = tools.concat(r.tools); cursor = r.nextCursor; } while (cursor);
const re = new RegExp(process.argv[2]);
for (const t of tools.filter((t) => re.test(t.name))) {
  console.log(`\n## ${t.name}\n   ${(t.description || '').replace(/\s+/g, ' ').slice(0, 200)}`);
  const p = t.inputSchema?.properties || {};
  for (const [k, v] of Object.entries(p)) {
    const enums = v.enum ? ` enum=[${v.enum.join('|')}]` : '';
    const req = (t.inputSchema?.required || []).includes(k) ? '*' : '';
    console.log(`   - ${k}${req}: ${v.type || ''}${enums} ${(v.description || '').replace(/\s+/g, ' ').slice(0, 90)}`);
  }
}
await client.close(); process.exit(0);
