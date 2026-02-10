"""
工具函数模块
"""
import re
from datetime import datetime, timedelta

from .logging_config import get_logger

logger = get_logger("alert-router")


def convert_to_cst(time_str: str) -> str:
    """
    将时间字符串转换为 CST（北京时间）并格式化为 YYYY-MM-DD HH:MM:SS

    支持格式：
    - 2024-01-15T10:30:00Z
    - 2024-01-15T10:30:00.123Z
    - 2026-02-10T01:47:51.122980105+08:00（Grafana ISO 8601 带时区）
    - 2024-01-15 10:30:15.418 +0000 UTC
    """
    if not time_str or time_str == "未知时间" or time_str == "未知恢复时间" or time_str == "0001-01-01T00:00:00Z":
        return time_str

    original_time = time_str  # 保存原始值用于日志

    try:
        # Grafana/Prometheus ISO 8601 格式（带时区，如 +08:00 或 Z）
        # 微秒超过 6 位需截断，否则 fromisoformat 可能失败
        m = re.match(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([+-]\d{2}:\d{2}|Z)?",
            time_str.strip()
        )
        if m:
            base, frac, tz = m.groups()
            # 截断微秒至 6 位
            if frac:
                frac = frac[:7] if len(frac) > 7 else frac  # .123456 或 .123
            else:
                frac = ""
            # Z 表示 UTC，fromisoformat 需 +00:00
            tz_str = "+00:00" if (tz == "Z" or time_str.strip().endswith("Z")) else (tz or "+00:00")
            normalized = base + frac + tz_str
            dt = datetime.fromisoformat(normalized)
            # 统一转为 CST：已是 +08 则仅格式化，UTC 则加 8 小时
            if dt.tzinfo:
                from datetime import timezone
                cst_dt = dt.astimezone(timezone(timedelta(hours=8)))
                result = cst_dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                cst_dt = dt + timedelta(hours=8)
                result = cst_dt.strftime("%Y-%m-%d %H:%M:%S")
            logger.debug(f"时间转换: {original_time} -> {result} (CST)")
            return result

        # 尝试解析 %Y-%m-%dT%H:%M:%S.%fZ 格式（例如：2025-03-28T00:30:15.418Z）
        try:
            clean_time = time_str.rstrip("Z")
            dt = datetime.strptime(clean_time, "%Y-%m-%dT%H:%M:%S.%f")
            # 直接加 8 小时
            cst_dt = dt + timedelta(hours=8)
            result = cst_dt.strftime("%Y-%m-%d %H:%M:%S")
            logger.debug(f"时间转换: {original_time} (UTC) -> {result} (CST)")
            return result
        except ValueError:
            pass
        
        # 尝试解析 %Y-%m-%dT%H:%M:%SZ 格式（不带毫秒）
        try:
            clean_time = time_str.rstrip("Z")
            dt = datetime.strptime(clean_time, "%Y-%m-%dT%H:%M:%S")
            # 直接加 8 小时
            cst_dt = dt + timedelta(hours=8)
            result = cst_dt.strftime("%Y-%m-%d %H:%M:%S")
            logger.debug(f"时间转换: {original_time} (UTC) -> {result} (CST)")
            return result
        except ValueError:
            pass
        
        # 尝试解析 %Y-%m-%d %H:%M:%S.%f +0000 UTC 格式（例如：2025-03-28 00:30:15.418 +0000 UTC）
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S.%f +0000 UTC")
            # 直接加 8 小时
            cst_dt = dt + timedelta(hours=8)
            result = cst_dt.strftime("%Y-%m-%d %H:%M:%S")
            logger.debug(f"时间转换: {original_time} (UTC) -> {result} (CST)")
            return result
        except ValueError:
            pass
        
        # 如果都解析失败，记录警告并返回原值
        logger.warning(f"无法解析时间格式: {original_time}，返回原值")
        return time_str
    except Exception as e:
        logger.error(f"时间转换异常: {original_time}, 错误: {e}")
        return time_str  # 如果解析失败，返回原值


def replace_times_in_description(description: str) -> str:
    """
    替换 description 中的时间（严格匹配不破坏原有格式）
    将 UTC 时间替换为北京时间
    """
    if not description:
        return description
    
    try:
        # 精确匹配时间部分的正则表达式：2025-03-28 00:30:15.418 +0000 UTC
        time_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \+0000 UTC)"
        
        # 定义替换函数
        def replace_match(match):
            original_time = match.group(0)
            beijing_time = convert_to_cst(original_time)
            return beijing_time
        
        # 使用正则替换所有匹配项
        updated_description = re.sub(time_pattern, replace_match, description)
        return updated_description
    except Exception as e:
        return description  # 如果替换失败，返回原值


def url_to_link(text: str) -> str:
    """
    将文本中的 URL 转换为 HTML 链接标签
    用于 Telegram HTML 格式
    """
    if not text or not isinstance(text, str):
        return text
    
    # 匹配 http:// 或 https:// 开头的 URL
    url_pattern = r"(https?://[^\s\)]+)"
    
    def replace_url(match):
        url = match.group(1)
        # 移除 URL 末尾可能存在的标点符号（除了在 HTML 标签中）
        url_clean = url.rstrip(".,;:!?)")
        return f"<a href=\"{url_clean}\">{url_clean}</a>"
    
    return re.sub(url_pattern, replace_url, text)
