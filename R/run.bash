#!/bin/bash
set -e

cleanup() {
    # SERVER_PID有值说明server已启动，终止掉
    if [ -n "$SERVER_PID" ]; then
        echo "Stopping LLM Server (PID: $SERVER_PID)..."
        kill $SERVER_PID 2>/dev/null || true  # 2>/dev/null 防止已终止时报错
    fi
}
# 只要退出都会执行 cleanup
trap cleanup EXIT

# 工作负载分析器
python "./workload analyzer/WorkloadParser.py"

# 旋钮选择器(两个匿名化脚本的功能完全相同)
python "./knob selector/anonymize.py"
python "./knob selector/knob_select.py"

# 范围剪枝器
python "./range pruner/anonymize.py"
python "./range pruner/range_pruner.py"

# 配置推荐器
# 获取LLM_server端口号，赋值给LLM_PORT
LLM_PORT=$(python - <<'PY'
import configparser
config = configparser.ConfigParser()
config.read('./config.ini')
print(config['configuration recommender']['LLM_server_port'])
PY
)

# LLM_PORT端口的占用情况
if python - <<PY
import socket, sys
port = int("$LLM_PORT")
s = socket.socket()
try:
    s.connect(("127.0.0.1", port))
    sys.exit(0)
except Exception:
    sys.exit(1)
finally:
    s.close()
PY
then
    # 端口被占用，LLM_server已运行
    echo "LLM_server 已在端口 ${LLM_PORT} 运行，跳过启动。"
    # 不记录，避免误杀之前运行的进程
    SERVER_PID=""
else
    # 端口未被占用，启动LLM_server
    echo "Starting LLM Server on port ${LLM_PORT}..."
    python "./configuration recommender/LLM_server.py" &
    SERVER_PID=$!  # 记录后台进程PID
    sleep 3
fi

python "./configuration recommender/DB_client.py"