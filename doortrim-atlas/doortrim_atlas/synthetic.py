"""합성 도어트림 생성기(테스트/데모용).

실제 사내 데이터 없이도 파이프라인 전체(아틀라스 구축→학습→평가→신규 스킨 분할→예측)를
끝까지 돌려볼 수 있도록, 부품 배치가 비슷하지만 치수·위치·곡률·좌표계가 조금씩 다른
가짜 차종들을 만들어 STL(부품별 Body) + manifest.csv + SQLite DB 로 저장한다.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from typing import Dict, List, Tuple

import numpy as np
import trimesh
from scipy.spatial.transform import Rotation

# 코드: (종류, u0, u1, v0, v1, 돌출mm, 가장자리폭mm, 중량계수, 재질)
PART_SPECS: Dict[str, dict] = {
    "MAIN_TRIM":      dict(kind="rect", u=(0.00, 1.00), v=(0.00, 0.62), prot=0.0,  edge=10, factor=1.10, material="PP-TD20"),
    "UPR_TRIM":       dict(kind="rect", u=(0.00, 1.00), v=(0.62, 1.00), prot=0.0,  edge=10, factor=1.00, material="PP-TD20"),
    "ARMREST":        dict(kind="rect", u=(0.33, 0.78), v=(0.38, 0.50), prot=75.0, edge=15, factor=1.80, material="PP-TD20"),
    "PULL_HANDLE":    dict(kind="rect", u=(0.27, 0.37), v=(0.49, 0.57), prot=45.0, edge=8,  factor=1.50, material="PP-GF30"),
    "SWITCH_BEZEL":   dict(kind="rect", u=(0.47, 0.68), v=(0.43, 0.48), prot=78.0, edge=3,  factor=0.90, material="ABS"),
    "MAP_POCKET":     dict(kind="rect", u=(0.06, 0.46), v=(0.04, 0.30), prot=50.0, edge=12, factor=1.30, material="PP"),
    "SPEAKER_GRILLE": dict(kind="disk", c=(0.14, 0.40), r=0.075,          prot=3.0,  edge=3,  factor=0.80, material="ABS"),
    "SAIL_GARNISH":   dict(kind="tri",  u=(0.00, 0.14), v=(0.84, 1.00), prot=2.0,  edge=4,  factor=0.90, material="ABS"),
    "HANDLE_BEZEL":   dict(kind="rect", u=(0.80, 0.92), v=(0.70, 0.78), prot=4.0,  edge=3,  factor=1.00, material="ABS"),
}


def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _sdf(spec: dict, X: np.ndarray, Y: np.ndarray, L: float, H: float) -> np.ndarray:
    """footprint 부호거리(mm). 안쪽이 음수."""
    if spec["kind"] == "rect":
        x0, x1 = spec["u"][0] * L, spec["u"][1] * L
        y0, y1 = spec["v"][0] * H, spec["v"][1] * H
        r = min(spec.get("radius", 12.0), 0.5 * min(x1 - x0, y1 - y0))
        cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        hx, hy = 0.5 * (x1 - x0) - r, 0.5 * (y1 - y0) - r
        dx = np.abs(X - cx) - hx
        dy = np.abs(Y - cy) - hy
        out = np.sqrt(np.maximum(dx, 0) ** 2 + np.maximum(dy, 0) ** 2) + np.minimum(np.maximum(dx, dy), 0) - r
        return out
    if spec["kind"] == "disk":
        cx, cy, r = spec["c"][0] * L, spec["c"][1] * H, spec["r"] * L
        return np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) - r
    if spec["kind"] == "tri":  # 앞쪽 위 삼각형: 사각형을 대각선으로 자른 것
        x0, x1 = spec["u"][0] * L, spec["u"][1] * L
        y0, y1 = spec["v"][0] * H, spec["v"][1] * H
        rect = np.maximum(np.maximum(x0 - X, X - x1), np.maximum(y0 - Y, Y - y1))
        # 대각선: (x0,y0)-(x1,y1) 위쪽만 남김  → 직선 아래쪽은 양수
        nx, ny = (y1 - y0), -(x1 - x0)
        nrm = np.hypot(nx, ny)
        diag = ((X - x0) * nx + (Y - y0) * ny) / nrm
        return np.maximum(rect, diag)
    raise ValueError(spec["kind"])


def heightfield_part(spec: dict, L: float, H: float, base_fn, step_fn=None, grid: float = 6.0,
                     back: float = 3.0) -> trimesh.Trimesh:
    """footprint 안쪽 격자에서 윗면(base+돌출)·뒷면(base-back)·테두리 벽을 만든다."""
    xs = np.arange(-grid, L + 2 * grid, grid)
    ys = np.arange(-grid, H + 2 * grid, grid)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    sdf = _sdf(spec, X, Y, L, H)
    inside = sdf <= 0.0
    if inside.sum() < 4:
        raise ValueError("footprint 가 너무 작습니다")
    h = spec["prot"] * _smoothstep(-sdf / max(spec["edge"], 1e-6))
    Z = base_fn(X, Y) + h
    if step_fn is not None:
        Z = Z + step_fn(X, Y)
    nid = -np.ones(X.shape, dtype=np.int64)
    nid[inside] = np.arange(inside.sum())
    top = np.column_stack([X[inside], Y[inside], Z[inside]])
    bottom = np.column_stack([X[inside], Y[inside], base_fn(X, Y)[inside] - back])
    nv = len(top)
    V = np.vstack([top, bottom])
    # 격자 사각형 → 삼각형 (4 꼭짓점 모두 안쪽)
    a = nid[:-1, :-1]; b = nid[1:, :-1]; c = nid[1:, 1:]; d = nid[:-1, 1:]
    ok = (a >= 0) & (b >= 0) & (c >= 0) & (d >= 0)
    a, b, c, d = a[ok], b[ok], c[ok], d[ok]
    F_top = np.vstack([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    F_bot = np.vstack([np.column_stack([a + nv, c + nv, b + nv]), np.column_stack([a + nv, d + nv, c + nv])])
    # 경계 엣지 → 벽
    E = np.vstack([F_top[:, [0, 1]], F_top[:, [1, 2]], F_top[:, [2, 0]]])
    Es = np.sort(E, axis=1)
    uniq, cnt = np.unique(Es, axis=0, return_counts=True)
    border = uniq[cnt == 1]
    F_wall = np.vstack([np.column_stack([border[:, 0], border[:, 1], border[:, 1] + nv]),
                        np.column_stack([border[:, 0], border[:, 1] + nv, border[:, 0] + nv])]) if len(border) else np.zeros((0, 3), int)
    F = np.vstack([F_top, F_bot, F_wall])
    return trimesh.Trimesh(V, F, process=False)


def make_vehicle(vid: str, rng: np.random.Generator, car_coords: bool = True) -> Tuple[Dict[str, trimesh.Trimesh], Dict[str, dict], dict]:
    """부품별 메쉬(차량 좌표 또는 임의 좌표), 부품 메타, 차종 메타를 돌려준다."""
    L = float(rng.uniform(950, 1250))
    H = float(rng.uniform(520, 720))
    side = "LH" if rng.random() < 0.5 else "RH"
    a_curv = float(rng.uniform(5, 20))
    b_curv = float(rng.uniform(-30, 30))
    def base_fn(X, Y):
        return a_curv * np.sin(np.pi * X / L) + b_curv * ((Y / H - 0.45) ** 2)
    has_step = rng.random() < 0.3
    split_v = 0.62 + rng.normal(0, 0.03)
    meshes: Dict[str, trimesh.Trimesh] = {}
    metas: Dict[str, dict] = {}
    for code, spec0 in PART_SPECS.items():
        spec = dict(spec0)
        if spec["kind"] in ("rect", "tri"):
            u0, u1 = spec["u"]; v0, v1 = spec["v"]
            if code in ("MAIN_TRIM", "UPR_TRIM"):
                if code == "MAIN_TRIM":
                    v1 = split_v
                else:
                    v0 = split_v
            else:
                cu, cv = 0.5 * (u0 + u1) + rng.normal(0, 0.03), 0.5 * (v0 + v1) + rng.normal(0, 0.03)
                su, sv = (u1 - u0) * rng.uniform(0.85, 1.15), (v1 - v0) * rng.uniform(0.85, 1.15)
                u0, u1 = np.clip(cu - su / 2, 0, 1), np.clip(cu + su / 2, 0, 1)
                v0, v1 = np.clip(cv - sv / 2, 0, 1), np.clip(cv + sv / 2, 0, 1)
            spec["u"], spec["v"] = (float(u0), float(u1)), (float(v0), float(v1))
        else:
            spec["c"] = (float(spec["c"][0] + rng.normal(0, 0.03)), float(spec["c"][1] + rng.normal(0, 0.03)))
            spec["r"] = float(spec["r"] * rng.uniform(0.85, 1.15))
        spec["prot"] = float(spec["prot"] * rng.uniform(0.8, 1.2))
        step_fn = None
        if has_step and code == "UPR_TRIM":
            step_fn = lambda X, Y: np.full_like(X, 1.5)  # noqa: E731
        m = heightfield_part(spec, L, H, base_fn, step_fn)
        top_area = 0.5 * m.area  # 윗면 면적 근사(윗면+뒷면이 대부분)
        weight = top_area * 2.5 * 0.95e-3 * spec["factor"] * float(np.exp(rng.normal(0, 0.1)))
        meshes[code] = m
        metas[code] = {"weight_g": round(weight, 1), "material": spec["material"], "top_area_mm2": top_area}
    # 로컬 → 차량 좌표: x=앞→뒤(+X), v=위(+Z), 돌출=실내쪽
    for code, m in meshes.items():
        V = m.vertices.copy()
        x, y, z = V[:, 0], V[:, 1], V[:, 2]
        if side == "RH":
            car = np.column_stack([x + 800.0, 700.0 - z, y + 350.0])
        else:
            car = np.column_stack([x + 800.0, -700.0 + z, y + 350.0])
        m.vertices = car
        if side == "LH":  # 반사 변환이므로 면 방향을 뒤집어 바깥 법선을 유지(CATIA 출력과 동일하게)
            m.invert()
    T = np.eye(4)
    if not car_coords:
        Rm = Rotation.random(random_state=int(rng.integers(0, 2**31 - 1))).as_matrix()
        t = rng.uniform(-2000, 2000, size=3)
        T[:3, :3] = Rm
        T[:3, 3] = t
        for m in meshes.values():
            m.apply_transform(T)
    vmeta = {"vehicle": vid, "side": side, "L": L, "H": H, "car_coords": car_coords, "has_step": has_step}
    return meshes, metas, vmeta


def generate_dataset(out_dir: str, n_vehicles: int = 20, seed: int = 0, frac_car_coords: float = 0.6) -> str:
    """out_dir/<vid>/<code>.stl, out_dir/manifest.csv, out_dir/synthetic.db 를 만들고 manifest 경로를 돌려준다."""
    rng = np.random.default_rng(seed)
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    db_path = os.path.join(out_dir, "synthetic.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE vehicles(id INTEGER PRIMARY KEY, vehicle_name TEXT);
        CREATE TABLE parts(id INTEGER PRIMARY KEY, vehicle_id INTEGER, side TEXT, part_no TEXT);
        CREATE TABLE part_bodies(id INTEGER PRIMARY KEY, part_id INTEGER, body_name TEXT, stl_path TEXT, standard_part_code TEXT);
        CREATE TABLE part_measurements(body_id INTEGER, volume_mm3 REAL, area_mm2 REAL, bbox_x REAL, bbox_y REAL, bbox_z REAL, weight_g REAL, material TEXT);
        CREATE TABLE standard_parts(code TEXT PRIMARY KEY, name_ko TEXT);
    """)
    names_ko = {"MAIN_TRIM": "메인 트림", "UPR_TRIM": "어퍼 트림", "ARMREST": "암레스트", "PULL_HANDLE": "풀 핸들",
                "SWITCH_BEZEL": "스위치 베젤", "MAP_POCKET": "맵 포켓", "SPEAKER_GRILLE": "스피커 그릴",
                "SAIL_GARNISH": "세일 가니시", "HANDLE_BEZEL": "핸들 베젤"}
    cur.executemany("INSERT INTO standard_parts VALUES(?,?)", list(names_ko.items()))
    body_id = 0
    for i in range(n_vehicles):
        vid = f"SYN{i:02d}"
        car = True if i == 0 else (rng.random() < frac_car_coords)
        meshes, metas, vmeta = make_vehicle(vid, rng, car_coords=car)
        vdir = os.path.join(out_dir, vid)
        os.makedirs(vdir, exist_ok=True)
        cur.execute("INSERT INTO vehicles(id, vehicle_name) VALUES(?,?)", (i + 1, vid))
        cur.execute("INSERT INTO parts(id, vehicle_id, side, part_no) VALUES(?,?,?,?)", (i + 1, i + 1, vmeta["side"], f"8230{i:02d}"))
        for code, m in meshes.items():
            p = os.path.join(vdir, f"{code}.stl")
            m.export(p)
            body_id += 1
            bname = f"{code}_{vmeta['side']}_{i:02d}"
            rows.append({"atlas_id": vid, "stl_path": p, "standard_part_code": code, "weight_g": metas[code]["weight_g"],
                         "material": metas[code]["material"], "vehicle": vid, "side": vmeta["side"], "body_name": bname})
            ext = m.bounding_box.extents
            cur.execute("INSERT INTO part_bodies VALUES(?,?,?,?,?)", (body_id, i + 1, bname, p, code))
            cur.execute("INSERT INTO part_measurements VALUES(?,?,?,?,?,?,?,?)",
                        (body_id, float(m.volume) if m.is_volume else None, float(m.area), float(ext[0]), float(ext[1]), float(ext[2]),
                         metas[code]["weight_g"], metas[code]["material"]))
        with open(os.path.join(vdir, "vehicle.json"), "w", encoding="utf-8") as f:
            import json
            json.dump(vmeta, f, ensure_ascii=False, indent=1)
    con.commit()
    con.close()
    manifest = os.path.join(out_dir, "manifest.csv")
    with open(manifest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return manifest


def make_skin_from_vehicle(vehicle_dir: str, out_path: str, pieces_dir: str | None = None) -> Tuple[str, Dict[str, float]]:
    """차종 폴더의 모든 부품 STL을 하나로 합쳐 '통짜 스킨' STL을 만든다(라벨 없음).
    pieces_dir 를 주면 라벨을 지운 조각 파일(piece_00.stl ...)도 함께 만든다. 정답 면적도 돌려준다."""
    import glob
    files = sorted(glob.glob(os.path.join(vehicle_dir, "*.stl")))
    meshes = [trimesh.load(f, force="mesh", process=False) for f in files]
    merged = trimesh.util.concatenate(meshes)
    merged.export(out_path)
    if pieces_dir:
        os.makedirs(pieces_dir, exist_ok=True)
        for k, m in enumerate(meshes):
            m.export(os.path.join(pieces_dir, f"piece_{k:02d}.stl"))
    truth = {os.path.splitext(os.path.basename(f))[0]: float(m.area) for f, m in zip(files, meshes)}
    return out_path, truth
