"""engineering: CAD snapshot 수입, 단위, 중량, 원가, 설계안 비교.

표준 라이브러리 + pydantic 만 사용한다 (third-party 수치 라이브러리 불필요).
"""

from corp_dl_agent.engineering.cad_snapshot import (
    CadSnapshot,
    CadSnapshotRow,
    SnapshotIssue,
    import_snapshot,
    load_snapshot,
    summarize_snapshot,
    write_snapshot,
)
from corp_dl_agent.engineering.compare import (
    ComparisonRow,
    DeltaRow,
    DesignCandidate,
    DesignComparison,
    compare_designs,
    load_candidate_dir,
)
from corp_dl_agent.engineering.cost import (
    CostBreakdown,
    CostLine,
    GenericProcessRecipe,
    InjectionMoldingRecipe,
    PurchasedPartRecipe,
    Recipe,
    compute_cost,
    load_recipes,
    parse_recipe,
)
from corp_dl_agent.engineering.mass import (
    MassItem,
    MassResult,
    MassScope,
    ScopeItem,
    compute_mass,
    load_material_table,
)
from corp_dl_agent.engineering.units import (
    convert_density,
    convert_mass,
    convert_volume,
    mass_kg,
)

__all__ = [
    "CadSnapshot",
    "CadSnapshotRow",
    "SnapshotIssue",
    "import_snapshot",
    "load_snapshot",
    "summarize_snapshot",
    "write_snapshot",
    "ComparisonRow",
    "DeltaRow",
    "DesignCandidate",
    "DesignComparison",
    "compare_designs",
    "load_candidate_dir",
    "CostBreakdown",
    "CostLine",
    "GenericProcessRecipe",
    "InjectionMoldingRecipe",
    "PurchasedPartRecipe",
    "Recipe",
    "compute_cost",
    "load_recipes",
    "parse_recipe",
    "MassItem",
    "MassResult",
    "MassScope",
    "ScopeItem",
    "compute_mass",
    "load_material_table",
    "convert_density",
    "convert_mass",
    "convert_volume",
    "mass_kg",
]
