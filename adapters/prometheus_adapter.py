"""
Prometheus Alertmanager Webhook 适配器

将 Prometheus Alertmanager 的 webhook payload 转换为标准告警格式
采用适配器模式（Adapter Pattern）实现格式转换

来源：Prometheus Alertmanager（Prometheus 生态系统的告警管理器）
"""

from typing import Dict, Any, List


def detect(payload: Dict[str, Any]) -> bool:
    """
    检测是否为 Prometheus Alertmanager 格式
    
    识别特征：
    - 包含 "version" 字段且为 "4"（Grafana 用 "1"）
    - 或 包含 "alerts" + "groupKey" 且无 Grafana 特有 orgId
    """
    # Grafana 已优先在 normalizer 中识别（orgId / version "1"），此处仅识别 Alertmanager
    if "orgId" in payload:
        return False
    if payload.get("version") == "1":
        return False
    return "version" in payload or ("alerts" in payload and "groupKey" in payload)


def parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    解析并转换 Prometheus Alertmanager webhook payload 为标准格式
    
    Prometheus Alertmanager 格式示例:
    {
        "version": "4",
        "groupKey": "{}:{alertname=\"HighCPU\"}",
        "status": "firing",
        "receiver": "webhook",
        "groupLabels": {...},
        "commonLabels": {...},
        "commonAnnotations": {...},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {...},
                "annotations": {...},
                "startsAt": "2024-01-01T00:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=..."
            }
        ]
    }
    """
    alerts = []
    if "alerts" in payload and isinstance(payload["alerts"], list):
        for alert in payload["alerts"]:
            # 添加来源标识到 labels，用于路由区分
            labels = alert.get("labels", {})
            labels["_source"] = "prometheus"  # 添加来源标识
            
            alerts.append({
                "status": alert.get("status", payload.get("status", "firing")),
                "labels": labels,
                "annotations": alert.get("annotations", {}),
                "startsAt": alert.get("startsAt", ""),
                "endsAt": alert.get("endsAt", ""),
                "generatorURL": alert.get("generatorURL", payload.get("externalURL", ""))
            })
    return alerts
