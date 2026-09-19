#!/usr/bin/env bash
# TEST_REPORT.md 생성기: results/ 의 실제 출력에서 발췌해 보고서를 다시 만듭니다.
# 실행: bash make-report.sh   (run-sample-test.sh 실행 후)
set -eu
cd "$(dirname "$0")"
excerpt() { # $1=step번호 $2=제목 $3=명령 $4=줄수
  echo "### STEP $1 · $2"; echo; echo '```bash'; echo "$ $3"; echo '```'; echo
  echo '```text'; sed -n "1,${4}p" "results/step-$1.txt" | sed '/^[[:space:]]*$/N;/^\n$/D'; echo '```'; echo
}
{
cat <<'HEAD'
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
HEAD
echo; echo "## 4. 요약표"; echo
sed -n '/^| #/,$p' results/summary.md
echo
echo "## 5. 단계별 결과 발췌 (실제 출력)"; echo
excerpt 02 "시스템 진단" "npx ruflo doctor" 32
excerpt 04 "스웜 초기화" "npx ruflo swarm init --topology hierarchical --max-agents 5" 20
excerpt 08 "에이전트 목록" "npx ruflo agent list" 14
excerpt 09 "작업 생성" 'npx ruflo task create -t research -d "코인 손익 계산 로직 요구사항 정리" -p high --tags crypto,pnl' 18
excerpt 13 "Q-Learning 라우팅" 'npx ruflo route task "price-calc.js 의 percentChange 함수에 대한 단위 테스트 작성"' 28
excerpt 15 "메모리 저장" 'npx ruflo memory store --namespace crypto --key "rule/avg-cost" --value "평균 매수 단가는 ..."' 18
excerpt 18 "메모리 의미 검색" 'npx ruflo memory search --namespace crypto -q "손익 계산 규칙"' 14
excerpt 20 "훅 pre-task (에이전트 추천)" 'npx ruflo hooks pre-task --description "price-calc.js 리팩터링"' 26
excerpt 22 "코드 분석: 심볼 추출" "npx ruflo analyze symbols sample/src" 26
excerpt 23 "코드 분석: 복잡도" "npx ruflo analyze complexity sample/src" 20
excerpt 24 "샘플 단위 테스트" "node --test sample/test/*.test.js" 60
cat <<'TAIL'
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
TAIL
} > TEST_REPORT.md
echo "TEST_REPORT.md 생성 완료 ($(wc -l < TEST_REPORT.md) 줄)"
