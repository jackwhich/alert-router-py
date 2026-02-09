"""
应用启动入口
从 config.yaml 读取配置并启动 uvicorn 服务器
"""
import uvicorn
from alert_router.config import load_config

if __name__ == "__main__":
    # 加载配置
    CONFIG, _ = load_config()
    
    # 从配置读取服务器设置
    server_config = CONFIG.get("server", {})
    host = server_config.get("host", "0.0.0.0")
    port = server_config.get("port", 8080)
    
    # 从环境变量读取工作进程数和超时时间（如果设置了）
    import os
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
