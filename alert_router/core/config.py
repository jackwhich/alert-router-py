"""
配置加载模块（只负责读配置，不初始化日志；日志由 app 在启动时显式初始化）
"""
import os
from pathlib import Path
from typing import Dict, Tuple

import yaml

from .models import Channel


def _config_path() -> Path:
    """解析 config.yaml 路径：优先环境变量 CONFIG_FILE，否则为项目根目录下的 config.yaml"""
    env_path = os.environ.get("CONFIG_FILE")
    if env_path and os.path.isfile(env_path):
        return Path(env_path)
    # 项目根：当前文件 alert_router/config.py -> 上级目录 alert-router-py
    root = Path(__file__).resolve().parent.parent
    return root / "config.yaml"


def _normalize_proxy_url(url: str) -> str:
    """
    将 socks5:// 转为 socks5h://，使 DNS 在代理端解析，避免本机无法解析外网域名（如 hooks.slack.com）报错。
    """
    if url and url.startswith("socks5://") and not url.startswith("socks5h://"):
        return "socks5h://" + url[len("socks5://") :]
    return url


def _validate_logging_config(raw: Dict) -> None:
    """
    校验 logging 配置必须存在且字段完整，不在代码里兜底默认值。
    """
    logging_cfg = raw.get("logging")
    if not isinstance(logging_cfg, dict):
        raise ValueError("config.yaml 中必须配置 logging 节点")

    required_fields = ["log_dir", "log_file", "level", "max_bytes", "backup_count"]
    missing = [field for field in required_fields if field not in logging_cfg]
    if missing:
        raise ValueError(f"config.yaml 中 logging 缺少必要字段: {', '.join(missing)}")


def load_config() -> Tuple[Dict, Dict[str, Channel]]:
    """
    加载配置文件
    
    Returns:
        Tuple[Dict, Dict[str, Channel]]: (配置字典, 渠道字典)
    """
    path = _config_path()
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}，可设置环境变量 CONFIG_FILE 指定路径")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raw = {}
    _validate_logging_config(raw)
    
    # 检查 channels 配置是否存在
    if "channels" not in raw or not isinstance(raw["channels"], dict):
        raise ValueError("config.yaml 中必须配置 channels 节点")
    
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
        # 如果代理配置是字符串，转换为字典格式，并统一使用 socks5h 以便代理端解析 DNS
        if isinstance(proxy, str):
            proxy = {
                "http": _normalize_proxy_url(proxy),
                "https": _normalize_proxy_url(proxy),
            }
        elif isinstance(proxy, dict):
            # 使用不同的变量名避免与外层循环的 k, v 冲突
            proxy = {
                proxy_key: _normalize_proxy_url(str(proxy_val)) if isinstance(proxy_val, str) else proxy_val
                for proxy_key, proxy_val in proxy.items()
            }
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
