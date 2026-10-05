"""사내 SQLite DB(catia_parts.db) → manifest.csv 변환 도구.

DB 스키마는 회사마다 다르므로, 스키마를 읽어 열 이름을 추측하고 SQL을 출력한다.
추측이 틀리면 `--sql` 로 직접 쓴 SQL을 넘기면 된다(열 별칭만 맞추면 됨).
"""
from __future__ import annotations

import csv
import os
import sqlite3
from typing import Dict, List, Optional

REQUIRED_ALIASES = ["atlas_id", "stl_path", "standard_part_code"]
OPTIONAL_ALIASES = ["weight_g", "material", "vehicle", "side", "body_name", "part_no"]


def inspect_db(db_path: str) -> Dict[str, dict]:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    schema = {}
    for t in tables:
        cols = [(r[1], r[2]) for r in cur.execute(f'PRAGMA table_info("{t}")')]
        n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        schema[t] = {"columns": cols, "rows": n}
    con.close()
    return schema


def _find_col(cols: List[str], *keywords: str) -> Optional[str]:
    low = {c.lower(): c for c in cols}
    for kw in keywords:
        for lc, c in low.items():
            if kw in lc:
                return c
    return None


def guess_sql(schema: Dict[str, dict]) -> str:
    """스키마에서 bodies/measurements 테이블과 열을 추측해 SQL 문자열을 만든다."""
    body_t = None
    for t, info in schema.items():
        names = [c for c, _ in info["columns"]]
        if any(c.lower() == "standard_part_code" for c in names):
            body_t = t
            break
    if body_t is None:
        raise ValueError("standard_part_code 열을 가진 테이블을 찾지 못했습니다. --sql 로 직접 지정하세요.")
    bcols = [c for c, _ in schema[body_t]["columns"]]
    path_c = _find_col(bcols, "stl_path", "stl", "file_path", "path", "file")
    name_c = _find_col(bcols, "body_name", "name")
    group_c = _find_col(bcols, "part_id", "parent_id", "assy_id", "vehicle_id", "vehicle")
    id_c = _find_col(bcols, "body_id", "id") or "rowid"
    if path_c is None:
        raise ValueError(f"{body_t} 테이블에서 STL 경로 열을 찾지 못했습니다. 열: {bcols}")

    # 측정 테이블(중량)
    meas_t, meas_key, weight_c, mat_c = None, None, None, None
    for t, info in schema.items():
        names = [c for c, _ in info["columns"]]
        wc = _find_col(names, "weight", "mass")
        if wc and t != body_t:
            key = _find_col(names, "body_id", "part_body_id", "body")
            if key:
                meas_t, meas_key, weight_c = t, key, wc
                mat_c = _find_col(names, "material", "mat")
                break
    if weight_c is None:
        weight_c = _find_col(bcols, "weight", "mass")
        if weight_c:
            weight_expr = f'b."{weight_c}"'
        else:
            weight_expr = "NULL"
        join = ""
        mat_expr = f'b."{_find_col(bcols, "material")}"' if _find_col(bcols, "material") else "NULL"
    else:
        weight_expr = f'm."{weight_c}"'
        mat_expr = f'm."{mat_c}"' if mat_c else "NULL"
        join = f'LEFT JOIN "{meas_t}" m ON m."{meas_key}" = b."{id_c}"'

    # 상위 테이블(차종/좌우)
    parts_t = None
    veh_expr, side_expr, partno_expr = "NULL", "NULL", "NULL"
    atlas_expr = f'b."{group_c}"' if group_c else f'b."{id_c}"'
    if group_c and "parts" in schema:
        pcols = [c for c, _ in schema["parts"]["columns"]]
        pid = _find_col(pcols, "id") or "id"
        parts_t = "parts"
        join += f' LEFT JOIN "parts" p ON p."{pid}" = b."{group_c}"'
        side_c = _find_col(pcols, "side", "lh_rh", "hand")
        pn_c = _find_col(pcols, "part_no", "partno", "part_number")
        if side_c:
            side_expr = f'p."{side_c}"'
        if pn_c:
            partno_expr = f'p."{pn_c}"'
        vehid = _find_col(pcols, "vehicle_id", "vehicle")
        if vehid and "vehicles" in schema:
            vcols = [c for c, _ in schema["vehicles"]["columns"]]
            vname = _find_col(vcols, "name", "code", "vehicle")
            vid = _find_col(vcols, "id") or "id"
            if vname:
                join += f' LEFT JOIN "vehicles" v ON v."{vid}" = p."{vehid}"'
                veh_expr = f'v."{vname}"'
                atlas_expr = f'(COALESCE(v."{vname}", \'\') || \'_\' || COALESCE({side_expr}, \'\') || \'_\' || b."{group_c}")'
    sql = f"""SELECT
    {atlas_expr} AS atlas_id,
    b."{path_c}" AS stl_path,
    b."standard_part_code" AS standard_part_code,
    {weight_expr} AS weight_g,
    {mat_expr} AS material,
    {veh_expr} AS vehicle,
    {side_expr} AS side,
    {('b."' + name_c + '"') if name_c else 'NULL'} AS body_name,
    {partno_expr} AS part_no
FROM "{body_t}" b
{join}
WHERE b."standard_part_code" IS NOT NULL AND TRIM(b."standard_part_code") <> ''"""
    return sql


def make_manifest(db_path: str, out_csv: str, sql: Optional[str] = None, stl_root: Optional[str] = None,
                  log=print) -> str:
    schema = inspect_db(db_path)
    if sql is None:
        sql = guess_sql(schema)
        log("자동 추측한 SQL:\n" + sql + "\n")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql)]
    con.close()
    if not rows:
        raise ValueError("SQL 결과가 비어 있습니다.")
    missing = [a for a in REQUIRED_ALIASES if a not in rows[0]]
    if missing:
        raise ValueError(f"SQL 결과에 필수 열 별칭이 없습니다: {missing}")
    n_missing_file = 0
    for r in rows:
        p = str(r["stl_path"] or "")
        if stl_root and not os.path.isabs(p):
            p = os.path.join(stl_root, p)
        r["stl_path"] = p
        if not os.path.exists(p):
            n_missing_file += 1
    fields = REQUIRED_ALIASES + [a for a in OPTIONAL_ALIASES if a in rows[0]]
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    n_atlas = len({r["atlas_id"] for r in rows})
    log(f"manifest 저장: {out_csv}  (바디 {len(rows)}개, 아틀라스 {n_atlas}개, 파일 없음 {n_missing_file}개)")
    if n_missing_file:
        log("  → 파일이 없는 행이 있습니다. --stl-root 로 STL 폴더를 지정했는지 확인하세요.")
    return out_csv


def format_schema(schema: Dict[str, dict]) -> str:
    lines = []
    for t, info in schema.items():
        lines.append(f"[{t}]  ({info['rows']:,} rows)")
        for c, ty in info["columns"]:
            lines.append(f"    - {c}  {ty}")
    return "\n".join(lines)
