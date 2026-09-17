"""wheel METADATA 에서 라이선스 고지(DEPENDENCY_NOTICES.md)를 생성한다 (표준 라이브러리만).

사용: python tools/gen_dependency_notices.py --wheelhouse wheelhouse/win-x64-cp312-cpu --out docs/DEPENDENCY_NOTICES.md
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import zipfile
from pathlib import Path


def read_metadata(whl: Path) -> tuple[dict[str, str], list[str], dict[str, str]]:
    with zipfile.ZipFile(whl) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        raw = zf.read(meta_name).decode("utf-8", errors="replace")
        msg = email.parser.Parser().parsestr(raw)
        fields = {k: (msg.get(k) or "").strip() for k in ("Name", "Version", "License", "License-Expression", "Home-page", "Author", "Summary")}
        classifiers = [c for c in msg.get_all("Classifier", []) if c.startswith("License ::")]
        urls = {}
        for pu in msg.get_all("Project-URL", []):
            if "," in pu:
                k, v = pu.split(",", 1)
                urls[k.strip()] = v.strip()
        license_files = {}
        distinfo = meta_name.rsplit("/", 1)[0]
        for n in zf.namelist():
            if n.startswith(distinfo + "/licenses/") or (n.startswith(distinfo + "/") and n.rsplit("/", 1)[-1].upper().startswith(("LICENSE", "COPYING", "NOTICE"))):
                try:
                    license_files[n.rsplit("/", 1)[-1]] = zf.read(n).decode("utf-8", errors="replace")
                except Exception:
                    pass
    fields["_classifiers"] = "; ".join(classifiers)
    fields["_urls"] = json.dumps(urls, ensure_ascii=False)
    return fields, classifiers, license_files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheelhouse", required=True, nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--full-text", action="store_true", help="라이선스 전문 포함 (길어짐)")
    args = ap.parse_args()
    seen: dict[str, dict] = {}
    for wh in args.wheelhouse:
        for whl in sorted(Path(wh).glob("*.whl")):
            fields, classifiers, lic_files = read_metadata(whl)
            key = f"{fields['Name']}=={fields['Version']}"
            entry = seen.setdefault(key, {"name": fields["Name"], "version": fields["Version"], "license": fields["License-Expression"] or fields["License"] or ("; ".join(classifiers) if classifiers else "UNKNOWN"), "classifiers": classifiers, "home": fields["Home-page"] or fields["_urls"], "wheels": [], "license_files": lic_files})
            entry["wheels"].append({"file": whl.name, "sha256": hashlib.sha256(whl.read_bytes()).hexdigest(), "profile": Path(wh).name})
    lines = ["# DEPENDENCY_NOTICES", "", "재배포되는 third-party Python 패키지의 라이선스 고지. 출처: PyPI (wheel METADATA 기준). 회사 반입/사용 승인은 별도 절차이며 여기 기재는 승인을 뜻하지 않습니다.", "", "| 패키지 | 버전 | 라이선스 | 홈페이지/URL | wheel (profile) |", "|---|---|---|---|---|"]
    for key in sorted(seen, key=str.lower):
        e = seen[key]
        lic = e["license"].replace("|", "/").replace("\n", " ")[:120]
        wheels = "<br>".join(f"{w['file']} ({w['profile']})" for w in e["wheels"])
        home = e["home"].replace("|", "/")[:100]
        lines.append(f"| {e['name']} | {e['version']} | {lic} | {home} | {wheels} |")
    lines += ["", "## 라이선스 파일 (wheel 에 동봉된 것)", ""]
    for key in sorted(seen, key=str.lower):
        e = seen[key]
        if not e["license_files"]:
            lines.append(f"- {key}: (wheel 에 라이선스 파일 없음 — 프로젝트 홈페이지 참조)")
            continue
        for fname, text in e["license_files"].items():
            if args.full_text:
                lines += [f"### {key} — {fname}", "", "```", text.strip(), "```", ""]
            else:
                first = " ".join(text.strip().splitlines()[:2])[:160]
                lines.append(f"- {key}: {fname} — {first}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    inv = {k: {kk: vv for kk, vv in v.items() if kk != "license_files"} for k, v in seen.items()}
    Path(args.out).with_suffix(".json").write_text(json.dumps(inv, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    unknown = [k for k, v in seen.items() if v["license"] == "UNKNOWN"]
    print(f"{len(seen)} packages -> {args.out}; UNKNOWN license: {unknown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
