"""
Grafana Unified Alerting Webhook 适配器

将 Grafana Unified Alerting 的 webhook payload 转换为标准告警格式
采用适配器模式（Adapter Pattern）实现格式转换

来源：Grafana Unified Alerting（Grafana 的统一告警系统）
"""
import logging
import re
from typing import Dict, Any, List

from . import build_alert_object

logger = logging.getLogger("alert-router")


def _parse_current_value(alert: Dict[str, Any]) -> str:
    """
    从 Grafana 告警中解析「当前值」。
    优先从 values.B 获取，否则从 valueString 正则解析（兼容旧版 webhook_nginx_8081 逻辑）。
    """
    try:
        values = alert.get("values")
        if values is not None and isinstance(values, dict) and "B" in values:
            return str(values["B"])
    except (AttributeError, TypeError):
        pass
    value_string = alert.get("valueString") or ""
    match = re.search(r"var='B' labels=\{.*?\} value=(\d+)", value_string)
    if match:
        return match.group(1)
    return ""


def detect(payload: Dict[str, Any]) -> bool:
    """
    检测是否为 Grafana Unified Alerting 格式
    
    识别特征：
    - 包含 "alerts" 数组
    - Grafana 特有：orgId、state、title（与 Prometheus Alertmanager 区分）
    - Grafana 的 version 为 "1"，Prometheus Alertmanager 通常为 "4"
    """
    if "alerts" not in payload:
        return False
    # Grafana Unified Alerting 必有 orgId 或 version "1"
    if "orgId" in payload:
        return True
    if payload.get("version") == "1" and "receiver" in payload:
        return True
    # 无 version 或 version 非 "4" 且像 Grafana（有 state/title）
    if payload.get("version") != "4" and "state" in payload:
        return True
    return False


def parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    解析并转换 Grafana Unified Alerting webhook payload 为标准格式
    
    Grafana Unified Alerting 格式示例:
    {
        "receiver": "webhook",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {...},
                "annotations": {...},
                "startsAt": "2024-01-01T00:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://grafana:3000/alerting/...",
                "fingerprint": "abc123"
            }
        ],
        "groupLabels": {...},
        "commonLabels": {...},
        "commonAnnotations": {...},
        "externalURL": "http://grafana:3000"
    }
    """
    alerts: List[Dict[str, Any]] = []
    if "alerts" in payload and isinstance(payload["alerts"], list):
        raw_alerts = payload["alerts"]
        logger.debug(f"Grafana Unified Alerting 收到 {len(raw_alerts)} 条原始告警")
        for alert in raw_alerts:
            # 添加来源标识到 labels，用于路由区分
            labels: Dict[str, Any] = dict(alert.get("labels") or {})
            labels["_source"] = "grafana"  # 添加来源标识

            annotations: Dict[str, Any] = dict(alert.get("annotations") or {})
            # 解析 Grafana 特有「当前值」（values.B 或 valueString），与 old_py/webhook_nginx_8081 一致
            current_value = _parse_current_value(alert)
            if current_value:
                annotations["当前值"] = current_value
                logger.debug(f"Grafana 告警解析到当前值: {current_value}")

            alerts.append(
                build_alert_object(
                    alert=alert,
                    payload=payload,
                    labels=labels,
                    annotations=annotations,
                    include_fingerprint=True,
                )
            )
    else:
        logger.warning("Grafana payload 中 alerts 字段不存在或不是列表类型")
    return alerts
