#!/usr/bin/env bash
# Linux 새 환경 설치 rehearsal (transfer-test).
# - 반입 ZIP 만 복사 (source checkout/개발 venv/pip cache 비의존)
# - 비관리자 사용자(tester), 한글/공백 경로, 다른 CWD(/tmp), 네트워크 네임스페이스(unshare -n: 외부 인터페이스 없음, lo 만)
# - 각 단계는 tools/collect_evidence.py 로 test-evidence/transfer-*.json 에 기록 (경로 정규화)
# 사용: sudo bash tools/transfer_test_linux.sh <ZIP> <profile_id> [evidence_dir]
set -u
ZIP="$(readlink -f "$1")"; PROFILE="$2"; EVID="${3:-test-evidence}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TESTER=tester
TEST_ROOT="/home/$TESTER/반입 테스트/설계 자동화 DIA"
INSTALL_ROOT="$TEST_ROOT/DIA 설치"
DATA_ROOT="$TEST_ROOT/work space"
PY312=/usr/bin/python3.12
ZIPNAME="$(basename "$ZIP")"
id "$TESTER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$TESTER"
rm -rf "$TEST_ROOT"; mkdir -p "$TEST_ROOT/in"; cp "$ZIP" "$ZIP.sha256" "$TEST_ROOT/in/"; chown -R "$TESTER:$TESTER" "/home/$TESTER"
# 변조본 (negative test)
python3 - "$TEST_ROOT/in/$ZIPNAME" "$TEST_ROOT/in/tampered.zip" <<'PY'
import sys, zipfile, shutil
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
    for info in zi.infolist():
        data = zi.read(info)
        if info.filename.endswith("release-manifest.json"):
            data = data.replace(b'"version"', b'"version_x"', 1)
        zo.writestr(info, data)
PY
chown "$TESTER:$TESTER" "$TEST_ROOT/in/tampered.zip"

run_step() {  # name, command-string (tester 로, netns 안에서, CWD=/tmp)
  local name="$1"; shift
  local cmd="$*"
  python3 "$PROJECT_ROOT/tools/collect_evidence.py" --name "$name" --out-dir "$EVID" \
    --normalize "<PROJECT_ROOT>=$PROJECT_ROOT" --normalize "<TEST_ROOT>=$TEST_ROOT" \
    --python-note "CPython 3.12.3 (/usr/bin/python3.12, 회사 승인 Python 역할)" \
    --network-note "unshare -n (network namespace, external interfaces absent, loopback only)" \
    --user-note "non-root user '$TESTER' (no sudo), cwd=/tmp, Korean+space install path" \
    --timeout 1800 -- \
    unshare -n bash -c "ip link set lo up 2>/dev/null; cd /tmp && su - $TESTER -c 'export PYTHONUTF8=1 LANG=C.UTF-8 LC_ALL=C.UTF-8; unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy PIP_INDEX_URL PIP_EXTRA_INDEX_URL PIP_FIND_LINKS PIP_CERT; export PIP_INDEX_URL=https://blocked.invalid/simple; $cmd'"
}

PKG="$TEST_ROOT/pkg"
run_step transfer-00-sha256   "cd '$TEST_ROOT/in' && sha256sum -c '$ZIPNAME.sha256'"
run_step transfer-01-extract  "$PY312 -c \"import zipfile,sys; z=zipfile.ZipFile('$TEST_ROOT/in/$ZIPNAME'); z.extractall('$PKG'); print(len(z.namelist()),'members')\""
run_step transfer-02-preflight "$PY312 '$PKG/scripts/preflight.py' --python $PY312 --package '$TEST_ROOT/in/$ZIPNAME' --report '$TEST_ROOT/preflight_report.json' --min-out '$TEST_ROOT/target_profile.min.json' --json > '$TEST_ROOT/preflight.json'; rc=\$?; head -c 4000 '$TEST_ROOT/preflight.json'; echo; cat '$TEST_ROOT/target_profile.min.json'; exit \$rc"
run_step transfer-03-verify   "$PY312 '$PKG/scripts/verify.py' --package '$TEST_ROOT/in/$ZIPNAME' --json | head -c 3000"
run_step transfer-03b-verify-tampered-must-fail "$PY312 '$PKG/scripts/verify.py' --package '$TEST_ROOT/in/tampered.zip' --json | head -c 2000; rc=\${PIPESTATUS[0]}; echo exit=\$rc; test \$rc -ne 0"
run_step transfer-04-install  "$PY312 '$PKG/scripts/install.py' --package '$TEST_ROOT/in/$ZIPNAME' --install-root '$INSTALL_ROOT' --data-root '$DATA_ROOT' --python $PY312 --json | tail -c 4000"
run_step transfer-05-active   "cat '$INSTALL_ROOT/active.json' && ls '$INSTALL_ROOT/releases'"
run_step transfer-06-selftest "$PY312 '$PKG/scripts/selftest.py' --install-root '$INSTALL_ROOT' --data-root '$DATA_ROOT' --json | tail -c 3000"
run_step transfer-07-doctor   "$PY312 '$PKG/scripts/launch.py' --install-root '$INSTALL_ROOT' --data-root '$DATA_ROOT' doctor --json | head -c 2500"
run_step transfer-08-demo     "$PY312 '$PKG/scripts/launch.py' --install-root '$INSTALL_ROOT' --data-root '$DATA_ROOT' demo --offline --device cpu --output-dir '$DATA_ROOT/outputs/demo' --json | tail -c 4000"
run_step transfer-09-install-tampered-keeps-active "cp '$INSTALL_ROOT/active.json' /tmp/active.before; $PY312 '$PKG/scripts/install.py' --package '$TEST_ROOT/in/tampered.zip' --install-root '$INSTALL_ROOT' --data-root '$DATA_ROOT' --python $PY312 --json | tail -c 1500; rc=\${PIPESTATUS[0]}; echo exit=\$rc; cmp /tmp/active.before '$INSTALL_ROOT/active.json' && test \$rc -ne 0"
run_step transfer-10-no-pip-index "cd '$INSTALL_ROOT' && ls releases/*/*/install_log.json && grep -c -- '--no-index' releases/*/*/install_log.json"
echo "transfer-test done -> $EVID/transfer-*.json"
