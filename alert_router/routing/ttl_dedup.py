"""
进程内 TTL 去重缓存。

Jenkins / Grafana 去重共用加锁、过期扫描与 resolved 清理，只在 key 策略上分叉。
当前为单进程内存实现，多 worker 之间不共享。
"""
import logging
import time
from threading import RLock
from typing import Dict, Optional

logger = logging.getLogger("alert-router")

RESOLVED_STATUSES = frozenset({"resolved", "ok"})


def is_resolved_status(alert_status: str) -> bool:
    return (alert_status or "").lower() in RESOLVED_STATUSES


class TtlDedupCache:
    """key -> 过期时间戳 的进程内去重缓存。"""

    def __init__(self, name: str):
        self._name = name
        self._cache: Dict[str, float] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def should_skip(
        self,
        key: Optional[str],
        alert_status: str,
        *,
        enabled: bool,
        ttl_seconds: int,
        clear_on_resolved: bool,
        skip_non_firing: bool = False,
    ) -> bool:
        """
        同一 key 在 TTL 内只放行第一次。

        Args:
            skip_non_firing: True 时，非 firing 且非 resolved 的状态直接放行（Jenkins）。
        """
        if not enabled or not key:
            return False

        status = (alert_status or "").lower()
        now = time.time()
        ttl_seconds = max(1, int(ttl_seconds))

        with self._lock:
            expired_keys = [k for k, exp in self._cache.items() if exp <= now]
            if expired_keys:
                for k in expired_keys:
                    self._cache.pop(k, None)
                logger.debug("%s 去重缓存清理了 %s 个过期 key", self._name, len(expired_keys))

            if is_resolved_status(status):
                if clear_on_resolved and key in self._cache:
                    self._cache.pop(key, None)
                    logger.debug("%s 去重：resolved，已清理 key: %s", self._name, key)
                return False

            if skip_non_firing and status != "firing":
                return False

            expires_at = self._cache.get(key)
            if expires_at and expires_at > now:
                logger.debug(
                    "%s 去重：命中窗口，跳过 (key: %s, 剩余: %ds)",
                    self._name,
                    key,
                    int(expires_at - now),
                )
                return True

            self._cache[key] = now + ttl_seconds
            logger.debug("%s 去重：首次发送，已记录 key: %s (TTL: %ds)", self._name, key, ttl_seconds)
            return False
