"""
Prometheus Alertmanager Webhook 适配器

将 Prometheus Alertmanager 的 webhook payload 转换为标准告警格式
采用适配器模式（Adapter Pattern）实现格式转换

来源：Prometheus Alertmanager（Prometheus 生态系统的告警管理器）
"""

import re
from typing import Dict, Any, List

from . import build_alert_object


def _extract_value_from_summary(summary: str) -> str:
    """
    从 summary 中提取当前值
    支持格式：
    - "nginx 1分钟内状态码5XX大于50|当前值：325"
    - "CPU usage is above 80%|当前值：85%"
    - "当前值：123"
    """
    if not summary:
        return ""
    # 尝试匹配 "当前值：XXX" 或 "当前值: XXX"
    match = re.search(r'当前值[：:]\s*([^\s|]+)', summary)
    if match:
        return match.group(1).strip()
    return ""


def _build_pod_values(raw_alerts: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    构建 pod 到值的映射
    返回格式: {"pod-name": "32.96%"}
    注意：同一个 pod 可能出现在多个告警中，取第一个值
    """
    pod_values: Dict[str, str] = {}
    for alert in raw_alerts:
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        
        # 提取 pod
        pod = labels.get("pod")
        if not pod:
            continue
        
        # 如果这个 pod 已经有值了，跳过（避免覆盖）
        if f"pod:{pod}" in pod_values:
            continue
        
        # 从 summary 中提取值
        summary = annotations.get("summary", "")
        value = _extract_value_from_summary(summary)
        
        if not value:
            # 如果没有从 summary 提取到，尝试从 description 或其他字段
            description = annotations.get("description", "")
            value = _extract_value_from_summary(description)
        
        # 存储 pod 的值
        if value:
            pod_values[f"pod:{pod}"] = value
    
    return pod_values


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

    # 同组多条告警合并为一条发送；从各条告警汇总 replicas 与 pods，与原始多副本/多 pod 一致
    if len(raw_alerts) > 1 and payload.get("groupKey"):
        receiver_name = payload.get("receiver")
        first_lbl = (raw_alerts[0].get("labels") or {})
        # 共同 label：仅保留在所有条中取值相同的键（pod/replica 用下面的列表）
        common_labels: Dict[str, Any] = {}
        for k, v in first_lbl.items():
            if k in ("replica", "pod"):
                continue
            if all((a.get("labels") or {}).get(k) == v for a in raw_alerts):
                common_labels[k] = v
        replicas = []
        pods = []
        for a in raw_alerts:
            lbl = a.get("labels") or {}
            if "replica" in lbl:
                replicas.append(lbl["replica"])
            if "pod" in lbl:
                pods.append(lbl["pod"])
        common_labels["_source"] = "prometheus"
        if receiver_name:
            common_labels["_receiver"] = receiver_name
        if replicas:
            common_labels["replicas"] = replicas
        if pods:
            common_labels["pods"] = pods
        
        # 提取每个 pod/replica 的值
        pod_values = _build_pod_values(raw_alerts)
        if pod_values:
            common_labels["_pod_values"] = pod_values
        
        common_annotations: Dict[str, Any] = dict(payload.get("commonAnnotations") or {})
        first = raw_alerts[0]
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
    receiver_name = payload.get("receiver")
    for alert in raw_alerts:
        labels: Dict[str, Any] = dict(alert.get("labels") or {})
        labels["_source"] = "prometheus"
        if receiver_name:
            labels["_receiver"] = receiver_name
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
