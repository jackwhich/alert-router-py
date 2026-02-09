"""
统一解析入口模块

自动识别并解析不同格式的 webhook payload，调用对应的解析器
"""

from typing import Dict, Any, List
from enum import Enum
from .prometheus_adapter import detect as detect_prometheus, parse as parse_prometheus
from .grafana_adapter import detect as detect_grafana, parse as parse_grafana


class WebhookFormat(Enum):
    """Webhook 格式类型枚举"""
    PROMETHEUS_ALERTMANAGER = "prometheus_alertmanager"
    GRAFANA_UNIFIED_ALERTING = "grafana_unified_alerting"
    SINGLE_ALERT = "single_alert"
    UNKNOWN = "unknown"


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


def detect_format(payload: Dict[str, Any]) -> WebhookFormat:
    """
    检测 webhook payload 的格式类型
    """
    if detect_prometheus(payload):
        return WebhookFormat.PROMETHEUS_ALERTMANAGER
    elif detect_grafana(payload):
        return WebhookFormat.GRAFANA_UNIFIED_ALERTING
    elif "labels" in payload or "annotations" in payload:
        return WebhookFormat.SINGLE_ALERT
    else:
        return WebhookFormat.UNKNOWN


def normalize(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    统一解析入口：自动识别并解析不同格式的 webhook payload
    
    支持的格式：
    1. Prometheus Alertmanager 格式 → 调用 prometheus_adapter.parse()
    2. Grafana Unified Alerting 格式 → 调用 grafana_adapter.parse()
    3. 单个告警格式（兼容格式） → 本地解析
    
    返回：标准化后的告警列表
    """
    format_type = detect_format(payload)
    
    if format_type == WebhookFormat.PROMETHEUS_ALERTMANAGER:
        return parse_prometheus(payload)
    elif format_type == WebhookFormat.GRAFANA_UNIFIED_ALERTING:
        return parse_grafana(payload)
    elif format_type == WebhookFormat.SINGLE_ALERT:
        return parse_single_alert(payload)
    else:
        # 未知格式，返回空列表
        return []
