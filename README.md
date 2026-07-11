# MoocManus - 通用 AI Agent 系统

MoocManus 是一个通用的 AI Agent 系统，支持完全私有化部署，使用 A2A + MCP 连接 Agent/Tool，同时支持在沙箱中运行各种内置工具和操作。

## 项目结构

```
mooc-manus/
├── api/              # 后端 API 服务（FastAPI）
├── ui/               # 前端服务（Next.js）
├── sandbox/          # 沙箱服务（Ubuntu + Chrome + VNC）
├── nginx/            # Nginx 网关配置
│   ├── nginx.conf
│   └── conf.d/
│       └── default.conf
├── docker-compose.yml
├── .env              # 环境变量配置（需自行创建）
└── README.md
```

## 快速部署

### 前置要求

- Docker >= 20.10
- Docker Compose >= 2.0

### 一键启动

1. **配置环境变量**

   根目录有 `.env.example` 模板，先复制一份作为本机配置：

   ```bash
   cp .env.example .env
   ```

   至少检查这些配置：

   ```bash
   # 对外访问端口
   NGINX_PORT=8088

   # 如果修改 Postgres 用户/密码/库名，也要同步修改 SQLALCHEMY_DATABASE_URI
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=manus
   SQLALCHEMY_DATABASE_URI=postgresql+asyncpg://postgres:postgres@manus-postgres:5432/manus

   # 文件上传/下载依赖阿里云 OSS
   OSS_ACCESS_KEY_ID=
   OSS_ACCESS_KEY_SECRET=
   OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
   OSS_BUCKET=
   OSS_PUBLIC_BASE_URL=
   ```

2. **配置 AI 模型**

   修改 `api/config.yaml` 中的 LLM 配置，填入自己的模型服务地址和 API Key：

   ```yaml
   llm_config:
     base_url: https://api.deepseek.com/
     api_key: your_api_key_here
     model_name: deepseek-reasoner
   ```

3. **启动所有服务**

   ```bash
   docker compose up -d --build
   ```

4. **访问系统**

   本机访问：

   ```text
   http://localhost:8088
   ```

   如果改了 `NGINX_PORT`，把端口换成对应值。

5. **检查服务状态**

   ```bash
   docker compose ps
   docker compose logs -f manus-api
   ```

   API 启动时会自动执行 Alembic 数据库迁移，不需要手动建表。

### 服务架构

```
                    ┌─────────────┐
     Port 8088      │   Nginx     │
   ─────────────────►  (Gateway)  │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │ /                       │ /api
              ▼                         ▼
       ┌─────────────┐          ┌─────────────┐
       │  Next.js UI │          │  FastAPI     │
       │  (Port 3000)│          │  (Port 8000) │
       └─────────────┘          └──────┬──────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                   │
                    ▼                  ▼                   ▼
             ┌───────────┐     ┌───────────┐       ┌───────────┐
             │ PostgreSQL│     │   Redis   │       │  Sandbox  │
             │(Port 5432)│     │(Port 6379)│       │ (VNC/HTTP)│
             └───────────┘     └───────────┘       └───────────┘
```

### 容器列表

| 容器名称 | 服务 | 说明 |
|---------|------|------|
| manus-nginx | Nginx | 反向代理网关，唯一对外暴露端口 |
| manus-ui | Next.js | 前端 UI 服务 |
| manus-api | FastAPI | 后端 API 服务 |
| manus-postgres | PostgreSQL | 数据库 |
| manus-redis | Redis | 缓存 |
| manus-sandbox | Sandbox | 沙箱环境（Chrome + VNC） |

### 常用命令

```bash
# 启动所有服务（后台运行）
docker compose up -d --build

# 查看所有服务状态
docker compose ps

# 查看服务日志
docker compose logs -f              # 所有服务
docker compose logs -f manus-api    # 仅 API 服务
docker compose logs -f manus-ui     # 仅 UI 服务

# 重启单个服务
docker compose restart manus-api

# 停止所有服务
docker compose down

# 停止并清除数据卷（谨慎操作）
docker compose down -v
```

### 沙箱模式

`.env.example` 默认使用动态沙箱：

```bash
SANDBOX_ADDRESS=
SANDBOX_IMAGE=manus-sandbox
SANDBOX_NETWORK=manus-network
```

这种模式会让 API 通过 Docker Socket 为任务创建独立沙箱容器。若只想复用 `docker-compose.yml` 中固定的 `manus-sandbox` 服务，可以改成：

```bash
SANDBOX_ADDRESS=manus-sandbox
```

### 启用 HTTPS

1. 将 SSL 证书放入 `nginx/ssl/` 目录：
   - `fullchain.pem`（证书链）
   - `privkey.pem`（私钥）

2. 修改 `nginx/conf.d/default.conf`，取消 SSL server 块注释

3. 修改 `docker-compose.yml`，取消 443 端口映射注释

4. 重启 Nginx：
   ```bash
   docker compose restart manus-nginx
   ```

## 本地开发

各子项目的本地开发说明请参考对应目录下的 README：

- [API 服务](./api/README.md)
- [前端 UI](./ui/README.md)
- [沙箱服务](./sandbox/README.md)
