"""
消息发送模块
"""
from .senders import send_telegram, send_webhook

__all__ = [
    "send_telegram",
    "send_webhook",
]
