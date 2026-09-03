"""
Alert Router 核心模块

保持向后兼容的导入接口
"""
# 核心模块
from .core import (
    Channel,
    load_config,
    setup_logging,
    get_logger,
    convert_to_cst,
    replace_times_in_description,
    url_to_link,
)

# 路由模块
from .routing import route, match, should_skip_jenkins_firing

# 模板渲染
from .templates import render

# 发送器
from .senders import send_telegram, send_webhook, send_tongsheng

# 服务层（新增）
from .services import AlertService, ImageService, ChannelFilter

__all__ = [
    # 核心模块
    "Channel",
    "load_config",
    "setup_logging",
    "get_logger",
    "convert_to_cst",
    "replace_times_in_description",
    "url_to_link",
    # 路由
    "route",
    "match",
    "should_skip_jenkins_firing",
    # 模板渲染
    "render",
    # 发送器
    "send_telegram",
    "send_webhook",
    "send_tongsheng",
    # 服务层
    "AlertService",
    "ImageService",
    "ChannelFilter",
    # 绘图器（延迟导入，避免未安装 matplotlib 时拖垮其它模块）
    "generate_plot_from_generator_url",
    "generate_plot_from_result",
    "generate_plot_from_grafana_generator_url",
]


def __getattr__(name: str):
    if name in {
        "generate_plot_from_generator_url",
        "generate_plot_from_result",
        "generate_plot_from_grafana_generator_url",
    }:
        from .plotters import (
            generate_plot_from_generator_url,
            generate_plot_from_grafana_generator_url,
            generate_plot_from_result,
        )
        mapping = {
            "generate_plot_from_generator_url": generate_plot_from_generator_url,
            "generate_plot_from_result": generate_plot_from_result,
            "generate_plot_from_grafana_generator_url": generate_plot_from_grafana_generator_url,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
