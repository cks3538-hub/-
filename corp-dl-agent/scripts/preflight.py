"""preflight: 설치 전 진단 (표준 라이브러리만, 앱 미설치 상태에서 실행).

사용:
  python preflight.py [--python <경로>] [--install-root DIR] [--data-root DIR] [--package <zip|dir>]
                      [--report <json>] [--min-out <json>] [--required-free-gb 2] [--json]

검사: OS/아키텍처, Python 버전/구현/64bit, venv/ensurepip/pip 유무, 디스크 여유, 쓰기 권한, 한글/공백 경로,
      (--package 지정 시) 패키지 target 과의 호환.
출력: 상세 보고서(로컬 보관, 경로 포함) + target_profile.min.json (허용 필드만: OS/arch/Python ABI/기능 상태).
선행 조건 미충족(Python 없음/venv·ensurepip 없음/32bit/버전 불일치) 이면 exit 8 (BLOCKED_PREREQUISITE).
Python 을 자동 설치하지 않는다 (Store/웹 설치 금지). Python 자체가 없으면 preflight.ps1 이 진단한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

MIN_PYTHON = (3, 11)
REFERENCE_PYTHON = "3.12"


def _check(name: str, ok: Optional[bool], detail: str, *, prerequisite: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "status": "NOT_RUN" if ok is None else ("PASS" if ok else "FAIL"),
        "detail": detail,
        "prerequisite": prerequisite,
    }


def run_preflight(
    *,
    python: str,
    install_root: Path,
    data_root: Path,
    package: Optional[str],
    required_free_gb: float,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    probe = c.probe_python(python)
    if probe is None:
        checks.append(_check("python_present", False, f"지정한 Python 을 실행할 수 없습니다: {python}"))
        host = c.detect_host()
        host["python_version"] = "0.0.0"
        return {
            "status": "BLOCKED_PREREQUISITE",
            "exit_code": c.EXIT_BLOCKED_PREREQUISITE,
            "checks": checks,
            "host": host,
            "python": python,
        }
    host = c.detect_host(probe)
    ve = host["verified_environment"]
    checks.append(
        _check(
            "python_present",
            True,
            "{} {} ({})".format(host["python_implementation"], host["python_version"], python),
        )
    )
    parts = host["python_version"].split(".")
    ver = (
        (int(parts[0]), int(parts[1]))
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit()
        else (0, 0)
    )
    checks.append(
        _check(
            "python_version",
            ver >= MIN_PYTHON,
            "{} (최소 {}.{}, 참조 타깃 {})".format(
                host["python_version"], MIN_PYTHON[0], MIN_PYTHON[1], REFERENCE_PYTHON
            ),
        )
    )
    checks.append(
        _check(
            "python_implementation", host["python_implementation"] == "CPython", host["python_implementation"]
        )
    )
    checks.append(_check("python_64bit", int(ve.get("bits", 0)) == 64, "{}-bit".format(ve.get("bits"))))
    checks.append(
        _check(
            "venv_module",
            bool(ve.get("has_venv")),
            "venv 모듈 {}".format(
                "있음" if ve.get("has_venv") else "없음 (회사 Python 배포본에 venv 가 빠져 있음)"
            ),
        )
    )
    checks.append(
        _check(
            "ensurepip_module",
            bool(ve.get("has_ensurepip")),
            "ensurepip {}".format(
                "있음" if ve.get("has_ensurepip") else "없음 (venv 안에 pip 을 만들 수 없음)"
            ),
        )
    )
    checks.append(
        _check(
            "pip_module",
            True if ve.get("pip_version") else None,
            "pip {}".format(ve.get("pip_version") or "없음: venv 생성 시 ensurepip 로 설치"),
            prerequisite=False,
        )
    )
    os_ok = c._norm_os(host["os"]) in ("windows", "linux")
    checks.append(
        _check(
            "os_supported",
            os_ok if os_ok else None,
            "{} {} ({})".format(host["os"], host["os_version"], host["architecture"]),
            prerequisite=False,
        )
    )

    free, where = c.disk_free_bytes(install_root)
    need = int(required_free_gb * (1024**3))
    checks.append(
        _check(
            "disk_space",
            (free >= need) if free >= 0 else None,
            f"여유 {free / 1024**3:.1f} GB / 필요 {required_free_gb:.1f} GB ({where})",
        )
    )
    w_ok, w_where = c.writable_dir(install_root)
    checks.append(_check("write_permission_install_root", w_ok, w_where))
    if data_root != install_root:
        d_ok, d_where = c.writable_dir(data_root)
        checks.append(_check("write_permission_data_root", d_ok, d_where))
    k_ok, k_where = c.korean_space_path_ok(install_root)
    checks.append(_check("korean_space_path", k_ok, k_where, prerequisite=False))

    manifest_target: Optional[dict[str, Any]] = None
    if package:
        try:
            with c.PackageSource(package) as src:
                if src.exists(c.MANIFEST_NAME):
                    m = json.loads(src.read_bytes(c.MANIFEST_NAME).decode("utf-8"))
                    manifest_target = m.get("target") if isinstance(m, dict) else None
            if manifest_target:
                problems = c.target_mismatches(manifest_target, host)
                checks.append(
                    _check(
                        "package_target",
                        not problems,
                        "; ".join(problems)
                        if problems
                        else "패키지 target {} 과 호환".format(manifest_target.get("profile_id")),
                    )
                )
                if isinstance(manifest_target.get("features"), list):
                    host["features"] = list(manifest_target["features"])
            else:
                checks.append(_check("package_target", False, "패키지에 release-manifest.json 이 없습니다"))
        except c.ScriptError as exc:
            checks.append(_check("package_target", False, exc.message))

    blocked = [ch["name"] for ch in checks if ch["status"] == "FAIL" and ch["prerequisite"]]
    warn = [ch["name"] for ch in checks if ch["status"] == "FAIL" and not ch["prerequisite"]]
    if blocked:
        status, code = "BLOCKED_PREREQUISITE", c.EXIT_BLOCKED_PREREQUISITE
    elif warn:
        status, code = "PARTIAL", c.EXIT_OK
    else:
        status, code = "PASS", c.EXIT_OK
    return {
        "status": status,
        "exit_code": code,
        "blocked": blocked,
        "warnings": warn,
        "checks": checks,
        "host": host,
        "python": python,
        "install_root": str(install_root),
        "data_root": str(data_root),
        "package_target": manifest_target,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="설치 전 진단 (표준 라이브러리만). Python 을 설치하지 않습니다.")
    ap.add_argument(
        "--python",
        default=None,
        help="검사할 회사 승인 Python 경로 (기본: PYTHON 환경변수 또는 이 스크립트를 실행한 Python)",
    )
    ap.add_argument(
        "--install-root", default=None, help="설치 root (기본: DIA_INSTALL_ROOT 또는 %%LOCALAPPDATA%%\\DIA)"
    )
    ap.add_argument(
        "--data-root",
        default=None,
        help="업무 데이터 root (기본: DIA_DATA_ROOT 또는 <install_root>/workspace)",
    )
    ap.add_argument("--package", default=None, help="반입 ZIP 또는 추출 폴더 (target 호환 검사)")
    ap.add_argument(
        "--report",
        default=None,
        help="상세 보고서 JSON 경로 (로컬 보관; 기본 <install_root>/preflight/preflight_report.json)",
    )
    ap.add_argument(
        "--min-out",
        default=None,
        help="target_profile.min.json 경로 (기본 <install_root>/preflight/target_profile.min.json)",
    )
    ap.add_argument("--required-free-gb", type=float, default=2.0, help="필요 디스크 여유 (GB, 기본 2)")
    ap.add_argument("--json", action="store_true", help="결과 JSON 을 표준 출력")
    args = ap.parse_args(argv)

    python = args.python or os.environ.get("PYTHON", "").strip() or sys.executable
    install_root = Path(args.install_root).expanduser() if args.install_root else c.default_install_root()
    data_root = Path(args.data_root).expanduser() if args.data_root else c.default_data_root(install_root)
    result = run_preflight(
        python=python,
        install_root=install_root,
        data_root=data_root,
        package=args.package,
        required_free_gb=args.required_free_gb,
    )

    report_dir = install_root / "preflight"
    report_path = Path(args.report) if args.report else report_dir / "preflight_report.json"
    min_path = Path(args.min_out) if args.min_out else report_path.parent / "target_profile.min.json"
    feature_status = {"preflight": result["status"]}
    minimal = c.min_profile(result["host"], preflight_status=result["status"], feature_status=feature_status)
    written: dict[str, str] = {}
    for path, obj, key in ((report_path, result, "report"), (min_path, minimal, "min_profile")):
        try:
            c.write_json_atomic(path, obj)
            written[key] = str(path)
        except OSError:
            fallback = Path(tempfile.gettempdir()) / "DIA" / path.name
            c.write_json_atomic(fallback, obj)
            written[key] = str(fallback)
    result["written"] = written
    result["min_profile"] = minimal

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        h = result["host"]
        print(
            "preflight: {} {} / {} {} ({})".format(
                h.get("os"),
                h.get("architecture"),
                h.get("python_implementation"),
                h.get("python_version"),
                h.get("python_abi"),
            )
        )
        for ch in result["checks"]:
            print("  [{}] {}: {}".format(ch["status"], ch["name"], ch["detail"]))
        print("결과: {}".format(result["status"]))
        if result["status"] == "BLOCKED_PREREQUISITE":
            print("선행 조건 미충족: {}".format(", ".join(result.get("blocked", []))))
            print(
                "해결 방법: 회사 승인 CPython 3.12 x64 (venv/ensurepip 포함) 를 설치한 뒤 PYTHON 환경변수 또는 --python 으로 경로를 지정하세요. 이 스크립트는 Python 을 자동 설치하지 않습니다."
            )
        print("상세 보고서(로컬): {}".format(written.get("report")))
        print("최소 요약(전달용, 허용 필드만): {}".format(written.get("min_profile")))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
