"""install: 반입 패키지 오프라인 설치기 (표준 라이브러리만, 앱 미설치 상태에서 실행).

사용:
  python install.py --package <zip|dir> --install-root <dir> [--data-root <dir>] [--python <path>]
                    [--skip-selftest] [--json] [--target-profile <json>] [--no-activate]

순서:
  verify → releases/<release_id>/<profile_id>/ 최종 위치에 추출 (같은 release 가 이미 있고 manifest hash 가 같으면
  재사용, 다르면 중단) → 그 위치에 .venv 생성 (기존 .venv 가 있으면 --clear 로 덮어쓰지 않고 중단; venv/ensurepip
  없으면 exit 8) → pip 환경/설정 차단 → pip install --isolated --no-index --find-links <wheelhouse> --require-hashes
  --only-binary=:all: --no-cache-dir --disable-pip-version-check -r locks/<profile>.txt → pip check → import →
  version smoke → self-test → company/ workspace/ backups/ 폴더 생성(기존 보존) → active.json 원자적 교체.
  실패 시 기존 active.json 유지, 새 폴더에 install_log.json(status=install_failed) 기록.
환경변수 기본값: DIA_INSTALL_ROOT, DIA_DATA_ROOT, PYTHON.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

# 설정으로 --config 를 받지 않는 앱 명령 (launch.py 와 공유)
NO_CONFIG_COMMANDS = frozenset(
    {"version", "validate", "run", "status", "pause", "resume", "cancel", "report", "predict"}
)


class InstallFailure(c.ScriptError):
    pass


def _emit(sink: Any, msg: str) -> None:
    if sink is not None:
        sink(msg)


def _record(log: dict[str, Any], name: str, result: dict[str, Any], dest: Optional[Path]) -> None:
    entry = {"name": name}
    entry.update(result)
    log["steps"].append(entry)
    if dest is not None and dest.is_dir():
        c.write_json_atomic(dest / c.INSTALL_LOG_NAME, log)


def _fail(
    log: dict[str, Any],
    dest: Optional[Path],
    code: str,
    message: str,
    *,
    hint: str = "",
    details: Optional[dict[str, Any]] = None,
) -> InstallFailure:
    log["status"] = "install_failed"
    log["finished_at"] = c.now_iso()
    log["error"] = {"code": code, "message": message, "details": details or {}}
    if dest is not None and dest.is_dir():
        c.write_json_atomic(dest / c.INSTALL_LOG_NAME, log)
    return InstallFailure(code, message, hint=hint, details=details)


def _step_ok(
    name: str,
    log: dict[str, Any],
    dest: Optional[Path],
    result: dict[str, Any],
    *,
    code: str = "E_INSTALL_FAILED",
    what: str = "",
) -> None:
    _record(log, name, result, dest)
    if result.get("exit_code", 1) != 0:
        raise _fail(
            log,
            dest,
            code,
            "{} 실패 (exit {})".format(what or name, result.get("exit_code")),
            hint="releases/<id>/<profile>/install_log.json 의 stderr_tail 을 확인하세요. 기존 활성 버전은 유지됩니다.",
            details={
                "step": name,
                "stderr_tail": result.get("stderr_tail", "")[-1500:],
                "stdout_tail": result.get("stdout_tail", "")[-800:],
            },
        )


def run_install(
    *,
    package: str,
    install_root: Path,
    data_root: Path,
    python: str,
    skip_selftest: bool = False,
    activate: bool = True,
    target_profile: Optional[dict[str, Any]] = None,
    timeout: int = 3600,
    sink: Any = None,
) -> dict[str, Any]:
    install_root = Path(install_root).expanduser()
    data_root = Path(data_root).expanduser()
    log: dict[str, Any] = {
        "status": "install_started",
        "started_at": c.now_iso(),
        "package": str(Path(package).expanduser().resolve()),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "base_python": python,
        "skip_selftest": skip_selftest,
        "steps": [],
    }
    dest: Optional[Path] = None

    # 0. base Python 선행 조건
    probe = c.probe_python(python)
    if probe is None:
        raise InstallFailure(
            "E_BLOCKED_PREREQUISITE",
            f"venv 를 만들 Python 을 실행할 수 없습니다: {python}",
            hint="회사 승인 Python 경로를 --python 또는 PYTHON 환경변수로 지정하세요.",
        )
    if not probe.get("has_venv") or not probe.get("has_ensurepip"):
        raise InstallFailure(
            "E_BLOCKED_PREREQUISITE",
            "Python 에 venv/ensurepip 모듈이 없습니다 (venv={}, ensurepip={})".format(
                probe.get("has_venv"), probe.get("has_ensurepip")
            ),
            hint="venv/ensurepip 가 포함된 회사 승인 CPython 배포본이 필요합니다. 자동 설치/다운로드는 하지 않습니다.",
        )
    if int(probe.get("bits", 0)) != 64:
        raise InstallFailure(
            "E_BLOCKED_PREREQUISITE", "64-bit Python 이 필요합니다 ({}-bit)".format(probe.get("bits"))
        )
    host = c.detect_host(probe)
    log["python_probe"] = {
        k: probe.get(k)
        for k in (
            "python_version",
            "python_implementation",
            "bits",
            "system",
            "machine",
            "pip_version",
            "ensurepip_version",
        )
    }
    target = target_profile if target_profile is not None else host

    # 1. verify
    _emit(sink, "[1/8] 패키지 검증")
    with c.PackageSource(package) as src:
        report = c.verify_package(src, target=target, install_root=install_root)
        failed = [ch for ch in report["checks"] if ch["status"] == "FAIL"]
        log["verify"] = {"ok": report["ok"], "checks": report["checks"]}
        if failed:
            raise InstallFailure(
                "E_PACKAGE_INVALID",
                "패키지 검증 실패: "
                + "; ".join("{}: {}".format(ch["name"], ch["detail"]) for ch in failed)[:1500],
                hint="verify.py 로 상세를 확인하세요. 설치는 시작하지 않았고 기존 활성 버전은 그대로입니다.",
                details={"failed": ["{}: {}".format(ch["name"], ch["detail"]) for ch in failed]},
            )
        manifest = report["manifest"]
        manifest_sha = report["manifest_sha256"]
        release_id, profile_id = manifest["release_id"], manifest["profile_id"]
        app_module = manifest["app_module"]
        dest = c.release_dir(install_root, release_id, profile_id)
        log.update(
            {
                "release_id": release_id,
                "profile_id": profile_id,
                "release_dir": str(dest),
                "manifest_sha256": manifest_sha,
            }
        )
        venv_dir = dest / ".venv"
        venv_py = c.venv_python_path(venv_dir)

        # 2. 기존 release 폴더 처리 (idempotent / 다른 내용 거부)
        reused = False
        if dest.exists():
            existing_manifest = dest / c.MANIFEST_NAME
            if not existing_manifest.is_file():
                raise InstallFailure(
                    "E_INSTALL_FAILED",
                    f"release 폴더가 이미 있으나 manifest 가 없습니다 (불완전한 폴더): {dest}",
                    hint="해당 폴더를 확인/정리한 뒤 다시 설치하세요. 설치기는 임의로 삭제하지 않습니다.",
                )
            if c.sha256_file(existing_manifest) != manifest_sha:
                raise InstallFailure(
                    "E_INSTALL_FAILED",
                    f"같은 release_id {release_id} 가 다른 내용으로 이미 설치되어 있습니다. 조용히 덮어쓰지 않습니다.",
                    hint="새 내용이면 버전을 올려 새 release 로 만들거나, 기존 폴더를 관리자가 확인 후 정리하세요.",
                    details={"release_dir": str(dest)},
                )
            prev_log: dict[str, Any] = {}
            if (dest / c.INSTALL_LOG_NAME).is_file():
                try:
                    prev_log = c.read_json(dest / c.INSTALL_LOG_NAME)
                except (OSError, ValueError):
                    prev_log = {}
            if prev_log.get("status") == "installed" and venv_py.is_file():
                reused = True
                _emit(sink, f"동일 release (manifest hash 일치) 가 이미 설치되어 있어 재사용합니다: {dest}")
                log["reused"] = True
                log["steps"] = list(prev_log.get("steps", []))
                _record(
                    log,
                    "reuse_existing",
                    {"exit_code": 0, "detail": "manifest sha256 일치, 기존 venv 재사용"},
                    None,
                )
            else:
                if venv_dir.exists():
                    raise InstallFailure(
                        "E_INSTALL_FAILED",
                        f"이전 설치 시도 폴더에 .venv 가 남아 있습니다: {venv_dir}",
                        hint="설치기는 기존 .venv 를 --clear 로 덮어쓰지 않습니다. 실패한 폴더를 관리자가 확인 후 삭제한 뒤 다시 설치하세요.",
                    )
                # 이전 추출 파일을 재검증하여 재사용
                bad = []
                for f in manifest["files"]:
                    fp = dest / f["path"]
                    if not fp.is_file() or fp.stat().st_size != f["size"] or c.sha256_file(fp) != f["sha256"]:
                        bad.append(f["path"])
                if bad:
                    raise InstallFailure(
                        "E_INSTALL_FAILED",
                        "이전 추출 파일이 manifest 와 다릅니다",
                        details={"files": bad[:20]},
                    )
                log["steps"] = []
                _record(
                    log,
                    "reuse_extracted",
                    {
                        "exit_code": 0,
                        "detail": "이전 추출 파일 {}개 재검증 통과".format(len(manifest["files"])),
                    },
                    dest,
                )
        else:
            # 3. 추출 (임시 폴더 → 검증 → rename)
            _emit(sink, f"[2/8] 추출: {dest}")
            tmp = dest.parent / f".{dest.name}.extracting-{os.getpid()}"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            try:
                src.extract_to(tmp)
                bad = []
                for f in manifest["files"]:
                    fp = tmp / f["path"]
                    if not fp.is_file() or fp.stat().st_size != f["size"] or c.sha256_file(fp) != f["sha256"]:
                        bad.append(f["path"])
                if bad:
                    raise InstallFailure(
                        "E_PACKAGE_INVALID", "추출 결과가 manifest 와 다릅니다", details={"files": bad[:20]}
                    )
                os.replace(tmp, dest)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            _record(
                log,
                "extract",
                {"exit_code": 0, "files": len(manifest["files"]), "bytes": report.get("total_bytes")},
                dest,
            )

    if not reused:
        env = c.sanitized_env()
        # 4. venv (최종 위치, 이동 금지, --clear 없음)
        _emit(sink, f"[3/8] venv 생성: {venv_dir}")
        if venv_dir.exists():
            raise _fail(
                log,
                dest,
                "E_INSTALL_FAILED",
                f".venv 가 이미 존재합니다: {venv_dir}",
                hint="--clear 로 덮어쓰지 않습니다.",
            )
        r = c.run_logged([python, "-m", "venv", str(venv_dir)], env=env, timeout=timeout)
        _record(log, "venv_create", r, dest)
        if r["exit_code"] != 0 or not venv_py.is_file():
            raise _fail(
                log,
                dest,
                "E_BLOCKED_PREREQUISITE",
                "venv 생성 실패 (exit {})".format(r["exit_code"]),
                hint="ensurepip/venv 가 있는 회사 승인 Python 인지 확인하세요. 자동 다운로드는 하지 않습니다.",
                details={"stderr_tail": r["stderr_tail"][-1500:]},
            )
        r = c.run_logged([str(venv_py), "-m", "pip", "--version"], env=env, timeout=timeout)
        _record(log, "pip_version", r, dest)
        if r["exit_code"] != 0:
            raise _fail(
                log,
                dest,
                "E_BLOCKED_PREREQUISITE",
                "새 venv 에 pip 이 없습니다 (ensurepip 실패)",
                details={"stderr_tail": r["stderr_tail"][-1500:]},
            )

        # 5. pip install (offline, hash lock)
        _emit(sink, "[4/8] 오프라인 설치 (--no-index --require-hashes)")
        wheelhouse = dest / manifest["wheelhouse_dir"]
        lock = dest / manifest["lock_file"]
        app_wheel_dir = (dest / manifest["app_wheel"]).parent
        cmd = [
            str(venv_py),
            "-m",
            "pip",
            "install",
            "--isolated",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--find-links",
            str(app_wheel_dir),
            "--require-hashes",
            "--only-binary=:all:",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--no-input",
            "-r",
            str(lock),
        ]
        r = c.run_logged(cmd, env=env, cwd=str(dest), timeout=timeout, tail_lines=80)
        _step_ok("pip_install", log, dest, r, what="pip 오프라인 설치")
        _emit(sink, "[5/8] pip check / import / version")
        _step_ok(
            "pip_check",
            log,
            dest,
            c.run_logged([str(venv_py), "-m", "pip", "check"], env=env, timeout=timeout),
            what="pip check",
        )
        _step_ok(
            "import_app",
            log,
            dest,
            c.run_logged(
                [str(venv_py), "-c", f"import {app_module}"], env=env, cwd=str(install_root), timeout=timeout
            ),
            what="앱 import",
        )
        r = c.run_logged(
            [str(venv_py), "-m", app_module, "version", "--json"],
            env=env,
            cwd=str(install_root),
            timeout=timeout,
        )
        _step_ok("smoke_version", log, dest, r, what="version smoke")
        try:
            vinfo = json.loads(r["stdout_tail"].strip().splitlines()[-1])
            reported = str(vinfo.get("version"))
        except (ValueError, IndexError, AttributeError):
            reported = "?"
        if reported != manifest["version"]:
            _record(
                log,
                "smoke_version_match",
                {"exit_code": 1, "reported": reported, "expected": manifest["version"]},
                dest,
            )
            raise _fail(
                log,
                dest,
                "E_INSTALL_FAILED",
                "설치된 앱 버전 {} 이 manifest {} 과 다릅니다".format(reported, manifest["version"]),
            )
        _record(log, "smoke_version_match", {"exit_code": 0, "reported": reported}, dest)

        # 6. self-test
        if skip_selftest:
            _emit(sink, "[6/8] self-test 생략 (--skip-selftest)")
            _record(
                log,
                "selftest",
                {
                    "exit_code": 0,
                    "status": "skipped",
                    "detail": "--skip-selftest 지정. 설치 후 04_SelfTest.cmd / selftest.py 로 확인하세요.",
                },
                dest,
            )
        else:
            _emit(sink, "[6/8] self-test")
            data_root.mkdir(parents=True, exist_ok=True)
            company = install_root / "company"
            company.mkdir(parents=True, exist_ok=True)
            cmd = [
                str(venv_py),
                "-m",
                app_module,
                "self-test",
                "--json",
                "--output",
                str(dest / "selftest_result.json"),
                "--set",
                f"paths.data_root={data_root}",
                "--set",
                f"paths.company_root={company}",
            ]
            r = c.run_logged(cmd, env=env, cwd=str(install_root), timeout=timeout, tail_lines=80)
            _step_ok("selftest", log, dest, r, what="self-test")

        log["status"] = "installed"
        log["finished_at"] = c.now_iso()
        log["venv_python"] = str(venv_py)
        c.write_json_atomic(dest / c.INSTALL_LOG_NAME, log)

    # 7. 폴더 구조 (기존 보존)
    _emit(sink, "[7/8] company/ workspace/ backups/ 폴더 준비 (기존 보존)")
    layout = c.ensure_layout(install_root, data_root)
    log["layout"] = layout

    # 8. active.json
    result: dict[str, Any] = {
        "ok": True,
        "release_id": release_id,
        "profile_id": profile_id,
        "release_dir": str(dest),
        "venv_python": str(venv_py),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "app_module": app_module,
        "manifest": manifest,
        "reused": reused,
        "activated": False,
        "log_path": str(dest / c.INSTALL_LOG_NAME),
    }
    if activate:
        _emit(sink, "[8/8] active.json 전환")
        result["active"] = activate_release(install_root, data_root, manifest, dest, venv_py)
        result["activated"] = True
    else:
        _emit(sink, "[8/8] active.json 전환 보류 (--no-activate)")
    return result


def activate_release(
    install_root: Path,
    data_root: Path,
    manifest: dict[str, Any],
    dest: Path,
    venv_py: Path,
    *,
    reason: str = "install",
) -> dict[str, Any]:
    """active.json 을 원자적으로 교체한다 (모든 검사 통과 후에만 호출)."""
    prev = c.read_active(install_root)
    active = {
        "release_id": manifest["release_id"],
        "profile_id": manifest["profile_id"],
        "venv_python": str(venv_py),
        "release_dir": str(dest),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "installed_at": c.now_iso(),
        "schema_version": manifest["schema_version"],
        "db_schema_version": manifest["db_schema_version"],
        "app_version": manifest["version"],
        "app_module": manifest["app_module"],
        "activated_by": reason,
        "previous": {k: prev.get(k) for k in ("release_id", "profile_id", "installed_at", "release_dir")}
        if prev
        else None,
    }
    c.write_active(install_root, active)
    return active


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="반입 패키지 오프라인 설치 (표준 라이브러리만). 인터넷/pip index 를 사용하지 않습니다."
    )
    ap.add_argument("--package", required=True, help="반입 ZIP 또는 추출 폴더")
    ap.add_argument(
        "--install-root",
        default=None,
        help="설치 root (기본: DIA_INSTALL_ROOT 또는 %%LOCALAPPDATA%%\\DIA). 한글/공백 가능",
    )
    ap.add_argument(
        "--data-root",
        default=None,
        help="업무 데이터 root (기본: DIA_DATA_ROOT 또는 <install_root>/workspace)",
    )
    ap.add_argument(
        "--python",
        default=None,
        help="venv 를 만들 회사 승인 Python (기본: PYTHON 환경변수 또는 이 스크립트의 base Python)",
    )
    ap.add_argument(
        "--skip-selftest", action="store_true", help="self-test 생략 (install_log 에 skipped 기록)"
    )
    ap.add_argument(
        "--target-profile", default=None, help="검증에 쓸 target profile JSON (기본: 선택한 Python 으로 감지)"
    )
    ap.add_argument(
        "--no-activate", action="store_true", help="설치만 하고 active.json 을 바꾸지 않음 (upgrade 가 사용)"
    )
    ap.add_argument("--timeout", type=int, default=3600, help="각 단계 제한 시간(초)")
    ap.add_argument("--json", action="store_true", help="결과 JSON 출력")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    install_root = Path(args.install_root).expanduser() if args.install_root else c.default_install_root()
    data_root = Path(args.data_root).expanduser() if args.data_root else c.default_data_root(install_root)
    python = args.python or c.default_base_python()
    sink = None if args.json else print
    try:
        target = None
        if args.target_profile:
            target = c.read_json(args.target_profile)
        result = run_install(
            package=args.package,
            install_root=install_root,
            data_root=data_root,
            python=python,
            skip_selftest=args.skip_selftest,
            activate=not args.no_activate,
            target_profile=target,
            timeout=args.timeout,
            sink=sink,
        )
    except c.ScriptError as exc:
        if args.json:
            print(
                json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2, default=str)
            )
        else:
            print(exc.format_ko(), file=sys.stderr)
            print("기존 active.json 은 변경되지 않았습니다.", file=sys.stderr)
        return exc.exit_code
    if args.json:
        slim = {k: v for k, v in result.items() if k != "manifest"}
        print(json.dumps(slim, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "설치 완료: release {} / {} → {}".format(
                result["release_id"], result["profile_id"], result["release_dir"]
            )
        )
        print("  venv python: {}".format(result["venv_python"]))
        print("  active.json: {}".format("전환됨" if result["activated"] else "보류"))
        print("  설치 로그: {}".format(result["log_path"]))
        print("다음: 04_SelfTest.cmd (또는 scripts/selftest.py) 로 확인 후 03_Start.cmd 로 시작하세요.")
    return c.EXIT_OK


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
