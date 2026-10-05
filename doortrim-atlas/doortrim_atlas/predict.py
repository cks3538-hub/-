"""분할 결과 → 사이즈·중량·재질·(단가가 있으면) 원가 예측.

* 보이는 부품: 분할된 면적·bbox 를 과거 같은 코드 부품들과 비교해 k-최근접 + 로그회귀로 중량 추정
  (사내 기존 방식인 '형상유사도 zero-shot 매칭'과 같은 철학, 단 입력이 스킨이므로 A면 면적을 기준으로 함)
* 숨은 부품(브래킷 등 스킨에 안 보이는 것): 가장 유사한 차종들의 BOM을 가중 평균해 보충
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def _feat(area: float, L: float, H: float, D: float) -> np.ndarray:
    return np.log(np.maximum([area, L, H, D], 1e-3))


def predict_visible_parts(stats: Dict[str, dict], library, cfg, log=print) -> Dict[str, dict]:
    """분할된 부품별 중량 추정. 세 가지 추정치(최근접, 면적밀도, 로그회귀)의 중앙값을 최종값으로 쓴다."""
    out: Dict[str, dict] = {}
    for code, s in stats.items():
        b = s["bbox_mm"]
        area = s.get("area_visible_mm2") or s["area_mm2"]
        x = _feat(area, b["L_u"], b["H_v"], b["D_w"])
        cands = []
        for a in library.atlases:
            pt = a.parts.get(code)
            if not pt or pt.get("weight_g") in (None, 0) or pt["area_visible_mm2"] <= 0:
                continue
            bb = pt.get("bbox_visible_mm") or pt["bbox_mm"]
            cands.append((a.atlas_id, pt, _feat(pt["area_visible_mm2"], bb[0], bb[1], bb[2])))
        rec = {"code": code, "area_visible_mm2": area, "area_mm2": s["area_mm2"], "bbox_mm": b, "confidence": s["confidence"],
               "n_reference": len(cands), "weight_knn_g": None, "weight_density_g": None, "weight_reg_g": None,
               "weight_g": None, "material": None, "neighbors": [], "note": ""}
        if not cands:
            rec["note"] = "같은 코드의 중량 참조 데이터 없음"
            out[code] = rec
            continue
        Y = np.array([y for _, _, y in cands])
        W = np.array([pt["weight_g"] for _, pt, _ in cands], float)
        A_vis = np.array([pt["area_visible_mm2"] for _, pt, _ in cands], float)
        scale = np.array([1.0, 0.5, 0.5, 0.3])  # 면적을 가장 중요하게, 치수는 보조
        d = np.sqrt((((Y - x) * scale) ** 2).sum(axis=1))
        order = np.argsort(d)[: cfg.pred_k]
        wk = np.exp(-d[order])
        knn = float((W[order] * wk).sum() / wk.sum())
        density = float(np.median(W / A_vis) * area)  # g/mm² 중앙값 × 보이는 면적
        ests = [knn, density]
        rec["weight_knn_g"], rec["weight_density_g"] = knn, density
        if len(cands) >= 5:
            Amat = np.column_stack([np.ones(len(Y)), Y[:, 0]])
            coef, *_ = np.linalg.lstsq(Amat, np.log(np.maximum(W, 1e-3)), rcond=None)
            reg = float(np.exp(coef[0] + coef[1] * x[0]))
            reg = float(np.clip(reg, 0.5 * knn, 2.0 * knn))  # 외삽 폭주 방지
            rec["weight_reg_g"] = reg
            ests.append(reg)
        else:
            rec["note"] = f"참조 {len(cands)}건(5건 미만) → 회귀 생략"
        rec["weight_g"] = float(np.median(ests))
        rec["neighbors"] = [{"atlas_id": cands[i][0], "weight_g": float(W[i]), "area_visible_mm2": float(A_vis[i]),
                             "distance": float(d[i]), "material": cands[i][1].get("material")} for i in order]
        mats = [cands[i][1].get("material") for i in order if cands[i][1].get("material")]
        rec["material"] = max(set(mats), key=mats.count) if mats else None
        out[code] = rec
    return out


def predict_hidden_parts(stats: Dict[str, dict], library, ranking: List[Tuple[str, float]], cfg) -> Dict[str, dict]:
    """상위 유사 차종들에서 (스킨에서 분할되지 않은) 코드의 중량을 가중 평균한다."""
    top = ranking[: cfg.k_atlas]
    if not top:
        return {}
    d0 = top[0][1]
    acc: Dict[str, dict] = {}
    wsum_total = 0.0
    for aid, d in top:
        w = float(np.exp(-(d - d0) / max(d0, 1e-6)))
        wsum_total += w
        a = library.get(aid)
        for code, p in a.parts.items():
            if code in stats:
                continue
            r = acc.setdefault(code, {"code": code, "w": 0.0, "wsum_weight": 0.0, "n": 0, "vis_frac": [], "materials": [], "from": []})
            r["n"] += 1
            r["wsum_weight"] += w
            r["vis_frac"].append(p.get("visible_fraction", 0.0))
            if p.get("weight_g") is not None:
                r["w"] += w * p["weight_g"]
            if p.get("material"):
                r["materials"].append(p["material"])
            r["from"].append(aid)
    out = {}
    for code, r in acc.items():
        rate = r["wsum_weight"] / max(wsum_total, 1e-9)
        if rate < cfg.hidden_min_rate:
            continue
        out[code] = {"code": code, "appearance_rate": float(rate), "weight_g": float(r["w"] / max(r["wsum_weight"], 1e-9)) if r["w"] > 0 else None,
                     "mean_visible_fraction": float(np.mean(r["vis_frac"])), "material": (max(set(r["materials"]), key=r["materials"].count) if r["materials"] else None),
                     "from_atlases": r["from"]}
    return out


def run_prediction(seg_summary: dict, library, cfg, out_dir: str, cost_table: Optional[Dict[str, float]] = None, log=print) -> dict:
    stats = seg_summary["parts"]
    ranking = [(d["atlas_id"], d["chamfer"]) for d in seg_summary["similar_atlases"]]
    visible = predict_visible_parts(stats, library, cfg, log=log)
    hidden = predict_hidden_parts(stats, library, ranking, cfg)
    cost_table = cost_table or {}

    def _cost(rec):
        m, w = rec.get("material"), rec.get("weight_g")
        if m and w and m in cost_table:
            return float(w / 1000.0 * cost_table[m])
        return None

    for rec in list(visible.values()) + list(hidden.values()):
        rec["material_cost"] = _cost(rec)
    tot_vis = sum(r["weight_g"] or 0 for r in visible.values())
    tot_hid = sum(r["weight_g"] or 0 for r in hidden.values())
    result = {"visible_parts": visible, "hidden_parts": hidden,
              "totals": {"visible_weight_g": tot_vis, "hidden_weight_g": tot_hid, "total_weight_g": tot_vis + tot_hid,
                         "n_visible": len(visible), "n_hidden": len(hidden)},
              "similar_atlases": seg_summary["similar_atlases"]}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "prediction.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    lines = ["code,kind,area_visible_mm2,L_mm,H_mm,D_mm,weight_g,weight_knn_g,weight_density_g,weight_reg_g,material,material_cost,confidence,note"]
    for code, r in sorted(visible.items(), key=lambda kv: -(kv[1]["weight_g"] or 0)):
        b = r["bbox_mm"]
        lines.append(f"{code},visible,{r['area_visible_mm2']:.0f},{b['L_u']:.0f},{b['H_v']:.0f},{b['D_w']:.0f},{_f(r['weight_g'])},{_f(r['weight_knn_g'])},{_f(r['weight_density_g'])},{_f(r['weight_reg_g'])},{r['material'] or ''},{_f(r['material_cost'])},{r['confidence']:.2f},{r['note']}")
    for code, r in sorted(hidden.items(), key=lambda kv: -(kv[1]["weight_g"] or 0)):
        lines.append(f"{code},hidden,,,,,{_f(r['weight_g'])},,,,{r['material'] or ''},{_f(r['material_cost'])},{r['appearance_rate']:.2f},유사차종 {len(r['from_atlases'])}건 평균")
    with open(os.path.join(out_dir, "prediction.csv"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    md = ["# 예측 요약", "", f"- 유사 차종(상위): " + ", ".join(f"{aid}({d:.3f})" for aid, d in ranking[:5]), "",
          "## 스킨에서 분할된 부품", "", "| 코드 | 면적(cm²) | L×H×D(mm) | 예측중량(g) | 재질 | 신뢰도 |", "|---|---:|---|---:|---|---:|"]
    for code, r in sorted(visible.items(), key=lambda kv: -(kv[1]["weight_g"] or 0)):
        b = r["bbox_mm"]
        md.append(f"| {code} | {r['area_visible_mm2']/100:.0f} | {b['L_u']:.0f}×{b['H_v']:.0f}×{b['D_w']:.0f} | {_f(r['weight_g'])} | {r['material'] or '-'} | {r['confidence']:.2f} |")
    md += ["", "## 스킨에 보이지 않는 부품(유사 차종 BOM 기반 추정)", "", "| 코드 | 예측중량(g) | 재질 | 유사차종 보유율 |", "|---|---:|---|---:|"]
    for code, r in sorted(hidden.items(), key=lambda kv: -(kv[1]["weight_g"] or 0)):
        md.append(f"| {code} | {_f(r['weight_g'])} | {r['material'] or '-'} | {r['appearance_rate']:.0%} |")
    t = result["totals"]
    md += ["", f"**합계: 보이는 부품 {t['visible_weight_g']:.0f} g + 숨은 부품 {t['hidden_weight_g']:.0f} g = {t['total_weight_g']:.0f} g**"]
    with open(os.path.join(out_dir, "prediction_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log("\n중량 예측:")
    for code, r in sorted(visible.items(), key=lambda kv: -(kv[1]["weight_g"] or 0)):
        log(f"  {code:20s} {_f(r['weight_g']):>8s} g  (최근접 {_f(r['weight_knn_g'])}, 면적밀도 {_f(r['weight_density_g'])}, 회귀 {_f(r['weight_reg_g'])}) {r['note']}")
    if hidden:
        log("  숨은 부품(유사차종 기반): " + ", ".join(f"{c} {_f(r['weight_g'])}g" for c, r in hidden.items()))
    log(f"  합계 {t['total_weight_g']:.0f} g")
    return result


def _f(x) -> str:
    return "" if x is None else f"{x:.0f}"


def load_cost_table(path: Optional[str]) -> Dict[str, float]:
    """재질코드,kg당단가 CSV → dict"""
    if not path:
        return {}
    import csv
    out = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                try:
                    out[row[0].strip()] = float(row[1])
                except ValueError:
                    continue
    return out
