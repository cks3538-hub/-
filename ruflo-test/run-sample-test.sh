#!/usr/bin/env bash
# =============================================================================
# ruflo 샘플 테스트 시나리오
#
# 시나리오: "코인 매매 손익 계산기" 샘플 코드를 대상으로
#   진단 → 스웜 구성 → 에이전트 생성 → 작업 등록/라우팅 → 메모리 저장/검색
#   → 훅(hooks) → 코드 분석 → 단위 테스트 → 상태 확인
# 순서로 ruflo의 핵심 기능을 한 번에 점검합니다.
#
# 실행:  bash run-sample-test.sh   (또는 npm run test:sample)
# 결과:  results/sample-test.log  (전체 로그)
#        results/step-NN.txt      (단계별 출력)
#        results/summary.md       (요약표)
# =============================================================================
set -u
cd "$(dirname "$0")"

export NO_COLOR=1 FORCE_COLOR=0 npm_config_update_notifier=false
RESULTS=results
mkdir -p "$RESULTS"
LOG="$RESULTS/sample-test.log"
SUMMARY="$RESULTS/summary.md"
: > "$LOG"
PASS=0; FAIL=0; N=0
ROWS=()

step() {
  local title="$1"; shift
  N=$((N+1))
  local id; id=$(printf "%02d" "$N")
  local out="$RESULTS/step-$id.txt"
  {
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo " STEP $id: $title"
    echo " \$ $*"
    echo "════════════════════════════════════════════════════════════"
  } | tee -a "$LOG"
  local start; start=$(date +%s%N)
  local rc=0
  timeout 180 "$@" > "$out" 2>&1 || rc=$?
  local ms=$(( ($(date +%s%N) - start) / 1000000 ))
  # 스피너(\r) 줄바꿈 정리 + 스피너 프레임 줄 · 공백만 있는 줄 제거
  sed -i -e 's/\r/\n/g' "$out"
  sed -i -e '/^[.:]\{3\} /d' -e '/^[[:space:]]\+$/d' "$out"
  tee -a "$LOG" < "$out"
  local status
  if [ "$rc" -eq 0 ]; then PASS=$((PASS+1)); status="PASS";
  else FAIL=$((FAIL+1)); status="FAIL (exit $rc)"; fi
  echo "→ $status (${ms}ms)" | tee -a "$LOG"
  ROWS+=("| $id | $title | $status | ${ms}ms |")
}

echo "ruflo 샘플 테스트 시작: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"

# ── 1. 설치/환경 진단 ────────────────────────────────────────────────────────
step "버전 확인"            npx ruflo --version
step "시스템 진단 (doctor)" npx ruflo doctor
step "초기화 상태 확인"      npx ruflo init check

# ── 2. 스웜 구성 + 에이전트 생성 ─────────────────────────────────────────────
step "스웜 초기화 (hierarchical, 최대 5)" npx ruflo swarm init --topology hierarchical --max-agents 5
step "에이전트 생성: researcher" npx ruflo agent spawn -t researcher --name researcher-1
step "에이전트 생성: coder"      npx ruflo agent spawn -t coder      --name coder-1
step "에이전트 생성: tester"     npx ruflo agent spawn -t tester     --name tester-1
step "에이전트 목록"             npx ruflo agent list

# ── 3. 작업(task) 등록 + 라우팅 ──────────────────────────────────────────────
step "작업 생성: 리서치"  npx ruflo task create -t research       -d "코인 손익 계산 로직 요구사항 정리" -p high   --tags crypto,pnl
step "작업 생성: 구현"    npx ruflo task create -t implementation -d "price-calc.js 에 수수료 반영 기능 추가" -p normal --tags crypto,pnl
step "작업 생성: 테스트"  npx ruflo task create -t testing        -d "수수료 반영 단위 테스트 작성"         -p normal --tags crypto,test
step "작업 목록 (전체)"   npx ruflo task list --all
step "작업 라우팅 (Q-Learning)" npx ruflo route task "price-calc.js 의 percentChange 함수에 대한 단위 테스트 작성"
step "라우팅 가능 에이전트 목록" npx ruflo route list-agents

# ── 4. 메모리 저장 + 의미 검색 ───────────────────────────────────────────────
step "메모리 저장 1" npx ruflo memory store --namespace crypto --key "rule/avg-cost" --value "평균 매수 단가는 총 매수금액을 총 수량으로 나눈다"
step "메모리 저장 2" npx ruflo memory store --namespace crypto --key "rule/risk"     --value "변동률 5% 미만 low, 15% 미만 medium, 그 이상 high"
step "메모리 저장 3" npx ruflo memory store --namespace crypto --key "todo/fee"      --value "매매 수수료 0.05% 를 손익 계산에 반영해야 함"
step "메모리 의미 검색: 손익"  npx ruflo memory search --namespace crypto -q "손익 계산 규칙"
step "메모리 목록"            npx ruflo memory list --namespace crypto

# ── 5. 훅(hooks) ──────────────────────────────────────────────────────────────
step "훅 pre-task"   npx ruflo hooks pre-task --description "price-calc.js 리팩터링"
step "훅 목록"       npx ruflo hooks list

# ── 6. 코드 분석 + 단위 테스트 ────────────────────────────────────────────────
step "코드 분석: 심볼 추출"   npx ruflo analyze symbols sample/src
step "코드 분석: 복잡도"      npx ruflo analyze complexity sample/src
step "샘플 단위 테스트 (node --test)" bash -c 'node --test sample/test/*.test.js'

# ── 7. 최종 상태 ─────────────────────────────────────────────────────────────
step "워크플로 목록"  npx ruflo workflow list
step "시스템 상태"    npx ruflo status
step "스웜 상태"      npx ruflo swarm status

# ── 요약 ─────────────────────────────────────────────────────────────────────
{
  echo "# ruflo 샘플 테스트 요약"
  echo ""
  echo "- 실행 시각: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "- ruflo 버전: $(npx ruflo --version 2>/dev/null | tail -1)"
  echo "- Node: $(node -v)"
  echo "- 결과: **PASS $PASS / FAIL $FAIL / 총 $N**"
  echo ""
  echo "| # | 단계 | 결과 | 소요 |"
  echo "|---|------|------|------|"
  printf '%s\n' "${ROWS[@]}"
} > "$SUMMARY"

echo "" | tee -a "$LOG"
cat "$SUMMARY" | tee -a "$LOG"
[ "$FAIL" -eq 0 ]
