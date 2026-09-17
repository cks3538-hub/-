"""CAD snapshot 수입 (CSV UTF-8 / UTF-8-SIG / CP949, JSON) 과 검증.

- 원본 파일은 읽기 전용으로 취급하며 수정하지 않는다.
- CSV 원본 단위(mm3, g/cm3 등)는 `units` 에 보존하고 SI(m3, kg/m3, kg, m) 로 변환해 저장한다.
- 오류 행은 자동 삭제하지 않는다. strict=True 이면 error 급 issue 가 하나라도 있으면 E_INPUT_INVALID.
- 표준 라이브러리 + pydantic 만 사용한다.
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from corp_dl_agent.common import (
    LaxModel,
    StrictModel,
    atomic_write_json,
    now_iso,
    read_json,
    sha256_file,
    validate_strict,
)
from corp_dl_agent.engineering import units as U
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import __version__

GeometryStatus = Literal["solid", "surface", "unloaded", "suppressed", "missing"]
GEOMETRY_STATUSES: tuple[str, ...] = ("solid", "surface", "unloaded", "suppressed", "missing")
EXTRACTOR_VERSION = f"csv_json_import/{__version__}"

IssueLevel = Literal["error", "warning", "info"]


class SnapshotIssue(StrictModel):
    level: IssueLevel
    code: str
    message: str
    row_index: int | None = None  # CSV 데이터 행 번호 (헤더 제외, 1부터) / JSON 배열 index
    occurrence_path: str | None = None
    field: str | None = None


class CadSnapshotRow(LaxModel):
    """CSV 유래이므로 LaxModel (문자열 -> 숫자 허용) 이지만 extra 는 금지."""

    document_id: str
    revision: str
    configuration_id: str | None = None
    occurrence_path: str  # 예 "ROOT/ASM_A/PART_1.2" (instance 경로, 고유)
    reference_id: str  # 부품 참조 (동일 reference 가 여러 occurrence 로 등장 가능)
    quantity: int = Field(1, ge=1)  # 이 occurrence 의 수량 (instance 수)
    material_id: str | None = None
    density_kg_m3: float | None = Field(None, ge=0)
    volume_m3: float | None = Field(None, ge=0)
    mass_kg: float | None = Field(None, ge=0)  # CAD 가 직접 준 질량 (계산값과 비교, 출처 구분)
    parameter_map: dict[str, float | str] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)  # {"volume": "mm3", "density": "g/cm3"} 원본 단위
    geometry_status: GeometryStatus = "solid"
    surface_thickness_m: float | None = Field(None, ge=0)  # surface 인 경우 명시 두께
    is_assembly: bool = False  # True 면 자식들의 총량 노드 (질량 이중합산 금지)
    parent_path: str | None = None
    source_hash: str = ""
    extracted_at: str = ""
    extractor_version: str = ""

    @field_validator("document_id", "revision", "occurrence_path", "reference_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("빈 값은 허용되지 않습니다")
        return v


class CadSnapshot(StrictModel):
    rows: list[CadSnapshotRow]
    source_path: str
    source_hash: str
    extracted_at: str
    extractor_version: str
    issues: list[SnapshotIssue] = Field(default_factory=list)
    encoding: str = ""
    synthetic: bool = False

    def error_issues(self) -> list[SnapshotIssue]:
        return [i for i in self.issues if i.level == "error"]

    def revisions(self) -> list[str]:
        return sorted({r.revision for r in self.rows})

    def document_ids(self) -> list[str]:
        return sorted({r.document_id for r in self.rows})


# ---------------------------------------------------------------------------
# 인코딩 감지
# ---------------------------------------------------------------------------

_BOM = b"\xef\xbb\xbf"
BOM_CHAR = chr(0xFEFF)  # 열 이름 앞 BOM 제거용 (escape 가 formatter 로 풀리지 않도록 chr 사용)


def read_text_detect_encoding(path: str | os.PathLike[str]) -> tuple[str, str]:
    """UTF-8-SIG -> UTF-8 -> CP949 순으로 시도. (text, encoding) 반환."""
    p = Path(path)
    if not p.is_file():
        raise AgentError(
            "E_INPUT_INVALID", f"입력 파일을 찾을 수 없습니다: {p.name}", details={"path": str(p)}
        )
    data = p.read_bytes()
    if data.startswith(_BOM):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp949"), "cp949"
    except UnicodeDecodeError as exc:
        raise AgentError(
            "E_INPUT_INVALID",
            f"파일 인코딩을 판별할 수 없습니다 (utf-8/utf-8-sig/cp949 실패): {p.name}",
            details={"path": str(p), "error": str(exc)[:200]},
        ) from exc


# ---------------------------------------------------------------------------
# CSV 파싱
# ---------------------------------------------------------------------------

# 인식하는 CSV 열 (snapshot 필드 + 원본 단위 열). 이 밖의 열은 warning 후 무시한다.
_DIRECT_STR_FIELDS = (
    "document_id",
    "revision",
    "configuration_id",
    "occurrence_path",
    "reference_id",
    "material_id",
    "parent_path",
    "source_hash",
    "extracted_at",
    "extractor_version",
)
_MEASURE_COLUMNS: dict[str, tuple[str, str, str, str]] = {
    # 원본 열 -> (단위 열, units key, SI 필드, 종류)
    "volume": ("volume_unit", "volume", "volume_m3", "volume"),
    "density": ("density_unit", "density", "density_kg_m3", "density"),
    "mass": ("mass_unit", "mass", "mass_kg", "mass"),
    "surface_thickness": ("thickness_unit", "thickness", "surface_thickness_m", "length"),
}
_SI_COLUMNS = {"volume_m3": "m3", "density_kg_m3": "kg/m3", "mass_kg": "kg", "surface_thickness_m": "m"}
_KNOWN_COLUMNS = (
    set(_DIRECT_STR_FIELDS)
    | set(_MEASURE_COLUMNS)
    | {v[0] for v in _MEASURE_COLUMNS.values()}
    | set(_SI_COLUMNS)
    | {
        "quantity",
        "geometry_status",
        "is_assembly",
        "parameter_map",
        "units",
        "area",
        "area_unit",
        "synthetic",
    }
)
_TRUE = {"true", "1", "yes", "y", "t", "예", "참"}
_FALSE = {"false", "0", "no", "n", "f", "아니오", "거짓", ""}

_CONVERTERS = {
    "volume": U.convert_volume,
    "density": U.convert_density,
    "mass": U.convert_mass,
    "length": U.convert_length,
    "area": U.convert_area,
}


def _parse_bool(raw: str | None) -> bool:
    s = (raw or "").strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"불리언 값이 아닙니다: {raw!r}")


def _parse_number(raw: str) -> float:
    s = raw.strip().replace(",", "")
    return float(s)


def _param_value(raw: str) -> float | str:
    try:
        return _parse_number(raw)
    except ValueError:
        return raw.strip()


def _apply_mapping(raw: dict[str, Any], mapping: dict[str, str] | None) -> dict[str, Any]:
    """field_mapping: {snapshot 필드 <- 회사 export 열}."""
    if not mapping:
        return raw
    out = dict(raw)
    for field, column in mapping.items():
        if column in out and field != column:
            if field in out and out[field] not in (None, ""):
                # 둘 다 있으면 회사 열 이름을 우선하지 않고 충돌로 본다
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"mapping 충돌: '{field}' 열과 매핑 열 '{column}' 이 모두 존재합니다",
                    details={"field": field, "column": column},
                )
            out[field] = out.pop(column)
    return out


def _csv_row_to_dict(
    raw_in: dict[str, Any],
    *,
    row_index: int,
    issues: list[SnapshotIssue],
    file_hash: str,
    extracted_at: str,
) -> dict[str, Any] | None:
    raw: dict[str, str | None] = {}
    for k, v in raw_in.items():
        if k is None:
            continue
        key = str(k).strip().lstrip(BOM_CHAR)
        val = v.strip() if isinstance(v, str) else v
        raw[key] = val if val not in ("",) else None
    occ = raw.get("occurrence_path")
    row: dict[str, Any] = {}
    ok = True

    def err(code: str, msg: str, field: str | None = None) -> None:
        nonlocal ok
        ok = False
        issues.append(
            SnapshotIssue(
                level="error", code=code, message=msg, row_index=row_index, occurrence_path=occ, field=field
            )
        )

    for f in _DIRECT_STR_FIELDS:
        if raw.get(f) is not None:
            row[f] = raw[f]
    row["quantity"] = raw.get("quantity") if raw.get("quantity") is not None else 1
    if raw.get("geometry_status") is not None:
        row["geometry_status"] = str(raw["geometry_status"]).strip().lower()
    try:
        row["is_assembly"] = _parse_bool(raw.get("is_assembly"))
    except ValueError as exc:
        err("ROW_INVALID", str(exc), "is_assembly")

    units: dict[str, str] = {}
    params: dict[str, float | str] = {}
    # SI 열 우선 (이미 변환된 값)
    for si_field, si_unit in _SI_COLUMNS.items():
        if raw.get(si_field) is not None:
            try:
                row[si_field] = _parse_number(str(raw[si_field]))
            except ValueError:
                err("ROW_INVALID", f"숫자가 아닙니다: {raw[si_field]!r}", si_field)
            units[[k for k, v in _MEASURE_COLUMNS.items() if v[2] == si_field][0]] = si_unit
    # 원본 단위 열
    for col, (unit_col, unit_key, si_field, kind) in _MEASURE_COLUMNS.items():
        val = raw.get(col)
        if val is None:
            continue
        if si_field in row:
            err("ROW_INVALID", f"'{col}' 와 '{si_field}' 열이 동시에 있습니다", col)
            continue
        unit = raw.get(unit_col)
        if unit is None:
            err("UNIT_MISSING", f"'{col}' 값에 단위 열 '{unit_col}' 이 없습니다", col)
            continue
        try:
            num = _parse_number(str(val))
        except ValueError:
            err("ROW_INVALID", f"숫자가 아닙니다: {val!r}", col)
            continue
        try:
            row[si_field] = _CONVERTERS[kind](num, unit)
        except AgentError as exc:
            code = "UNIT_UNKNOWN" if exc.code == "E_UNIT_MISMATCH" else "NEGATIVE_VALUE"
            err(code, exc.message, col)
            continue
        units[unit_key] = U.normalize_unit(unit)
    # 면적 (surface 근사용) 은 parameter_map 에 원본 값 + units 에 단위
    if raw.get("area") is not None:
        unit = raw.get("area_unit")
        if unit is None:
            err("UNIT_MISSING", "'area' 값에 단위 열 'area_unit' 이 없습니다", "area")
        else:
            try:
                num = _parse_number(str(raw["area"]))
                U.convert_area(num, unit)  # 단위/부호 검증만
                params["area"] = num
                units["area"] = U.normalize_unit(unit)
            except ValueError:
                err("ROW_INVALID", f"숫자가 아닙니다: {raw['area']!r}", "area")
            except AgentError as exc:
                err(
                    "UNIT_UNKNOWN" if exc.code == "E_UNIT_MISMATCH" else "NEGATIVE_VALUE", exc.message, "area"
                )
    # param:<name> 열 과 parameter_map JSON 열
    for k, v in raw.items():
        if k.startswith("param:") and v is not None:
            params[k[len("param:") :].strip()] = _param_value(str(v))
    if raw.get("parameter_map") is not None:
        try:
            pm = json.loads(str(raw["parameter_map"]))
            if not isinstance(pm, dict):
                raise ValueError("dict 아님")
            for k, v in pm.items():
                params[str(k)] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
        except ValueError as exc:
            err("ROW_INVALID", f"parameter_map JSON 파싱 실패: {exc}", "parameter_map")
    if raw.get("units") is not None:
        try:
            um = json.loads(str(raw["units"]))
            if not isinstance(um, dict):
                raise ValueError("dict 아님")
            for k, v in um.items():
                units.setdefault(str(k), U.normalize_unit(str(v)))
        except ValueError as exc:
            err("ROW_INVALID", f"units JSON 파싱 실패: {exc}", "units")
    row["parameter_map"] = params
    row["units"] = units
    row.setdefault("source_hash", file_hash)
    row.setdefault("extracted_at", extracted_at)
    row.setdefault("extractor_version", EXTRACTOR_VERSION)
    return row if ok else None


def _validation_issues(
    exc: ValidationError, *, row_index: int | None, occ: str | None
) -> list[SnapshotIssue]:
    out: list[SnapshotIssue] = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        etype = str(e.get("type", ""))
        msg = str(e.get("msg", ""))
        if loc == "geometry_status":
            code = "UNKNOWN_GEOMETRY_STATUS"
            msg = f"알 수 없는 geometry_status (허용: {', '.join(GEOMETRY_STATUSES)})"
        elif etype.startswith("greater_than") or etype.startswith("less_than"):
            code = "NEGATIVE_VALUE"
        elif etype == "missing":
            code = "REQUIRED_MISSING"
        elif etype == "extra_forbidden":
            code = "UNKNOWN_FIELD"
        else:
            code = "ROW_INVALID"
        out.append(
            SnapshotIssue(
                level="error",
                code=code,
                message=f"{loc}: {msg}",
                row_index=row_index,
                occurrence_path=occ,
                field=loc or None,
            )
        )
    return out


def _cross_row_checks(rows: list[CadSnapshotRow], issues: list[SnapshotIssue]) -> None:
    seen: dict[str, int] = {}
    for idx, r in enumerate(rows, start=1):
        if r.occurrence_path in seen:
            issues.append(
                SnapshotIssue(
                    level="error",
                    code="DUPLICATE_OCCURRENCE",
                    message=f"occurrence_path 중복: '{r.occurrence_path}' (행 {seen[r.occurrence_path]} 과 중복)",
                    row_index=idx,
                    occurrence_path=r.occurrence_path,
                    field="occurrence_path",
                )
            )
        else:
            seen[r.occurrence_path] = idx
    paths = set(seen)
    for idx, r in enumerate(rows, start=1):
        if r.parent_path and r.parent_path not in paths:
            issues.append(
                SnapshotIssue(
                    level="warning",
                    code="PARENT_NOT_FOUND",
                    message=f"parent_path '{r.parent_path}' 에 해당하는 행이 없습니다 (최상위로 취급)",
                    row_index=idx,
                    occurrence_path=r.occurrence_path,
                    field="parent_path",
                )
            )
        if (
            r.geometry_status == "solid"
            and not r.is_assembly
            and r.volume_m3 is not None
            and "volume" not in r.units
        ):
            issues.append(
                SnapshotIssue(
                    level="error",
                    code="UNIT_MISSING",
                    message="volume 값에 단위 정보(units.volume)가 없습니다",
                    row_index=idx,
                    occurrence_path=r.occurrence_path,
                    field="units.volume",
                )
            )
        if r.is_assembly and (r.volume_m3 is not None or r.density_kg_m3 is not None):
            issues.append(
                SnapshotIssue(
                    level="info",
                    code="ASSEMBLY_HAS_GEOMETRY",
                    message="assembly 총량 노드에 부피/밀도가 있습니다. 합산에서 제외되고 참고값으로만 보존됩니다",
                    row_index=idx,
                    occurrence_path=r.occurrence_path,
                )
            )
    revisions = {r.revision for r in rows}
    if len(revisions) > 1:
        issues.append(
            SnapshotIssue(
                level="warning",
                code="MIXED_REVISION",
                message=f"snapshot 에 여러 revision 이 섞여 있습니다: {', '.join(sorted(revisions))}",
            )
        )


def _finalize(
    rows: list[CadSnapshotRow],
    issues: list[SnapshotIssue],
    *,
    source_path: Path,
    file_hash: str,
    extracted_at: str,
    encoding: str,
    synthetic: bool,
    strict: bool,
) -> CadSnapshot:
    _cross_row_checks(rows, issues)
    snap = CadSnapshot(
        rows=rows,
        source_path=str(source_path),
        source_hash=file_hash,
        extracted_at=extracted_at,
        extractor_version=EXTRACTOR_VERSION,
        issues=issues,
        encoding=encoding,
        synthetic=synthetic,
    )
    errors = snap.error_issues()
    if strict and errors:
        raise AgentError(
            "E_INPUT_INVALID",
            f"CAD snapshot 검증 실패: 오류 {len(errors)}건 ({source_path.name})",
            details={
                "path": str(source_path),
                "errors": [
                    {
                        "row": i.row_index,
                        "occurrence_path": i.occurrence_path,
                        "field": i.field,
                        "code": i.code,
                        "message": i.message,
                    }
                    for i in errors[:50]
                ],
                "error_count": len(errors),
            },
        )
    return snap


def _import_csv(
    path: Path,
    *,
    field_mapping: dict[str, str] | None,
    delimiter: str,
    strict: bool,
    synthetic: bool | None,
    file_hash: str,
) -> CadSnapshot:
    text, encoding = read_text_detect_encoding(path)
    issues: list[SnapshotIssue] = [
        SnapshotIssue(level="info", code="ENCODING", message=f"CSV 인코딩: {encoding}")
    ]
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if not reader.fieldnames:
        raise AgentError("E_INPUT_INVALID", f"CSV 헤더가 없습니다: {path.name}", details={"path": str(path)})
    mapping = field_mapping or {}
    header = [h.strip().lstrip(BOM_CHAR) for h in reader.fieldnames]
    mapped_columns = set(mapping.values())
    unknown = [
        h
        for h in header
        if h not in _KNOWN_COLUMNS and h not in mapped_columns and not h.startswith("param:")
    ]
    if unknown:
        issues.append(
            SnapshotIssue(
                level="warning",
                code="UNKNOWN_COLUMNS",
                message=f"인식되지 않은 열은 무시됩니다: {', '.join(unknown)}",
            )
        )
    extracted_at = now_iso()
    rows: list[CadSnapshotRow] = []
    synthetic_flags: list[bool] = []
    n = 0
    for raw in reader:
        if all((v is None or str(v).strip() == "") for v in raw.values()):
            continue
        n += 1
        raw = _apply_mapping(raw, mapping)
        if raw.get("synthetic") is not None:
            try:
                synthetic_flags.append(_parse_bool(str(raw.get("synthetic"))))
            except ValueError:
                synthetic_flags.append(False)
        raw = {
            k: v
            for k, v in raw.items()
            if k is not None and (k in _KNOWN_COLUMNS or str(k).startswith("param:")) and k != "synthetic"
        }
        row = _csv_row_to_dict(
            raw, row_index=n, issues=issues, file_hash=file_hash, extracted_at=extracted_at
        )
        if row is None:
            continue
        try:
            rows.append(CadSnapshotRow(**row))
        except ValidationError as exc:
            issues.extend(_validation_issues(exc, row_index=n, occ=row.get("occurrence_path")))
    if n == 0:
        issues.append(SnapshotIssue(level="error", code="EMPTY", message="데이터 행이 없습니다"))
    is_synthetic = synthetic if synthetic is not None else (bool(synthetic_flags) and all(synthetic_flags))
    return _finalize(
        rows,
        issues,
        source_path=path,
        file_hash=file_hash,
        extracted_at=extracted_at,
        encoding=encoding,
        synthetic=is_synthetic,
        strict=strict,
    )


def _import_json(
    path: Path,
    *,
    field_mapping: dict[str, str] | None,
    strict: bool,
    synthetic: bool | None,
    file_hash: str,
) -> CadSnapshot:
    try:
        data = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AgentError(
            "E_INPUT_INVALID",
            f"JSON 파싱 실패: {path.name}",
            details={"path": str(path), "error": str(exc)[:200]},
        ) from exc
    issues: list[SnapshotIssue] = []
    top_synthetic: bool | None = None
    if isinstance(data, dict):
        raw_rows = data.get("rows")
        if isinstance(data.get("synthetic"), bool):
            top_synthetic = data["synthetic"]
    else:
        raw_rows = data
    if not isinstance(raw_rows, list):
        raise AgentError(
            "E_INPUT_INVALID",
            "JSON snapshot 은 행 배열 또는 {'rows': [...]} 이어야 합니다",
            details={"path": str(path)},
        )
    extracted_at = (
        str(data.get("extracted_at")) if isinstance(data, dict) and data.get("extracted_at") else now_iso()
    )
    rows: list[CadSnapshotRow] = []
    for idx, item in enumerate(raw_rows, start=1):
        if not isinstance(item, dict):
            issues.append(
                SnapshotIssue(
                    level="error", code="ROW_INVALID", message="행이 객체(dict)가 아닙니다", row_index=idx
                )
            )
            continue
        item = _apply_mapping(item, field_mapping)
        item.setdefault("source_hash", file_hash)
        item.setdefault("extracted_at", extracted_at)
        item.setdefault("extractor_version", EXTRACTOR_VERSION)
        try:
            rows.append(CadSnapshotRow(**item))
        except ValidationError as exc:
            occ = item.get("occurrence_path")
            issues.extend(_validation_issues(exc, row_index=idx, occ=str(occ) if occ is not None else None))
    if not raw_rows:
        issues.append(SnapshotIssue(level="error", code="EMPTY", message="데이터 행이 없습니다"))
    is_synthetic = synthetic if synthetic is not None else bool(top_synthetic)
    return _finalize(
        rows,
        issues,
        source_path=path,
        file_hash=file_hash,
        extracted_at=extracted_at,
        encoding="utf-8",
        synthetic=is_synthetic,
        strict=strict,
    )


def import_snapshot(
    path: str | Path,
    *,
    field_mapping: dict[str, str] | None = None,
    strict: bool = True,
    delimiter: str = ",",
    synthetic: bool | None = None,
) -> CadSnapshot:
    """CSV(UTF-8/UTF-8-SIG/CP949) 또는 JSON snapshot 을 읽어 검증한다.

    field_mapping: {snapshot 필드: 회사 export 열 이름}.
    strict: error 급 issue 가 있으면 E_INPUT_INVALID (오류 행은 삭제하지 않는다).
    synthetic: None 이면 파일의 synthetic 표시(열/키)로 판단.
    """
    p = Path(path)
    if not p.is_file():
        raise AgentError(
            "E_INPUT_INVALID", f"입력 파일을 찾을 수 없습니다: {p.name}", details={"path": str(p)}
        )
    file_hash = sha256_file(p)
    suffix = p.suffix.lower()
    if suffix == ".json":
        return _import_json(
            p, field_mapping=field_mapping, strict=strict, synthetic=synthetic, file_hash=file_hash
        )
    if suffix in (".csv", ".txt", ".tsv"):
        if suffix == ".tsv" and delimiter == ",":
            delimiter = "\t"
        return _import_csv(
            p,
            field_mapping=field_mapping,
            delimiter=delimiter,
            strict=strict,
            synthetic=synthetic,
            file_hash=file_hash,
        )
    raise AgentError(
        "E_INPUT_INVALID",
        f"지원되지 않는 snapshot 형식입니다: {p.suffix}",
        hint="CSV(.csv/.tsv) 또는 JSON(.json) 만 수입할 수 있습니다. CATIA live 추출은 cad extract 를 참고하세요.",
        details={"path": str(p)},
    )


def write_snapshot(snapshot: CadSnapshot, path: str | Path) -> Path:
    """snapshot 을 JSON 으로 원자적 저장."""
    out = Path(path)
    atomic_write_json(out, snapshot.model_dump(mode="json"))
    return out


def load_snapshot(path: str | Path) -> CadSnapshot:
    """write_snapshot 으로 저장한 JSON 을 strict 로 재로드."""
    p = Path(path)
    if not p.is_file():
        raise AgentError("E_INPUT_INVALID", f"snapshot JSON 이 없습니다: {p.name}", details={"path": str(p)})
    try:
        return validate_strict(CadSnapshot, read_json(p))
    except ValidationError as exc:
        raise AgentError(
            "E_SCHEMA_INVALID",
            f"snapshot JSON 이 schema 와 일치하지 않습니다: {p.name}",
            details={
                "errors": [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]]
            },
        ) from exc


def summarize_snapshot(snapshot: CadSnapshot) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for r in snapshot.rows:
        counts[r.geometry_status] = counts.get(r.geometry_status, 0) + 1
    return {
        "source_path": snapshot.source_path,
        "source_hash": snapshot.source_hash,
        "encoding": snapshot.encoding,
        "synthetic": snapshot.synthetic,
        "n_rows": len(snapshot.rows),
        "n_references": len({r.reference_id for r in snapshot.rows}),
        "n_assemblies": sum(1 for r in snapshot.rows if r.is_assembly),
        "revisions": snapshot.revisions(),
        "document_ids": snapshot.document_ids(),
        "geometry_status_counts": counts,
        "issues": {
            "error": sum(1 for i in snapshot.issues if i.level == "error"),
            "warning": sum(1 for i in snapshot.issues if i.level == "warning"),
            "info": sum(1 for i in snapshot.issues if i.level == "info"),
        },
    }
