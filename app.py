"""
FastAPI 应用主入口
"""
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from adapters.alert_normalizer import normalize
from alert_router.config import load_config
from alert_router.routing import route
from alert_router.template_renderer import render
from alert_router.senders import send_telegram, send_webhook
from logging_config import setup_logging

# 短时去重：同一批告警在 DEDUPE_SECONDS 秒内只处理一次（避免 Grafana/客户端重复推送）
DEDUPE_SECONDS = 10
_dedupe_cache = {}  # key -> timestamp

# 加载配置（config 只读配置，不初始化日志）
CONFIG, CHANNELS = load_config()
# 由 app 在启动时显式初始化日志（仅此一处），避免重复 handler 导致同一条日志打两遍
log_cfg = CONFIG.get("logging", {}) or {}
setup_logging(
    log_dir=log_cfg.get("log_dir", "logs"),
    log_file=log_cfg.get("log_file", "alert-router.log"),
    level=log_cfg.get("level", "INFO"),
    max_bytes=log_cfg.get("max_bytes", 10 * 1024 * 1024),
    backup_count=log_cfg.get("backup_count", 5),
)
logger = logging.getLogger("alert-router")
logger.info(f"配置加载完成，共 {len(CHANNELS)} 个渠道")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理（替代已弃用的 on_event）
    """
    # 启动时的初始化
    logger.info("=" * 60)
    logger.info("Alert Router 服务启动")
    server_config = CONFIG.get('server', {})
    host = server_config.get('host')
    port = server_config.get('port')
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


def _dedupe_key(payload: dict) -> str:
    """生成去重键：优先用 groupKey（Grafana/Alertmanager），否则用 payload 哈希。"""
    if isinstance(payload, dict) and payload.get("groupKey"):
        return str(payload["groupKey"])
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _dedupe_prune():
    """删除过期条目，避免缓存无限增长。"""
    now = time.time()
    expired = [k for k, t in _dedupe_cache.items() if now - t > DEDUPE_SECONDS]
    for k in expired:
        del _dedupe_cache[k]


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
        targets = route(labels, CONFIG)
        logger.info(f"告警 {alertname} 路由到渠道: {targets}")

        ctx = {
            "title": f'{CONFIG["defaults"]["title_prefix"]} {alertname}',
            "status": a.get("status"),
            "labels": labels,
            "annotations": a.get("annotations", {}),
            "startsAt": a.get("startsAt"),
            "endsAt": a.get("endsAt"),
            "generatorURL": a.get("generatorURL"),
        }

        alert_status = a.get("status", "firing")
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
                    send_telegram(ch, body)
                else:
                    send_webhook(ch, body)
                sent_channels.append(t)
                results.append({"alert": alertname, "channel": t, "status": "sent", "alert_status": alert_status})
            except Exception as e:
                error_msg = str(e)
                logger.error(f"告警 {alertname} 发送到渠道 {t} 失败: {error_msg}", exc_info=True)
                results.append({"alert": alertname, "channel": t, "error": error_msg})
        if sent_channels:
            logger.info(f"告警 {alertname} 已发送到 {len(sent_channels)} 个渠道: {', '.join(sent_channels)} (状态: {alert_status})")

    return {"ok": True, "sent": results}


@app.post("/webhook")
async def webhook(req: Request):
    """接收告警 Webhook 并路由分发（请使用 /webhook 无尾斜杠，避免 307）"""
    request_id = str(uuid.uuid4())[:8]
    try:
        payload = await req.json()
        # 完整 payload 仅 DEBUG 输出，避免刷屏；INFO 只打一行摘要，便于区分是否收到多次请求
        raw_preview = json.dumps(payload, ensure_ascii=False, indent=2)
        logger.debug(f"[{request_id}] 接收到的 Webhook 数据:\n{raw_preview}")
        logger.info(f"[{request_id}] Webhook 收到 (status=%s, alerts=%s)",
                    payload.get("status"), payload.get("alerts", []) and len(payload["alerts"]) or 0)

        # 短时去重：同一批告警在 DEDUPE_SECONDS 秒内只处理一次
        key = _dedupe_key(payload)
        now = time.time()
        _dedupe_prune()
        if key in _dedupe_cache and (now - _dedupe_cache[key]) < DEDUPE_SECONDS:
            logger.info(f"[{request_id}] 重复 Webhook 已忽略 (key 在 {DEDUPE_SECONDS}s 内已处理)")
            return {"ok": True, "deduplicated": True, "request_id": request_id}
        _dedupe_cache[key] = now

        result = _handle_webhook(payload)
        if not result.get("ok"):
            logger.warning(f"[{request_id}] Webhook 处理结果异常: {result}")
        return result
    except Exception as e:
        logger.error(f"[{request_id}] 处理 Webhook 请求失败: {e}", exc_info=True)
        return {"ok": False, "error": f"处理失败: {str(e)}"}


if __name__ == "__main__":
    """
    直接启动入口（从 config.yaml 读取配置）
    """
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
