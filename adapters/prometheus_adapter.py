"""
Prometheus Alertmanager Webhook 适配器

将 Prometheus Alertmanager 的 webhook payload 转换为标准告警格式
采用适配器模式（Adapter Pattern）实现格式转换

来源：Prometheus Alertmanager（Prometheus 生态系统的告警管理器）
"""

from typing import Dict, Any, List

from . import build_alert_object


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
    raw_alerts = payload.get("alerts") or []
    if not isinstance(raw_alerts, list):
        return []

    # 同组多条告警合并为一条发送；从各条告警汇总 replicas（与原始多副本一致），不注入 replica_count
    if len(raw_alerts) > 1 and payload.get("groupKey") and payload.get("commonLabels"):
        common_labels: Dict[str, Any] = dict(payload.get("commonLabels") or {})
        common_labels["_source"] = "prometheus"
        replicas = []
        for a in raw_alerts:
            lbl = a.get("labels") or {}
            if "replica" in lbl:
                replicas.append(lbl["replica"])
        if replicas:
            # 有 replica 才添加；单行、多空格分隔：replica: prom-01   prom-02   prom-03
            common_labels["replica"] = "   ".join(sorted(replicas))
        common_annotations: Dict[str, Any] = dict(payload.get("commonAnnotations") or {})
        first = raw_alerts[0]
        # 合并时 commonAnnotations 可能不含 summary，直接取第一条告警的 summary（与 old webhook-telegram 一致）
        first_ann = first.get("annotations") or {}
        if not common_annotations.get("summary") and first_ann.get("summary"):
            common_annotations["summary"] = first_ann["summary"]
        merged = {
            "status": payload.get("status", first.get("status", "firing")),
            "labels": common_labels,
            "annotations": common_annotations,
            "startsAt": first.get("startsAt", ""),
            "endsAt": first.get("endsAt", ""),
            "generatorURL": first.get("generatorURL", payload.get("externalURL", "")),
        }
        return [merged]

    alerts: List[Dict[str, Any]] = []
    for alert in raw_alerts:
        labels: Dict[str, Any] = dict(alert.get("labels") or {})
        labels["_source"] = "prometheus"

        annotations: Dict[str, Any] = dict(alert.get("annotations") or {})

        alerts.append(
            build_alert_object(
                alert=alert,
                payload=payload,
                labels=labels,
                annotations=annotations,
            )
        )
    return alerts
