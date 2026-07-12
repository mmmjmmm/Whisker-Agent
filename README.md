# MoocManus

MoocManus 是一个可私有化部署的通用 AI Agent 系统。当前代码由 FastAPI 后端、Next.js 前端、沙箱服务、PostgreSQL、Redis 和 Nginx 网关组成，支持会话式任务执行、文件上传与预览、MCP/A2A 工具配置、Skill 管理、任务 Trace 观测，以及通过 VNC 查看沙箱浏览器。

## 项目结构

```text
mooc-manus/
├── api/                 # FastAPI 后端，负责任务会话、Agent 编排、配置、文件、Trace
├── ui/                  # Next.js 前端，负责聊天界面、配置页、文件/工具/VNC/Trace 预览
├── sandbox/             # 沙箱镜像，内置 Python、Node.js、Chromium、Shell、VNC
├── nginx/               # Nginx 反向代理配置
├── docs/                # 设计与实现文档
├── docker-compose.yml   # 一体化部署编排
├── .env.example         # 根部署环境变量模板
└── README.md
```

## 核心能力

- 会话任务：创建会话、流式对话、停止/删除任务、未读计数、会话列表 SSE 刷新。
- Agent 模式：支持 `react` 单 Agent 模式和 `team` 多任务编排模式。
- 工具接入：通过配置文件和 API 管理 LLM、Agent、MCP Server、A2A Server。
- Skill 管理：上传 Skill ZIP，解析 `SKILL.md`，启用、禁用、查看和删除 Skill。
- 文件能力：上传文件到 OSS，查看会话文件列表，读取沙箱文件，下载文件。
- 沙箱能力：动态或固定沙箱，支持 Shell、文件操作、Chromium、CDP、VNC/WebSocket。
- Trace 观测：按会话查看 Trace 列表、详情、指标和 LLM/工具调用统计。

## 快速部署

### 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- 可访问目标 LLM 服务
- 如需使用上传、下载和文件预览能力，需要准备阿里云 OSS 配置

### 1. 准备环境变量

根目录提供 `.env.example` 模板。首次部署时复制为本机 `.env` 后按需填写：

```bash
cp .env.example .env
```

重点配置项：

```bash
# 对外访问端口，默认 http://localhost:8088
NGINX_PORT=8088

# PostgreSQL。若修改账号、密码或库名，需要同步修改 SQLALCHEMY_DATABASE_URI
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=manus
SQLALCHEMY_DATABASE_URI=postgresql+asyncpg://postgres:postgres@manus-postgres:5432/manus

# Redis。当前 compose 未设置 Redis 密码
REDIS_HOST=manus-redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# OSS。文件上传、下载、截图预览依赖这些配置
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_SCHEME=https
OSS_BUCKET=
OSS_PUBLIC_BASE_URL=

# 沙箱。留空 SANDBOX_ADDRESS 时使用动态沙箱，需要挂载 Docker Socket
SANDBOX_ADDRESS=
SANDBOX_IMAGE=manus-sandbox
SANDBOX_NAME_PREFIX=manus-sandbox
SANDBOX_TTL_MINUTES=60
SANDBOX_NETWORK=manus-network
```

不要把真实 `.env` 提交到代码仓库。

### 2. 配置 Agent 运行参数

后端读取 `api/config.yaml`，主要包含：

- `llm_config`：LLM 的 `base_url`、`api_key`、`model_name`、`temperature`、`max_tokens`。
- `agent_config`：最大迭代次数、搜索结果数、Team 任务并发和超时等。
- `mcp_config`：MCP Server，支持 `stdio`、`sse`、`streamable_http`。
- `a2a_config`：A2A Server 列表。

部署前应替换示例 Key 和 URL，只保留当前环境需要的服务配置。

### 3. 启动

```bash
docker compose up -d --build
```

启动后访问：

```text
http://localhost:8088
```

如果修改了 `NGINX_PORT`，使用对应端口访问。

### 4. 检查状态

```bash
docker compose ps
docker compose logs -f manus-api
```

API 进程启动时会自动执行 `alembic upgrade head`，无需手动建表。

## 服务架构

```text
Browser
  |
  | http://localhost:${NGINX_PORT:-8088}
  v
Nginx (manus-nginx)
  |-- /      -> Next.js UI (manus-ui:3000)
  |-- /api/* -> FastAPI API (manus-api:8000)
                  |-- PostgreSQL (manus-postgres:5432)
                  |-- Redis (manus-redis:6379)
                  |-- OSS
                  |-- Docker Sandbox / fixed sandbox
```

## 容器

| 容器 | 说明 |
| --- | --- |
| `manus-nginx` | 统一入口，代理 UI、API、SSE、WebSocket |
| `manus-ui` | Next.js 前端服务 |
| `manus-api` | FastAPI 后端服务，启动时自动迁移数据库 |
| `manus-postgres` | PostgreSQL 16 |
| `manus-redis` | Redis 7 |
| `manus-sandbox` | 固定沙箱镜像和动态沙箱镜像来源 |

## 常用命令

```bash
# 启动所有服务
docker compose up -d --build

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f
docker compose logs -f manus-api
docker compose logs -f manus-ui
docker compose logs -f manus-sandbox

# 重启单个服务
docker compose restart manus-api

# 停止服务
docker compose down

# 停止并删除数据卷，谨慎使用
docker compose down -v
```

## 沙箱模式

默认 `.env.example` 中 `SANDBOX_ADDRESS=` 为空，API 会通过 Docker Socket 为任务动态创建沙箱容器。当前 `docker-compose.yml` 已挂载：

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

如果只想复用 compose 中的固定沙箱容器，可以在 `.env` 中设置：

```bash
SANDBOX_ADDRESS=manus-sandbox
```

动态模式适合多会话隔离；固定模式适合快速跑通和调试。

## 本地开发

子项目说明：

- [API 服务](./api/README.md)
- [前端 UI](./ui/README.md)
- [沙箱服务](./sandbox/README.md)
- [数据库迁移](./api/alembic/README)

容器化开发可以直接启动整套服务：

```bash
docker compose up -d --build
```

如果要在宿主机上分别启动 API 或 UI，需要确认 API 能访问 PostgreSQL、Redis 和沙箱。当前 `docker-compose.yml` 默认不把 PostgreSQL、Redis、Sandbox 端口映射到宿主机，宿主机调试时需要使用本机依赖服务，或临时为这些容器增加端口映射。

## HTTPS

当前 Nginx 配置默认只监听 HTTP。若要启用 HTTPS：

1. 将证书放入 `nginx/ssl/`，例如 `fullchain.pem` 和 `privkey.pem`。
2. 修改 `nginx/conf.d/default.conf` 中的 SSL server 配置。
3. 修改 `docker-compose.yml`，打开 `443:443` 端口映射。
4. 重启 Nginx：

```bash
docker compose restart manus-nginx
```
