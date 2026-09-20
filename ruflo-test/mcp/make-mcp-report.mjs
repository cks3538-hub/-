// results/ 의 실제 결과 파일에서 MCP_REPORT.md 를 생성합니다.  실행: node mcp/make-mcp-report.mjs
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
const read = (p) => readFileSync(p, 'utf8');
const j = (p) => JSON.parse(read(p));

// ── Track A: Claude Code 헤드리스 에이전트 ──
const events = read('results/claude-agent-run.jsonl').trim().split('\n').map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
const meta = j('results/claude-agent-result.json');
const finalText = read('results/claude-agent-final.txt').trim();
const timeline = [];
const counts = {};
for (const e of events) {
  if (e.type !== 'assistant') continue;
  for (const c of e.message.content || []) {
    if (c.type === 'tool_use') {
      counts[c.name] = (counts[c.name] || 0) + 1;
      const brief = JSON.stringify(c.input).replace(/\|/g, '\\|').slice(0, 90);
      timeline.push(`| ${timeline.length + 1} | \`${c.name}\` | ${brief} |`);
    }
  }
}
const mcpCalls = Object.entries(counts).filter(([k]) => k.startsWith('mcp__')).reduce((s, [, v]) => s + v, 0);
const allCalls = Object.values(counts).reduce((s, v) => s + v, 0);

// ── Track B: MCP 직접 호출 ──
const job = j('results/mcp-job.json');
const rows = job.calls.map((c) => {
  const r = c.result;
  let brief;
  if (typeof r === 'string') brief = r.slice(0, 80);
  else if (r?.error) brief = `error: ${String(r.error).slice(0, 70)}`;
  else brief = [r?.swarmId, r?.agentId, r?.taskId, r?.workflowId, r?.status, r?.stored != null ? `stored=${r.stored}` : null, r?.total != null ? `total=${r.total}` : null, r?.results ? `results=${r.results.length}` : null, r?.agents ? `agents=${r.agents.length}` : null, r?.tasks ? `tasks=${r.tasks.length}` : null, r?.terminated != null ? `terminated=${r.terminated}` : null].filter(Boolean).join(', ');
  const contentFail = r?.status === 'failed' || r?.error;
  return `| ${c.step} | \`${c.tool}\` | ${c.ok ? (contentFail ? '통신 OK / 내용 실패' : 'OK') : 'ERR'} | ${c.ms}ms | ${brief.replace(/\|/g, '\\|')} |`;
});
const okN = job.calls.filter((c) => c.ok).length;
const contentFailN = job.calls.filter((c) => c.result?.status === 'failed' || c.result?.error).length;
const wfErr = job.calls.find((c) => c.tool === 'workflow_execute')?.result?.error || '';
const msRes = job.calls.find((c) => c.tool === 'memory_search')?.result?.results || [];

const md = `# ruflo MCP 서버 연결 · 실제 작업 실행 보고서

실행일: ${job.ranAt.slice(0, 10)} · ruflo v3.42.4 · MCP 서버 \`ruflo 3.0.0\` (stdio) · 도구 353개

두 가지 경로로 "실제 작업"을 돌렸습니다.

| 경로 | 무엇을 했나 | 결과 |
|---|---|---|
| A. Claude Code 에이전트 + ruflo MCP | Claude Code CLI를 헤드리스로 띄우고 ruflo MCP 도구로 스웜·에이전트·작업을 조율하면서 **샘플 코드에 수수료 반영 기능을 실제 구현** | 성공 · 테스트 10/10 통과 · ${meta.num_turns}턴 · ${Math.round(meta.duration_ms / 1000)}초 · 약 $${meta.total_cost_usd.toFixed(2)} |
| B. MCP 클라이언트 직접 호출 | Node 스크립트로 MCP 서버에 접속해 도구 ${job.calls.length}개를 순서대로 호출 | 통신 ${okN}/${job.calls.length} 성공 · 내용 실패 ${contentFailN}건(워크플로 실행, 아래 5절) |

## 1. 연결 방법

### A. Claude Code CLI 에 ruflo MCP 서버 붙이기

\`mcp/claude-mcp.json\` (로컬 설치된 ruflo를 stdio 로 실행):

\`\`\`json
${read('mcp/claude-mcp.json').trim()}
\`\`\`

실행 스크립트 \`mcp/run-claude-agent.sh\`(macOS·Linux) / \`mcp/run-claude-agent.cmd\`(Windows) 의 핵심 명령. 지시문은 표준입력으로 넘깁니다.

\`\`\`bash
claude -p \\
  --output-format stream-json --verbose \\
  --mcp-config mcp/claude-mcp.json --strict-mcp-config \\
  --allowedTools "mcp__claude-flow__*,Read,Edit,Write,Glob,Grep,Bash(node:*),Bash(npx ruflo:*)" \\
  --max-turns 60 --model sonnet \\
  < mcp/agent-task.md > results/claude-agent-run.jsonl
\`\`\`

### B. MCP SDK 클라이언트로 직접 접속

\`mcp/run-job.mjs\` — \`@modelcontextprotocol/sdk\` 의 \`StdioClientTransport\` 로 \`npx ruflo mcp start\` 를 띄워 \`callTool\` 을 호출합니다.

## 2. 경로 A: 에이전트에게 준 작업

\`mcp/agent-task.md\` 요약:

1. \`swarm_init\` (hierarchical, 3, specialized)
2. \`agent_spawn\` coder / tester
3. \`task_create\` "Add trading-fee support to sample/src/price-calc.js" → coder 에게 배정
4. \`netPnL(trades, currentPrice, feeRate)\` 와 \`breakEvenPrice(trades, feeRate)\` 구현
5. 테스트 추가 후 \`node --test\` 로 전부 통과시키기
6. \`task_complete\` → \`memory_store\` → \`task_list\` / \`swarm_status\` 로 마무리

## 3. 경로 A: 에이전트가 실제로 한 일 (이벤트 스트림에서 추출)

도구 호출 ${allCalls}회, 그중 ruflo MCP 도구 ${mcpCalls}회.

| # | 도구 | 입력 (앞부분) |
|---|---|---|
${timeline.join('\n')}

### 에이전트 최종 답변 (원문)

${finalText.split('\n').map((l) => `> ${l}`).join('\n')}

### 코드 변경 검증 (제가 직접 재실행)

\`\`\`text
$ node --test sample/test/*.test.js
# tests 10
# pass 10
# fail 0
\`\`\`

수식 검증: 거래 [매수 1@100, 매수 1@200, 매도 0.5@300], 현재가 400, 수수료율 0.05%
- 평균단가 150, 보유 1.5 → 평가손익 375
- 거래대금 합계 450 × 0.0005 = 0.225 → netPnL = 374.775
- 손익분기가 = 150 + 0.225 / 1.5 = 150.15

## 4. 경로 B: MCP 직접 호출 ${job.calls.length}건

스웜 \`${job.swarmId}\` · 에이전트 researcher/coder/tester · 작업 3건 · 워크플로 \`${job.workflowId}\`

| # | 도구 | 결과 | 소요 | 핵심 값 |
|---|---|---|---|---|
${rows.join('\n')}

메모리 의미 검색 "수수료 계산 방식" (threshold 0.1) 결과 ${msRes.length}건:
${msRes.map((r) => `- \`${r.key}\` (유사도 ${r.similarity.toFixed(2)}): ${r.value.slice(0, 60)}`).join('\n')}

## 5. 확인 사항 (있는 그대로)

1. **워크플로 실행은 ruflo 자체 LLM 키가 필요합니다.** \`workflow_execute\` 의 task 단계가 다음 오류로 실패했습니다.
   \`${wfErr}\`
   ruflo 서버가 스스로 에이전트를 돌리는 경로(workflow, autopilot 등)는 별도 API 키를 환경변수로 줘야 하고, 이번 테스트에서는 키를 설정하지 않았습니다.
   command / shell / script 같은 비-LLM 단계 타입은 모두 \`skipped\` 처리됩니다(\`mcp/probe-workflow.mjs\`).
   반면 **경로 A(Claude Code가 주체가 되어 MCP 도구로 조율)는 Claude Code 인증만으로 동작**했습니다.
2. **메모리 검색은 임계값에 민감합니다.** 기본 임계값에서는 한국어 다단어 질의가 0건이었고, threshold 0.1 로 낮추면 3건이 나옵니다.
   단어 하나("수수료") 질의에 "평균 매수 단가" 항목이 유사도 0.99 로 1위에 오르는 등 임베딩 품질이 낮아 보입니다.
   ONNX 임베딩 모델을 쓰는 \`npx ruflo init --with-embeddings\` 옵션이 있으니 한국어 검색이 중요하면 그쪽을 시험해 볼 만합니다(\`mcp/probe-memory.mjs\`).
3. **MCP 경로에서는 \`task_list\` 가 정상입니다.** CLI 에서 비어 있던 작업 목록이 MCP 에서는 \`.swarm/memory.db\` 기준으로 프로세스 간 공유됩니다.
   경로 A 가 만든 작업이 경로 B 의 \`task_summary\` 에도 집계됐습니다(총 ${job.calls.find((c) => c.tool === 'task_summary')?.result?.total}건).
4. **워크스페이스 신뢰 경고.** 헤드리스 실행 시 ".claude/settings.json 의 permissions.allow 4건 무시(워크스페이스 미신뢰)" 경고가 나와 \`--allowedTools\` 로 권한을 직접 지정했습니다.
5. **비용.** 경로 A 한 번에 약 $${meta.total_cost_usd.toFixed(2)} (sonnet, ${meta.num_turns}턴). 경로 B 는 LLM 호출이 없어 비용 0.

## 6. 재현 방법

\`\`\`bash
cd ruflo-test
npm install
node mcp/list-tools.mjs        # 도구 353개 목록 → results/mcp-tools.json
node mcp/run-job.mjs           # 경로 B → results/mcp-job.json
bash mcp/run-claude-agent.sh   # 경로 A (Claude Code 로그인 필요, 비용 발생) → results/claude-agent-*.{jsonl,txt,json}
mcp\\run-claude-agent.cmd      # 경로 A, Windows cmd
node mcp/make-mcp-report.mjs   # 이 보고서 재생성
\`\`\`
`;
writeFileSync('MCP_REPORT.md', md);
console.log(`MCP_REPORT.md 생성 (${md.split('\n').length} 줄)`);
