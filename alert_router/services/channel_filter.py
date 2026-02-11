"""
渠道过滤工具

提取渠道过滤逻辑，消除重复代码
"""
from typing import Dict, List

from ..core.models import Channel


class ChannelFilter:
    """渠道过滤器"""
    
    def __init__(self, channels: Dict[str, Channel]):
        """
        初始化渠道过滤器
        
        Args:
            channels: 渠道字典
        """
        self.channels = channels
    
    def filter_image_channels(
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
        image_channels = []
        for channel_name in target_channels:
            channel = self.channels.get(channel_name)
            if not channel:
                continue
            if channel.type != "telegram" or not channel.enabled:
                continue
            if self._should_skip_by_status(channel, alert_status):
                continue
            if not channel.image_enabled:
                continue
            image_channels.append(channel)
        return image_channels
    
    def filter_enabled_channels(
        self,
        target_channels: List[str],
        alert_status: str,
    ) -> List[Channel]:
        """
        过滤出启用的渠道
        
        Args:
            target_channels: 目标渠道列表
            alert_status: 告警状态
            
        Returns:
            启用的渠道列表
        """
        enabled_channels = []
        for channel_name in target_channels:
            channel = self.channels.get(channel_name)
            if not channel or not channel.enabled:
                continue
            if self._should_skip_by_status(channel, alert_status):
                continue
            enabled_channels.append(channel)
        return enabled_channels

    @staticmethod
    def _should_skip_by_status(channel: Channel, alert_status: str) -> bool:
        """检查是否应该跳过该渠道（基于告警状态）"""
        return alert_status == "resolved" and not channel.send_resolved
