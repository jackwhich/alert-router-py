#!/bin/bash

# 告警路由服务启动脚本

echo "正在启动告警路由服务..."

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3"
    exit 1
fi

# 检查依赖是否安装
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "正在安装依赖..."
    pip3 install -r requirements.txt
fi

# 检查配置文件是否存在
if [ ! -f "config.yaml" ]; then
    echo "错误: 未找到 config.yaml 配置文件"
    exit 1
fi

# 启动服务
echo "启动服务在 http://0.0.0.0:8080"
uvicorn app:app --host 0.0.0.0 --port 8080
