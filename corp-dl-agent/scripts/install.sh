#!/usr/bin/env bash
# Linux rehearsal 용 wrapper (사내 Windows 에서는 대응 .cmd 를 사용). 스크립트 위치 기준으로 동작한다.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
: "${DIA_INSTALL_ROOT:=${XDG_DATA_HOME:-$HOME/.local/share}/DIA}"
export DIA_INSTALL_ROOT PYTHONIOENCODING=utf-8 PYTHONUTF8=1
PY="${PYTHON:-}"
if [ -n "$PY" ] && [ ! -x "$PY" ] && ! command -v "$PY" >/dev/null 2>&1; then echo "[경고] PYTHON 환경변수의 경로를 실행할 수 없습니다: $PY"; PY=""; fi
if [ -z "$PY" ]; then
  for cand in python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "[BLOCKED_PREREQUISITE] Python 3.12 을 찾지 못했습니다. PYTHON 환경변수로 회사 승인 Python 경로를 지정하세요 (자동 설치 없음)."
  exit 8
fi
DATA_ARGS=()
if [ -n "${DIA_DATA_ROOT:-}" ]; then DATA_ARGS=(--data-root "$DIA_DATA_ROOT"); fi
echo "[preflight]"
"$PY" "$SCRIPT_DIR/preflight.py" --install-root "$DIA_INSTALL_ROOT" ${DATA_ARGS[@]+"${DATA_ARGS[@]}"} --package "$PKG_DIR" || exit $?
echo "[verify]"
"$PY" "$SCRIPT_DIR/verify.py" --package "$PKG_DIR" --install-root "$DIA_INSTALL_ROOT" || exit $?
echo "[install]"
exec "$PY" "$SCRIPT_DIR/install.py" --package "$PKG_DIR" --install-root "$DIA_INSTALL_ROOT" ${DATA_ARGS[@]+"${DATA_ARGS[@]}"} "$@"
