"""
数据模型定义
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Channel:
    """告警渠道配置"""
    name: str
    type: str
    enabled: bool = True  # 开关：是否启用此渠道
    webhook_url: Optional[str] = None
    template: Optional[str] = None
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    proxy: Optional[Dict[str, str]] = None  # 代理配置，格式: {"http": "socks5://proxy:port", "https": "socks5://proxy:port"} 或 {"http": "http://proxy:port", "https": "https://proxy:port"}
    proxy_enabled: bool = True  # 开关：是否启用代理（此渠道）
    send_resolved: bool = True  # 是否发送 resolved 状态的告警（默认发送）
