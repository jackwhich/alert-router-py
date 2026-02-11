"""
Jenkins 告警去重模块

将去重逻辑从 app 入口解耦，避免入口文件承载业务规则细节。
当前为单进程内存实现，适用于单进程部署场景。
"""
import logging
import time
from threading import RLock
from typing import Dict, Optional

logger = logging.getLogger("alert-router")

# 进程内 Jenkins 去重缓存（key -> 过期时间戳）
_JENKINS_DEDUP_CACHE: Dict[str, float] = {}
_JENKINS_DEDUP_LOCK = RLock()


def _build_dedup_key(alert: dict, labels: dict) -> Optional[str]:
    """
    生成 Jenkins 去重 key。
    优先使用 build_number 区分同一 commit 下的不同构建；
    若无 build_number，则回退 fingerprint，最后回退 commit 级别去重。
    """
    jenkins_job = labels.get("jenkins_job")
    commit_id = labels.get("check_commitID")
    if not jenkins_job or not commit_id:
        return None

    alertname = labels.get("alertname", "")
    git_branch = labels.get("gitBranch", "")
    build_number = labels.get("build_number")
    if build_number:
        return f"{alertname}|{jenkins_job}|{git_branch}|build={build_number}"

    fingerprint = alert.get("fingerprint")
    if fingerprint:
        return f"{alertname}|{jenkins_job}|{git_branch}|fp={fingerprint}"

    return f"{alertname}|{jenkins_job}|{git_branch}|commit={commit_id}"


def should_skip_jenkins_firing(alert: dict, labels: dict, alert_status: str, config: dict) -> bool:
    """
    Jenkins firing 告警去重：
    - status=firing：在去重窗口内仅首次发送，后续跳过
    - status=resolved 且 clear_on_resolved=true：清理该 key
    """
    dedup_cfg = (config or {}).get("jenkins_dedup", {}) or {}
    if not dedup_cfg.get("enabled", True):
        return False

    key = _build_dedup_key(alert, labels)
    if not key:
        return False

    ttl_seconds = int(dedup_cfg.get("ttl_seconds", 900))
    clear_on_resolved = bool(dedup_cfg.get("clear_on_resolved", True))
    now = time.time()

    with _JENKINS_DEDUP_LOCK:
        # 清理过期 key，避免缓存无限增长
        expired_keys = [k for k, exp in _JENKINS_DEDUP_CACHE.items() if exp <= now]
        if expired_keys:
            for k in expired_keys:
                _JENKINS_DEDUP_CACHE.pop(k, None)
            logger.debug(f"Jenkins 去重缓存清理了 {len(expired_keys)} 个过期 key")

        if alert_status == "resolved":
            if clear_on_resolved:
                if key in _JENKINS_DEDUP_CACHE:
                    _JENKINS_DEDUP_CACHE.pop(key, None)
                    logger.debug(f"Jenkins 去重：告警 {labels.get('alertname', 'Unknown')} resolved，已清理去重缓存 key: {key}")
            return False

        if alert_status != "firing":
            return False

        expires_at = _JENKINS_DEDUP_CACHE.get(key)
        if expires_at and expires_at > now:
            logger.debug(f"Jenkins 去重：告警 {labels.get('alertname', 'Unknown')} 命中去重窗口，跳过发送 (key: {key}, 剩余时间: {int(expires_at - now)}秒)")
            return True

        _JENKINS_DEDUP_CACHE[key] = now + max(1, ttl_seconds)
        logger.debug(f"Jenkins 去重：告警 {labels.get('alertname', 'Unknown')} 首次发送，已记录去重 key: {key} (TTL: {ttl_seconds}秒)")
        return False
