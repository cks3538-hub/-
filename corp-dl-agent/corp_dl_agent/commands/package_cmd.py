"""package 명령: 반입 패키지 빌드/검증/lock/inventory/타깃 프로파일.

- package build   --profile <id|target_profile.json> --wheelhouse wheelhouse/<profile> --app-wheel dist/<wheel>
                  [--out dist] [--evidence-dir test-evidence] [--package-kind SOURCE_ONLY|CPU_OFFLINE|GPU_OFFLINE]
                  [--verification KEY=STATUS:사유]... [--project-root DIR] [--extra-doc FILE]... [--json]
- package verify  --package <zip|dir> [--target-profile <json>] [--install-root DIR] [--json]
- package lock    --profile ... --wheelhouse ... [--app-wheel ...] [--out locks] [--json]
- package inventory --profile ... [--wheelhouse ...] [--app-wheel ...] --output dependency-inventory.json [--json]
- package host-profile [--output target_profile.json] [--min-output target_profile.min.json] [--json]

exit code: 0 / 2 사용법 / 5 검증 실패(E_PACKAGE_INVALID) / 9 알 수 없는 프로파일.
검증 로직은 corp_dl_agent.packaging.verifier_core (= scripts/_common.py) 와 동일하다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corp_dl_agent.common import Status, StatusRecord, atomic_write_json, now_iso
from corp_dl_agent.errors import EXIT_VALIDATION, AgentError

PACKAGE_KINDS: tuple[str, ...] = ("SOURCE_ONLY", "CPU_OFFLINE", "GPU_OFFLINE")


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("package", help="반입 패키지 빌드/검증 (ZIP, lock, inventory, manifest)")
    s = p.add_subparsers(dest="package_command", metavar="<하위명령>")

    b = s.add_parser(
        "build", help="dist/DIA_<version>_<profile>.zip 과 <zip>.sha256 생성 (allowlist 포장, 금지 패턴 검사)"
    )
    b.add_argument(
        "--profile",
        required=True,
        help="타깃 프로파일 ID (win-x64-cp312-cpu | linux-x64-cp312-cpu) 또는 target_profile.json 경로",
    )
    b.add_argument("--wheelhouse", default=None, help="dependency wheel 폴더 (없으면 SOURCE_ONLY)")
    b.add_argument("--app-wheel", required=True, help="미리 빌드한 앱 wheel 경로")
    b.add_argument(
        "--evidence-dir",
        "--evidence",
        dest="evidence_dir",
        default=None,
        help="test-evidence 폴더 (*.json 만 포장, raw/ 제외). HOST_CORE_TESTED 판정 근거",
    )
    b.add_argument("--out", default="dist", help="출력 폴더 (기본 dist)")
    b.add_argument(
        "--package-kind",
        choices=PACKAGE_KINDS,
        default=None,
        help="기대하는 package kind. 실제 구성(wheelhouse 유무/프로파일 device)과 다르면 실패",
    )
    b.add_argument(
        "--verification",
        action="append",
        default=[],
        metavar="KEY=STATUS:사유",
        help="manifest.verification 상태 지정 (예: TARGET_OFFLINE_TESTED=NOT_RUN:Windows 환경 없음). "
        "CORP_INSTALLED/CORP_INTEGRATED/BUSINESS_VALIDATED 는 PASS 불가",
    )
    b.add_argument("--project-root", default=None, help="프로젝트 루트 (기본: 현재 폴더)")
    b.add_argument("--extra-doc", action="append", default=[], help="docs/ 에 추가할 문서 파일")
    b.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")
    b.set_defaults(handler=run_build)

    v = s.add_parser(
        "verify", help="반입 ZIP/폴더 검증 (manifest/hash/lock/wheelhouse/대상 OS·Python·ABI/디스크)"
    )
    v.add_argument("--package", required=True, help="ZIP 파일 또는 추출 폴더")
    v.add_argument("--target-profile", default=None, help="검사에 쓸 target profile JSON (기본: 현 호스트)")
    v.add_argument("--install-root", default=None, help="디스크 여유 검사 위치")
    v.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")
    v.set_defaults(handler=run_verify)

    lk = s.add_parser("lock", help="locks/<profile>.txt 생성 (wheelhouse + 앱 wheel, tag 호환 검사)")
    lk.add_argument("--profile", required=True, help="타깃 프로파일 ID 또는 target_profile.json 경로")
    lk.add_argument("--wheelhouse", required=True, help="dependency wheel 폴더")
    lk.add_argument("--app-wheel", default=None, help="앱 wheel (lock 에 포함)")
    lk.add_argument("--out", default=None, help="출력 폴더 (기본 <wheelhouse>/../../locks)")
    lk.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")
    lk.set_defaults(handler=run_lock)

    inv = s.add_parser(
        "inventory", help="dependency-inventory.json 생성 (wheel METADATA 라이선스/홈페이지/hash)"
    )
    inv.add_argument("--profile", required=True, help="타깃 프로파일 ID 또는 target_profile.json 경로")
    inv.add_argument("--wheelhouse", default=None, help="dependency wheel 폴더")
    inv.add_argument("--app-wheel", default=None, help="앱 wheel")
    inv.add_argument("--output", required=True, help="출력 JSON 경로")
    inv.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")
    inv.set_defaults(handler=run_inventory)

    hp = s.add_parser("host-profile", help="현 호스트의 target_profile.json / target_profile.min.json 생성")
    hp.add_argument("--output", default=None, help="상세 프로파일 경로 (로컬 보관)")
    hp.add_argument("--min-output", default=None, help="최소 요약 경로 (허용 필드만)")
    hp.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")
    hp.set_defaults(handler=run_host_profile)

    p.set_defaults(handler=lambda args: (p.print_help(), 2)[1])


def _resolve_profile(spec: str) -> Any:
    from corp_dl_agent.packaging.target import get_profile, load_target_profile

    if spec.endswith(".json") or Path(spec).is_file():
        return load_target_profile(spec)
    return get_profile(spec)


def parse_verification_args(items: list[str]) -> dict[str, StatusRecord]:
    """'KEY=STATUS:사유' 목록 → StatusRecord. 사내 3 상태의 PASS 는 거부 (개인 개발 단계)."""
    from corp_dl_agent.packaging.manifest import CORP_ONLY_KEYS, VERIFICATION_KEYS

    allowed_keys = (*VERIFICATION_KEYS, "TARGET_CONFIRMED")
    out: dict[str, StatusRecord] = {}
    for item in items:
        if "=" not in item:
            raise AgentError("E_USAGE", f"--verification 형식은 KEY=STATUS[:사유] 입니다: {item}")
        key, rest = item.split("=", 1)
        status_s, _, reason = rest.partition(":")
        key = key.strip()
        status_s = status_s.strip().upper()
        if key not in allowed_keys:
            raise AgentError(
                "E_USAGE", f"알 수 없는 verification 키: {key}", details={"allowed": list(allowed_keys)}
            )
        try:
            status = Status(status_s)
        except ValueError as exc:
            raise AgentError(
                "E_USAGE",
                f"알 수 없는 상태 값: {status_s}",
                details={"allowed": [st.value for st in Status]},
            ) from exc
        if key in CORP_ONLY_KEYS and status is Status.PASS:
            raise AgentError(
                "E_PACKAGE_INVALID",
                f"{key} 는 개인 개발 단계에서 PASS 로 표시할 수 없습니다 (사내에서 근거와 함께만 가능)",
            )
        out[key] = StatusRecord(
            status=status, reason=reason.strip() or "사유 미기재", evidence=[], checked_at=now_iso()
        )
    return out


def run_build(args: argparse.Namespace) -> int:
    from corp_dl_agent.packaging.release import build_release_detailed

    profile = _resolve_profile(args.profile)
    root = Path(args.project_root) if args.project_root else Path.cwd()
    overrides = parse_verification_args(list(args.verification))
    result = build_release_detailed(
        root,
        profile=profile,
        wheelhouse_dir=args.wheelhouse,
        app_wheel=args.app_wheel,
        out_dir=args.out,
        evidence_dir=args.evidence_dir,
        extra_docs=list(args.extra_doc),
        verification_overrides=overrides or None,
        package_kind=args.package_kind,
    )
    m = result.manifest
    info = {
        "ok": True,
        "zip": str(result.zip_path),
        "zip_sha256": result.zip_sha256,
        "zip_size": result.zip_size,
        "sha256_file": str(result.sha256_path),
        "release_id": m.release_id,
        "profile_id": m.profile_id,
        "package_kind": m.package_kind,
        "file_count": m.file_count,
        "total_bytes": m.total_bytes,
        "wheel_count": m.wheel_count,
        "verification": {k: v.status.value for k, v in m.verification.items()},
        "warnings": result.warnings,
        "skipped_count": len(result.skipped),
    }
    if args.json_output:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"반입 ZIP 생성: {result.zip_path} ({result.zip_size:,} bytes)")
        print(f"  sha256: {result.zip_sha256}  ({result.sha256_path.name})")
        print(
            f"  release {m.release_id} / {m.profile_id} / {m.package_kind} / 파일 {m.file_count}개 / wheel {m.wheel_count}개"
        )
        for k, v in m.verification.items():
            print(f"  {k}: {v.status.value} — {v.reason}")
        for w in result.warnings:
            print(f"  경고: {w}")
        print(
            "다음: 최종 ZIP 을 새 환경에 설치 시험한 뒤 scripts/acceptance.py 로 외부 acceptance.json 을 만드세요 (ZIP 재포장 금지)."
        )
    return 0


def run_verify(args: argparse.Namespace) -> int:
    from corp_dl_agent.packaging.target import load_target_profile
    from corp_dl_agent.packaging.verify import format_report_ko, verify_package

    target = load_target_profile(args.target_profile) if args.target_profile else None
    report = verify_package(args.package, target=target, install_root=args.install_root)
    if args.json_output:
        slim = {k: v for k, v in report.items() if k not in ("hashes", "wheels", "lock_entries")}
        slim["wheel_count"] = len(report.get("wheels", []))
        print(json.dumps(slim, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_report_ko(report))
    return 0 if report["ok"] else EXIT_VALIDATION


def run_lock(args: argparse.Namespace) -> int:
    from corp_dl_agent.packaging.lockfile import build_lock

    profile = _resolve_profile(args.profile)
    path = build_lock(profile, args.wheelhouse, args.app_wheel, out_dir=args.out)
    if args.json_output:
        print(json.dumps({"ok": True, "lock": str(path)}, ensure_ascii=False))
    else:
        print(f"lock 생성: {path}")
    return 0


def run_inventory(args: argparse.Namespace) -> int:
    from corp_dl_agent.packaging.inventory import build_inventory

    profile = _resolve_profile(args.profile)
    if not args.wheelhouse and not args.app_wheel:
        raise AgentError("E_USAGE", "--wheelhouse 또는 --app-wheel 중 하나는 필요합니다")
    doc = build_inventory(profile, args.wheelhouse, args.app_wheel, out_path=args.output)
    if args.json_output:
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": args.output,
                    "count": doc["count"],
                    "unknown_license_count": doc["unknown_license_count"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"inventory 생성: {args.output} ({doc['count']}개, 라이선스 UNKNOWN {doc['unknown_license_count']}개, approval_state=UNREVIEWED)"
        )
    return 0


def run_host_profile(args: argparse.Namespace) -> int:
    from corp_dl_agent.packaging.target import detect_host, write_min_profile, write_target_profile

    prof = detect_host()
    out: dict[str, Any] = {"profile_id": prof.profile_id, "min": prof.min_dict(preflight_status="NOT_RUN")}
    if args.output:
        write_target_profile(prof, args.output)
        out["output"] = args.output
    if args.min_output:
        write_min_profile(prof, args.min_output)
        out["min_output"] = args.min_output
    if args.json_output:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(
            f"호스트 프로파일: {prof.profile_id} ({prof.os} {prof.architecture}, {prof.python_implementation} {prof.python_version}, {prof.python_abi}, tags={prof.platform_tags})"
        )
        print("  target_confirmed=false — 사내 타깃 확정값이 아닙니다.")
        if args.output:
            print(f"  상세: {args.output}")
        if args.min_output:
            print(f"  최소 요약: {args.min_output}")
    return 0


def _write_json(path: str, obj: Any) -> None:
    atomic_write_json(path, obj)
