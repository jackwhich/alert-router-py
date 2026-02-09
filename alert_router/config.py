"""
配置加载模块
"""
import yaml
from typing import Dict, Tuple
from .models import Channel
from logging_config import setup_logging, get_logger

# 初始化默认 logger（会在 load_config 中根据配置文件重新配置）
logger = get_logger("alert-router")


def load_config() -> Tuple[Dict, Dict[str, Channel]]:
    """
    加载配置文件
    
    Returns:
        Tuple[Dict, Dict[str, Channel]]: (配置字典, 渠道字典)
    """
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
