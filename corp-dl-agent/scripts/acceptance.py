"""acceptance: 최종 ZIP 의 외부 acceptance 파일 <zip_basename>.acceptance.json 을 만든다. ZIP 을 수정하지 않는다.

사용: python acceptance.py --zip <zip> --evidence-dir <dir> --out <dir>
                           [--status KEY=PASS|FAIL|NOT_RUN|BLOCKED[=사유]]... [--network-note "..."] [--user-note "..."]
                           [--confirm-corp-environment] [--json]
내용: zip sha256/size (옆의 .sha256 파일과 대조), 검증 환경(OS/arch/Python/네트워크 격리 방식/사용자 권한),
      evidence-dir 의 *.json 요약(명령/exit code/status), 상태 6종 + 사유.
사내 3 상태(CORP_INSTALLED/CORP_INTEGRATED/BUSINESS_VALIDATED) 는 --confirm-corp-environment 없이는 PASS 로 기록할 수 없다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

VALID_STATUS = (
    "PASS",
    "FAIL",
    "NOT_RUN",
    "BLOCKED",
    "PARTIAL",
    "MOCK_TESTED",
    "SOURCE_ONLY",
    "TARGET_UNCONFIRMED",
)


def parse_status_args(items: list[str], *, confirm_corp: bool) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {
        k: {"status": "NOT_RUN", "reason": "지정되지 않음"} for k in c.VERIFICATION_KEYS
    }
    for item in items:
        if "=" not in item:
            raise c.ScriptError("E_USAGE", f"--status 형식은 KEY=STATUS[=사유] 입니다: {item}")
        key, rest = item.split("=", 1)
        status, _, reason = rest.partition("=")
        key, status = key.strip(), status.strip().upper()
        if key not in c.VERIFICATION_KEYS:
            raise c.ScriptError(
                "E_USAGE", "알 수 없는 상태 키: {} (허용: {})".format(key, ", ".join(c.VERIFICATION_KEYS))
            )
        if status not in VALID_STATUS:
            raise c.ScriptError(
                "E_USAGE", "알 수 없는 상태 값: {} (허용: {})".format(status, ", ".join(VALID_STATUS))
            )
        if key in c.CORP_ONLY_KEYS and status == "PASS" and not confirm_corp:
            raise c.ScriptError(
                "E_USAGE",
                f"{key} 는 사내 환경에서만 PASS 로 기록할 수 있습니다 (--confirm-corp-environment 필요)",
                hint="개인 개발 단계에서는 NOT_RUN 으로 두세요.",
            )
        out[key] = {"status": status, "reason": reason.strip() or ("사유 미기재" if status != "PASS" else "")}
    return out


def summarize_evidence(evidence_dir: Optional[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if evidence_dir is None or not evidence_dir.is_dir():
        return out
    for p in sorted(evidence_dir.glob("*.json")):
        try:
            data = c.read_json(p)
        except (OSError, ValueError):
            out.append({"file": p.name, "status": "UNREADABLE"})
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "file": p.name,
                "name": data.get("name", p.stem),
                "command": data.get("command"),
                "exit_code": data.get("exit_code"),
                "status": data.get("status", "UNKNOWN"),
                "started_at": data.get("started_at"),
                "elapsed_seconds": data.get("elapsed_seconds"),
                "environment": data.get("environment"),
            }
        )
    return out


def user_privilege() -> str:
    if os.name == "nt":
        try:
            import ctypes

            return "administrator" if ctypes.windll.shell32.IsUserAnAdmin() else "standard user"  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return "unknown"
    try:
        return "root" if os.geteuid() == 0 else "non-root user"
    except AttributeError:
        return "unknown"


def build_acceptance(
    *,
    zip_path: Path,
    evidence_dir: Optional[Path],
    statuses: dict[str, dict[str, str]],
    network_note: str,
    user_note: Optional[str],
    notes: list[str],
) -> dict[str, Any]:
    if not zip_path.is_file():
        raise c.ScriptError("E_PACKAGE_INVALID", f"ZIP 파일이 없습니다: {zip_path}")
    digest = c.sha256_file(zip_path)
    sha_file = zip_path.with_name(zip_path.name + ".sha256")
    sha_match: Optional[bool] = None
    if sha_file.is_file():
        first = sha_file.read_text(encoding="utf-8").strip().split()
        sha_match = bool(first) and first[0].lower() == digest
    manifest_summary: dict[str, Any] = {}
    try:
        with c.PackageSource(zip_path) as src:
            if src.exists(c.MANIFEST_NAME):
                m = json.loads(src.read_bytes(c.MANIFEST_NAME).decode("utf-8"))
                if isinstance(m, dict):
                    manifest_summary = {
                        k: m.get(k)
                        for k in (
                            "release_id",
                            "profile_id",
                            "package_kind",
                            "schema_version",
                            "db_schema_version",
                            "file_count",
                            "created_at",
                        )
                    }
    except c.ScriptError as exc:
        manifest_summary = {"error": exc.message}
    evidence = summarize_evidence(evidence_dir)
    return {
        "format": c.ACCEPTANCE_FORMAT,
        "created_at": c.now_iso(),
        "zip": {
            "name": zip_path.name,
            "sha256": digest,
            "size_bytes": zip_path.stat().st_size,
            "sha256_file": sha_file.name if sha_file.is_file() else None,
            "sha256_file_match": sha_match,
        },
        "manifest": manifest_summary,
        "environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "network_isolation": network_note,
            "user_privilege": user_privilege(),
            "user_note": user_note,
        },
        "evidence_dir_summary": {
            "count": len(evidence),
            "pass": sum(1 for e in evidence if e.get("status") == "PASS"),
            "fail": sum(1 for e in evidence if e.get("status") == "FAIL"),
        },
        "evidence": evidence,
        "verification": statuses,
        "notes": [
            "이 파일은 ZIP 밖에 있으며 ZIP 은 수정되지 않았다 (ZIP 재포장 금지).",
            "zip.sha256 은 무결성 확인이며 배포자 진위 확인이 아니다.",
            *notes,
        ],
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="최종 ZIP 의 외부 acceptance.json 생성 (ZIP 은 수정하지 않음)")
    ap.add_argument("--zip", required=True, help="최종 반입 ZIP")
    ap.add_argument("--evidence-dir", default=None, help="설치/실행 시험 evidence JSON 폴더")
    ap.add_argument("--out", required=True, help="출력 폴더")
    ap.add_argument(
        "--status",
        action="append",
        default=[],
        help="KEY=STATUS[=사유] (예: TARGET_OFFLINE_TESTED=NOT_RUN=Windows 환경 없음)",
    )
    ap.add_argument(
        "--network-note",
        default="UNSPECIFIED",
        help="네트워크 격리 방식 (예: 'unshare -n', 'host network (no isolation)')",
    )
    ap.add_argument("--user-note", default=None, help="검증 사용자 권한 설명 (예: 'non-admin tester')")
    ap.add_argument("--note", action="append", default=[], help="추가 비고")
    ap.add_argument(
        "--confirm-corp-environment",
        action="store_true",
        help="사내 환경에서 실행 중임을 확인 (CORP_* PASS 허용)",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        statuses = parse_status_args(args.status, confirm_corp=args.confirm_corp_environment)
        zip_path = Path(args.zip).expanduser().resolve()
        doc = build_acceptance(
            zip_path=zip_path,
            evidence_dir=Path(args.evidence_dir).expanduser() if args.evidence_dir else None,
            statuses=statuses,
            network_note=args.network_note,
            user_note=args.user_note,
            notes=list(args.note),
        )
    except c.ScriptError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2))
        else:
            print(exc.format_ko(), file=sys.stderr)
        return exc.exit_code
    out_dir = Path(args.out).expanduser()
    out_path = out_dir / "{}.acceptance.json".format(
        zip_path.name[:-4] if zip_path.name.lower().endswith(".zip") else zip_path.name
    )
    c.write_json_atomic(out_path, doc)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(out_path),
                    "zip_sha256": doc["zip"]["sha256"],
                    "verification": {k: v["status"] for k, v in statuses.items()},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"acceptance 생성: {out_path}")
        print(
            "  zip sha256: {} (.sha256 파일 일치: {})".format(
                doc["zip"]["sha256"], doc["zip"]["sha256_file_match"]
            )
        )
        for k, v in statuses.items():
            print("  {}: {} {}".format(k, v["status"], ("— " + v["reason"]) if v["reason"] else ""))
        print(
            "  evidence: {}개 (PASS {}, FAIL {})".format(
                doc["evidence_dir_summary"]["count"],
                doc["evidence_dir_summary"]["pass"],
                doc["evidence_dir_summary"]["fail"],
            )
        )
    return c.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
