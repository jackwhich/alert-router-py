"""
路由匹配模块
"""
import re
from typing import Dict, List

from ..core.logging_config import get_logger

logger = get_logger("alert-router")

# 正则缓存，避免重复编译
_REGEX_CACHE: Dict[str, re.Pattern] = {}
_DEFAULT_RULE_WARNED = False


def _regex_search(pattern: str, text: str) -> bool:
    """带缓存的正则匹配"""
    compiled = _REGEX_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern)
        _REGEX_CACHE[pattern] = compiled
    return compiled.search(text) is not None


def match(labels: Dict[str, str], cond: Dict[str, str]) -> bool:
    """
    匹配路由条件：receiver、alertname、severity/级别 等，支持正则与中文。

    支持的正则格式：.*pattern.*、^...$、pattern1|pattern2 等；否则精确匹配。
    中文完全支持，如 severity: "严重"、severity: "critical|灾难"。
    """
    for k, v in cond.items():
        label_value = labels.get(k)
        if label_value is None:
            return False
        
        label_str = str(label_value)
        
        # 检查是否包含正则表达式特征字符
        regex_chars = ["*", "^", "$", "|", "(", ")", "[", "]", "+", "?", "{", "}"]
        has_regex_chars = any(char in v for char in regex_chars)
        
        # 检查是否为简化的正则表达式格式（.*pattern.*, .*pattern, pattern.*）
        is_simple_regex = v.startswith(".*") or v.endswith(".*")
        
        # 如果包含正则表达式特征字符或者是简化的正则格式，尝试正则匹配
        if has_regex_chars or is_simple_regex:
            try:
                # 构建正则表达式模式
                if v.startswith("^") or v.endswith("$"):
                    # 锚定匹配（已包含锚定符）
                    pattern = v
                elif v.startswith(".*") and v.endswith(".*"):
                    # 去掉首尾的 .*，使用 search
                    pattern = v[2:-2]
                elif v.startswith(".*"):
                    # 去掉开头的 .*，匹配结尾
                    pattern = v[2:] + "$"
                elif v.endswith(".*"):
                    # 去掉结尾的 .*，匹配开头
                    pattern = "^" + v[:-2]
                else:
                    # 直接使用正则表达式
                    pattern = v
                
                if not _regex_search(pattern, label_str):
                    return False
            except re.error:
                # 正则表达式错误，回退到精确匹配
                logger.warning(f"正则表达式错误: {v}，使用精确匹配")
                if labels.get(k) != v:
                    return False
        else:
            # 精确匹配
            if labels.get(k) != v:
                return False
    return True


def route(labels: Dict[str, str], config: Dict) -> List[str]:
    """
    路由告警到渠道列表
    支持多个规则叠加：默认渠道 + 匹配的特定规则渠道
    
    Args:
        labels: 告警标签字典
        config: 配置字典（包含 routing 规则）
    
    Returns:
        List[str]: 渠道名称列表（去重）
    """
    channels = set()
    default_channels = None
    matched_rules = []
    
    # 获取路由规则列表，如果不存在则返回空列表
    routing_rules = config.get("routing", [])
    if not routing_rules:
        logger.error("配置中未找到 routing 规则，告警将无法发送")
        return []

    logger.info(
        "开始路由匹配: source=%s receiver=%s alertname=%s 规则数=%d",
        labels.get("_source"),
        labels.get("_receiver"),
        labels.get("alertname"),
        len(routing_rules),
    )

    # 先收集所有匹配的规则和默认规则
    for idx, r in enumerate(routing_rules):
        if "match" in r:
            matched = match(labels, r["match"])
            if logger.isEnabledFor(10):  # DEBUG
                logger.debug(
                    "规则[%d] 匹配结果=%s 条件=%s",
                    idx,
                    matched,
                    r["match"],
                )
            if matched:
                rule_channels = r.get("send_to", [])
                channels.update(rule_channels)
                matched_rules.append(
                    {
                        "rule_index": idx,
                        "match": r["match"],
                        "send_to": rule_channels,
                    }
                )
        elif r.get("default"):
            # 默认规则：仅保留第一个，避免多条 default 覆盖
            if default_channels is None:
                default_channels = r["send_to"]
            else:
                global _DEFAULT_RULE_WARNED
                if not _DEFAULT_RULE_WARNED:
                    logger.warning("发现多个 default 规则，仅使用第一个默认规则")
                    _DEFAULT_RULE_WARNED = True
    
    if not channels:
        if default_channels:
            channels.update(default_channels)
            logger.info("未命中特定规则，使用默认规则渠道: %s", default_channels)
        else:
            logger.warning("未找到匹配规则且无默认规则，告警将无法发送")

    final_channels = sorted(list(channels))
    if matched_rules:
        logger.info("命中规则数=%d", len(matched_rules))
        for item in matched_rules:
            logger.info(
                "命中规则[%d]: match=%s -> send_to=%s",
                item["rule_index"],
                item["match"],
                item["send_to"],
            )
    logger.info("路由结果渠道: %s", final_channels)
    return final_channels
