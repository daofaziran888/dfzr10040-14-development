"""字段影响分析的数据模型（仅依赖标准库，可直接 JSON 序列化）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 证据状态：已确认来源可直接参与计算；待核来源尚未确认；已撤销来源不再可用。
EVIDENCE_CONFIRMED = "已确认"
EVIDENCE_PENDING = "待核"
EVIDENCE_REVOKED = "已撤销"
EVIDENCE_STATUSES = (EVIDENCE_CONFIRMED, EVIDENCE_PENDING, EVIDENCE_REVOKED)

# 目标依据判定：仍有独立依据 / 暂时无完整依据。
BASIS_SUPPORTED = "仍有独立依据"
BASIS_UNVERIFIED = "暂时无完整依据"

# 指标结果状态。
RESULT_PUBLISHED = "已发布"          # 上一次发布的指标（可能依赖已撤销来源）
RESULT_MATERIALIZED = "已物化"       # 重算成功并写入快照取数存储
RESULT_BLOCKED = "待核未物化"        # 存在待核输入，重算未成功
RESULT_SUPERSEDED = "已被新版本取代"  # 旧结果仍可查但不再被快照使用
RESULT_OUT_OF_SCOPE = "不在重算范围"

# 证据用途。
EVIDENCE_ROLE_OLD = "旧来源"
EVIDENCE_ROLE_NEW = "新来源"


@dataclass
class Evidence:
    """字段来源证据：带版本号与确认状态。"""

    code: str
    field_ref: str               # 证据支撑的字段（table.field）
    role: str                    # 旧来源 / 新来源
    version: str                 # 证据版本，如 EV-AMT-OLD-V1
    status: str
    detail: str
    value_hint: Any = None       # 该证据对应的原始数据值（聚合样本用）

    def is_usable(self) -> bool:
        """只有已确认证据是完整可用依据。"""
        return self.status == EVIDENCE_CONFIRMED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FieldLineageEdge:
    """字段级血缘边：上游字段 -> 下游字段。"""

    code: str
    upstream: str                # table.field
    downstream: str              # table.field
    layer: int                   # 1=源->宽表，2=宽表->指标
    transform: str
    evidence_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FactRow:
    """指标聚合入口消费的真实数据行。"""

    metric_code: str
    group: str                   # 分组维度值
    field_name: str              # 实际数据字段
    value: float
    evidence_code: str           # 该行数据来自哪个证据版本
    region: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetricAggregationEntry:
    """指标聚合入口：重算沿此入口选择实际数据字段与分组。"""

    code: str
    name: str
    target_table: str
    agg_function: str            # SUM / COUNT ...
    data_field: str              # 实际数据字段
    group_dimensions: list[str]
    time_partition: str          # 本日分区键值
    group_code: str              # 指标分组（一组本日指标）
    source_field_paths: list[str]   # 指标采用的字段路径
    published_value: float | None   # 已发布值
    published_evidence_versions: list[str]  # 发布时采用的证据版本
    storage_table: str           # 可供快照取数的结果表

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetricResult:
    """一次指标重算的结果记录，写入快照取数存储。"""

    result_id: str
    metric_code: str
    group_code: str
    time_partition: str
    status: str
    value: float | None
    field_path: str              # 采用的字段路径
    evidence_versions: list[str]  # 本次实际采用的证据版本
    pending_inputs: list[str]    # 仍在核对、阻止物化的输入
    run_id: str
    storage_table: str
    superseded_result_id: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecalcRun:
    """一次从影响视图发起的整组/选定重算。"""

    run_id: str
    group_code: str
    time_partition: str
    requested_metric_codes: list[str]
    results: list[MetricResult]
    blocked: list[str]
    untouched: list[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "group_code": self.group_code,
            "time_partition": self.time_partition,
            "requested_metric_codes": self.requested_metric_codes,
            "results": [item.to_dict() for item in self.results],
            "blocked": self.blocked,
            "untouched": self.untouched,
            "created_at": self.created_at,
        }
