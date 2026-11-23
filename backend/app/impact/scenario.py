"""题目场景的初始数据：本日「金额 / 税额」一组指标与同表无关库存。

字段链路（最多两层）：

    ods.amount_raw   --L1--> dwd_order.amount_fld --L2--> ads_daily.amount_metric
    ods.tax_raw      --L1--> dwd_order.tax_fld    --L2--> ads_daily.tax_metric
    ods.inv_qty      --L1--> dwd_inv.qty_fld      --L2--> ads_daily.inv_metric（同表无关）

证据状态对应业务叙述：
- 金额旧来源证据已撤销，金额新来源证据待核（旧撤销与新确认存在冲突，需人工确认）；
- 税额旧证据（结果 5）仍可用，税额另有独立的已确认更正证据（结果 7）；
- 库存证据始终已确认，且不属于金额/税额重算范围。
"""
from __future__ import annotations

from app.impact.models import (
    EVIDENCE_CONFIRMED,
    EVIDENCE_PENDING,
    EVIDENCE_REVOKED,
    EVIDENCE_ROLE_NEW,
    EVIDENCE_ROLE_OLD,
    Evidence,
    FactRow,
    FieldLineageEdge,
    MetricAggregationEntry,
)

PARTITION = "20261123"
GROUP_TODAY = "本日财税指标组"


def build_scenario() -> dict[str, object]:
    # ---- 证据台账 ----
    evidences = [
        Evidence(
            code="EV-AMT-OLD", field_ref="ods.amount_raw", role=EVIDENCE_ROLE_OLD,
            version="EV-AMT-OLD-V1", status=EVIDENCE_REVOKED,
            detail="金额旧来源，当日已撤销，不再作为金额字段依据",
            value_hint=20.0,
        ),
        Evidence(
            code="EV-AMT-NEW", field_ref="ods.amount_raw", role=EVIDENCE_ROLE_NEW,
            version="EV-AMT-NEW-V1", status=EVIDENCE_PENDING,
            detail="金额新来源，与旧来源撤销存在确认冲突，尚在核对",
            value_hint=30.0,
        ),
        Evidence(
            code="EV-TAX-OLD", field_ref="ods.tax_raw", role=EVIDENCE_ROLE_OLD,
            version="EV-TAX-OLD-V1", status=EVIDENCE_CONFIRMED,
            detail="税额旧证据，结果 5，仍然可用",
            value_hint=5.0,
        ),
        Evidence(
            code="EV-TAX-NEW", field_ref="ods.tax_raw", role=EVIDENCE_ROLE_NEW,
            version="EV-TAX-NEW-V1", status=EVIDENCE_CONFIRMED,
            detail="税额独立更正证据，结果 7，已确认",
            value_hint=7.0,
        ),
        Evidence(
            code="EV-INV-OLD", field_ref="ods.inv_qty", role=EVIDENCE_ROLE_OLD,
            version="EV-INV-OLD-V1", status=EVIDENCE_CONFIRMED,
            detail="库存数量证据，与金额/税额变更无关",
            value_hint=100.0,
        ),
    ]

    # ---- 字段血缘边（L1 源->宽表，L2 宽表->指标）----
    edges = [
        FieldLineageEdge("LINE-AMT-L1", "ods.amount_raw", "dwd_order.amount_fld",
                         1, "direct_map", ["EV-AMT-OLD", "EV-AMT-NEW"]),
        FieldLineageEdge("LINE-AMT-L2", "dwd_order.amount_fld", "ads_daily.amount_metric",
                         2, "sum_agg", ["EV-AMT-OLD", "EV-AMT-NEW"]),
        FieldLineageEdge("LINE-TAX-L1", "ods.tax_raw", "dwd_order.tax_fld",
                         1, "direct_map", ["EV-TAX-OLD", "EV-TAX-NEW"]),
        FieldLineageEdge("LINE-TAX-L2", "dwd_order.tax_fld", "ads_daily.tax_metric",
                         2, "sum_agg", ["EV-TAX-OLD", "EV-TAX-NEW"]),
        FieldLineageEdge("LINE-INV-L1", "ods.inv_qty", "dwd_inv.qty_fld",
                         1, "direct_map", ["EV-INV-OLD"]),
        FieldLineageEdge("LINE-INV-L2", "dwd_inv.qty_fld", "ads_daily.inv_metric",
                         2, "sum_agg", ["EV-INV-OLD"]),
    ]

    # ---- 指标聚合入口（沿此选择实际数据字段与分组）----
    metrics = [
        MetricAggregationEntry(
            code="METR-AMT", name="本日金额", target_table="ads_daily",
            agg_function="SUM", data_field="amount_fld",
            group_dimensions=["region"], time_partition=PARTITION,
            group_code=GROUP_TODAY,
            source_field_paths=[
                "ods.amount_raw -> dwd_order.amount_fld -> ads_daily.amount_metric",
            ],
            published_value=20.0,
            published_evidence_versions=["EV-AMT-OLD-V1"],
            storage_table="result_ads_daily",
        ),
        MetricAggregationEntry(
            code="METR-TAX", name="本日税额", target_table="ads_daily",
            agg_function="SUM", data_field="tax_fld",
            group_dimensions=["region"], time_partition=PARTITION,
            group_code=GROUP_TODAY,
            source_field_paths=[
                "ods.tax_raw -> dwd_order.tax_fld -> ads_daily.tax_metric",
            ],
            published_value=5.0,
            published_evidence_versions=["EV-TAX-OLD-V1"],
            storage_table="result_ads_daily",
        ),
        MetricAggregationEntry(
            code="METR-INV", name="本日库存", target_table="ads_daily",
            agg_function="SUM", data_field="qty_fld",
            group_dimensions=["warehouse"], time_partition=PARTITION,
            group_code="库存指标组",
            source_field_paths=[
                "ods.inv_qty -> dwd_inv.qty_fld -> ads_daily.inv_metric",
            ],
            published_value=100.0,
            published_evidence_versions=["EV-INV-OLD-V1"],
            storage_table="result_ads_daily",
        ),
    ]

    # ---- 实际数据行：按指标、分组与证据版本存放 ----
    facts = [
        # 金额：旧来源两行（已撤销），新来源两行（待核，合计 30）
        FactRow("METR-AMT", "华东", "amount_fld", 12.0, "EV-AMT-OLD", "华东"),
        FactRow("METR-AMT", "华北", "amount_fld", 8.0, "EV-AMT-OLD", "华北"),
        FactRow("METR-AMT", "华东", "amount_fld", 18.0, "EV-AMT-NEW", "华东"),
        FactRow("METR-AMT", "华北", "amount_fld", 12.0, "EV-AMT-NEW", "华北"),
        # 税额：旧证据 5（仍可用），独立新证据 7（已确认）
        FactRow("METR-TAX", "华东", "tax_fld", 3.0, "EV-TAX-OLD", "华东"),
        FactRow("METR-TAX", "华北", "tax_fld", 2.0, "EV-TAX-OLD", "华北"),
        FactRow("METR-TAX", "华东", "tax_fld", 4.0, "EV-TAX-NEW", "华东"),
        FactRow("METR-TAX", "华北", "tax_fld", 3.0, "EV-TAX-NEW", "华北"),
        # 库存：与金额/税额同表但无关
        FactRow("METR-INV", "一号仓", "qty_fld", 60.0, "EV-INV-OLD", ""),
        FactRow("METR-INV", "二号仓", "qty_fld", 40.0, "EV-INV-OLD", ""),
    ]

    return {
        "evidences": evidences,
        "edges": edges,
        "metrics": metrics,
        "facts": facts,
        "roots": ["ods.amount_raw", "ods.tax_raw"],
    }
