# WhiskerAgent API

`api/` 是 WhiskerAgent 的后端服务，基于 FastAPI 提供任务会话、Agent 调度、配置管理、文件处理、Skill 管理、Trace 观测和沙箱代理能力。

## 技术栈

- Python 3.12+
- FastAPI、Uvicorn、SSE、WebSocket
- SQLAlchemy Async、asyncpg、Alembic
- Redis Stream
- OpenAI 兼容 LLM 客户端
- MCP、A2A、Playwright、Docker SDK
- 阿里云 OSS

## 目录结构

```text
api/
├── app/
│   ├── application/          # 应用服务，编排会话、Agent、文件、Skill、Trace
│   ├── domain/               # 领域模型、仓储接口、Agent/Flow/Tool/Skill/Trace 核心逻辑
│   ├── infrastructure/       # 外部实现：PostgreSQL、Redis、OSS、Docker Sandbox、LLM、MCP 等
│   ├── interfaces/           # FastAPI 路由、请求响应模型、异常处理、依赖组装
│   └── main.py               # FastAPI 入口，启动时执行数据库迁移并初始化客户端
├── alembic/                  # 数据库迁移脚本
├── core/config.py            # 环境变量配置
├── tests/                    # Pytest 测试
├── config.yaml               # Agent 运行配置：LLM、Agent、MCP、A2A
├── dev.sh                    # 本地 reload 启动脚本
├── run.sh                    # 容器启动脚本
├── pyproject.toml
├── requirements.txt
└── Dockerfile
```

## 配置来源

API 同时读取环境变量和 `config.yaml`：

| 来源              | 说明                                                 |
| ----------------- | ---------------------------------------------------- |
| `.env` / 环境变量 | 运行环境、日志、PostgreSQL、Redis、OSS、沙箱连接参数 |
| `config.yaml`     | LLM、Agent、MCP Server、A2A Server 配置              |

核心环境变量见根目录 `.env.example`。容器部署时由根目录 `docker-compose.yml` 通过 `env_file: .env` 注入。

`config.yaml` 中的敏感字段只应放部署环境真实值，不要提交生产 Key。

## API 路由

所有路由统一挂载在 `/api` 前缀下。

| 方法           | 路径                                                    | 说明                                      |
| -------------- | ------------------------------------------------------- | ----------------------------------------- |
| `GET`          | `/api/status`                                           | 检查 FastAPI、PostgreSQL、Redis、OSS 状态 |
| `GET` / `POST` | `/api/app-config/llm`                                   | 获取或更新 LLM 配置，返回时隐藏 `api_key` |
| `GET` / `POST` | `/api/app-config/agent`                                 | 获取或更新 Agent 通用配置                 |
| `GET` / `POST` | `/api/app-config/mcp-servers`                           | 获取或新增 MCP Server                     |
| `POST`         | `/api/app-config/mcp-servers/{server_name}/enabled`     | 启用或禁用 MCP Server                     |
| `POST`         | `/api/app-config/mcp-servers/{server_name}/delete`      | 删除 MCP Server                           |
| `GET` / `POST` | `/api/app-config/a2a-servers`                           | 获取或新增 A2A Server                     |
| `POST`         | `/api/app-config/a2a-servers/{a2a_id}/enabled`          | 启用或禁用 A2A Server                     |
| `POST`         | `/api/app-config/a2a-servers/{a2a_id}/delete`           | 删除 A2A Server                           |
| `GET` / `POST` | `/api/app-config/skills`                                | 获取 Skill 列表或上传 Skill ZIP           |
| `GET`          | `/api/app-config/skills/{skill_id}`                     | 获取 Skill 详情                           |
| `POST`         | `/api/app-config/skills/{skill_id}/enabled`             | 启用或禁用 Skill                          |
| `POST`         | `/api/app-config/skills/{skill_id}/delete`              | 删除 Skill                                |
| `POST`         | `/api/files`                                            | 上传文件到 OSS 并登记文件信息             |
| `GET`          | `/api/files/{file_id}`                                  | 获取文件元信息                            |
| `GET`          | `/api/files/{file_id}/download`                         | 下载文件                                  |
| `GET` / `POST` | `/api/sessions`                                         | 获取会话列表或创建会话                    |
| `POST`         | `/api/sessions/stream`                                  | SSE 流式订阅会话列表                      |
| `GET`          | `/api/sessions/{session_id}`                            | 获取会话详情和事件列表                    |
| `POST`         | `/api/sessions/{session_id}/chat`                       | SSE 流式对话                              |
| `POST`         | `/api/sessions/{session_id}/stop`                       | 停止会话任务                              |
| `POST`         | `/api/sessions/{session_id}/delete`                     | 删除会话                                  |
| `POST`         | `/api/sessions/{session_id}/clear-unread-message-count` | 清空未读计数                              |
| `GET`          | `/api/sessions/{session_id}/files`                      | 获取会话文件列表                          |
| `POST`         | `/api/sessions/{session_id}/file`                       | 读取会话沙箱中的文件内容                  |
| `POST`         | `/api/sessions/{session_id}/shell`                      | 读取会话沙箱中的 Shell 输出               |
| `GET`          | `/api/sessions/{session_id}/traces`                     | 获取会话 Trace 列表                       |
| `GET`          | `/api/sessions/{session_id}/traces/{trace_id}`          | 获取 Trace 详情                           |
| `GET`          | `/api/sessions/{session_id}/trace-metrics`              | 获取会话 Trace 指标                       |
| `WS`           | `/api/sessions/{session_id}/vnc`                        | 代理沙箱 VNC WebSocket                    |

## 本地开发

### 1. 准备依赖

API 运行需要 PostgreSQL、Redis，并在执行任务时需要沙箱。

当前根目录 `docker-compose.yml` 默认只把 Nginx 暴露到宿主机，不暴露 PostgreSQL、Redis 和 Sandbox 端口。因此宿主机本地运行 API 时，需要满足以下任一条件：

- 使用本机可访问的 PostgreSQL、Redis 和 Sandbox。
- 临时为 compose 中的依赖容器增加端口映射。
- 直接通过 Docker Compose 运行 `manus-api`，让 API 在 `manus-network` 内访问依赖。

容器化运行 API 时，从仓库根目录执行：

```bash
docker compose up -d --build manus-api
```

如果本地 API 需要动态创建沙箱，还需要本机 Docker 可用，并让 API 能访问 Docker Socket。

### 2. 安装依赖

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -r requirements.txt
playwright install
```

### 3. 配置本地环境

本地直接运行 API 时，`.env` 中数据库和 Redis 要指向宿主机可访问的地址：

```bash
SQLALCHEMY_DATABASE_URI=postgresql+asyncpg://postgres:postgres@localhost:5432/manus
REDIS_HOST=localhost
REDIS_PORT=6379
```

如果 API 在 compose 网络中运行并复用固定沙箱：

```bash
SANDBOX_ADDRESS=manus-sandbox
```

如果 API 在宿主机运行并复用手动映射到本机的沙箱，`SANDBOX_ADDRESS` 写主机名或 IP，不带端口；沙箱端口由代码固定使用 `8080`、`9222` 和 `5901`：

```bash
SANDBOX_ADDRESS=127.0.0.1
```

### 4. 启动 API

```bash
cd api
./dev.sh
```

或直接运行：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问：

```text
http://localhost:8000/docs
```

## 数据库迁移

API 生命周期启动时自动执行：

```bash
alembic upgrade head
```

手动命令见 [alembic/README](./alembic/README)。

## 测试

```bash
cd api
pytest
```

当前测试覆盖状态接口、Skill、Trace、Agent Skill 注入、基础设施模型和部分外部实现。

## Docker 部署

API 通常不单独部署，而是由根目录 `docker-compose.yml` 统一构建和启动。以下命令从仓库根目录执行：

```bash
docker compose up -d --build manus-api
```

容器启动命令为 `./run.sh`，监听 `0.0.0.0:8000`。
