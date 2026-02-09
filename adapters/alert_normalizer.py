"""
统一解析入口模块

数据源识别与解析：
- 数据源（Grafana / Prometheus）仅根据「入参 payload 的顶层字段」在此处唯一判定。
- 判定结果决定调用哪个 adapter 解析，解析时由 adapter 写入 _source，路由再按 _source 分发。

两种数据源的 payload 长什么样、用哪些字段区分，见：docs/DATA_SOURCES.md
"""

from typing import Dict, Any, List
from enum import Enum
from .prometheus_adapter import parse as parse_prometheus
from .grafana_adapter import parse as parse_grafana


class WebhookFormat(Enum):
    """Webhook 格式类型枚举（即数据源类型）"""
    PROMETHEUS_ALERTMANAGER = "prometheus_alertmanager"
    GRAFANA_UNIFIED_ALERTING = "grafana_unified_alerting"
    SINGLE_ALERT = "single_alert"
    UNKNOWN = "unknown"


def identify_data_source(payload: Dict[str, Any]) -> WebhookFormat:
    """
    仅根据 payload 顶层结构区分数据源，不依赖渠道或路由配置。

    判定规则（按优先级）：
    - Grafana Unified Alerting：顶层存在 orgId（Grafana 独有）；或 version=="1" 且存在 state 或 title（Alertmanager 为 "4"）。
    - Prometheus Alertmanager：无 orgId 且（version 存在且不为 "1"；或 有 groupKey 且 alerts）。
    - 单条告警：仅有 labels/annotations 等单条结构。
    """
    if not isinstance(payload, dict):
        return WebhookFormat.UNKNOWN
    # 1) Grafana：有 orgId 或 (version "1" 且 有 state/title)
    if "orgId" in payload:
        return WebhookFormat.GRAFANA_UNIFIED_ALERTING
    if payload.get("version") == "1" and ("state" in payload or "title" in payload):
        return WebhookFormat.GRAFANA_UNIFIED_ALERTING
    # 2) Prometheus Alertmanager：无 orgId，且 version 非 "1" 或 具备 groupKey+alerts
    if "orgId" not in payload and "alerts" in payload:
        if payload.get("version") not in (None, "1"):
            return WebhookFormat.PROMETHEUS_ALERTMANAGER
        if "groupKey" in payload:
            return WebhookFormat.PROMETHEUS_ALERTMANAGER
    # 3) 单条告警或未知
    if "labels" in payload or "annotations" in payload:
        return WebhookFormat.SINGLE_ALERT
    return WebhookFormat.UNKNOWN


def parse_single_alert(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    解析单个告警格式（兼容简单格式）
    
    单个告警格式示例:
    {
        "status": "firing",
        "labels": {...},
        "annotations": {...},
        "startsAt": "2024-01-01T00:00:00Z",
        "endsAt": "",
        "generatorURL": "..."
    }
    """
    labels = payload.get("labels", {})
    # 如果 labels 中没有 _source，则标记为 unknown（兼容格式）
    if "_source" not in labels:
        labels["_source"] = "unknown"
    
    return [{
        "status": payload.get("status", "firing"),
        "labels": labels,
        "annotations": payload.get("annotations", {}),
        "startsAt": payload.get("startsAt", ""),
        "endsAt": payload.get("endsAt", ""),
        "generatorURL": payload.get("generatorURL", "")
    }]


def normalize(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    统一解析入口：先按 payload 结构识别数据源，再调用对应解析器。
    数据源识别仅在此处根据 payload 完成，解析器负责写入 _source，路由据此分发。
    """
    format_type = identify_data_source(payload)
    
    if format_type == WebhookFormat.PROMETHEUS_ALERTMANAGER:
        return parse_prometheus(payload)
    elif format_type == WebhookFormat.GRAFANA_UNIFIED_ALERTING:
        return parse_grafana(payload)
    elif format_type == WebhookFormat.SINGLE_ALERT:
        return parse_single_alert(payload)
    else:
        # 未知格式，返回空列表
        return []
