"""
FastAPI 应用主入口
"""
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request

from adapters.alert_normalizer import normalize
from alert_router.config import load_config
from alert_router.logging_config import setup_logging
from alert_router.routing import route
from requests.exceptions import HTTPError
from alert_router.senders import send_telegram, send_webhook
from alert_router.prometheus_plotter import generate_plot_from_generator_url
from alert_router.template_renderer import render

# 加载配置（config 只读配置，不初始化日志）
CONFIG, CHANNELS = load_config()
# 由 app 在启动时显式初始化日志（仅此一处），避免重复 handler 导致同一条日志打两遍
# logging 配置由 config.yaml 提供并在 load_config 中完成完整性校验
setup_logging(**CONFIG["logging"])
logger = logging.getLogger("alert-router")
logger.info(f"配置加载完成，共 {len(CHANNELS)} 个渠道")

# 进程内 Jenkins 去重缓存（key -> 过期时间戳）
_JENKINS_DEDUP_CACHE = {}


def _build_jenkins_dedup_key(labels: dict) -> Optional[str]:
    """
    生成 Jenkins 去重 key。
    仅在包含 jenkins_job 和 check_commitID 时启用去重。
    """
    jenkins_job = labels.get("jenkins_job")
    commit_id = labels.get("check_commitID")
    if not jenkins_job or not commit_id:
        return None
    alertname = labels.get("alertname", "")
    git_branch = labels.get("gitBranch", "")
    return f"{alertname}|{jenkins_job}|{commit_id}|{git_branch}"


def _should_skip_jenkins_firing(labels: dict, alert_status: str, config: dict) -> bool:
    """
    Jenkins firing 告警去重：
    - status=firing：在去重窗口内仅首次发送，后续跳过
    - status=resolved 且 clear_on_resolved=true：清理该 key
    """
    dedup_cfg = (config or {}).get("jenkins_dedup", {}) or {}
    if not dedup_cfg.get("enabled", True):
        return False

    key = _build_jenkins_dedup_key(labels)
    if not key:
        return False

    ttl_seconds = int(dedup_cfg.get("ttl_seconds", 900))
    clear_on_resolved = bool(dedup_cfg.get("clear_on_resolved", True))
    now = time.time()

    # 清理过期 key，避免缓存无限增长
    expired_keys = [k for k, exp in _JENKINS_DEDUP_CACHE.items() if exp <= now]
    for k in expired_keys:
        _JENKINS_DEDUP_CACHE.pop(k, None)

    if alert_status == "resolved":
        if clear_on_resolved:
            _JENKINS_DEDUP_CACHE.pop(key, None)
        return False

    if alert_status != "firing":
        return False

    expires_at = _JENKINS_DEDUP_CACHE.get(key)
    if expires_at and expires_at > now:
        return True

    _JENKINS_DEDUP_CACHE[key] = now + max(1, ttl_seconds)
    return False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    应用生命周期管理（替代已弃用的 on_event）
    """
    # 启动时的初始化
    logger.info("=" * 60)
    logger.info("Alert Router 服务启动")
    server_config = CONFIG.get("server", {})
    host = server_config.get("host")
    port = server_config.get("port")
    logger.info(f"监听地址: {host}:{port}")
    logger.info(f"已启用渠道数: {sum(1 for ch in CHANNELS.values() if ch.enabled)}/{len(CHANNELS)}")
    logger.info("=" * 60)
    
    yield
    
    # 关闭时的清理
    logger.info("=" * 60)
    logger.info("Alert Router 服务正在关闭...")
    logger.info("等待正在处理的请求完成...")
    
    # 关闭 requests 会话（如果有的话）
    # requests 库会自动管理连接池，这里主要是记录日志
    logger.info("清理资源...")
    
    logger.info("Alert Router 服务已关闭")
    logger.info("=" * 60)


app = FastAPI(lifespan=lifespan, redirect_slashes=False)


def _handle_webhook(payload: dict) -> dict:
    """处理 webhook 请求逻辑"""
    alerts = normalize(payload)
    if not alerts:
        logger.warning("无法解析告警数据格式")
        return {"ok": False, "error": "无法解析告警数据格式"}

    alert_summary = ", ".join(a.get("labels", {}).get("alertname", "?") for a in alerts)
    logger.info(f"收到告警请求: {len(alerts)} 条 [{alert_summary}]")

    results = []
    for a in alerts:
        labels = a.get("labels", {})
        alertname = labels.get("alertname", "Unknown")
        alert_status = a.get("status", "firing")

        # Jenkins 告警在去重窗口内只发送一次 firing，抑制 Alertmanager 组更新导致的重复通知
        if _should_skip_jenkins_firing(labels, alert_status, CONFIG):
            logger.info(f"告警 {alertname} 命中 Jenkins 去重窗口，跳过重复 firing 通知")
            results.append(
                {
                    "alert": alertname,
                    "skipped": "jenkins 去重窗口内重复 firing",
                    "alert_status": alert_status,
                }
            )
            continue

        targets = route(labels, CONFIG)
        logger.info(f"告警 {alertname} 路由到渠道: {targets}")

        ctx = {
            "title": f"{CONFIG['defaults']['title_prefix']} {alertname}",
            "status": a.get("status"),
            "labels": labels,
            "annotations": a.get("annotations", {}),
            "startsAt": a.get("startsAt"),
            "endsAt": a.get("endsAt"),
            "generatorURL": a.get("generatorURL"),
        }

        source = labels.get("_source")
        image_bytes = None
        if source == "prometheus":
            image_cfg = CONFIG.get("prometheus_image", {}) or {}
            image_enabled = image_cfg.get("enabled", True)
            image_channels = []
            for t in targets:
                ch = CHANNELS.get(t)
                if not ch or ch.type != "telegram" or not ch.enabled:
                    continue
                if alert_status == "resolved" and not ch.send_resolved:
                    continue
                if not ch.image_enabled:
                    continue
                image_channels.append(ch)
            if image_enabled and image_channels:
                # 优先复用目标 Telegram 渠道的代理配置（如果有）
                plot_proxy = next((c.proxy for c in image_channels if c.proxy), None)
                image_bytes = generate_plot_from_generator_url(
                    a.get("generatorURL", ""),
                    proxies=plot_proxy,
                    lookback_minutes=int(image_cfg.get("lookback_minutes", 15)),
                    step=str(image_cfg.get("step", "30s")),
                    timeout_seconds=int(image_cfg.get("timeout_seconds", 8)),
                    max_series=int(image_cfg.get("max_series", 8)),
                )
                if image_bytes:
                    logger.info(f"告警 {alertname} 已生成趋势图，将优先按图片发送 Telegram")
                else:
                    logger.info(f"告警 {alertname} 未生成趋势图，将按文本发送 Telegram")

        sent_channels = []
        for t in targets:
            ch = CHANNELS.get(t)
            if not ch:
                error_msg = f"渠道不存在: {t}"
                logger.warning(f"告警 {alertname}: {error_msg}")
                results.append({"alert": alertname, "channel": t, "error": error_msg})
                continue
            if not ch.enabled:
                logger.debug(f"告警 {alertname} 跳过已禁用的渠道: {t}")
                results.append({"alert": alertname, "channel": t, "skipped": "渠道已禁用"})
                continue
            if alert_status == "resolved" and not ch.send_resolved:
                logger.debug(f"告警 {alertname} 跳过 resolved 状态（渠道 {t} 配置为不发送 resolved）")
                results.append({"alert": alertname, "channel": t, "skipped": "resolved 状态已禁用"})
                continue
            try:
                body = render(ch.template, ctx)
                logger.info(f"发送到渠道 [{t}] 的内容:\n{body}")
                if ch.type == "telegram":
                    # Prometheus 告警仅对 image_enabled=true 的 Telegram 渠道发送图片
                    use_image = source == "prometheus" and ch.image_enabled and bool(image_bytes)
                    if use_image:
                        try:
                            send_telegram(ch, body, photo_bytes=image_bytes)
                        except Exception as img_err:
                            logger.warning(
                                f"告警 {alertname} 渠道 {t} 图片发送失败，自动回退文本发送: {img_err}"
                            )
                            send_telegram(ch, body)
                    else:
                        send_telegram(ch, body)
                else:
                    send_webhook(ch, body)
                sent_channels.append(t)
                results.append({"alert": alertname, "channel": t, "status": "sent", "alert_status": alert_status})
            except Exception as e:
                error_msg = str(e)
                # 404/401/410 为 Webhook URL 配置问题，不打印堆栈，避免被误认为代码错误
                is_config_error = (
                    isinstance(e, HTTPError)
                    and e.response is not None
                    and e.response.status_code in (401, 404, 410)
                )
                if is_config_error:
                    logger.warning(f"告警 {alertname} 发送到渠道 {t} 失败: {error_msg}（请检查该渠道 Webhook URL 配置）")
                else:
                    logger.error(f"告警 {alertname} 发送到渠道 {t} 失败: {error_msg}", exc_info=True)
                results.append({"alert": alertname, "channel": t, "error": error_msg})
        if sent_channels:
            channels_str = ", ".join(sent_channels)
            logger.info(f"告警 {alertname} 已发送到 {len(sent_channels)} 个渠道: {channels_str} (状态: {alert_status})")

    return {"ok": True, "sent": results}


@app.post("/webhook")
async def webhook(req: Request):
    """接收告警 Webhook 并路由分发（请使用 /webhook 无尾斜杠，避免 307）"""
    request_id = str(uuid.uuid4())[:8]
    try:
        payload = await req.json()
        # 打印完整的接收数据
        raw_preview = json.dumps(payload, ensure_ascii=False, indent=2)
        logger.info(f"[{request_id}] 接收到的完整 Webhook 数据:\n{raw_preview}")
        status = payload.get("status")
        alerts_count = len(payload.get("alerts", [])) if payload.get("alerts") else 0
        logger.info(f"[{request_id}] Webhook 收到 (status={status}, alerts={alerts_count})")

        result = _handle_webhook(payload)
        if not result.get("ok"):
            logger.warning(f"[{request_id}] Webhook 处理结果异常: {result}")
        return result
    except Exception as e:
        logger.error(f"[{request_id}] 处理 Webhook 请求失败: {e}", exc_info=True)
        return {"ok": False, "error": f"处理失败: {str(e)}"}


if __name__ == "__main__":
    # 直接启动入口（从 config.yaml 读取配置）
    import uvicorn
    
    # 从配置读取服务器设置（必须配置）
    server_config = CONFIG.get("server", {})
    if not server_config:
        raise ValueError("config.yaml 中必须配置 server 节点")
    
    host = server_config.get("host")
    port = server_config.get("port")
    
    if host is None:
        raise ValueError("config.yaml 中必须配置 server.host")
    if port is None:
        raise ValueError("config.yaml 中必须配置 server.port")
    
    # 从环境变量读取工作进程数和超时时间（如果设置了）
    workers = int(os.getenv("WORKERS", 4))
    timeout = int(os.getenv("TIMEOUT", 30))
    
    # 启动 uvicorn 服务器
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        workers=workers,
        timeout_keep_alive=timeout,
        log_level="info",
        access_log=True,
    )
