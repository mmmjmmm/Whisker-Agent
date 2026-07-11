# MoocManus API 服务

基于 FastAPI 构建的后端 API 服务，提供会话管理、AI Agent 调度、文件处理、沙箱管理等核心功能。

## 技术栈

- Python 3.12+
- FastAPI + Uvicorn
- SQLAlchemy (asyncpg) + Alembic
- Redis (异步客户端)
- Docker SDK (沙箱管理)
- Playwright (浏览器自动化)
- WebSocket (VNC 代理转发)

## 项目结构

```
api/
├── app/
│   ├── application/       # 应用层（业务服务编排）
│   ├── domain/            # 领域层（核心业务逻辑）
│   ├── infrastructure/    # 基础设施层（外部服务集成）
│   │   ├── external/      # 沙箱、浏览器等外部服务
│   │   ├── storage/       # PostgreSQL、Redis、OSS 存储
│   │   └── models/        # ORM 模型
│   ├── interfaces/        # 接口层（API 端点）
│   │   ├── endpoints/     # 路由定义
│   │   └── schemas/       # 请求/响应模型
│   └── main.py            # 应用入口
├── alembic/               # 数据库迁移
├── core/
│   └── config.py          # 配置管理（Pydantic Settings）
├── .env                   # 环境变量
├── config.yaml            # 应用配置（LLM、MCP、A2A）
├── Dockerfile
├── requirements.txt
└── run.sh                 # 启动脚本
```

## API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 健康检查 |
| GET/POST | `/api/app-config` | 应用配置管理 |
| POST | `/api/files` | 文件上传 |
| GET | `/api/files/{id}/download` | 文件下载 |
| POST | `/api/sessions` | 创建会话 |
| POST | `/api/sessions/stream` | SSE 流式获取会话列表 |
| GET | `/api/sessions/{id}` | 获取会话详情 |
| POST | `/api/sessions/{id}/chat` | SSE 流式对话 |
| WS | `/api/sessions/{id}/vnc` | VNC WebSocket 代理 |

### 多 Agent Team 模式

`POST /api/sessions/{id}/chat` 的 `mode` 默认是 `react`，显式传入 `team` 才启用动态 DAG 编排：

```json
{
  "message": "调研主流多 Agent 架构并形成报告",
  "attachments": [],
  "mode": "team"
}
```

Team 模式最多生成 5 个任务节点，同时运行不超过 3 个 Worker，每个失败节点最多重试 1 次。`analysis`、`search`、`file_read` 节点允许并行；Browser、FileWrite、Shell、MCP、A2A 节点串行独占共享运行环境。运行期间只允许停止，不接受追加消息；此时再次发送消息会返回 HTTP 409。

SSE 在原有事件之外会发送 `task_graph`、`task`，并在 Team 工具事件中附带 `graph_id`、`task_id`、`agent_id`、`attempt`。首期不支持 API 进程重启后的任务续跑；下次读取会话时会把失联运行收敛为 `process_interrupted`。该功能不要求额外修改 `.env` 或 `config.yaml`。

## 本地开发

### 环境准备

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 激活虚拟环境
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# 3. 安装依赖
pip install uv
uv pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
playwright install
```

### 配置环境变量

修改 `.env` 文件，将数据库和 Redis 地址改为 `localhost`：

```bash
SQLALCHEMY_DATABASE_URI=postgresql+asyncpg://postgres:postgres@localhost:5432/manus
REDIS_HOST=localhost
REDIS_PORT=6379
SANDBOX_ADDRESS=         # 留空则动态创建沙箱容器
```

### 启动服务

```bash
# 启动开发服务器
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后访问 `http://localhost:8000/docs` 查看 API 文档。

### 数据库迁移

```bash
# 生成迁移脚本
alembic revision --autogenerate -m "描述"

# 执行迁移
alembic upgrade head

# 回滚
alembic downgrade -1
```

## Docker 部署

API 服务通过根目录的 `docker-compose.yml` 统一部署，无需单独构建。环境变量由根目录 `.env` 文件提供。
