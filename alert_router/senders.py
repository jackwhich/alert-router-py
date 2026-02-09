"""
消息发送模块
"""
import json
from typing import Optional
import requests
from .models import Channel
from logging_config import get_logger

logger = get_logger("alert-router")


def send_telegram(ch: Channel, text: str, parse_mode: Optional[str] = None):
    """
    发送 Telegram 消息
    
    Args:
        ch: 渠道配置
        text: 消息文本
        parse_mode: 解析模式（None/HTML/Markdown），如果为 None 则根据模板文件名自动判断
    
    Returns:
        requests.Response: HTTP 响应对象
    """
    url = f"https://api.telegram.org/bot{ch.bot_token}/sendMessage"
    
    # 如果没有指定 parse_mode，根据模板文件名判断
    if parse_mode is None and ch.template:
        if ch.template.endswith(".html.j2") or ch.template.endswith(".html"):
            parse_mode = "HTML"
        elif ch.template.endswith(".md.j2") or ch.template.endswith(".md"):
            parse_mode = "Markdown"
    
    payload = {
        "chat_id": ch.chat_id,
        "text": text,
        "disable_web_page_preview": True
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    
    kwargs = {
        "json": payload,
        "timeout": 10
    }
    # 如果配置了代理，则使用代理
    if ch.proxy:
        kwargs["proxies"] = ch.proxy
    
    try:
        response = requests.post(url, **kwargs)
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        logger.error(f"发送 Telegram 消息失败 (渠道: {ch.name}): {e}")
        raise


def send_webhook(ch: Channel, body: str):
    """
    发送 Webhook 消息
    
    Args:
        ch: 渠道配置
        body: 消息体（JSON 字符串）
    
    Returns:
        requests.Response: HTTP 响应对象
    """
    kwargs = {"timeout": 10}
    # 如果配置了代理，则使用代理
    if ch.proxy:
        kwargs["proxies"] = ch.proxy
    
    try:
        # 尝试作为 JSON 发送
        return requests.post(ch.webhook_url, json=json.loads(body), **kwargs)
    except (json.JSONDecodeError, ValueError):
        # 如果不是有效的 JSON，则作为原始数据发送
        try:
            return requests.post(ch.webhook_url, data=body, **kwargs)
        except requests.exceptions.RequestException as e:
            logger.error(f"发送 Webhook 消息失败 (渠道: {ch.name}): {e}")
            raise
    except requests.exceptions.RequestException as e:
        logger.error(f"发送 Webhook 消息失败 (渠道: {ch.name}): {e}")
        raise
