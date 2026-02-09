"""
FastAPI 应用主入口
"""
import json
import asyncio
from fastapi import FastAPI, Request
from alert_normalizer import normalize
from logging_config import get_logger
from config import load_config
from routing import route
from template_renderer import render
from senders import send_telegram, send_webhook

# 加载配置（会初始化日志系统）
CONFIG, CHANNELS = load_config()
logger = get_logger("alert-router")
logger.info(f"配置加载完成，共 {len(CHANNELS)} 个渠道")

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化"""
    logger.info("=" * 60)
    logger.info("Alert Router 服务启动")
    logger.info(f"监听地址: {CONFIG.get('server', {}).get('host', '0.0.0.0')}:{CONFIG.get('server', {}).get('port', 8080)}")
    logger.info(f"已启用渠道数: {sum(1 for ch in CHANNELS.values() if ch.enabled)}/{len(CHANNELS)}")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时的清理"""
    logger.info("=" * 60)
    logger.info("Alert Router 服务正在关闭...")
    logger.info("等待正在处理的请求完成...")
    
    # 等待一小段时间，让正在处理的请求完成
    await asyncio.sleep(1)
    
    # 关闭 requests 会话（如果有的话）
    # requests 库会自动管理连接池，这里主要是记录日志
    logger.info("清理资源...")
    
    logger.info("Alert Router 服务已关闭")
    logger.info("=" * 60)


@app.post("/webhook")
async def webhook(req: Request):
    """
    接收告警 Webhook 并路由分发
    
    Returns:
        dict: 处理结果，包含成功和失败的详细信息
    """
    try:
        payload = await req.json()
        logger.info(f"收到告警请求: {json.dumps(payload, ensure_ascii=False)}")
        
        alerts = normalize(payload)
        
        if not alerts:
            logger.warning("无法解析告警数据格式")
            return {"ok": False, "error": "无法解析告警数据格式"}
        
        results = []

        for a in alerts:
            labels = a.get("labels", {})
            alertname = labels.get("alertname", "Unknown")
            targets = route(labels, CONFIG)
            
            logger.debug(f"告警 {alertname} 路由到渠道: {targets}")

            ctx = {
                "title": f'{CONFIG["defaults"]["title_prefix"]} {alertname}',
                "status": a.get("status"),
                "labels": labels,
                "annotations": a.get("annotations", {}),
                "startsAt": a.get("startsAt"),
                "endsAt": a.get("endsAt"),
                "generatorURL": a.get("generatorURL"),
            }

            for t in targets:
                ch = CHANNELS.get(t)
                if not ch:
                    error_msg = f"渠道不存在: {t}"
                    logger.warning(f"告警 {alertname}: {error_msg}")
                    results.append({"alert": alertname, "channel": t, "error": error_msg})
                    continue
                
                # 检查渠道是否启用（开关控制）
                if not ch.enabled:
                    logger.debug(f"告警 {alertname} 跳过已禁用的渠道: {t}")
                    results.append({"alert": alertname, "channel": t, "skipped": "渠道已禁用"})
                    continue
                
                # 检查是否发送 resolved 状态的告警
                alert_status = a.get("status", "firing")
                if alert_status == "resolved" and not ch.send_resolved:
                    logger.debug(f"告警 {alertname} 跳过 resolved 状态（渠道 {t} 配置为不发送 resolved）")
                    results.append({"alert": alertname, "channel": t, "skipped": "resolved 状态已禁用"})
                    continue
                
                try:
                    body = render(ch.template, ctx)
                    if ch.type == "telegram":
                        send_telegram(ch, body)
                    else:
                        send_webhook(ch, body)
                    logger.info(f"告警 {alertname} 已发送到渠道: {t} (状态: {alert_status})")
                    results.append({"alert": alertname, "channel": t, "status": "sent", "alert_status": alert_status})
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"告警 {alertname} 发送到渠道 {t} 失败: {error_msg}", exc_info=True)
                    results.append({"alert": alertname, "channel": t, "error": error_msg})

        return {"ok": True, "sent": results}
    except Exception as e:
        logger.error(f"处理 Webhook 请求失败: {e}", exc_info=True)
        return {"ok": False, "error": f"处理失败: {str(e)}"}
