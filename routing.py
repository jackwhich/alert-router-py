"""
路由匹配模块
"""
import re
from typing import Dict, List
from logging_config import get_logger

logger = get_logger("alert-router")


def match(labels: Dict[str, str], cond: Dict[str, str]) -> bool:
    """
    匹配路由条件，支持正则表达式
    
    支持的正则格式：
    1. 完整正则表达式：以 '.*' 开头和结尾，如 '.*pattern.*'
    2. 开头匹配：以 '.*' 开头，如 '.*pattern'
    3. 结尾匹配：以 '.*' 结尾，如 'pattern.*'
    4. Alertmanager 风格：直接使用正则，如 'Jenkins.*|jenkins.*'（自动识别）
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
        if any(char in v for char in ['*', '^', '$', '|', '(', ')', '[', ']', '+', '?', '{', '}']):
            try:
                # 尝试作为正则表达式匹配
                # 如果包含 ^ 或 $，则使用 match 或 fullmatch
                if v.startswith("^") or v.endswith("$"):
                    # 锚定匹配
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
                
                if not re.search(pattern, label_str):
                    return False
            except re.error:
                # 正则表达式错误，回退到精确匹配
                logger.warning(f"正则表达式错误: {v}，使用精确匹配")
                if labels.get(k) != v:
                    return False
        elif v.startswith(".*") and v.endswith(".*"):
            # 正则表达式匹配：.*pattern.*
            pattern = v[2:-2]  # 去掉首尾的 .*
            if not re.search(pattern, label_str):
                return False
        elif v.startswith(".*"):
            # 正则表达式匹配：.*pattern（匹配结尾）
            pattern = v[2:]  # 去掉开头的 .*
            if not re.search(pattern + "$", label_str):
                return False
        elif v.endswith(".*"):
            # 正则表达式匹配：pattern.*（匹配开头）
            pattern = v[:-2]  # 去掉结尾的 .*
            if not re.search("^" + pattern, label_str):
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
    default_channels = []
    
    # 先收集所有匹配的规则和默认规则
    for r in config["routing"]:
        if "match" in r and match(labels, r["match"]):
            # 匹配的规则：添加到渠道集合
            channels.update(r["send_to"])
        elif r.get("default"):
            # 默认规则：记录但不立即添加（最后添加）
            default_channels = r["send_to"]
    
    # 如果没有匹配到任何规则，使用默认渠道
    if not channels and default_channels:
        channels.update(default_channels)
    # 如果有匹配的规则，也添加默认渠道（实现叠加效果）
    elif channels and default_channels:
        channels.update(default_channels)
    
    return list(channels)
