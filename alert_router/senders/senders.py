"""
消息发送模块

性能优化：
- 使用 HTTP 连接池复用连接，减少连接建立开销
- 支持会话级别的代理配置
"""
import json
import logging
from typing import Optional, Dict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core.logging_config import get_logger
from ..core.utils import detect_template_format
from ..core.models import Channel

logger = get_logger("alert-router")

# PNG 文件头魔数，用于校验趋势图是否为有效 PNG
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 超时配置（秒）
TIMEOUTS = {
    "telegram_photo": 15,
    "telegram_text": 10,
    "webhook": 10,
}

# HTTP 连接池配置
# 使用连接池复用连接，提高性能
# 注意：在生产环境中，会话会长期复用，通常不需要手动清理
# 如果需要清理（例如测试环境），可以调用 clear_session_cache()
_session_cache: Dict[str, requests.Session] = {}


def _get_session(proxy: Optional[Dict[str, str]] = None) -> requests.Session:
    """
    获取或创建 HTTP 会话（带连接池）
    
    Args:
        proxy: 代理配置
        
    Returns:
        requests.Session 实例
    """
    # 使用代理配置作为缓存键
    cache_key = str(proxy) if proxy else "no_proxy"
    
    if cache_key not in _session_cache:
        session = requests.Session()
        
        # 配置重试策略
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        
        # 配置 HTTP 适配器（连接池）
        adapter = HTTPAdapter(
            pool_connections=10,  # 连接池大小
            pool_maxsize=20,     # 最大连接数
            max_retries=retry_strategy,
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # 设置代理
        if proxy:
            session.proxies.update(proxy)
        
        _session_cache[cache_key] = session
    
    return _session_cache[cache_key]


def clear_session_cache():
    """
    清理所有缓存的 HTTP 会话（主要用于测试或资源清理）
    
    注意：在生产环境中通常不需要调用此函数，会话会长期复用以提高性能
    """
    global _session_cache
    for session in _session_cache.values():
        session.close()
    _session_cache.clear()


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
        parse_mode = detect_template_format(ch.template)

    # 纯文本模式下把模板里的 <br> 转为换行，避免在 Telegram 里显示成字面 "<br>"
    if parse_mode == "":
        text = (text or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

    # 文本限制：caption 最大 1024，message 最大 4096，且不能为空
    text_safe = (text or "").strip() or " "
    caption = text_safe[:1024]
    message_text = text_safe[:4096]

    # 仅当图片有效时发图：长度足够且为 PNG 魔数，否则 Telegram 会 400
    photo_ok = (
        photo_bytes
        and len(photo_bytes) >= 100
        and photo_bytes[: len(_PNG_SIGNATURE)] == _PNG_SIGNATURE
    )
    if photo_ok:
        url = f"https://api.telegram.org/bot{ch.bot_token}/sendPhoto"
        payload = {
            "chat_id": ch.chat_id,
            "caption": caption,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        kwargs = {
            "data": payload,
            "files": {"photo": ("alert.png", photo_bytes, "image/png")},
            "timeout": TIMEOUTS["telegram_photo"],
        }
    else:
        url = f"https://api.telegram.org/bot{ch.bot_token}/sendMessage"
        payload = {
            "chat_id": ch.chat_id,
            "text": message_text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        kwargs = {
            "json": payload,
            "timeout": TIMEOUTS["telegram_text"],
        }

    # 使用连接池会话
    session = _get_session(proxy=ch.proxy)
    
    try:
        method = "sendPhoto" if photo_ok else "sendMessage"
        logger.info(
            f"[Telegram] 渠道 [{ch.name}] 请求: {method}, chat_id={ch.chat_id}, parse_mode={parse_mode or '(无)'}"
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"发送 Telegram 消息的完整 payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
        response = session.post(url, **kwargs)
        response.raise_for_status()
        logger.info(f"[Telegram] 渠道 [{ch.name}] 发送成功, 状态码: {response.status_code}")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Telegram 响应内容:\n{json.dumps(response.json(), ensure_ascii=False, indent=2)}")
        return response
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            try:
                err_body = e.response.text
                if err_body and logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Telegram 400 响应: {err_body[:500]}")
            except Exception:
                pass
        # 400 且使用了 parse_mode 时，可能是 HTML 解析错误，用纯文本重试一次（保留图片）
        if (
            e.response is not None
            and e.response.status_code == 400
            and parse_mode
        ):
            logger.warning(
                f"Telegram 返回 400 (渠道: {ch.name})，尝试以纯文本重发（去掉 parse_mode），保留图片"
            )
            try:
                # 纯文本下把 <br> 转为换行，避免在 Telegram 里显示成字面 "<br>"
                text_plain = message_text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
                # 传空字符串表示强制纯文本；保留 photo_bytes 以便仍发图
                return send_telegram(ch, text_plain, parse_mode="", photo_bytes=photo_bytes)
            except requests.exceptions.RequestException:
                pass
        _log_telegram_error(ch.name, e)
        raise
    except requests.exceptions.RequestException as e:
        _log_telegram_error(ch.name, e)
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
    kwargs = {"timeout": TIMEOUTS["webhook"]}
    body = body or ""
    
    # 使用连接池会话
    session = _get_session(proxy=ch.proxy)
    
    try:
        _log_webhook_request(ch.name, ch.webhook_url, body)
        # 尝试作为 JSON 发送
        if not body.strip():
            logger.debug(f"Webhook body 为空 (渠道: {ch.name})，按空 JSON 发送")
            json_body = {}
        else:
            json_body = json.loads(body)
        response = _post_webhook(session, ch.webhook_url, ch.name, json=json_body, **kwargs)
        return response
    except (json.JSONDecodeError, ValueError):
        # 如果不是有效的 JSON，则作为原始数据发送
        logger.debug(f"Webhook body 非 JSON，以原始数据发送 (渠道: {ch.name})")
        try:
            response = _post_webhook(session, ch.webhook_url, ch.name, data=body, **kwargs)
            return response
        except requests.exceptions.RequestException as e:
            _log_webhook_error(ch.name, e)
            raise
    except requests.exceptions.RequestException as e:
        _log_webhook_error(ch.name, e)
        raise


def _log_webhook_request(channel_name: str, url: str, body: str):
    logger.info(f"发送 Webhook 消息到渠道 [{channel_name}]，URL: {url}")
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"发送 Webhook 消息的完整 body:\n{body}")


def _post_webhook(
    session: requests.Session,
    url: str,
    channel_name: str,
    **kwargs,
):
    response = session.post(url, **kwargs)
    response.raise_for_status()
    logger.info(f"Webhook 消息发送成功 (渠道: {channel_name})，响应状态码: {response.status_code}")
    if logger.isEnabledFor(logging.DEBUG):
        try:
            logger.debug(f"Webhook 响应内容:\n{json.dumps(response.json(), ensure_ascii=False, indent=2)}")
        except (json.JSONDecodeError, ValueError):
            logger.debug(f"Webhook 响应内容（非 JSON）:\n{response.text}")
    return response


def _log_send_error(channel_type: str, channel_name: str, error: Exception):
    logger.error(f"发送 {channel_type} 消息失败 (渠道: {channel_name}): {error}")


def _log_telegram_error(channel_name: str, error: Exception):
    """记录 Telegram 发送失败，并输出 API 返回的 description 便于排查 400/401 等."""
    logger.error(f"发送 Telegram 消息失败 (渠道: {channel_name}): {error}")
    if isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
        try:
            body = error.response.json()
            desc = body.get("description", body.get("error", error.response.text))
            logger.error(f"Telegram API 响应说明: {desc}")
        except Exception:
            if error.response.text:
                logger.error(f"Telegram API 原始响应: {error.response.text[:500]}")


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
