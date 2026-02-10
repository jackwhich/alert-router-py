"""
告警适配器模块
"""
from typing import Dict, Any

from .alert_normalizer import normalize


def build_alert_object(
    alert: Dict[str, Any],
    payload: Dict[str, Any],
    labels: Dict[str, Any],
    annotations: Dict[str, Any],
    include_fingerprint: bool = False,
) -> Dict[str, Any]:
    """
    构建标准告警对象的公共工厂函数。

    不同 adapter 在各自模块中准备好 labels/annotations 等上下文，
    再调用本函数统一生成标准告警结构，避免重复字段拼装逻辑。
    """
    base = {
        "status": alert.get("status", payload.get("status", "firing")),
        "labels": labels,
        "annotations": annotations,
        "startsAt": alert.get("startsAt", ""),
        "endsAt": alert.get("endsAt", ""),
        "generatorURL": alert.get("generatorURL", payload.get("externalURL", "")),
    }
    if include_fingerprint:
        base["fingerprint"] = alert.get("fingerprint", "")
    return base


__all__ = ["normalize", "build_alert_object"]
