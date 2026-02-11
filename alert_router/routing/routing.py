"""
路由匹配模块
"""
import re
from typing import Dict, List

from ..core.logging_config import get_logger

logger = get_logger("alert-router")

# 正则缓存，避免重复编译
_REGEX_CACHE: Dict[str, re.Pattern] = {}


def _regex_search(pattern: str, text: str) -> bool:
    """带缓存的正则匹配"""
    compiled = _REGEX_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern)
        _REGEX_CACHE[pattern] = compiled
    return compiled.search(text) is not None


def match(labels: Dict[str, str], cond: Dict[str, str]) -> bool:
    """
    匹配路由条件，支持正则表达式
    
    支持的正则格式：
    1. 完整正则表达式：以 ".*" 开头和结尾，如 ".*pattern.*"
    2. 开头匹配：以 ".*" 开头，如 ".*pattern"
    3. 结尾匹配：以 ".*" 结尾，如 "pattern.*"
    4. Alertmanager 风格：直接使用正则，如 "Jenkins.*|jenkins.*"（自动识别）
    5. 精确匹配：普通字符串
    
    Args:
        labels: 告警标签字典
        cond: 匹配条件字典
    
    Returns:
        bool: 是否匹配
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
    
    # 获取路由规则列表，如果不存在则返回空列表
    routing_rules = config.get("routing", [])
    if not routing_rules:
        logger.error("配置中未找到 routing 规则，告警将无法发送")
        return []
    
    # 先收集所有匹配的规则和默认规则
    for r in routing_rules:
        if "match" in r and match(labels, r["match"]):
            # 匹配的规则：添加到渠道集合
            channels.update(r["send_to"])
        elif r.get("default"):
            # 默认规则：仅保留第一个，避免多条 default 覆盖
            if default_channels is None:
                default_channels = r["send_to"]
            else:
                logger.warning("发现多个 default 规则，仅使用第一个默认规则")
    
    # 默认渠道：仅当「完全没匹配到任何规则」时使用（兜底）
    # 有匹配的规则时，不再叠加 default，由各 match 规则自行包含「默认渠道」
    if not channels:
        if default_channels:
            channels.update(default_channels)
        else:
            logger.warning("未找到匹配规则且无默认规则，告警将无法发送")
    
    return list(channels)
