"""CSV 데이터 로딩·검증 (DataReport).

규칙:
- 인코딩은 utf-8-sig(BOM) / utf-8 / cp949 순으로 검사하고 채택 결과를 기록한다.
- 모든 열을 문자열로 읽어 ID 의 선행 0 등을 보존한 뒤, 숫자 열만 명시적으로 변환한다.
- 타깃 누락·중복 ID·NaN/Inf·비숫자·feature==target·그룹 누락은 원인 행과 함께 E_INPUT_INVALID 로 실패한다.
  오류 행을 조용히 삭제하지 않는다.
- pandas/numpy 는 함수 안에서 lazy import 한다.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from corp_dl_agent.common import StrictModel, now_iso, sha256_bytes
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.ml.taskspec import TaskSpec
from corp_dl_agent.security.paths import resolve_within

if TYPE_CHECKING:  # 타입 검사 전용 (런타임 최상위 import 금지)
    import pandas as pd

ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp949")
MAX_ROWS_PER_ISSUE = 20
_TRUE_TOKENS = {"1", "1.0", "true", "yes", "y", "pass", "ok"}
_FALSE_TOKENS = {"0", "0.0", "false", "no", "n", "fail", "ng"}

IssueLevel = Literal["error", "warning", "info"]


class RowRef(StrictModel):
    """오류 행 참조: DataFrame index(0 기반), CSV 줄 번호(헤더 포함 1 기반), ID 값(있으면)."""

    index: int
    line: int
    id: str | None = None
    value: str | None = None


class DataIssue(StrictModel):
    code: str
    level: IssueLevel
    message: str
    column: str | None = None
    count: int = 0
    rows: list[RowRef] = Field(default_factory=list)


class DataReport(StrictModel):
    path: str
    sha256: str
    encoding_detected: str
    encodings_tried: list[str] = Field(default_factory=list)
    n_rows: int
    n_cols: int
    columns: list[str]
    dtypes: dict[str, str]
    target: str
    target_stats: dict[str, float | int | str | None]
    group_column: str
    n_groups: int
    issues: list[DataIssue] = Field(default_factory=list)
    passed: bool
    data_origin: Literal["synthetic", "corporate"]
    synthetic: bool
    created_at: str

    def errors(self) -> list[DataIssue]:
        return [i for i in self.issues if i.level == "error"]


def _pd() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise blocked_dependency("pandas", "CSV 데이터 로딩") from exc
    return pd


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "CSV 데이터 검증") from exc
    return np


def detect_encoding(data: bytes) -> tuple[str, str, list[str]]:
    """(text, encoding, tried) — BOM 이 있으면 utf-8-sig, 아니면 utf-8, 그 다음 cp949."""
    tried: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        tried.append("utf-8-sig")
        return data.decode("utf-8-sig"), "utf-8-sig", tried
    for enc in ("utf-8", "cp949"):
        tried.append(enc)
        try:
            return data.decode(enc), enc, tried
        except UnicodeDecodeError:
            continue
    raise AgentError(
        "E_INPUT_INVALID",
        "CSV 인코딩을 판별할 수 없습니다 (utf-8-sig/utf-8/cp949 모두 실패)",
        details={"encodings_tried": tried},
    )


def read_csv_text(path: Path, *, delimiter: str | None = None) -> tuple[Any, str, list[str], str]:
    """모든 열을 문자열로 읽는다. 반환: (DataFrame[str], encoding, tried, sha256)."""
    pd = _pd()
    data = path.read_bytes()
    digest = sha256_bytes(data)
    text, enc, tried = detect_encoding(data)
    sep = delimiter or ("\t" if path.suffix.lower() == ".tsv" else ",")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_filter=False, sep=sep, skipinitialspace=False)
    except (ValueError, pd.errors.ParserError) as exc:
        raise AgentError("E_INPUT_INVALID", f"CSV 파싱 실패: {path.name}", details={"error": str(exc)[:500]}) from exc
    df.columns = [str(c).strip() for c in df.columns]
    return df, enc, tried, digest


def _rows(df_index: Iterable[int], ids: Any | None, values: Any | None = None) -> tuple[int, list[RowRef]]:
    idx = list(df_index)
    refs: list[RowRef] = []
    for i in idx[:MAX_ROWS_PER_ISSUE]:
        ref = RowRef(index=int(i), line=int(i) + 2)
        if ids is not None:
            ref = ref.model_copy(update={"id": str(ids.iloc[int(i)])})
        if values is not None:
            ref = ref.model_copy(update={"value": str(values.iloc[int(i)])[:80]})
        refs.append(ref)
    return len(idx), refs


def _issue(code: str, level: IssueLevel, message: str, *, column: str | None = None, count: int = 0, rows: list[RowRef] | None = None) -> DataIssue:
    return DataIssue(code=code, level=level, message=message, column=column, count=count, rows=rows or [])


def _numeric_column_issues(raw: Any, column: str, ids: Any | None) -> tuple[Any, list[DataIssue]]:
    """문자열 열을 float 로 변환하고 빈 값/비숫자/비유한 값을 행과 함께 보고한다."""
    pd = _pd()
    np = _np()
    issues: list[DataIssue] = []
    stripped = raw.astype(str).str.strip()
    empty = stripped == ""
    num = pd.to_numeric(stripped.where(~empty, other="nan"), errors="coerce").astype("float64")
    non_numeric = num.isna() & ~empty & ~stripped.str.lower().isin(["nan", "+nan", "-nan"])
    non_finite = (~num.isna() & ~np.isfinite(num.to_numpy())) | (stripped.str.lower().isin(["nan", "+nan", "-nan"]))
    if empty.any():
        n, rows = _rows(raw.index[empty.to_numpy()], ids)
        issues.append(_issue("MISSING_VALUE", "error", f"열 '{column}' 에 빈 값이 있습니다", column=column, count=n, rows=rows))
    if non_numeric.any():
        n, rows = _rows(raw.index[non_numeric.to_numpy()], ids, raw)
        issues.append(_issue("NON_NUMERIC", "error", f"열 '{column}' 에 숫자가 아닌 값이 있습니다", column=column, count=n, rows=rows))
    if non_finite.any():
        n, rows = _rows(raw.index[non_finite.to_numpy()], ids, raw)
        issues.append(_issue("NON_FINITE", "error", f"열 '{column}' 에 NaN/Inf 값이 있습니다", column=column, count=n, rows=rows))
    return num, issues


def _classification_target(raw: Any, column: str, ids: Any | None) -> tuple[Any, list[DataIssue]]:
    pd = _pd()
    issues: list[DataIssue] = []
    stripped = raw.astype(str).str.strip()
    low = stripped.str.lower()
    empty = stripped == ""
    is_true = low.isin(sorted(_TRUE_TOKENS))
    is_false = low.isin(sorted(_FALSE_TOKENS))
    bad = ~(is_true | is_false) & ~empty
    if empty.any():
        n, rows = _rows(raw.index[empty.to_numpy()], ids)
        issues.append(_issue("MISSING_TARGET", "error", f"target '{column}' 이 비어 있는 행이 있습니다", column=column, count=n, rows=rows))
    if bad.any():
        n, rows = _rows(raw.index[bad.to_numpy()], ids, raw)
        issues.append(
            _issue(
                "TARGET_NOT_BINARY",
                "error",
                f"이진 분류 target '{column}' 은 0/1(또는 true/false) 이어야 합니다",
                column=column,
                count=n,
                rows=rows,
            )
        )
    y = pd.Series(is_true.astype("int64"), index=raw.index, name=column)
    return y, issues


def _target_stats(y: Any, task_type: str, unit: str) -> dict[str, float | int | str | None]:
    np = _np()
    arr = y.to_numpy()
    if task_type == "regression":
        a = arr.astype("float64")
        return {
            "count": int(a.size),
            "mean": float(np.mean(a)),
            "std": float(np.std(a, ddof=0)),
            "min": float(np.min(a)),
            "p50": float(np.percentile(a, 50)),
            "max": float(np.max(a)),
            "unit": unit,
        }
    pos = int((arr == 1).sum())
    return {
        "count": int(arr.size),
        "positives": pos,
        "negatives": int(arr.size) - pos,
        "positive_rate": (pos / arr.size) if arr.size else None,
        "unit": "",
    }


def _build_report(
    *,
    path: Path,
    digest: str,
    enc: str,
    tried: list[str],
    df: Any,
    spec: TaskSpec,
    issues: list[DataIssue],
    target_stats: dict[str, float | int | str | None],
    n_groups: int,
) -> DataReport:
    return DataReport(
        path=str(path),
        sha256=digest,
        encoding_detected=enc,
        encodings_tried=tried,
        n_rows=int(len(df)),
        n_cols=int(len(df.columns)),
        columns=[str(c) for c in df.columns],
        dtypes={str(c): str(df[c].dtype) for c in df.columns},
        target=spec.target,
        target_stats=target_stats,
        group_column=spec.group_column,
        n_groups=n_groups,
        issues=issues,
        passed=not any(i.level == "error" for i in issues),
        data_origin=spec.data_origin,
        synthetic=spec.data_origin == "synthetic",
        created_at=now_iso(),
    )


def load_dataset(spec: TaskSpec, *, roots: Iterable[str | Path], forbid_links: bool = True) -> tuple[pd.DataFrame, DataReport]:
    """TaskSpec 의 CSV 를 승인 root 안에서 읽고 검증한다. 오류가 있으면 E_INPUT_INVALID (details.issues, details.report)."""
    pd = _pd()
    np = _np()
    path = resolve_within(spec.data_path, roots, forbid_links=forbid_links)
    if not path.is_file():
        raise AgentError("E_INPUT_INVALID", f"데이터 파일을 찾을 수 없습니다: {path.name}", details={"path": str(path)})
    raw_df, enc, tried, digest = read_csv_text(path)
    issues: list[DataIssue] = []

    # 1) 열 존재
    columns = list(raw_df.columns)
    dup_cols = sorted({c for c in columns if columns.count(c) > 1})
    if dup_cols:
        issues.append(_issue("DUPLICATE_COLUMN", "error", f"CSV 헤더에 중복 열이 있습니다: {dup_cols}"))
    missing_cols = [c for c in spec.required_columns() if c not in columns]
    if missing_cols:
        issues.append(_issue("MISSING_COLUMN", "error", f"TaskSpec 이 요구하는 열이 CSV 에 없습니다: {missing_cols}"))
    absent_excluded = [c for c in spec.excluded_columns if c not in columns]
    if absent_excluded:
        issues.append(_issue("EXCLUDED_ABSENT", "info", f"excluded_columns 중 CSV 에 없는 열: {absent_excluded}"))
    referenced = set(spec.required_columns()) | set(spec.excluded_columns)
    unused = [c for c in columns if c not in referenced]
    if unused:
        issues.append(
            _issue(
                "UNUSED_COLUMN",
                "warning",
                f"TaskSpec 에서 참조하지 않는 열 (feature 로 사용되지 않음; 사후 결과라면 excluded_columns 에 명시하세요): {unused}",
            )
        )
    if raw_df.empty:
        issues.append(_issue("EMPTY", "error", "CSV 에 데이터 행이 없습니다"))
    if missing_cols or dup_cols or raw_df.empty:
        report = _build_report(path=path, digest=digest, enc=enc, tried=tried, df=raw_df, spec=spec, issues=issues, target_stats={}, n_groups=0)
        _raise_invalid(report)

    out = pd.DataFrame(index=raw_df.index)
    ids = raw_df[spec.id_column].astype(str)

    # 2) ID: 문자열 보존, 빈 값/중복/공백 검사
    out[spec.id_column] = ids
    id_stripped = ids.str.strip()
    empty_id = id_stripped == ""
    if empty_id.any():
        n, rows = _rows(raw_df.index[empty_id.to_numpy()], None)
        issues.append(_issue("MISSING_ID", "error", f"ID 열 '{spec.id_column}' 이 비어 있는 행이 있습니다", column=spec.id_column, count=n, rows=rows))
    ws_id = (ids != id_stripped) & ~empty_id
    if ws_id.any():
        n, rows = _rows(raw_df.index[ws_id.to_numpy()], ids)
        issues.append(_issue("ID_WHITESPACE", "warning", "ID 앞뒤에 공백이 있습니다 (값은 수정하지 않았습니다)", column=spec.id_column, count=n, rows=rows))
    dup_mask = ids.duplicated(keep=False) & ~empty_id
    if dup_mask.any():
        n, rows = _rows(raw_df.index[dup_mask.to_numpy()], ids)
        dup_values = sorted(set(ids[dup_mask].tolist()))[:10]
        issues.append(
            _issue("DUPLICATE_ID", "error", f"ID 가 중복되었습니다 (예: {dup_values})", column=spec.id_column, count=n, rows=rows)
        )

    # 3) 그룹
    groups = raw_df[spec.group_column].astype(str)
    out[spec.group_column] = groups
    empty_group = groups.str.strip() == ""
    if empty_group.any():
        n, rows = _rows(raw_df.index[empty_group.to_numpy()], ids)
        issues.append(_issue("MISSING_GROUP", "error", f"그룹 열 '{spec.group_column}' 이 비어 있는 행이 있습니다", column=spec.group_column, count=n, rows=rows))
    n_groups = int(groups[~empty_group].nunique())

    # 4) 시간 열
    if spec.time_column is not None:
        traw = raw_df[spec.time_column].astype(str).str.strip()
        parsed = pd.to_datetime(traw.where(traw != "", other=None), errors="coerce", format="ISO8601")
        bad_t = parsed.isna()
        if bad_t.any():
            n, rows = _rows(raw_df.index[bad_t.to_numpy()], ids, raw_df[spec.time_column])
            issues.append(
                _issue(
                    "TIME_UNPARSABLE",
                    "error",
                    f"시간 열 '{spec.time_column}' 을 ISO 8601 로 해석할 수 없는 행이 있습니다",
                    column=spec.time_column,
                    count=n,
                    rows=rows,
                )
            )
        out[spec.time_column] = parsed

    # 5) 숫자 feature
    numeric_frames: dict[str, Any] = {}
    for col in spec.numeric_features:
        num, col_issues = _numeric_column_issues(raw_df[col], col, ids)
        issues.extend(col_issues)
        numeric_frames[col] = num
        out[col] = num
        if not col_issues and num.nunique(dropna=True) <= 1:
            issues.append(_issue("CONSTANT_FEATURE", "warning", f"숫자 feature '{col}' 이 상수입니다", column=col))

    # 6) 범주 feature (빈 값은 오류; 값은 문자열 보존)
    for col in spec.categorical_features:
        cat = raw_df[col].astype(str)
        out[col] = cat
        empty_cat = cat.str.strip() == ""
        if empty_cat.any():
            n, rows = _rows(raw_df.index[empty_cat.to_numpy()], ids)
            issues.append(_issue("MISSING_VALUE", "error", f"범주 feature '{col}' 에 빈 값이 있습니다", column=col, count=n, rows=rows))
        n_unique = int(cat.nunique())
        if len(cat) >= 20 and n_unique >= 20 and n_unique / max(len(cat), 1) > 0.5:
            issues.append(
                _issue(
                    "HIGH_CARDINALITY",
                    "warning",
                    f"범주 feature '{col}' 의 고유값 비율이 높습니다 ({n_unique}/{len(cat)}). ID 성격의 열이면 E_LEAKAGE 로 거부됩니다",
                    column=col,
                )
            )

    # 7) target
    unit = spec.target_unit()
    if spec.task_type == "regression":
        y, t_issues = _numeric_column_issues(raw_df[spec.target], spec.target, ids)
        for it in t_issues:
            if it.code == "MISSING_VALUE":
                it = it.model_copy(update={"code": "MISSING_TARGET", "message": f"target '{spec.target}' 이 비어 있는 행이 있습니다"})
            issues.append(it)
        if not t_issues and y.nunique() <= 1:
            issues.append(_issue("CONSTANT_TARGET", "error", f"target '{spec.target}' 이 상수라 학습/평가가 불가능합니다", column=spec.target))
    else:
        y, t_issues = _classification_target(raw_df[spec.target], spec.target, ids)
        issues.extend(t_issues)
        if not t_issues and y.nunique() < 2:
            issues.append(_issue("SINGLE_CLASS", "error", f"target '{spec.target}' 에 한 클래스만 있습니다 (0/1 모두 필요)", column=spec.target))
    out[spec.target] = y

    # 8) feature == target (값 동일) → 누수
    y_arr = y.to_numpy().astype("float64")
    for col, num in numeric_frames.items():
        arr = num.to_numpy()
        if arr.shape == y_arr.shape and not np.isnan(arr).any() and not np.isnan(y_arr).any() and np.allclose(arr, y_arr, rtol=0.0, atol=0.0):
            issues.append(
                _issue("FEATURE_EQUALS_TARGET", "error", f"feature '{col}' 의 값이 target '{spec.target}' 과 동일합니다 (누수)", column=col)
            )
    for col in spec.categorical_features:
        if out[col].astype(str).tolist() == raw_df[spec.target].astype(str).tolist():
            issues.append(
                _issue("FEATURE_EQUALS_TARGET", "error", f"범주 feature '{col}' 의 값이 target '{spec.target}' 과 동일합니다 (누수)", column=col)
            )

    # 9) 기타 열은 문자열로 보존 (excluded 포함; feature 로는 쓰지 않음)
    for col in columns:
        if col not in out.columns:
            out[col] = raw_df[col].astype(str)
    out = out[columns]

    has_error = any(i.level == "error" for i in issues)
    t_stats = _target_stats(y, spec.task_type, unit) if not has_error else {}
    report = _build_report(path=path, digest=digest, enc=enc, tried=tried, df=out, spec=spec, issues=issues, target_stats=t_stats, n_groups=n_groups)
    if has_error:
        _raise_invalid(report)
    return out, report


def _raise_invalid(report: DataReport) -> None:
    errs = report.errors()
    summary = "; ".join(f"{e.code}({e.count})" if e.count else e.code for e in errs[:8])
    raise AgentError(
        "E_INPUT_INVALID",
        f"데이터 검증 실패 ({len(errs)}건): {summary}",
        hint="오류 행(index/line/id)을 수정한 뒤 다시 실행하세요. 오류 행은 자동 삭제되지 않습니다.",
        details={
            "path": report.path,
            "encoding_detected": report.encoding_detected,
            "issues": [e.model_dump(mode="json") for e in errs],
            "report": report.model_dump(mode="json"),
        },
    )


def is_finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
