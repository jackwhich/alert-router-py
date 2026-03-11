"""
Grafana 告警去重模块

同一告警（相同 fingerprint + status）在短时间窗口内只发送一次，避免：
- Grafana 多条通知策略/联系点指向同一 webhook 导致重复推送
- 同一 payload 中重复的告警条目
"""
import hashlib
import logging
import time
from threading import RLock
from typing import Dict, Optional

logger = logging.getLogger("alert-router")

# 进程内 Grafana 去重缓存（key -> 过期时间戳）
_GRAFANA_DEDUP_CACHE: Dict[str, float] = {}
_GRAFANA_DEDUP_LOCK = RLock()


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
    # 用一组能区分“同一条告警”的 label 做哈希，避免不同告警被误去重
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
    if not dedup_cfg.get("enabled", True):
        return False

    key = _build_dedup_key(alert, alert_status)
    if not key:
        return False

    ttl_seconds = int(dedup_cfg.get("ttl_seconds", 90))
    clear_on_resolved = bool(dedup_cfg.get("clear_on_resolved", True))
    now = time.time()

    with _GRAFANA_DEDUP_LOCK:
        expired_keys = [k for k, exp in _GRAFANA_DEDUP_CACHE.items() if exp <= now]
        if expired_keys:
            for k in expired_keys:
                _GRAFANA_DEDUP_CACHE.pop(k, None)
            logger.debug(f"Grafana 去重缓存清理了 {len(expired_keys)} 个过期 key")

        if alert_status in ("resolved", "ok"):
            if clear_on_resolved:
                if key in _GRAFANA_DEDUP_CACHE:
                    _GRAFANA_DEDUP_CACHE.pop(key, None)
                    logger.debug("Grafana 去重：resolved，已清理去重缓存 key: %s", key)
            return False

        expires_at = _GRAFANA_DEDUP_CACHE.get(key)
        if expires_at and expires_at > now:
            logger.info(
                "Grafana 去重：同一条告警在窗口内已发送过，跳过 (key: %s, 剩余: %ds)",
                key,
                int(expires_at - now),
            )
            return True

        _GRAFANA_DEDUP_CACHE[key] = now + max(1, ttl_seconds)
        logger.debug("Grafana 去重：首次发送，已记录 key: %s (TTL: %ds)", key, ttl_seconds)
        return False
