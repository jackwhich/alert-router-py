"""
日志配置模块

提供统一的日志配置，支持文件输出和日志轮转。
不在本模块内自动实例化，由 app 在启动时显式调用 setup_logging。
已配置标记挂在 logger 上，避免模块被多次导入时重复添加 handler（同一条日志打两遍）。
"""

import inspect
import json
import logging
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

# 已配置标记存在 logger 上，保证同一 logger 只配置一次（即使用户或框架多次导入本模块）
_ATTR_CONFIGURED = "_alert_router_logging_configured"
_TRACE_ID_CTX: ContextVar[str] = ContextVar("alert_router_trace_id", default="-")
_ORIGINAL_RECORD_FACTORY = logging.getLogRecordFactory()


def _get_caller_class_name() -> Optional[str]:
    """从当前调用栈中获取打日志处的类名（若在类方法内）。"""
    try:
        for frame_info in inspect.stack():
            pathname = (frame_info.filename or "").replace(os.sep, "/")
            if "logging" in pathname or "logging_config" in pathname:
                continue
            frame = frame_info.frame
            if "self" in frame.f_locals:
                self_obj = frame.f_locals["self"]
                return type(self_obj).__name__
            return None
    except Exception:
        pass
    return None


def _log_record_factory(
    name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None
) -> logging.LogRecord:
    """自定义 LogRecord 工厂：在创建记录时注入调用处的类名。"""
    # 标准库 LogRecord 只接受 9 个位置参数（不含 extra），多传会触发 TypeError
    record = _ORIGINAL_RECORD_FACTORY(
        name, level, fn, lno, msg, args, exc_info, func, sinfo
    )
    record.code_class = _get_caller_class_name()
    return record


def set_trace_id(trace_id: str) -> None:
    """设置当前上下文 traceId。"""
    _TRACE_ID_CTX.set(trace_id or "-")


def get_trace_id() -> str:
    """获取当前上下文 traceId。"""
    return _TRACE_ID_CTX.get()


class TraceIdFilter(logging.Filter):
    """将 traceId 注入到每条日志记录中。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.traceId = get_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    """统一 JSON 单行日志格式（用于文件，便于机器解析）。"""

    def format(self, record: logging.LogRecord) -> str:
        code_loc = f"{record.filename}:{record.lineno}"
        code_class = getattr(record, "code_class", None)
        if code_class:
            code_value = f"{code_class} ({code_loc})"
        else:
            code_value = code_loc
        payload: Dict[str, Any] = {
            "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "traceId": getattr(record, "traceId", "-"),
            "message": record.getMessage(),
            "logger": record.name,
            "code": code_value,
        }
        if code_class:
            payload["class"] = code_class
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """控制台人类可读格式：不把 message 再包一层 JSON，保留真实换行，便于直接阅读 payload。"""

    def format(self, record: logging.LogRecord) -> str:
        code_loc = f"{record.filename}:{record.lineno}"
        code_class = getattr(record, "code_class", None)
        if code_class:
            code_value = f"{code_class} ({code_loc})"
        else:
            code_value = code_loc
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        trace_id = getattr(record, "traceId", "-")
        head = f"{ts} {record.levelname:5} [{trace_id}] {code_value} - "
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        # 多行 message 时首行带 head，后续行缩进对齐，便于阅读
        if "\n" in msg:
            lines = msg.split("\n")
            return head + "\n  ".join(lines)
        return head + msg


def setup_logging(
    log_dir: str,
    log_file: str,
    level: str,
    max_bytes: int,
    backup_count: int
) -> logging.Logger:
    """
    配置日志系统。由 app 在启动时调用一次；若 logger 已配置过则直接返回，不重复添加 handler。
    
    Args:
        log_dir: 日志目录
        log_file: 日志文件名
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        max_bytes: 单个日志文件最大大小（字节）
        backup_count: 保留的备份文件数量
    
    Returns:
        logging.Logger: 配置好的 logger 实例
    """
    logger = logging.getLogger("alert-router")
    if getattr(logger, _ATTR_CONFIGURED, False):
        return logger

    # 注入自定义 LogRecord 工厂，使每条日志带上调用处的类名（便于排查）
    logging.setLogRecordFactory(_log_record_factory)

    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # 日志文件完整路径
    log_file_path = log_path / log_file
    
    # 获取日志级别
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    logger.setLevel(log_level)
    logger.propagate = False
    # 始终清空已有 handler（防止其它地方或旧逻辑曾添加过），再只加一个 file、一个 console
    logger.handlers.clear()
    
    # JSON 单行日志格式
    formatter = JsonFormatter()
    trace_filter = TraceIdFilter()
    
    # 文件 handler（带轮转），仅一个
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(trace_filter)
    logger.addHandler(file_handler)
    
    # 控制台 handler：人类可读格式，不转义换行，便于直接看 payload；仅 TTY 时添加
    if sys.stderr.isatty():
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(ConsoleFormatter())
        console_handler.addFilter(trace_filter)
        logger.addHandler(console_handler)
    
    setattr(logger, _ATTR_CONFIGURED, True)
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


def new_trace_id() -> str:
    """生成新的 traceId。"""
    return str(uuid.uuid4())[:12]
