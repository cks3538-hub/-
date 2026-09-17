"""predict CLI 핵심: export 번들 로드 → 입력 CSV 검증 → 예측 CSV(+ provenance sidecar) 작성.

- 입력 CSV 는 모든 열을 문자열로 읽고(인코딩 utf-8-sig/utf-8/cp949 검사) 숫자 feature 만 명시적으로 변환한다.
  비어 있는 값은 train 중앙값으로 대치하고 그 수를 보고한다. 숫자가 아닌 값은 행 번호와 함께 E_INPUT_INVALID (행 삭제 없음).
- synthetic 번들은 allow_synthetic (기본: cfg.ml.allow_synthetic_models_in_production=False) 이 True 일 때만 허용.
- 출력: <output>.csv (utf-8-sig) 와 <output>.manifest.json (모델 dir/manifest sha/행 수/외삽 행 수/생성 시각, synthetic 표시).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from corp_dl_agent.common import atomic_write_bytes, atomic_write_json, now_iso, sha256_file
from corp_dl_agent.config.schemas import AppConfig
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.ml.data import read_csv_text
from corp_dl_agent.ml.export import LoadedBundle, load_bundle, predict
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.workspace import Workspace

MAX_ROWS_REPORTED = 20


def _pd() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("pandas", "predict") from exc
    return pd


def predict_roots(cfg: AppConfig, *extra: Path) -> list[Path]:
    """번들/입력/출력 허용 root: 작업 폴더, workspace/company root, 설정의 input/sources/output root."""
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [Path.cwd().resolve(), ws.data_root, ws.company_root]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.input_roots]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.sources_roots]
    if cfg.paths.output_root:
        roots.append(Path(cfg.paths.output_root).expanduser().resolve())
    roots += [Path(p).expanduser().resolve() for p in extra]
    return roots


def prepare_input_frame(raw_df: Any, bundle: LoadedBundle) -> tuple[Any, dict[str, Any]]:
    """문자열 DataFrame → 번들 input_schema 에 맞춘 프레임. 반환 (df, report{missing_values, non_numeric...})."""
    pd = _pd()
    m = bundle.manifest
    numeric = [str(c) for c in m.input_schema.get("numeric_features", [])]
    categorical = [str(c) for c in m.input_schema.get("categorical_features", [])]
    missing_cols = [c for c in numeric + categorical if c not in raw_df.columns]
    if missing_cols:
        raise AgentError(
            "E_INPUT_INVALID",
            f"입력 CSV 에 모델이 요구하는 열이 없습니다: {missing_cols}",
            details={"missing_columns": missing_cols, "required": numeric + categorical},
        )
    out = pd.DataFrame(index=raw_df.index)
    if m.id_column in raw_df.columns:
        out[m.id_column] = raw_df[m.id_column].astype(str)
    imputed: dict[str, int] = {}
    for col in numeric:
        stripped = raw_df[col].astype(str).str.strip()
        empty = stripped == ""
        num = pd.to_numeric(stripped.where(~empty, other="nan"), errors="coerce").astype("float64")
        bad = num.isna() & ~empty
        if bad.any():
            rows = [int(i) + 2 for i in raw_df.index[bad.to_numpy()][:MAX_ROWS_REPORTED]]
            raise AgentError(
                "E_INPUT_INVALID",
                f"숫자 feature '{col}' 에 숫자가 아닌 값이 있습니다 (CSV 줄 번호: {rows})",
                details={"column": col, "lines": rows, "count": int(bad.sum())},
            )
        if empty.any():
            imputed[col] = int(empty.sum())
        out[col] = num
    for col in categorical:
        out[col] = raw_df[col].astype(str)
    return out, {"imputed_values": imputed, "n_rows": int(len(out))}


def predict_cli(
    model_dir: str | Path,
    input_csv: str | Path,
    output_csv: str | Path,
    cfg: AppConfig,
    *,
    allow_synthetic: bool | None = None,
) -> dict[str, Any]:
    """번들 로드 → 예측 → CSV/sidecar 작성. 반환: 요약 dict (JSON 가능)."""
    roots = predict_roots(cfg)
    forbid = cfg.security.forbid_symlinks
    model_path = resolve_within(model_dir, roots, forbid_links=forbid)
    input_path = resolve_within(input_csv, roots, forbid_links=forbid)
    out_p = Path(output_csv).expanduser()
    out_parent = out_p.parent if str(out_p.parent) not in ("", ".") else Path.cwd()
    out_parent.mkdir(parents=True, exist_ok=True)
    output_path = resolve_within(out_parent, roots, forbid_links=forbid) / out_p.name
    if not input_path.is_file():
        raise AgentError(
            "E_INPUT_INVALID", f"입력 CSV 가 없습니다: {input_path.name}", details={"path": str(input_path)}
        )
    allow = cfg.ml.allow_synthetic_models_in_production if allow_synthetic is None else bool(allow_synthetic)
    bundle = load_bundle(model_path, allow_synthetic=allow)
    raw_df, encoding, tried, input_sha = read_csv_text(input_path)
    if raw_df.empty:
        raise AgentError("E_INPUT_INVALID", "입력 CSV 에 데이터 행이 없습니다")
    frame, prep = prepare_input_frame(raw_df, bundle)
    result = predict(bundle, frame, allow_missing=True)
    text = result.to_csv(index=False, lineterminator="\n")
    atomic_write_bytes(output_path, b"\xef\xbb\xbf" + text.encode("utf-8"))
    n_out_of_range = int((~result["in_train_range"]).sum()) if "in_train_range" in result.columns else 0
    m = bundle.manifest
    summary: dict[str, Any] = {
        "output_csv": str(output_path),
        "output_sha256": sha256_file(output_path),
        "input_csv": str(input_path),
        "input_sha256": input_sha,
        "input_encoding": encoding,
        "input_encodings_tried": tried,
        "model_dir": str(model_path),
        "manifest_sha256": bundle.manifest_sha256,
        "model_kind": m.kind,
        "model_name": m.model_name,
        "run_id": m.run_id,
        "task_id": m.task_id,
        "task_type": m.task_type,
        "target": m.target,
        "target_unit": m.target_inverse.unit or m.units.get(m.target, ""),
        "threshold": m.threshold,
        "data_origin": m.data_origin,
        "synthetic": m.synthetic,
        "allow_synthetic": allow,
        "n_rows": int(len(result)),
        "n_out_of_train_range": n_out_of_range,
        "imputed_values": prep["imputed_values"],
        "value_type": "predicted",
        "model_run_id": m.run_id,
        "notices": list(m.notices),
        "created_at": now_iso(),
        "created_by": "corp-dl-agent",
    }
    sidecar = output_path.with_name(output_path.stem + ".manifest.json")
    atomic_write_json(sidecar, summary)
    summary["sidecar"] = str(sidecar)
    return summary
