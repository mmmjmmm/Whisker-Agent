# WhiskerAgent Sandbox

`sandbox/` 是 Agent 任务运行环境。镜像基于 Ubuntu 22.04，内置 Python、Node.js、Chromium、Shell、文件 API、CDP、VNC 和 WebSocket VNC 代理。后端 API 可以复用固定沙箱，也可以按任务动态创建独立沙箱容器。

## 技术栈

- Ubuntu 22.04
- Python 3.10、FastAPI、Uvicorn
- Node.js 24
- Chromium
- Xvfb、x11vnc、websockify、socat
- Supervisor

## 进程与端口

沙箱容器由 Supervisor 管理进程：

| 进程         | 端口   | 说明                                          |
| ------------ | ------ | --------------------------------------------- |
| `app`        | `8080` | 沙箱 FastAPI，提供文件、Shell、Supervisor API |
| `xvfb`       | -      | 虚拟显示器 `:1`                               |
| `chrome`     | `8222` | Chromium DevTools 原始端口                    |
| `socat`      | `9222` | CDP 对外代理端口                              |
| `x11vnc`     | `5900` | VNC RFB 服务                                  |
| `websockify` | `5901` | VNC WebSocket 代理                            |

在根目录 Docker Compose 部署中，这些端口只暴露在 `manus-network` 内部，不直接映射到宿主机。

## API 路由

所有路由统一挂载在 `/api` 前缀下。

### 文件

| 方法   | 路径                                   | 说明                                  |
| ------ | -------------------------------------- | ------------------------------------- |
| `POST` | `/api/file/read-file`                  | 读取文件，可指定行号、最大长度和 sudo |
| `POST` | `/api/file/write-file`                 | 写入文件，支持追加和首尾换行控制      |
| `POST` | `/api/file/replace-in-file`            | 替换文件中的指定字符串                |
| `POST` | `/api/file/search-in-file`             | 使用正则搜索文件内容                  |
| `POST` | `/api/file/find-files`                 | 按目录和 glob 查找文件                |
| `POST` | `/api/file/upload-file`                | 上传文件到沙箱路径                    |
| `GET`  | `/api/file/download-file?filepath=...` | 下载沙箱文件                          |
| `POST` | `/api/file/check-file-exists`          | 检查文件是否存在                      |
| `POST` | `/api/file/delete-file`                | 删除文件                              |

### Shell

| 方法   | 路径                           | 说明                    |
| ------ | ------------------------------ | ----------------------- |
| `POST` | `/api/shell/exec-command`      | 在 Shell 会话中执行命令 |
| `POST` | `/api/shell/read-shell-output` | 读取 Shell 输出         |
| `POST` | `/api/shell/wait-process`      | 等待进程结束            |
| `POST` | `/api/shell/write-shell-input` | 向进程写入输入          |
| `POST` | `/api/shell/kill-process`      | 终止 Shell 会话进程     |

### Supervisor

| 方法   | 路径                                 | 说明                               |
| ------ | ------------------------------------ | ---------------------------------- |
| `GET`  | `/api/supervisor/status`             | 查看沙箱内所有 Supervisor 进程状态 |
| `POST` | `/api/supervisor/stop-all-processes` | 停止所有子进程                     |
| `POST` | `/api/supervisor/shutdown`           | 关闭 Supervisor                    |
| `POST` | `/api/supervisor/restart`            | 重启所有子进程                     |
| `POST` | `/api/supervisor/activate-timeout`   | 设置超时销毁                       |
| `POST` | `/api/supervisor/extend-timeout`     | 延长超时销毁时间                   |
| `POST` | `/api/supervisor/cancel-timeout`     | 取消超时销毁                       |
| `GET`  | `/api/supervisor/timeout-status`     | 查看超时销毁状态                   |

## 配置

沙箱 FastAPI 读取 `.env` 或环境变量：

```bash
LOG_LEVEL=INFO
SERVER_TIMEOUT_MINUTES=60
```

Supervisor 还会读取容器环境变量：

```bash
CHROME_ARGS=   # 追加 Chromium 启动参数
UVI_ARGS=      # 追加 Uvicorn 参数
```

## 本地开发

仅调试沙箱 FastAPI：

```bash
cd sandbox
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

这种方式只启动 API，不会启动 Chromium、VNC、Xvfb 等 Supervisor 管理的进程。

调试完整沙箱镜像：

```bash
cd sandbox
docker build -t manus-sandbox .
docker run --rm -it \
  -p 8080:8080 \
  -p 9222:9222 \
  -p 5901:5901 \
  manus-sandbox
```

## 与 API 的连接方式

动态沙箱模式：

```bash
SANDBOX_ADDRESS=
SANDBOX_IMAGE=manus-sandbox
SANDBOX_NETWORK=manus-network
SANDBOX_TTL_MINUTES=60
```

固定沙箱模式：

```bash
SANDBOX_ADDRESS=manus-sandbox
```

动态模式由 API 通过 Docker Socket 创建容器，适合任务隔离；固定模式复用 `docker-compose.yml` 中的 `manus-sandbox`，适合本地跑通。`SANDBOX_ADDRESS` 只填写主机名或 IP，不填写端口；API 固定访问沙箱的 `8080`、`9222` 和 `5901` 端口。
