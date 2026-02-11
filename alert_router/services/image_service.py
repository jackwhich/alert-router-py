"""
图片生成服务

统一管理 Prometheus 和 Grafana 的图片生成逻辑，消除重复代码
"""
import logging
from typing import Dict, List, Optional

from ..plotters.prometheus_plotter import generate_plot_from_generator_url
from ..plotters.grafana_plotter import generate_plot_from_grafana_generator_url
from ..core.models import Channel

logger = logging.getLogger("alert-router")


class ImageService:
    """图片生成服务"""
    
    def __init__(self, config: Dict, channels: Dict[str, Channel] = None, channel_filter=None):
        """
        初始化图片服务
        
        Args:
            config: 配置字典
            channels: 渠道字典（可选，用于过滤渠道）
            channel_filter: ChannelFilter 实例（可选，避免重复创建）
        """
        self.config = config
        self.channels = channels or {}
        self._channel_filter = channel_filter
    
    def generate_image(
        self,
        source: str,
        alert: dict,
        alert_status: str,
        target_channels: List[str],
        alertname: str,
    ) -> Optional[bytes]:
        """
        生成告警趋势图
        
        Args:
            source: 告警来源（prometheus/grafana）
            alert: 告警对象
            alert_status: 告警状态
            target_channels: 目标渠道列表
            alertname: 告警名称
            
        Returns:
            图片字节（如果生成失败则返回 None）
        """
        if source == "prometheus":
            return self._generate_prometheus_image(
                alert=alert,
                alert_status=alert_status,
                target_channels=target_channels,
                alertname=alertname,
            )
        elif source == "grafana":
            return self._generate_grafana_image(
                alert=alert,
                alert_status=alert_status,
                target_channels=target_channels,
                alertname=alertname,
            )
        return None
    
    def _generate_prometheus_image(
        self,
        alert: dict,
        alert_status: str,
        target_channels: List[str],
        alertname: str,
    ) -> Optional[bytes]:
        """
        生成 Prometheus 告警趋势图
        
        Args:
            alert: 告警对象
            alert_status: 告警状态
            target_channels: 目标渠道列表
            alertname: 告警名称
            
        Returns:
            图片字节（如果生成失败则返回 None）
        """
        image_cfg = self.config.get("prometheus_image", {}) or {}
        if not image_cfg.get("enabled", True):
            return None

        # 过滤出需要图片的 Telegram 渠道
        image_channels = self._filter_image_channels(
            target_channels=target_channels,
            alert_status=alert_status,
        )
        
        if not image_channels:
            return None

        # 根据配置决定是否使用代理
        use_proxy = image_cfg.get("use_proxy", False)
        plot_proxy = None
        if use_proxy:
            # 如果启用代理，从渠道配置获取代理设置
            plot_proxy = next(
                (c.proxy for c in image_channels if c.proxy),
                None
            )

        prometheus_url = image_cfg.get("prometheus_url") or None
        
        # 根据告警状态选择时间：firing 用 startsAt，resolved 用 endsAt
        alert_time = (
            alert.get("endsAt")
            if alert_status == "resolved"
            else alert.get("startsAt")
        )

        # 从配置读取绘图引擎（默认使用 plotly）
        plot_engine = image_cfg.get("plot_engine", "plotly")
        use_plotly = plot_engine.lower() == "plotly"

        image_bytes = generate_plot_from_generator_url(
            alert.get("generatorURL", ""),
            prometheus_url=prometheus_url,
            proxies=plot_proxy,
            lookback_minutes=int(image_cfg.get("lookback_minutes", 15)),
            step=str(image_cfg.get("step", "30s")),
            timeout_seconds=int(image_cfg.get("timeout_seconds", 8)),
            max_series=int(image_cfg.get("max_series", 8)),
            alertname=alertname,
            alert_time=alert_time,
            use_plotly=use_plotly,
        )
        
        if image_bytes:
            logger.info(f"告警 {alertname} 已生成趋势图，将优先按图片发送 Telegram")
        else:
            logger.info(f"告警 {alertname} 未生成趋势图，将按文本发送 Telegram")
        
        return image_bytes
    
    def _generate_grafana_image(
        self,
        alert: dict,
        alert_status: str,
        target_channels: List[str],
        alertname: str,
    ) -> Optional[bytes]:
        """
        生成 Grafana 告警趋势图
        
        Args:
            alert: 告警对象
            alert_status: 告警状态
            target_channels: 目标渠道列表
            alertname: 告警名称
            
        Returns:
            图片字节（如果生成失败则返回 None）
        """
        image_cfg = self.config.get("grafana_image", {}) or {}
        if not image_cfg.get("enabled", True):
            return None

        # 过滤出需要图片的 Telegram 渠道
        image_channels = self._filter_image_channels(
            target_channels=target_channels,
            alert_status=alert_status,
        )
        
        if not image_channels:
            return None

        # 根据配置决定是否使用代理
        use_proxy = image_cfg.get("use_proxy", False)
        plot_proxy = None
        if use_proxy:
            # 如果启用代理，从渠道配置获取代理设置
            plot_proxy = next(
                (c.proxy for c in image_channels if c.proxy),
                None
            )

        grafana_url = image_cfg.get("grafana_url") or None
        prometheus_url = image_cfg.get("prometheus_url") or None
        
        # 根据告警状态选择时间：firing 用 startsAt，resolved 用 endsAt
        alert_time = (
            alert.get("endsAt")
            if alert_status == "resolved"
            else alert.get("startsAt")
        )

        image_bytes = generate_plot_from_grafana_generator_url(
            alert.get("generatorURL", ""),
            grafana_url=grafana_url,
            prometheus_url=prometheus_url,
            proxies=plot_proxy,
            lookback_minutes=int(image_cfg.get("lookback_minutes", 15)),
            step=str(image_cfg.get("step", "30s")),
            timeout_seconds=int(image_cfg.get("timeout_seconds", 8)),
            max_series=int(image_cfg.get("max_series", 8)),
            alertname=alertname,
            alert_time=alert_time,
        )
        
        if image_bytes:
            logger.info(f"告警 {alertname} 已生成趋势图，将优先按图片发送 Telegram")
        else:
            logger.info(f"告警 {alertname} 未生成趋势图，将按文本发送 Telegram")
        
        return image_bytes
    
    def _filter_image_channels(
        self,
        target_channels: List[str],
        alert_status: str,
    ) -> List[Channel]:
        """
        过滤出需要图片的 Telegram 渠道
        
        Args:
            target_channels: 目标渠道列表
            alert_status: 告警状态
            
        Returns:
            需要图片的渠道列表
        """
        # 复用传入的 channel_filter 实例，避免重复创建
        if self._channel_filter:
            return self._channel_filter.filter_image_channels(target_channels, alert_status)
        # 如果没有传入，则创建临时实例（向后兼容）
        from .channel_filter import ChannelFilter
        channel_filter = ChannelFilter(self.channels)
        return channel_filter.filter_image_channels(target_channels, alert_status)
