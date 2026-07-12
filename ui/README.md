# WhiskerAgent UI

`ui/` 是 WhiskerAgent 的前端应用，基于 Next.js App Router 构建。它提供会话列表、任务对话、文件上传、配置管理、Skill 管理、工具调用展示、Trace 面板、文件预览和沙箱 VNC 预览。

## 技术栈

- Node.js 22+
- Next.js 16、React 19、TypeScript
- Tailwind CSS 4
- Radix UI、lucide-react、sonner
- noVNC
- react-markdown、remark-gfm

## 目录结构

```text
ui/
├── src/
│   ├── app/                  # App Router 页面：/、/sessions、/sessions/[id]
│   ├── components/           # 业务组件：聊天、会话、设置、预览、Trace、VNC
│   ├── components/ui/        # 基础 UI 组件
│   ├── components/tool-use/  # 工具调用事件展示组件
│   ├── config/               # 前端静态配置
│   ├── hooks/                # 会话和响应式相关 Hooks
│   ├── lib/api/              # API 客户端与类型
│   ├── lib/                  # SSE 事件和工具函数
│   └── providers/            # 全局状态 Provider
├── public/                   # 静态资源
├── docs/design/              # UI 设计参考图
├── next.config.ts
├── package.json
├── package-lock.json
└── Dockerfile
```

## 页面与功能

| 页面             | 说明                                                           |
| ---------------- | -------------------------------------------------------------- |
| `/`              | 首页输入任务，选择 `react` 或 `team` 模式，上传附件后创建会话  |
| `/sessions`      | 重定向到 `/`                                                   |
| `/sessions/[id]` | 会话详情页，展示事件流、任务进度、工具调用、文件、Trace 和 VNC |

主要组件：

- `left-panel`、`session-list`：会话列表和实时刷新。
- `chat-input`、`chat-message`、`session-detail-view`：任务输入和事件时间线。
- `manus-settings`：LLM、Agent、MCP、A2A 配置。
- `skill-settings`：Skill 上传、查看、启用、删除。
- `trace-panel`：Trace 列表、详情和指标展示。
- `file-preview-panel`、`tool-preview-panel`、`vnc-viewer`：文件、工具和沙箱浏览器预览。

## API 地址

前端通过 `NEXT_PUBLIC_API_BASE_URL` 配置后端地址：

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8088/api
```

当前代码默认值是 `http://localhost:8088/api`。Docker 构建时由根目录 `docker-compose.yml` 注入：

```yaml
args:
  NEXT_PUBLIC_API_BASE_URL: /api
```

生产容器中使用 `/api`，由 Nginx 反向代理到后端。

## 本地开发

```bash
cd ui
npm install
npm run dev
```

开发服务默认运行在：

```text
http://localhost:3000
```

如果 API 没有通过根目录 Nginx 暴露在 `8088`，启动前显式设置：

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api npm run dev
```

## 构建与检查

```bash
cd ui
npm run build
npm run start
npm run lint
```

`next.config.ts` 使用 `output: "standalone"`，Dockerfile 会复制 standalone 产物到生产镜像。

## Docker 部署

UI 通常由根目录 `docker-compose.yml` 统一部署：

```bash
docker compose up -d --build manus-ui
```

镜像构建阶段：

1. `deps`：通过 `npm ci` 安装依赖。
2. `builder`：执行 `npm run build`。
3. `runner`：以 standalone 模式运行 `server.js`，监听 `3000`。

## 设计素材

`ui/docs/design/` 保存当前界面的设计参考图，包含首页、配置页、文件预览、VNC、命令行预览和任务列表等状态。
