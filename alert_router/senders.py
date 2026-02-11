"""
消息发送模块
"""
import json
from typing import Optional

import requests

from .logging_config import get_logger
from .models import Channel

logger = get_logger("alert-router")


def send_telegram(
    ch: Channel,
    text: str,
    parse_mode: Optional[str] = None,
    photo_bytes: Optional[bytes] = None,
):
    """
    发送 Telegram 消息
    
    Args:
        ch: 渠道配置
        text: 消息文本
        parse_mode: 解析模式（None/HTML/Markdown），如果为 None 则根据模板文件名自动判断
    
    Returns:
        requests.Response: HTTP 响应对象
    """
    # 如果没有指定 parse_mode，根据模板文件名判断
    if parse_mode is None and ch.template:
        if ch.template.endswith(".html.j2") or ch.template.endswith(".html"):
            parse_mode = "HTML"
        elif ch.template.endswith(".md.j2") or ch.template.endswith(".md"):
            parse_mode = "Markdown"

    # 优先发送图片（caption 最大 1024）
    if photo_bytes:
        url = f"https://api.telegram.org/bot{ch.bot_token}/sendPhoto"
        payload = {
            "chat_id": ch.chat_id,
            "caption": text[:1024],
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        kwargs = {
            "data": payload,
            "files": {"photo": ("alert.png", photo_bytes, "image/png")},
            "timeout": 15,
        }
    else:
        url = f"https://api.telegram.org/bot{ch.bot_token}/sendMessage"
        payload = {
            "chat_id": ch.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        kwargs = {
            "json": payload,
            "timeout": 10,
        }

    # 如果配置了代理，则使用代理
    if ch.proxy:
        kwargs["proxies"] = ch.proxy

    try:
        logger.info(f"发送 Telegram 消息到渠道 [{ch.name}]，URL: {url}")
        logger.debug(f"发送 Telegram 消息的完整 payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
        response = requests.post(url, **kwargs)
        response.raise_for_status()
        logger.info(f"Telegram 消息发送成功 (渠道: {ch.name})，响应状态码: {response.status_code}")
        logger.debug(f"Telegram 响应内容:\n{json.dumps(response.json(), ensure_ascii=False, indent=2)}")
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
        logger.info(f"发送 Webhook 消息到渠道 [{ch.name}]，URL: {ch.webhook_url}")
        logger.debug(f"发送 Webhook 消息的完整 body:\n{body}")
        json_body = json.loads(body)
        response = requests.post(ch.webhook_url, json=json_body, **kwargs)
        response.raise_for_status()
        logger.info(f"Webhook 消息发送成功 (渠道: {ch.name})，响应状态码: {response.status_code}")
        try:
            logger.debug(f"Webhook 响应内容:\n{json.dumps(response.json(), ensure_ascii=False, indent=2)}")
        except (json.JSONDecodeError, ValueError):
            logger.debug(f"Webhook 响应内容（非 JSON）:\n{response.text}")
        return response
    except (json.JSONDecodeError, ValueError):
        # 如果不是有效的 JSON，则作为原始数据发送
        logger.debug(f"Webhook body 非 JSON，以原始数据发送 (渠道: {ch.name})")
        logger.info(f"发送 Webhook 消息到渠道 [{ch.name}]，URL: {ch.webhook_url}")
        logger.debug(f"发送 Webhook 消息的完整 body:\n{body}")
        try:
            response = requests.post(ch.webhook_url, data=body, **kwargs)
            response.raise_for_status()
            logger.info(f"Webhook 消息发送成功 (渠道: {ch.name})，响应状态码: {response.status_code}")
            try:
                logger.debug(f"Webhook 响应内容:\n{json.dumps(response.json(), ensure_ascii=False, indent=2)}")
            except (json.JSONDecodeError, ValueError):
                logger.debug(f"Webhook 响应内容（非 JSON）:\n{response.text}")
            return response
        except requests.exceptions.RequestException as e:
            _log_webhook_error(ch.name, e)
            raise
    except requests.exceptions.RequestException as e:
        _log_webhook_error(ch.name, e)
        raise


def _log_webhook_error(channel_name: str, e: requests.exceptions.RequestException):
    """Webhook 发送失败时统一日志：404/401/410 视为配置问题，不按代码错误报错。"""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        code = e.response.status_code
        if code in (401, 404, 410):
            logger.warning(
                f"Webhook 发送失败 (渠道: {channel_name}): HTTP {code}，"
                "请检查该渠道的 Webhook URL 是否有效、未过期或已被删除（非代码错误）。"
            )
            return
    logger.error(f"发送 Webhook 消息失败 (渠道: {channel_name}): {e}")
