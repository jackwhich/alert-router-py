"""
消息发送模块

性能优化：
- 使用 HTTP 连接池复用连接，减少连接建立开销
- 支持会话级别的代理配置
"""
import base64
import html
import json
import logging
import re
import time
import uuid
from typing import Optional, Dict, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core.logging_config import get_logger
from ..core.utils import detect_template_format
from ..core.models import Channel
from ..core.http_metrics import request_with_metrics

logger = get_logger("alert-router")

# PNG 文件头魔数，用于校验趋势图是否为有效 PNG
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 超时配置（秒）
TIMEOUTS = {
    "telegram_photo": 15,
    "telegram_text": 10,
    "webhook": 10,
    "tongsheng": 10,
}

_TONGSHENG_PUSH_PATH = "/api/v1/alert/push"
_TONGSHENG_MAX_ATTEMPTS = 3
_TONGSHENG_RATE_LIMIT_WAIT = 1
_HTML_TAG_RE = re.compile(r"</?(?:b|i|u|em|strong|code|pre|span|p|div|a)(?:\s[^>]*)?>", re.IGNORECASE)

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

    # Telegram HTML 模式不支持 <br>，只支持 <b>/<i>/<code> 等；统一把 <br> 转为换行，避免 400 "Unsupported start tag br"
    text = (text or "")
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

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
            # 在 JSON 日志中通过结构化字段输出 Telegram 请求 payload
            logger.debug(
                "发送 Telegram 消息的完整 payload",
                extra={"telegram_request": payload},
            )
        response = request_with_metrics(
            session,
            "POST",
            url,
            target="telegram",
            **kwargs,
        )
        response.raise_for_status()
        logger.info(f"[Telegram] 渠道 [{ch.name}] 发送成功, 状态码: {response.status_code}")
        if logger.isEnabledFor(logging.DEBUG):
            try:
                resp_json = response.json()
            except (ValueError, json.JSONDecodeError):
                resp_json = None
            if resp_json is not None:
                logger.debug(
                    "Telegram 响应内容",
                    extra={"telegram_response": resp_json},
                )
            else:
                logger.debug("Telegram 响应内容（非 JSON）", extra={"telegram_response_text": response.text})
        return response
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            try:
                err_body = e.response.text
                if err_body and logger.isEnabledFor(logging.DEBUG):
                    try:
                        err_json = json.loads(err_body)
                        logger.debug(
                            "Telegram 400 响应",
                            extra={"telegram_response": err_json},
                        )
                    except (json.JSONDecodeError, TypeError):
                        logger.debug(
                            "Telegram 400 响应（非 JSON）",
                            extra={"telegram_response_text": err_body[:800]},
                        )
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
            # requests 在 data=str 时会按 latin-1 编码，中文/emoji 会触发 UnicodeEncodeError
            # 回退路径强制使用 UTF-8 bytes，并显式声明 charset
            utf8_headers = {"Content-Type": "application/json; charset=utf-8"}
            response = _post_webhook(
                session,
                ch.webhook_url,
                ch.name,
                data=body.encode("utf-8"),
                headers=utf8_headers,
                **kwargs,
            )
            return response
        except requests.exceptions.RequestException as e:
            _log_webhook_error(ch.name, e)
            raise
    except requests.exceptions.RequestException as e:
        _log_webhook_error(ch.name, e)
        raise


def _html_to_plain_text(text: str) -> str:
    """把 Telegram HTML 模板转成通盛纯文本。"""
    text = text or ""
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    return text.strip()


def _to_aes16_bytes(value: Optional[str], field_name: str) -> bytes:
    """解析 16 字节 AES key/IV：UTF-8 16 字节，或 32 位 hex。不把原值写入异常信息。"""
    if not value:
        raise requests.exceptions.RequestException(f"通盛 {field_name} 未配置")
    raw = value.encode("utf-8")
    if len(raw) == 16:
        return raw
    try:
        decoded = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise requests.exceptions.RequestException(
            f"通盛 {field_name} 必须为 16 字节（或 32 位 hex）"
        ) from exc
    if len(decoded) != 16:
        raise requests.exceptions.RequestException(f"通盛 {field_name} 必须为 16 字节（或 32 位 hex）")
    return decoded


def _aes128_cbc_encrypt(plaintext: str, key: str, iv: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key_bytes = _to_aes16_bytes(key, "aes_key")
    iv_bytes = _to_aes16_bytes(iv, "aes_iv")
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def _build_tongsheng_envelope(ch: Channel, plain: Dict) -> Dict:
    encrypt = True if ch.encrypt is None else bool(ch.encrypt)
    if encrypt:
        ciphertext = _aes128_cbc_encrypt(
            json.dumps(plain, ensure_ascii=False),
            ch.aes_key or "",
            ch.aes_iv or "",
        )
        return {"data": ciphertext}
    return {"data": plain}


def _parse_tongsheng_response(response: Optional[requests.Response]) -> Tuple[str, str]:
    if response is None:
        return "", "无响应"
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        text = (response.text or "").strip()
        return "", text[:200] or f"HTTP {response.status_code}"
    if not isinstance(body, dict):
        return "", str(body)[:200]
    code = body.get("code", "")
    msg = body.get("msg", "")
    return str(code), str(msg) if msg is not None else ""


def send_tongsheng(ch: Channel, text: str):
    """
    发送通盛群组预警。

    只认响应 JSON 的 code/msg；10179 等待 1 秒后用同一 msg_no 重试。
    日志不输出 token、aes_key、aes_iv。
    """
    if not ch.base_url:
        raise requests.exceptions.RequestException(f"通盛渠道 [{ch.name}] 未配置 base_url")
    if not ch.token:
        raise requests.exceptions.RequestException(f"通盛渠道 [{ch.name}] 未配置 token")
    if not ch.robot_id:
        raise requests.exceptions.RequestException(f"通盛渠道 [{ch.name}] 未配置 robot_id")
    if not ch.channel_id:
        raise requests.exceptions.RequestException(f"通盛渠道 [{ch.name}] 未配置 channel_id")

    content = _html_to_plain_text(text)
    if not content:
        content = " "

    msg_no = uuid.uuid4().hex
    plain = {
        "robot_id": str(ch.robot_id),
        "messages": [
            {
                "channel_id": str(ch.channel_id),
                "content": content,
                "msg_no": msg_no,
            }
        ],
    }
    envelope = _build_tongsheng_envelope(ch, plain)
    url = ch.base_url.rstrip("/") + _TONGSHENG_PUSH_PATH
    headers = {
        "Content-Type": "application/json",
        "X-Alert-Token": ch.token,
    }
    session = _get_session(proxy=ch.proxy)

    logger.info(
        f"[通盛] 渠道 [{ch.name}] 请求: channel_id={ch.channel_id}, "
        f"encrypt={True if ch.encrypt is None else bool(ch.encrypt)}, msg_no={msg_no}"
    )

    last_code = ""
    last_msg = ""
    last_response = None
    for attempt in range(1, _TONGSHENG_MAX_ATTEMPTS + 1):
        try:
            response = request_with_metrics(
                session,
                "POST",
                url,
                target="tongsheng",
                json=envelope,
                headers=headers,
                timeout=TIMEOUTS["tongsheng"],
            )
            last_response = response
            last_code, last_msg = _parse_tongsheng_response(response)
        except requests.exceptions.HTTPError as e:
            last_response = e.response
            last_code, last_msg = _parse_tongsheng_response(e.response)
            if last_code != "10179":
                logger.error(
                    f"发送通盛消息失败 (渠道: {ch.name}): "
                    f"code={last_code or 'http_error'}, msg={last_msg or e}"
                )
                raise
        except requests.exceptions.RequestException as e:
            logger.error(f"发送通盛消息失败 (渠道: {ch.name}): {e}")
            raise

        if last_code == "200":
            logger.info(f"[通盛] 渠道 [{ch.name}] 发送成功, code={last_code}, msg={last_msg}")
            if logger.isEnabledFor(logging.DEBUG) and last_response is not None:
                try:
                    logger.debug(
                        "通盛响应内容",
                        extra={"tongsheng_response": last_response.json()},
                    )
                except (ValueError, json.JSONDecodeError):
                    logger.debug(
                        "通盛响应内容（非 JSON）",
                        extra={"tongsheng_response_text": last_response.text},
                    )
            return last_response

        if last_code == "10179" and attempt < _TONGSHENG_MAX_ATTEMPTS:
            logger.warning(
                f"[通盛] 渠道 [{ch.name}] 频率限制 (code=10179)，"
                f"{_TONGSHENG_RATE_LIMIT_WAIT} 秒后重试 ({attempt}/{_TONGSHENG_MAX_ATTEMPTS})"
            )
            time.sleep(_TONGSHENG_RATE_LIMIT_WAIT)
            continue

        raise requests.exceptions.RequestException(
            f"通盛推送失败 (渠道: {ch.name}): code={last_code}, msg={last_msg}"
        )


def _log_webhook_request(channel_name: str, url: str, body: str):
    logger.info(f"发送 Webhook 消息到渠道 [{channel_name}]，URL: {url}")
    if logger.isEnabledFor(logging.DEBUG):
        # 发送前记录下游 Webhook 请求体（可能是 JSON 字符串或其他格式）
        logger.debug(
            "发送 Webhook 消息的完整 body",
            extra={"webhook_body": body},
        )


def _post_webhook(
    session: requests.Session,
    url: str,
    channel_name: str,
    **kwargs,
):
    response = request_with_metrics(
        session,
        "POST",
        url,
        target="webhook",
        **kwargs,
    )
    response.raise_for_status()
    logger.info(f"Webhook 消息发送成功 (渠道: {channel_name})，响应状态码: {response.status_code}")
    if logger.isEnabledFor(logging.DEBUG):
        try:
            resp_json = response.json()
            logger.debug(
                "Webhook 响应内容",
                extra={"webhook_response": resp_json},
            )
        except (json.JSONDecodeError, ValueError):
            logger.debug(
                "Webhook 响应内容（非 JSON）",
                extra={"webhook_response_text": response.text},
            )
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
