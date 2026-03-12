"""
统一解析入口模块

- 判断：仅根据 payload 顶层 version（"1"=Grafana，"4"=Prometheus）与是否有 alerts 判定来源。
- 解析：adapter 只写 _receiver、labels 等；normalizer 按判断结果统一写入 _source。
- 路由：按 _receiver、alertname、severity 等匹配，见 config 与 README「路由与流程详解」。
"""
import logging
from typing import Dict, Any, List
from enum import Enum
from .prometheus_adapter import parse as parse_prometheus
from .grafana_adapter import parse as parse_grafana

logger = logging.getLogger("alert-router")


class WebhookFormat(Enum):
    """Webhook 格式类型枚举（即数据源类型）"""
    PROMETHEUS_ALERTMANAGER = "prometheus_alertmanager"
    GRAFANA_UNIFIED_ALERTING = "grafana_unified_alerting"
    SINGLE_ALERT = "single_alert"
    UNKNOWN = "unknown"


def identify_data_source(payload: Dict[str, Any]) -> WebhookFormat:
    """
    只判断是哪个软件发来的：version "1" = Grafana，version "4" = Prometheus。
    需含 alerts 数组才视为有效 webhook；其余交给路由按 receiver/alertname/severity 匹配。
    """
    if not isinstance(payload, dict):
        return WebhookFormat.UNKNOWN
    version = payload.get("version")
    has_alerts = "alerts" in payload and isinstance(payload.get("alerts"), list)
    if not has_alerts:
        if "labels" in payload or "annotations" in payload:
            return WebhookFormat.SINGLE_ALERT
        return WebhookFormat.UNKNOWN
    if version == "1":
        return WebhookFormat.GRAFANA_UNIFIED_ALERTING
    if version == "4":
        return WebhookFormat.PROMETHEUS_ALERTMANAGER
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
    labels = payload.get("labels", {}) or {}
    
    return [{
        "status": payload.get("status", "firing"),
        "labels": labels,
        "annotations": payload.get("annotations", {}),
        "startsAt": payload.get("startsAt", ""),
        "endsAt": payload.get("endsAt", ""),
        "generatorURL": payload.get("generatorURL", ""),
        "_source": "unknown",
    }]


def normalize(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    统一解析入口：先判断来源（version 1/4），再调用对应 adapter 解析，最后由 normalizer 写入 _source。
    """
    format_type = identify_data_source(payload)
    logger.debug(f"识别数据源类型: {format_type.value}")
    
    if format_type == WebhookFormat.PROMETHEUS_ALERTMANAGER:
        alerts = parse_prometheus(payload)
        for a in alerts:
            a["_source"] = "prometheus"
        logger.info(f"Prometheus Alertmanager 解析完成，共 {len(alerts)} 条告警")
        return alerts
    elif format_type == WebhookFormat.GRAFANA_UNIFIED_ALERTING:
        alerts = parse_grafana(payload)
        for a in alerts:
            a["_source"] = "grafana"
        logger.info(f"Grafana Unified Alerting 解析完成，共 {len(alerts)} 条告警")
        return alerts
    elif format_type == WebhookFormat.SINGLE_ALERT:
        alerts = parse_single_alert(payload)
        logger.info(f"单条告警格式解析完成，共 {len(alerts)} 条告警")
        return alerts
    else:
        # 未知格式，返回空列表
        logger.warning(f"无法识别的数据源格式，payload 顶层字段: {list(payload.keys()) if isinstance(payload, dict) else '非字典类型'}")
        return []
