"""
Prometheus Alertmanager Webhook 适配器

将 Prometheus Alertmanager 的 webhook payload 转换为标准告警格式
采用适配器模式（Adapter Pattern）实现格式转换

来源：Prometheus Alertmanager（Prometheus 生态系统的告警管理器）
"""
import logging
import re
from typing import Dict, Any, List

from . import build_alert_object

logger = logging.getLogger("alert-router")


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


def _build_entity_values(raw_alerts: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    构建实体到值的映射，支持多种告警类型
    返回格式: {"pod:pod-name": "32.96%", "instance:10.8.64.72:9100": "50%", "service_name:my-service": "10%"}
    
    支持的实体类型（按优先级）：
    - pod: Kubernetes Pod
    - instance: 节点实例（Node Exporter, Nginx等）
    - service_name: RPC服务名
    - consumergroup/topic: Kafka消费组和主题
    - jenkins_job: Jenkins任务
    - device: 设备名（磁盘等）
    - container: 容器名
    
    注意：同一个实体可能出现在多个告警中，取第一个值
    """
    entity_values: Dict[str, str] = {}
    
    # 定义实体标签的优先级顺序（按常见程度）
    entity_labels = [
        "pod",           # Kubernetes Pod
        "instance",      # Node Exporter, Nginx等节点实例
        "service_name",  # RPC服务名
        "consumergroup", # Kafka消费组
        "topic",         # Kafka主题
        "jenkins_job",   # Jenkins任务
        "device",        # 设备名（磁盘等）
        "container",     # 容器名
        "namespace",     # 命名空间
        "name",          # 通用名称（Kafka等）
        "status",        # 状态码（如 Nginx status）
    ]
    
    for alert in raw_alerts:
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        
        # 按优先级查找实体标签
        entity_key = None
        entity_value = None
        
        for label_key in entity_labels:
            if label_key in labels:
                entity_key = label_key
                entity_value = labels[label_key]
                break
        
        if not entity_key or not entity_value:
            continue
        
        # 构建唯一键
        unique_key = f"{entity_key}:{entity_value}"
        
        # 如果这个实体已经有值了，跳过（避免覆盖）
        if unique_key in entity_values:
            continue
        
        # 从 summary 中提取值
        summary = annotations.get("summary", "")
        value = _extract_value_from_summary(summary)
        
        if not value:
            # 如果没有从 summary 提取到，尝试从 description 或其他字段
            description = annotations.get("description", "")
            value = _extract_value_from_summary(description)
        
        # 存储实体的值
        if value:
            entity_values[unique_key] = value
    
    return entity_values


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
        logger.warning("Prometheus payload 中 alerts 字段不是列表类型")
        return []

    logger.debug(f"Prometheus Alertmanager 收到 {len(raw_alerts)} 条原始告警")

    # 同组多条告警合并为一条发送；从各条告警汇总各种实体类型（pod、instance、service_name等）
    if len(raw_alerts) > 1 and payload.get("groupKey"):
        receiver_name = payload.get("receiver")
        first_lbl = (raw_alerts[0].get("labels") or {})
        
        # 定义需要单独收集的标签（这些标签在不同告警中可能有不同值）
        # 这些标签会被收集到列表中，而不是作为共同标签
        collectable_labels = [
            "replica",        # Kubernetes副本
            "pod",            # Kubernetes Pod
            "instance",       # 节点实例（Node Exporter, Nginx等）
            "service_name",   # RPC服务名
            "consumergroup",  # Kafka消费组
            "topic",          # Kafka主题
            "jenkins_job",    # Jenkins任务
            "device",         # 设备名（磁盘等）
            "container",      # 容器名
            "build_number",   # Jenkins构建号
            "status",         # 状态码（如 Nginx status）
        ]
        
        # 共同 label：仅保留在所有条中取值相同的键（可收集的标签用下面的列表）
        common_labels: Dict[str, Any] = {}
        for k, v in first_lbl.items():
            if k in collectable_labels:
                continue
            if all((a.get("labels") or {}).get(k) == v for a in raw_alerts):
                common_labels[k] = v
        
        # 收集各种实体类型的列表
        collected_entities: Dict[str, List[str]] = {}
        for label_key in collectable_labels:
            values = []
            for a in raw_alerts:
                lbl = a.get("labels") or {}
                if label_key in lbl:
                    value = lbl[label_key]
                    if value not in values:  # 去重
                        values.append(value)
            if values:
                collected_entities[label_key] = values
        
        # raw_labels：用于展示原始 labels（不包含内部字段），多值标签以列表形式保留
        raw_labels: Dict[str, Any] = dict(payload.get("commonLabels") or {})
        for label_key, values in collected_entities.items():
            if values:
                raw_labels[label_key] = values if len(values) > 1 else values[0]
        
        # 提取每个实体的值（支持pod、instance、service_name等多种类型）
        entity_values = _build_entity_values(raw_alerts)
        if entity_values:
            logger.debug(f"Prometheus 合并告警：提取到 {len(entity_values)} 个实体的值: {list(entity_values.keys())}")
        
        common_annotations: Dict[str, Any] = dict(payload.get("commonAnnotations") or {})
        first = raw_alerts[0]
        first_ann = first.get("annotations") or {}
        if not common_annotations.get("summary") and first_ann.get("summary"):
            common_annotations["summary"] = first_ann["summary"]
        merged = {
            "status": payload.get("status", first.get("status", "firing")),
            "labels": raw_labels,
            "annotations": common_annotations,
            "startsAt": first.get("startsAt", ""),
            "endsAt": first.get("endsAt", ""),
            "generatorURL": first.get("generatorURL", payload.get("externalURL", "")),
            "_source": "prometheus",
        }
        if receiver_name:
            merged["_receiver"] = receiver_name
        logger.info(f"Prometheus 将 {len(raw_alerts)} 条告警合并为 1 条发送 (groupKey: {payload.get('groupKey')})")
        return [merged]

    alerts: List[Dict[str, Any]] = []
    receiver_name = payload.get("receiver")
    logger.debug(f"Prometheus 单独处理 {len(raw_alerts)} 条告警 (receiver: {receiver_name})")
    for alert in raw_alerts:
        raw_labels = dict(alert.get("labels") or {})
        labels: Dict[str, Any] = dict(raw_labels)
        annotations: Dict[str, Any] = dict(alert.get("annotations") or {})
        alert_obj = build_alert_object(
            alert=alert,
            payload=payload,
            labels=labels,
            annotations=annotations,
        )
        alert_obj["_source"] = "prometheus"
        if receiver_name:
            alert_obj["_receiver"] = receiver_name
        alerts.append(alert_obj)
    return alerts
