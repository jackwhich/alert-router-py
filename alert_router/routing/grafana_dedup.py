"""
Grafana 告警去重模块

同一告警（相同 fingerprint + status）在短时间窗口内只发送一次，避免：
- Grafana 多条通知策略/联系点指向同一 webhook 导致重复推送
- 同一 payload 中重复的告警条目
"""
import hashlib
import logging
from typing import Optional

from .ttl_dedup import TtlDedupCache

logger = logging.getLogger("alert-router")

_CACHE = TtlDedupCache("Grafana")


def _build_dedup_key(alert: dict, alert_status: str) -> Optional[str]:
    """
    生成 Grafana 去重 key。
    优先使用 fingerprint + status；无 fingerprint 时用 alertname + 关键 labels 的稳定哈希。
    """
    fp = alert.get("fingerprint")
    if fp:
        return f"grafana|{fp}|{alert_status}"

    labels = alert.get("labels") or {}
    alertname = labels.get("alertname", "")
    if not alertname:
        return None
    parts = [alertname, alert_status]
    for k in ("grafana_folder", "nginx-alert", "service_name.keyword", "uri.keyword", "status"):
        if k in labels:
            parts.append(f"{k}={labels[k]}")
    raw = "|".join(parts)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"grafana|no_fp|{h}|{alert_status}"


def should_skip_grafana_duplicate(
    alert: dict,
    alert_status: str,
    config: dict,
) -> bool:
    """
    Grafana 告警去重：
    - 相同 fingerprint + status 在 ttl_seconds 内只发送第一次，后续跳过
    - resolved 后若 clear_on_resolved=true 则清理 key，下次 firing 会再发
    """
    dedup_cfg = (config or {}).get("grafana_dedup", {}) or {}
    return _CACHE.should_skip(
        _build_dedup_key(alert, alert_status),
        alert_status,
        enabled=bool(dedup_cfg.get("enabled", True)),
        ttl_seconds=int(dedup_cfg.get("ttl_seconds", 90)),
        clear_on_resolved=bool(dedup_cfg.get("clear_on_resolved", True)),
        skip_non_firing=False,
    )
