# ruflo 샘플 테스트 보고서

## 1. 개요

| 항목 | 값 |
|---|---|
| 대상 | ruflo v3.42.4 (npm, Claude Flow v3) |
| 실행 환경 | Linux, Node v22.22.2, npm 10.9.7 |
| 실행일 | 2026-09-19 |
| 시나리오 | 코인 매매 손익 계산기 샘플 코드로 27단계 기능 점검 |
| 결과 | **27 / 27 통과** (종료 코드 기준), 세부 확인 사항 4건은 6절 참고 |

## 2. 설치 과정

```bash
npm install ruflo            # 591개 패키지, 약 1분
npx ruflo --version          # ruflo v3.42.4
npx ruflo init --minimal --no-global --no-signup --no-skills-sh --no-codex-detect
```

`init` 결과: 디렉토리 12개, 파일 15개 생성
(`CLAUDE.md`, `.mcp.json`, `.claude/settings.json`, `.claude/skills/` 8개, `.claude-flow/config.yaml`).

## 3. 시나리오 흐름

```
① 진단 (doctor)
   ↓
② 스웜 초기화 (hierarchical, 최대 5 에이전트)
   ↓
③ 에이전트 3개 생성 (researcher / coder / tester)
   ↓
④ 작업 3건 등록 (리서치 → 구현 → 테스트) + Q-Learning 라우팅
   ↓
⑤ 메모리 3건 저장 (벡터 자동 생성) + 한국어 의미 검색
   ↓
⑥ 훅 pre-task (에이전트 추천) · 훅 목록
   ↓
⑦ 코드 분석 (심볼 추출 · 복잡도) + 단위 테스트 5건
   ↓
⑧ 시스템 · 스웜 상태 확인
```

## 4. 요약표

| # | 단계 | 결과 | 소요 |
|---|------|------|------|
| 01 | 버전 확인 | PASS | 318ms |
| 02 | 시스템 진단 (doctor) | PASS | 1345ms |
| 03 | 초기화 상태 확인 | PASS | 540ms |
| 04 | 스웜 초기화 (hierarchical, 최대 5) | PASS | 551ms |
| 05 | 에이전트 생성: researcher | PASS | 608ms |
| 06 | 에이전트 생성: coder | PASS | 601ms |
| 07 | 에이전트 생성: tester | PASS | 597ms |
| 08 | 에이전트 목록 | PASS | 599ms |
| 09 | 작업 생성: 리서치 | PASS | 586ms |
| 10 | 작업 생성: 구현 | PASS | 573ms |
| 11 | 작업 생성: 테스트 | PASS | 581ms |
| 12 | 작업 목록 (전체) | PASS | 576ms |
| 13 | 작업 라우팅 (Q-Learning) | PASS | 563ms |
| 14 | 라우팅 가능 에이전트 목록 | PASS | 572ms |
| 15 | 메모리 저장 1 | PASS | 731ms |
| 16 | 메모리 저장 2 | PASS | 743ms |
| 17 | 메모리 저장 3 | PASS | 720ms |
| 18 | 메모리 의미 검색: 손익 | PASS | 685ms |
| 19 | 메모리 목록 | PASS | 712ms |
| 20 | 훅 pre-task | PASS | 584ms |
| 21 | 훅 목록 | PASS | 578ms |
| 22 | 코드 분석: 심볼 추출 | PASS | 564ms |
| 23 | 코드 분석: 복잡도 | PASS | 593ms |
| 24 | 샘플 단위 테스트 (node --test) | PASS | 140ms |
| 25 | 워크플로 목록 | PASS | 573ms |
| 26 | 시스템 상태 | PASS | 789ms |
| 27 | 스웜 상태 | PASS | 551ms |

## 5. 단계별 결과 발췌 (실제 출력)

### STEP 02 · 시스템 진단

```bash
$ npx ruflo doctor
```

```text

RuFlo Doctor
System diagnostics and health check
──────────────────────────────────────────────────

✓ Version Freshness: v3.42.4 (up to date)
✓ Node.js Version: v22.22.2 (>= 20 required)
✓ npm Version: v10.9.7
✓ Claude Code CLI: v2.1.278
✓ Git: v2.43.0
✓ Git Repository: In a git repository
✓ Config File: Found: .claude-flow/config.yaml
✓ Stale npx@latest in settings (#2448): no runaway commands detected
⚠ Daemon Status: Not running
⚠ Memory Database Presence: Not initialized
⚠ Memory Structural Integrity (quick_check): no memory.db found (see Memory Database Presence above) — structural-only check skipped
⚠ Memory Persistence Driver: no memory.db found (see Memory Database Presence above) — driver check skipped
✓ Learning Bridge: auto-memory hook not installed (run: npx ruflo@latest init)
✓ API Keys: Claude Code (managed internally)
✓ MCP Servers: 1 servers (ruflo configured: .mcp.json top-level: claude-flow)
⚠ MCP Schema Overhead: 353 advertised tools ≈ 65835 schema tokens (all tools)
⚠ AIDefence: @claude-flow/aidefence not loadable — aidefence_* MCP tools will fail (optional package)
✓ Disk Space: 28G available
✓ TypeScript: v6.0.2
✓ agentic-flow: v3.0.0-alpha.2 (ReasoningBank, Router, QUIC)
⚠ Encryption at Rest: Off — session/terminal/memory stores are plaintext (mode 0600 only)
✓ Federation Breaker: ADR-097 breaker loadable — federation_breaker_status / federation_evict / federation_reactivate MCP tools available
✓ MetaHarness (ADR-150): v0.4.2 — run `npx ruflo metaharness score` for the full scorecard
✓ MetaHarness declared packages (ADR-150): 4 declared package(s) resolve: @metaharness/darwin, @metaharness/flywheel, @metaharness/radio, @metaharness/turn-credit
✓ MetaHarness integration (ADR-150): plugin scripts intact, _similarity.mjs + parseMcpScanText load, smoke OK
✓ Funnel (ADR-305): enabled (decided by: package-default; disclosure: never_seen)
```

### STEP 04 · 스웜 초기화

```bash
$ npx ruflo swarm init --topology hierarchical --max-agents 5
```

```text

[INFO] Initializing swarm...
  Creating coordination topology...
  Initializing memory namespace...
  Setting up communication channels...

+------------+----------------------------+
| Property   | Value                      |
+------------+----------------------------+
| Swarm ID   | swarm-1789832970923-fxdgrl |
| Topology   | hierarchical               |
| Max Agents | 5                          |
| Auto Scale | Enabled                    |
| Protocol   | message-bus                |
| V3 Mode    | Disabled                   |
+------------+----------------------------+

[OK] Swarm initialized successfully
```

### STEP 08 · 에이전트 목록

```bash
$ npx ruflo agent list
```

```text

Active Agents

+----------------------+------------+--------+------------+--------------+
| ID                   | Type       | Status | Created    | Last Acti... |
+----------------------+------------+--------+------------+--------------+
| agent-17898329715... | researcher | idle   | 3:49:31 PM | N/A          |
| agent-17898329721... | coder      | idle   | 3:49:32 PM | N/A          |
| agent-17898329727... | tester     | idle   | 3:49:32 PM | N/A          |
+----------------------+------------+--------+------------+--------------+

[INFO] Total: 3 agents
```

### STEP 09 · 작업 생성

```bash
$ npx ruflo task create -t research -d "코인 손익 계산 로직 요구사항 정리" -p high --tags crypto,pnl
```

```text

[INFO] Creating research task...

[OK] Task created: task-1789832973957-oe45j5

+-------------+---------------------------+
| Property    | Value                     |
+-------------+---------------------------+
| ID          | task-1789832973957-oe45j5 |
| Type        | research                  |
| Description | 코인 손익 계산 로직 요구사항 정리       |
| Priority    | high                      |
| Status      | pending                   |
| Assigned To | Unassigned                |
| Tags        | crypto, pnl               |
| Created     | 9/19/2026, 3:49:33 PM     |
+-------------+---------------------------+
```

### STEP 13 · Q-Learning 라우팅

```bash
$ npx ruflo route task "price-calc.js 의 percentChange 함수에 대한 단위 테스트 작성"
```

```text

Routed to Documenter

+----------------- Q-Learning Routing -----------------+
| Task: price-calc.js 의 percentChange 함수에 대한 단위 테스트 작성 |
|                                                      |
| Agent: Documenter (documenter)                       |
| Confidence: 12.5%                                    |
| Q-Value: 0.000                                       |
| Exploration: Yes                                     |
|                                                      |
| Description: Creates and updates documentation       |
| Capabilities: documentation, writing, explaining     |
+------------------------------------------------------+

Alternatives:
+-----------+-------+
| Agent     | Score |
+-----------+-------+
| Tester    | 0.000 |
| Reviewer  | 0.000 |
| Architect | 0.000 |
+-----------+-------+
```

### STEP 15 · 메모리 저장

```bash
$ npx ruflo memory store --namespace crypto --key "rule/avg-cost" --value "평균 매수 단가는 ..."
```

```text
[INFO] Storing in crypto/rule/avg-cost...

+------------+----------------------+
| Property   | Value                |
+------------+----------------------+
| Key        | rule/avg-cost        |
| Namespace  | crypto               |
| Size       | 70 bytes             |
| TTL        | None                 |
| Tags       | None                 |
| Vector     | Yes (384-dim)        |
| Provenance | unknown              |
| ID         | entry_1789832977597_ |
+------------+----------------------+

[OK] Data stored successfully
```

### STEP 18 · 메모리 의미 검색

```bash
$ npx ruflo memory search --namespace crypto -q "손익 계산 규칙"
```

```text
[INFO] Searching: "손익 계산 규칙" (semantic)

  Search time: 2ms

+----------+-------+-----------+------------------------------+
| Key      | Score | Namespace | Preview                      |
+----------+-------+-----------+------------------------------+
| todo/fee |  0.34 | crypto    | 매매 수수료 0.05% 를 손익 계산에 반영해야 함 |
+----------+-------+-----------+------------------------------+

[INFO] Found 1 results
```

### STEP 20 · 훅 pre-task (에이전트 추천)

```bash
$ npx ruflo hooks pre-task --description "price-calc.js 리팩터링"
```

```text
[INFO] Starting task: task-mu8kcew4

+-------- Task Registered --------+
| Task ID: task-mu8kcew4          |
| Description: price-calc.js 리팩터링 |
| Complexity: LOW                 |
| Est. Duration: 10-30 min        |
+---------------------------------+

Suggested Agents
+------------+------------+-------------------------------------+
| Agent Type | Confidence | Reason                              |
+------------+------------+-------------------------------------+
| coder      |      70.0% | Primary agent for coder tasks ba... |
| researcher |      65.0% | Alternative agent with researche... |
| tester     |      60.0% | Alternative agent with tester ca... |
+------------+------------+-------------------------------------+

Recommendations
  - Use coder as primary agent
  - Consider using swarm coordination
```

### STEP 22 · 코드 분석: 심볼 추출

```bash
$ npx ruflo analyze symbols sample/src
```

```text
[INFO] Extracting symbols: sample/src

+ Symbol Extraction +
| Total symbols: 5  |
| Functions: 5      |
| Classes: 0        |
| Files: 2          |
+-------------------+

Symbols
------------------------------------------------------------
+------+---------------+-----------------------------------+------+
| Type | Name          | File                              | Line |
+------+---------------+-----------------------------------+------+
| fn   | summarize     | ...o-test/sample/src/portfolio.js |    4 |
| fn   | averageCost   | ...-test/sample/src/price-calc.js |    7 |
| fn   | percentChange | ...-test/sample/src/price-calc.js |   26 |
| fn   | riskLevel     | ...-test/sample/src/price-calc.js |   32 |
| fn   | unrealizedPnL | ...-test/sample/src/price-calc.js |   16 |
+------+---------------+-----------------------------------+------+
```

### STEP 23 · 코드 분석: 복잡도

```bash
$ npx ruflo analyze complexity sample/src
```

```text
[INFO] Analyzing complexity: sample/src

+- Complexity Analysis -+
| Files analyzed: 2     |
| Threshold: 10         |
| Flagged files: 0      |
| Average complexity: 3 |
+-----------------------+

All Files
------------------------------------------------------------
+------------------------------------------+-------+-------+-----+
| File                                     | Cyclo | Cogni | LOC |
+------------------------------------------+-------+-------+-----+
| ...user/-/ruflo-test/sample/src/price... |     5 |     8 |  33 |
| .../user/-/ruflo-test/sample/src/port... |     1 |     0 |  17 |
+------------------------------------------+-------+-------+-----+
```

### STEP 24 · 샘플 단위 테스트

```bash
$ node --test sample/test/*.test.js
```

```text
TAP version 13
# Subtest: averageCost: 매수 평균가 계산
ok 1 - averageCost: 매수 평균가 계산
  ---
  duration_ms: 0.674849
  type: 'test'
  ...
# Subtest: unrealizedPnL: 보유 1.5개, 현재가 400 → (400-150)*1.5
ok 2 - unrealizedPnL: 보유 1.5개, 현재가 400 → (400-150)*1.5
  ---
  duration_ms: 0.155241
  type: 'test'
  ...
# Subtest: percentChange: 100 → 150 은 +50%
ok 3 - percentChange: 100 → 150 은 +50%
  ---
  duration_ms: 0.40523
  type: 'test'
  ...
# Subtest: riskLevel: 등급 경계
ok 4 - riskLevel: 등급 경계
  ---
  duration_ms: 1.175528
  type: 'test'
  ...
# Subtest: summarize: 포트폴리오 요약
ok 5 - summarize: 포트폴리오 요약
  ---
  duration_ms: 0.264377
  type: 'test'
  ...
1..5
# tests 5
# suites 0
# pass 5
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 81.561246
```

## 6. 확인 사항 (있는 그대로)

1. **`task list`가 비어 있음.** 작업 3건은 `.claude-flow/tasks/store.json`에 정상 저장됐지만
   `task list --all`은 "No tasks found"를 반환했습니다. 데몬(`ruflo daemon start`)을 켜도 동일했습니다.
   `task status <id>`는 내용을 보여주지만 제목이 `Task: undefined`로 나옵니다.
   → CLI 단독 사용 시 작업 관리는 파일 기록 수준이며, 실제 실행은 MCP 서버(Claude Code 연결) 경로입니다.
2. **`status`가 항상 STOPPED.** `agent list`는 에이전트 3개를 보여주는데 `status`는 0개로 표시됩니다.
   `status`는 실행 중인 오케스트레이터 세션 기준이라 CLI로 만든 에이전트를 세지 않습니다.
3. **라우팅 신뢰도 12.5%.** Q-Learning 테이블이 비어 있어 탐색(Exploration) 모드로 동작했고,
   "단위 테스트 작성" 작업을 tester가 아닌 documenter로 보냈습니다. 반면 `hooks pre-task`는
   규칙 기반으로 coder 70% / researcher 65% / tester 60%를 추천했습니다. 학습 데이터가 쌓이면 개선되는 구조입니다.
4. **의존성 취약점.** `npm install` 시 36건(critical 1, high 11) 경고. 실서비스 적용 전 `npm audit` 확인 필요.

## 7. 결론

- 설치 · 초기화 · 스웜 · 에이전트 · 메모리(벡터 검색) · 훅 · 코드 분석 · MCP 서버 기동은 모두 정상 동작합니다.
- 한국어 데이터의 메모리 저장과 의미 검색이 문제없이 됩니다.
- CLI 단독으로는 "작업을 실제로 실행"하지 않습니다. 실제 에이전트 실행은 Claude Code에 MCP로 붙여서 사용해야 합니다.
- 다음 단계로 권장: 이 폴더에서 Claude Code를 열어 `.mcp.json`의 `claude-flow` 서버가 잡히는지 확인한 뒤,
  `swarm_init` → `agent_spawn` → `task_orchestrate` MCP 도구로 실제 작업을 돌려보는 것.
