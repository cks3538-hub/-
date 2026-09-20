# ruflo MCP 서버 연결 · 실제 작업 실행 보고서

실행일: 2026-09-20 · ruflo v3.42.4 · MCP 서버 `ruflo 3.0.0` (stdio) · 도구 353개

두 가지 경로로 "실제 작업"을 돌렸습니다.

| 경로 | 무엇을 했나 | 결과 |
|---|---|---|
| A. Claude Code 에이전트 + ruflo MCP | Claude Code CLI를 헤드리스로 띄우고 ruflo MCP 도구로 스웜·에이전트·작업을 조율하면서 **샘플 코드에 수수료 반영 기능을 실제 구현** | 성공 · 테스트 10/10 통과 · 16턴 · 92초 · 약 $0.48 |
| B. MCP 클라이언트 직접 호출 | Node 스크립트로 MCP 서버에 접속해 도구 21개를 순서대로 호출 | 통신 21/21 성공 · 내용 실패 2건(워크플로 실행, 아래 5절) |

## 1. 연결 방법

### A. Claude Code CLI 에 ruflo MCP 서버 붙이기

`mcp/claude-mcp.json` (로컬 설치된 ruflo를 stdio 로 실행):

```json
{
  "mcpServers": {
    "claude-flow": {
      "type": "stdio",
      "command": "npx",
      "args": ["ruflo", "mcp", "start"],
      "env": {
        "npm_config_update_notifier": "false",
        "NO_COLOR": "1",
        "CLAUDE_FLOW_MODE": "v3",
        "CLAUDE_FLOW_HOOKS_ENABLED": "true",
        "CLAUDE_FLOW_TOPOLOGY": "hierarchical",
        "CLAUDE_FLOW_MAX_AGENTS": "5",
        "CLAUDE_FLOW_MEMORY_BACKEND": "memory"
      }
    }
  }
}
```

실행 스크립트 `mcp/run-claude-agent.sh` 의 핵심 명령:

```bash
claude -p "$(cat mcp/agent-task.md)" \
  --output-format stream-json --verbose \
  --mcp-config mcp/claude-mcp.json --strict-mcp-config \
  --allowedTools "mcp__claude-flow__*,Read,Edit,Write,Glob,Grep,Bash(node:*),Bash(npx ruflo:*)" \
  --max-turns 60 --model sonnet
```

### B. MCP SDK 클라이언트로 직접 접속

`mcp/run-job.mjs` — `@modelcontextprotocol/sdk` 의 `StdioClientTransport` 로 `npx ruflo mcp start` 를 띄워 `callTool` 을 호출합니다.

## 2. 경로 A: 에이전트에게 준 작업

`mcp/agent-task.md` 요약:

1. `swarm_init` (hierarchical, 3, specialized)
2. `agent_spawn` coder / tester
3. `task_create` "Add trading-fee support to sample/src/price-calc.js" → coder 에게 배정
4. `netPnL(trades, currentPrice, feeRate)` 와 `breakEvenPrice(trades, feeRate)` 구현
5. 테스트 추가 후 `node --test` 로 전부 통과시키기
6. `task_complete` → `memory_store` → `task_list` / `swarm_status` 로 마무리

## 3. 경로 A: 에이전트가 실제로 한 일 (이벤트 스트림에서 추출)

도구 호출 15회, 그중 ruflo MCP 도구 8회.

| # | 도구 | 입력 (앞부분) |
|---|---|---|
| 1 | `ToolSearch` | {"query":"select:mcp__claude-flow__swarm_init,mcp__claude-flow__agent_spawn,mcp__claude-fl |
| 2 | `mcp__claude-flow__swarm_init` | {"topology":"hierarchical","maxAgents":3,"strategy":"specialized"} |
| 3 | `mcp__claude-flow__agent_spawn` | {"agentType":"coder"} |
| 4 | `mcp__claude-flow__agent_spawn` | {"agentType":"tester","model":"haiku"} |
| 5 | `mcp__claude-flow__task_create` | {"type":"feature","priority":"high","description":"Add trading-fee support to sample/src/p |
| 6 | `Read` | {"file_path":"/home/user/-/ruflo-test/sample/src/price-calc.js"} |
| 7 | `Edit` | {"replace_all":false,"file_path":"/home/user/-/ruflo-test/sample/src/price-calc.js","old_s |
| 8 | `Read` | {"file_path":"/home/user/-/ruflo-test/sample/test/price-calc.test.js"} |
| 9 | `Edit` | {"replace_all":false,"file_path":"/home/user/-/ruflo-test/sample/test/price-calc.test.js", |
| 10 | `Edit` | {"replace_all":false,"file_path":"/home/user/-/ruflo-test/sample/test/price-calc.test.js", |
| 11 | `Bash` | {"command":"node --test sample/test/*.test.js","description":"Run price-calc and related t |
| 12 | `mcp__claude-flow__task_complete` | {"taskId":"task-1789883933098-xy6a7y","result":{"summary":"netPnL and breakEvenPrice added |
| 13 | `mcp__claude-flow__memory_store` | {"namespace":"crypto","key":"feature/fee-support","value":"sample/src/price-calc.js에 netPn |
| 14 | `mcp__claude-flow__task_list` | {} |
| 15 | `mcp__claude-flow__swarm_status` | {} |

### 에이전트 최종 답변 (원문)

> 모든 작업을 완료했습니다.
> 
> **호출한 MCP 도구와 결과:**
> - `swarm_init` (hierarchical, maxAgents 3, specialized) → swarmId: `swarm-1789883926451-71qdvl` (running, agentCount 2)
> - `agent_spawn` × 2 → coder: `agent-1789883929015-o6bsxz` (model sonnet), tester: `agent-1789883929632-zl7sr5` (model haiku)
> - `task_create` (feature/high, coder에게 할당) → taskId: `task-1789883933098-xy6a7y`
> - `task_complete`(taskId, 결과 요약) → status: completed, progress 100
> - `memory_store`(namespace "crypto", key "feature/fee-support") → 저장 성공(임베딩 384차원)
> - `task_list` → 이번에 만든 태스크는 completed, 그 외 과거 대기 중인 태스크 4개 확인
> - `swarm_status` → swarm-1789883926451-71qdvl, running, agent 2개
> 
> **코드 변경:**
> - `sample/src/price-calc.js`: 내부 헬퍼 `totalFees(trades, feeRate)`(모든 거래의 qty×price 합계 × feeRate)를 추가하고, 이를 이용해 `netPnL(trades, currentPrice, feeRate=0)`(unrealizedPnL − 수수료)와 `breakEvenPrice(trades, feeRate=0)`(보유 수량 0이면 0, 아니면 평균 매수가 + 수수료/보유 수량)을 새로 export. 기존 함수(averageCost, unrealizedPnL, percentChange, riskLevel)는 변경하지 않음.
> - `sample/test/price-calc.test.js`: netPnL(feeRate 0 = unrealizedPnL과 동일, feeRate 0.0005), breakEvenPrice(feeRate 0 = 평균 매수가와 동일, feeRate 0.0005, 보유 수량 0 케이스) 테스트 5개 추가.
> 
> **테스트 결과:** `node --test sample/test/*.test.js` → **10 pass, 0 fail** (전부 통과).
> 
> git 커밋은 생성하지 않았습니다.

### 코드 변경 검증 (제가 직접 재실행)

```text
$ node --test sample/test/*.test.js
# tests 10
# pass 10
# fail 0
```

수식 검증: 거래 [매수 1@100, 매수 1@200, 매도 0.5@300], 현재가 400, 수수료율 0.05%
- 평균단가 150, 보유 1.5 → 평가손익 375
- 거래대금 합계 450 × 0.0005 = 0.225 → netPnL = 374.775
- 손익분기가 = 150 + 0.225 / 1.5 = 150.15

## 4. 경로 B: MCP 직접 호출 21건

스웜 `swarm-1789884080813-fdk4u1` · 에이전트 researcher/coder/tester · 작업 3건 · 워크플로 `workflow-1789884081127-v4wlh4`

| # | 도구 | 결과 | 소요 | 핵심 값 |
|---|---|---|---|---|
| 1 | `swarm_init` | OK | 20ms | swarm-1789884080813-fdk4u1 |
| 2 | `agent_spawn` | OK | 36ms | agent-1789884080832-k7trr3, registered |
| 3 | `agent_spawn` | OK | 9ms | agent-1789884080856-u8tjws, registered |
| 4 | `agent_spawn` | OK | 8ms | agent-1789884080867-kewbdd, registered |
| 5 | `task_create` | OK | 8ms | task-1789884080876-2t4gun, pending |
| 6 | `task_create` | OK | 5ms | task-1789884080881-hv6qbf, pending |
| 7 | `task_create` | OK | 6ms | task-1789884080887-wmj4ck, pending |
| 8 | `task_list` | OK | 8ms | total=11, tasks=11 |
| 9 | `task_update` | OK | 5ms | task-1789884080876-2t4gun, in_progress |
| 10 | `task_complete` | OK | 5ms | task-1789884080876-2t4gun, completed |
| 11 | `task_update` | OK | 6ms | task-1789884080881-hv6qbf, in_progress |
| 12 | `task_status` | OK | 5ms | task-1789884080881-hv6qbf, in_progress |
| 13 | `memory_store` | OK | 193ms | stored=true |
| 14 | `memory_search` | OK | 11ms | total=3, results=3 |
| 15 | `workflow_create` | OK | 6ms | workflow-1789884081127-v4wlh4, ready |
| 16 | `workflow_execute` | 통신 OK / 내용 실패 | 11ms | error: No LLM provider configured. Set ANTHROPIC_API_KEY (Tier-3), OPENROUTER |
| 17 | `workflow_status` | 통신 OK / 내용 실패 | 6ms | error: No LLM provider configured. Set ANTHROPIC_API_KEY (Tier-3), OPENROUTER |
| 18 | `task_summary` | OK | 8ms | total=11 |
| 19 | `agent_list` | OK | 5ms | total=11, agents=11 |
| 20 | `swarm_status` | OK | 6ms | swarm-1789884080813-fdk4u1, running |
| 21 | `swarm_shutdown` | OK | 7ms | swarm-1789884080813-fdk4u1, terminated=true |

메모리 의미 검색 "수수료 계산 방식" (threshold 0.1) 결과 3건:
- `todo/fee` (유사도 0.27): 매매 수수료 0.05% 를 손익 계산에 반영해야 함
- `rule/risk` (유사도 0.21): 변동률 5% 미만 low, 15% 미만 medium, 그 이상 high
- `decision/fee-formula` (유사도 0.13): 수수료 = feeRate × Σ(qty×price), 매수·매도 모두 부과. 0.0005 = 0.05%

## 5. 확인 사항 (있는 그대로)

1. **워크플로 실행은 ruflo 자체 LLM 키가 필요합니다.** `workflow_execute` 의 task 단계가 다음 오류로 실패했습니다.
   `No LLM provider configured. Set ANTHROPIC_API_KEY (Tier-3), OPENROUTER_API_KEY (#2042), or OLLAMA_API_KEY (Tier-2 — #1725).`
   ruflo 서버가 스스로 에이전트를 돌리는 경로(workflow, autopilot 등)는 별도 API 키를 환경변수로 줘야 하고, 이번 테스트에서는 키를 설정하지 않았습니다.
   command / shell / script 같은 비-LLM 단계 타입은 모두 `skipped` 처리됩니다(`mcp/probe-workflow.mjs`).
   반면 **경로 A(Claude Code가 주체가 되어 MCP 도구로 조율)는 Claude Code 인증만으로 동작**했습니다.
2. **메모리 검색은 임계값에 민감합니다.** 기본 임계값에서는 한국어 다단어 질의가 0건이었고, threshold 0.1 로 낮추면 3건이 나옵니다.
   단어 하나("수수료") 질의에 "평균 매수 단가" 항목이 유사도 0.99 로 1위에 오르는 등 임베딩 품질이 낮아 보입니다.
   ONNX 임베딩 모델을 쓰는 `npx ruflo init --with-embeddings` 옵션이 있으니 한국어 검색이 중요하면 그쪽을 시험해 볼 만합니다(`mcp/probe-memory.mjs`).
3. **MCP 경로에서는 `task_list` 가 정상입니다.** CLI 에서 비어 있던 작업 목록이 MCP 에서는 `.swarm/memory.db` 기준으로 프로세스 간 공유됩니다.
   경로 A 가 만든 작업이 경로 B 의 `task_summary` 에도 집계됐습니다(총 11건).
4. **워크스페이스 신뢰 경고.** 헤드리스 실행 시 ".claude/settings.json 의 permissions.allow 4건 무시(워크스페이스 미신뢰)" 경고가 나와 `--allowedTools` 로 권한을 직접 지정했습니다.
5. **비용.** 경로 A 한 번에 약 $0.48 (sonnet, 16턴). 경로 B 는 LLM 호출이 없어 비용 0.

## 6. 재현 방법

```bash
cd ruflo-test
npm install
node mcp/list-tools.mjs        # 도구 353개 목록 → results/mcp-tools.json
node mcp/run-job.mjs           # 경로 B → results/mcp-job.json
bash mcp/run-claude-agent.sh   # 경로 A (Claude Code 로그인 필요, 비용 발생) → results/claude-agent-*.{jsonl,txt,json}
node mcp/make-mcp-report.mjs   # 이 보고서 재생성
```
