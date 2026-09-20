# ruflo 설치 · 샘플 실행 테스트

[ruflo](https://www.npmjs.com/package/ruflo)(= Claude Flow v3)를 설치하고,
"코인 매매 손익 계산기" 샘플 코드를 대상으로 핵심 기능을 한 번에 점검하는 폴더입니다.

- CLI 테스트 보고서: [TEST_REPORT.md](./TEST_REPORT.md)
- **MCP 서버 연결 · 실제 작업 실행 보고서: [MCP_REPORT.md](./MCP_REPORT.md)**
- 실행 로그 전체: [results/sample-test.log](./results/sample-test.log)
- 단계별 출력: `results/step-NN.txt`

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `run-sample-test.sh` | 27단계 샘플 테스트 시나리오 (한 번에 실행) |
| `make-report.sh` | `results/` 실제 출력을 발췌해 `TEST_REPORT.md` 재생성 |
| `mcp/run-claude-agent.sh` | Claude Code CLI 헤드리스 + ruflo MCP 로 실제 코드 작업 수행 (경로 A) |
| `mcp/run-job.mjs` | MCP SDK 클라이언트로 ruflo 도구 21개 직접 호출 (경로 B) |
| `mcp/agent-task.md` | 경로 A 에서 에이전트에게 주는 작업 지시서 |
| `mcp/claude-mcp.json` | Claude Code 용 ruflo MCP 서버 설정 (로컬 설치 사용) |
| `sample/src/price-calc.js` | 평균단가 · 평가손익 · 변동률 · 리스크 등급 계산 함수 |
| `sample/src/portfolio.js` | 여러 코인 손익 요약 |
| `sample/test/price-calc.test.js` | Node 내장 테스트 러너용 단위 테스트 5건 |
| `results/` | 테스트 실행 결과 (로그 · 단계별 출력 · 요약표) |
| `CLAUDE.md`, `.mcp.json`, `.claude/`, `.claude-flow/` | `npx ruflo init` 이 생성한 설정 |

## 따라하기 (처음부터 끝까지)

1. 터미널을 열고 이 폴더로 이동합니다.
   ```bash
   cd ruflo-test
   ```
2. ruflo를 설치합니다. (1분 정도 걸립니다)
   ```bash
   npm install
   ```
3. ruflo를 초기화합니다. 이 폴더 안에만 설정을 만들고 홈 폴더는 건드리지 않는 옵션입니다.
   ```bash
   npx ruflo init --minimal --no-global --no-signup --no-skills-sh --no-codex-detect
   ```
4. 샘플 테스트를 실행합니다.
   ```bash
   npm run test:sample
   ```
5. 결과를 확인합니다.
   ```bash
   cat results/summary.md
   ```

## 자주 쓰는 명령

| 명령 | 하는 일 |
|---|---|
| `npx ruflo doctor` | 설치 상태 진단 |
| `npx ruflo swarm init --topology mesh --max-agents 3` | 에이전트 스웜 만들기 |
| `npx ruflo agent spawn -t coder --name my-coder` | 에이전트 추가 |
| `npx ruflo agent list` | 에이전트 목록 |
| `npx ruflo memory store --key k --value "v" --namespace ns` | 메모리 저장 (384차원 벡터 자동 생성) |
| `npx ruflo memory search -q "검색어" --namespace ns` | 의미 검색 |
| `npx ruflo route task "할 일 설명"` | 어떤 에이전트가 적합한지 라우팅 |
| `npx ruflo analyze symbols <폴더>` | 함수 · 클래스 목록 추출 |
| `npx ruflo analyze complexity <폴더>` | 복잡도 측정 |
| `npx ruflo mcp start` | Claude Code용 MCP 서버 실행 |
| `npx ruflo cleanup` | ruflo가 만든 파일 정리 |

## Claude Code에 연결하기

`npx ruflo init`이 만든 `.mcp.json`을 Claude Code가 읽으면 `claude-flow` MCP 서버가 등록됩니다.
이 폴더에서 Claude Code를 실행하면 자동으로 인식합니다.

헤드리스(터미널 한 줄)로 실제 작업을 시키려면:

```bash
bash mcp/run-claude-agent.sh      # 결과: results/claude-agent-final.txt
```

MCP 서버에 프로그램으로 직접 붙어 도구를 호출하려면:

```bash
node mcp/run-job.mjs              # 결과: results/mcp-job.json
```

실행 결과와 발견 사항은 [MCP_REPORT.md](./MCP_REPORT.md)에 있습니다.

## 주의사항

- `npm install` 시 취약점 경고(36건)가 나옵니다. 실서비스에 넣기 전 `npm audit`으로 확인하세요.
- 이 버전(3.42.4)에서는 CLI만으로 만든 작업이 `task list`에 나타나지 않습니다.
  작업 자체는 `.claude-flow/tasks/store.json`에 저장되며, 실제 오케스트레이션은
  Claude Code에 MCP 서버로 연결해서 사용하는 구조입니다. 자세한 내용은 TEST_REPORT.md 참고.
