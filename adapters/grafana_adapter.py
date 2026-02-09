"""
Grafana Unified Alerting Webhook 适配器

将 Grafana Unified Alerting 的 webhook payload 转换为标准告警格式
采用适配器模式（Adapter Pattern）实现格式转换

来源：Grafana Unified Alerting（Grafana 的统一告警系统）
"""

import re
from typing import Dict, Any, List


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
    - 包含 "receiver" 字段
    - 包含 "alerts" 数组
    - 不包含 "version" 字段（区别于 Prometheus Alertmanager）
    - 告警对象可能包含 "fingerprint" 字段
    """
    return (
        "receiver" in payload 
        and "alerts" in payload 
        and "version" not in payload
    )


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
    alerts = []
    if "alerts" in payload and isinstance(payload["alerts"], list):
        for alert in payload["alerts"]:
            # 添加来源标识到 labels，用于路由区分
            labels = dict(alert.get("labels") or {})
            labels["_source"] = "grafana"  # 添加来源标识

            annotations = dict(alert.get("annotations") or {})
            # 解析 Grafana 特有「当前值」（values.B 或 valueString），与 old_py/webhook_nginx_8081 一致
            current_value = _parse_current_value(alert)
            if current_value:
                annotations["当前值"] = current_value

            alerts.append({
                "status": alert.get("status", payload.get("status", "firing")),
                "labels": labels,
                "annotations": annotations,
                "startsAt": alert.get("startsAt", ""),
                "endsAt": alert.get("endsAt", ""),
                "generatorURL": alert.get("generatorURL", payload.get("externalURL", "")),
                "fingerprint": alert.get("fingerprint", ""),
            })
    return alerts
