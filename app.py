import json
import re
import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import requests
import yaml
from fastapi import FastAPI, Request
from jinja2 import Environment, FileSystemLoader
from alert_normalizer import normalize
from logging_config import setup_logging, get_logger

# 初始化默认 logger（会在 load_config 中根据配置文件重新配置）
logger = get_logger("alert-router")

@dataclass
class Channel:
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

def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    
    # 配置日志（如果配置文件中指定了日志配置）
    log_config = raw.get("logging", {})
    if log_config:
        global logger
        logger = setup_logging(
            log_dir=log_config.get("log_dir", "logs"),
            log_file=log_config.get("log_file", "alert-router.log"),
            level=log_config.get("level", "INFO"),
            max_bytes=log_config.get("max_bytes", 10 * 1024 * 1024),
            backup_count=log_config.get("backup_count", 5)
        )
        logger.info("日志系统已初始化")
    
    channels = {}
    # 获取全局代理配置和开关
    global_proxy = raw.get("proxy", None)
    global_proxy_enabled = raw.get("proxy_enabled", True)  # 默认启用
    
    for k, v in raw["channels"].items():
        # enabled 默认为 True（如果未配置）
        enabled = v.get("enabled", True)
        
        # 代理开关：优先使用渠道级别的，如果没有则使用全局开关
        proxy_enabled = v.get("proxy_enabled", global_proxy_enabled)
        
        # 代理配置：优先使用渠道级别的，如果没有则使用全局代理
        proxy = v.get("proxy", global_proxy)
        # 如果代理配置是字符串，转换为字典格式
        if isinstance(proxy, str):
            proxy = {"http": proxy, "https": proxy}
        elif proxy is False or proxy == "none":
            proxy = None
        
        # 如果代理开关关闭，则不使用代理
        if not proxy_enabled:
            proxy = None
        
        # send_resolved 默认为 True（如果未配置）
        send_resolved = v.get("send_resolved", True)
        
        # 创建 channel_data，排除已单独处理的字段，避免重复传递
        channel_data = {k: v for k, v in v.items() if k not in ["enabled", "proxy", "proxy_enabled", "send_resolved"]}
        channel_data.update({"proxy": proxy, "proxy_enabled": proxy_enabled, "send_resolved": send_resolved})
        channels[k] = Channel(name=k, enabled=enabled, **channel_data)
    return raw, channels

# 加载配置（会初始化日志系统）
CONFIG, CHANNELS = load_config()
logger.info(f"配置加载完成，共 {len(CHANNELS)} 个渠道")

env = Environment(loader=FileSystemLoader("templates"))

def convert_to_cst(time_str: str) -> str:
    """
    将时间字符串直接加 8 小时转换为 CST（北京时间）
    
    支持格式：
    - 2024-01-15T10:30:00Z
    - 2024-01-15T10:30:00.123Z
    - 2024-01-15 10:30:15.418 +0000 UTC
    """
    if not time_str or time_str == "未知时间" or time_str == "未知恢复时间":
        return time_str
    
    try:
        # 尝试解析 %Y-%m-%dT%H:%M:%S.%fZ 格式（例如：2025-03-28T00:30:15.418Z）
        try:
            clean_time = time_str.rstrip('Z')
            dt = datetime.strptime(clean_time, '%Y-%m-%dT%H:%M:%S.%f')
            # 直接加 8 小时
            cst_dt = dt + timedelta(hours=8)
            return cst_dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        
        # 尝试解析 %Y-%m-%dT%H:%M:%SZ 格式（不带毫秒）
        try:
            clean_time = time_str.rstrip('Z')
            dt = datetime.strptime(clean_time, '%Y-%m-%dT%H:%M:%S')
            # 直接加 8 小时
            cst_dt = dt + timedelta(hours=8)
            return cst_dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        
        # 尝试解析 %Y-%m-%d %H:%M:%S.%f +0000 UTC 格式（例如：2025-03-28 00:30:15.418 +0000 UTC）
        try:
            dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S.%f +0000 UTC')
            # 直接加 8 小时
            cst_dt = dt + timedelta(hours=8)
            return cst_dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        
        # 如果都解析失败，返回原值
        return time_str
    except Exception:
        return time_str  # 如果解析失败，返回原值

def replace_times_in_description(description: str) -> str:
    """
    替换 description 中的时间（严格匹配不破坏原有格式）
    将 UTC 时间替换为北京时间
    """
    if not description:
        return description
    
    try:
        # 精确匹配时间部分的正则表达式：2025-03-28 00:30:15.418 +0000 UTC
        time_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \+0000 UTC)"
        
        # 定义替换函数
        def replace_match(match):
            original_time = match.group(0)
            beijing_time = convert_to_cst(original_time)
            return beijing_time
        
        # 使用正则替换所有匹配项
        updated_description = re.sub(time_pattern, replace_match, description)
        return updated_description
    except Exception as e:
        return description  # 如果替换失败，返回原值

def render(template: str, ctx: Dict[str, Any]) -> str:
    # 转换时间为 CST
    if ctx.get("startsAt"):
        ctx["startsAt"] = convert_to_cst(ctx["startsAt"])
    if ctx.get("endsAt"):
        ctx["endsAt"] = convert_to_cst(ctx["endsAt"])
    
    # 替换 description 中的时间（仅对 Slack 模板）
    if template.endswith(".json.j2") and ctx.get("annotations", {}).get("description"):
        ctx["annotations"]["description"] = replace_times_in_description(ctx["annotations"]["description"])
    
    return env.get_template(template).render(**ctx)

def match(labels, cond):
    """
    匹配路由条件，支持正则表达式
    
    支持的正则格式：
    1. 完整正则表达式：以 '.*' 开头和结尾，如 '.*pattern.*'
    2. 开头匹配：以 '.*' 开头，如 '.*pattern'
    3. 结尾匹配：以 '.*' 结尾，如 'pattern.*'
    4. Alertmanager 风格：直接使用正则，如 'Jenkins.*|jenkins.*'（自动识别）
    5. 精确匹配：普通字符串
    """
    for k, v in cond.items():
        label_value = labels.get(k)
        label_str = str(label_value or "")
        
        if not isinstance(v, str):
            # 非字符串类型，精确匹配
            if labels.get(k) != v:
                return False
            continue
        
        # 检查是否是正则表达式（包含特殊字符或 | 符号）
        is_regex = False
        if "|" in v or "*" in v or "^" in v or "$" in v or "[" in v or "(" in v:
            is_regex = True
        
        if is_regex:
            # 正则表达式匹配
            try:
                # 如果正则不包含 ^ 或 $，则使用 search（部分匹配）
                # 如果包含 ^ 或 $，则使用 match 或 fullmatch
                if v.startswith("^") or v.endswith("$"):
                    # 锚定匹配
                    pattern = v
                elif v.startswith(".*") and v.endswith(".*"):
                    # 去掉首尾的 .*，使用 search
                    pattern = v[2:-2]
                elif v.startswith(".*"):
                    # 去掉开头的 .*，匹配结尾
                    pattern = v[2:] + "$"
                elif v.endswith(".*"):
                    # 去掉结尾的 .*，匹配开头
                    pattern = "^" + v[:-2]
                else:
                    # 直接使用正则表达式
                    pattern = v
                
                if not re.search(pattern, label_str):
                    return False
            except re.error:
                # 正则表达式错误，回退到精确匹配
                logger.warning(f"正则表达式错误: {v}，使用精确匹配")
                if labels.get(k) != v:
                    return False
        elif v.startswith(".*") and v.endswith(".*"):
            # 正则表达式匹配：.*pattern.*
            pattern = v[2:-2]  # 去掉首尾的 .*
            if not re.search(pattern, label_str):
                return False
        elif v.startswith(".*"):
            # 正则表达式匹配：.*pattern（匹配结尾）
            pattern = v[2:]  # 去掉开头的 .*
            if not re.search(pattern + "$", label_str):
                return False
        elif v.endswith(".*"):
            # 正则表达式匹配：pattern.*（匹配开头）
            pattern = v[:-2]  # 去掉结尾的 .*
            if not re.search("^" + pattern, label_str):
                return False
        else:
            # 精确匹配
            if labels.get(k) != v:
                return False
    return True

def route(labels):
    """
    路由告警到渠道列表
    支持多个规则叠加：默认渠道 + 匹配的特定规则渠道
    
    Returns:
        list: 渠道名称列表（去重）
    """
    channels = set()
    default_channels = []
    
    # 先收集所有匹配的规则和默认规则
    for r in CONFIG["routing"]:
        if "match" in r and match(labels, r["match"]):
            # 匹配的规则：添加到渠道集合
            channels.update(r["send_to"])
        elif r.get("default"):
            # 默认规则：记录但不立即添加（最后添加）
            default_channels = r["send_to"]
    
    # 如果没有匹配到任何规则，使用默认渠道
    if not channels and default_channels:
        channels.update(default_channels)
    # 如果有匹配的规则，也添加默认渠道（实现叠加效果，类似旧代码的 send_to_telegram_v2）
    elif channels and default_channels:
        channels.update(default_channels)
    
    return list(channels)

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
    import asyncio
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
            targets = route(labels)
            
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
