"""
FastAPI 应用主入口

职责：
- HTTP 路由定义
- 请求/响应处理
- 应用生命周期管理

业务逻辑已提取到 alert_router.service.AlertService
"""
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from alert_router.core.config import load_config
from alert_router.core.logging_config import setup_logging

# 加载配置（config 只读配置，不初始化日志）
CONFIG, CHANNELS = load_config()
# 由 app 在启动时显式初始化日志（仅此一处），避免重复 handler 导致同一条日志打两遍
# logging 配置由 config.yaml 提供并在 load_config 中完成完整性校验
setup_logging(**CONFIG["logging"])
logger = logging.getLogger("alert-router")
log_cfg = CONFIG["logging"]
logger.info(
    f"配置加载完成，共 {len(CHANNELS)} 个渠道；"
    f"日志: level={log_cfg.get('level', 'INFO')}, 文件={log_cfg.get('log_dir', 'logs')}/{log_cfg.get('log_file', 'alert-router.log')}"
)

# 初始化服务实例（在应用启动时创建，避免重复加载配置）
from alert_router.services.alert_service import AlertService
_alert_service = AlertService(CONFIG, CHANNELS)


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
    
    # 关闭 requests 会话（清理连接池）
    try:
        from alert_router.senders.senders import clear_session_cache
        clear_session_cache()
        logger.info("已清理 HTTP 会话缓存")
    except Exception as e:
        logger.warning(f"清理 HTTP 会话缓存时出错: {e}")
    
    logger.info("Alert Router 服务已关闭")
    logger.info("=" * 60)


app = FastAPI(lifespan=lifespan, redirect_slashes=False)


def _handle_webhook(payload: dict) -> dict:
    """
    处理 webhook 请求（委托给服务层）
    
    Args:
        payload: Webhook 请求体
        
    Returns:
        处理结果字典
    """
    return _alert_service.process_webhook(payload)


@app.post("/webhook")
async def webhook(req: Request):
    """接收告警 Webhook 并路由分发（请使用 /webhook 无尾斜杠，避免 307）"""
    request_id = str(uuid.uuid4())[:8]
    logger.info(f"[{request_id}] [Webhook 入口] 收到 POST /webhook")
    try:
        payload = await req.json()
        status = payload.get("status")
        alerts_count = len(payload.get("alerts", [])) if payload.get("alerts") else 0
        logger.info(f"[{request_id}] [接收数据] status={status}, alerts={alerts_count}")
        raw_preview = json.dumps(payload, ensure_ascii=False, indent=2)
        logger.info(f"[{request_id}] [接收数据] 完整 Webhook 负载:\n{raw_preview}")

        result = _handle_webhook(payload)
        ok = result.get("ok", False)
        sent = result.get("sent", [])
        logger.info(f"[{request_id}] [Webhook 完成] ok={ok}, 发送结果数={len(sent)}")
        if not ok:
            logger.warning(f"[{request_id}] [Webhook 完成] 处理结果异常: {result}")
        return result
    except json.JSONDecodeError as e:
        logger.warning(f"[{request_id}] JSON 解析失败: {e}")
        return {"ok": False, "error": "Invalid JSON"}
    except ValueError as e:
        logger.warning(f"[{request_id}] 数据验证失败: {e}")
        return {"ok": False, "error": "Validation failed"}
    except Exception as e:
        logger.error(f"[{request_id}] 处理 Webhook 请求失败: {e}", exc_info=True)
        return {"ok": False, "error": "Internal error"}


if __name__ == "__main__":
    # 直接启动入口（从 config.yaml 读取配置）
    import uvicorn
    
    # 从已加载的配置读取服务器设置（复用 CONFIG，避免重复加载）
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
