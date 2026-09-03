"""
Jenkins 告警去重模块

当前为单进程内存实现，适用于单进程部署场景。
"""
import logging
from typing import Optional

from .ttl_dedup import TtlDedupCache

logger = logging.getLogger("alert-router")

_CACHE = TtlDedupCache("Jenkins")


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
    return _CACHE.should_skip(
        _build_dedup_key(alert, labels),
        alert_status,
        enabled=bool(dedup_cfg.get("enabled", True)),
        ttl_seconds=int(dedup_cfg.get("ttl_seconds", 900)),
        clear_on_resolved=bool(dedup_cfg.get("clear_on_resolved", True)),
        skip_non_firing=True,
    )
