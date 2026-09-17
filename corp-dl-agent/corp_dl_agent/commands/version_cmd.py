from __future__ import annotations

import argparse
import json
import platform
import sys

from corp_dl_agent.version import CALCULATION_VERSION, DB_SCHEMA_VERSION, SCHEMA_VERSION, __version__


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("version", help="버전 정보 출력")
    p.add_argument("--json", dest="json_output", action="store_true")
    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    info = {
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "db_schema_version": DB_SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "machine": platform.machine(),
    }
    if args.json_output:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(
            f"corp-dl-agent {__version__} (schema {SCHEMA_VERSION}, calc {CALCULATION_VERSION}, db {DB_SCHEMA_VERSION})"
        )
        print(f"Python {info['python']} {info['implementation']} / {info['platform']} {info['machine']}")
    return 0
