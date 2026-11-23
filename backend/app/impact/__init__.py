"""字段影响分析与指标重算领域。

围绕「字段血缘 + 证据版本 + 指标聚合入口 + 快照结果」组织：

- 字段血缘边描述源字段到宽表字段、再到指标字段的分层路径；
- 证据（Evidence）承载字段来源的版本与状态（已确认 / 待核 / 已撤销），
  用于判断下游目标是否仍有独立依据；
- 指标聚合入口记录真实计算所需的数据字段、分组维度与重算范围；
- 重算结果写入可供快照取数的结果存储，快照只读取已物化的结果。
"""
from __future__ import annotations

from app.impact.engine import ImpactEngine, impact_engine
from app.impact.models import (
    Evidence,
    FactRow,
    FieldLineageEdge,
    MetricAggregationEntry,
    MetricResult,
    RecalcRun,
)
from app.impact.scenario import build_scenario

__all__ = [
    "ImpactEngine",
    "impact_engine",
    "Evidence",
    "FactRow",
    "FieldLineageEdge",
    "MetricAggregationEntry",
    "MetricResult",
    "RecalcRun",
    "build_scenario",
]
