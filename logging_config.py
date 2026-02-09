"""
日志配置模块

提供统一的日志配置，支持文件输出和日志轮转
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


# 避免同一进程内重复配置导致重复添加 handler（日志打两遍）
_alert_router_configured = False


def setup_logging(
    log_dir: str = "logs",
    log_file: str = "alert-router.log",
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
) -> logging.Logger:
    """
    配置日志系统（同一进程内多次调用只会生效一次，避免重复 handler 导致日志打两遍）
    
    Args:
        log_dir: 日志目录
        log_file: 日志文件名
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        max_bytes: 单个日志文件最大大小（字节）
        backup_count: 保留的备份文件数量
    
    Returns:
        logging.Logger: 配置好的 logger 实例
    """
    global _alert_router_configured
    logger = logging.getLogger("alert-router")
    if _alert_router_configured:
        return logger

    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # 日志文件完整路径
    log_file_path = log_path / log_file
    
    # 获取日志级别
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    logger.setLevel(log_level)
    logger.propagate = False
    
    # 清空已有 handler，避免多次初始化或其它地方添加导致同一条日志输出多遍
    logger.handlers.clear()
    
    # 日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 文件 handler（带轮转）
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 控制台 handler：仅保留一个写入 stderr 的 handler，避免重复打印
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    _alert_router_configured = True
    return logger


def get_logger(name: str = "alert-router") -> logging.Logger:
    """
    获取 logger 实例
    
    Args:
        name: logger 名称
    
    Returns:
        logging.Logger: logger 实例
    """
    return logging.getLogger(name)
