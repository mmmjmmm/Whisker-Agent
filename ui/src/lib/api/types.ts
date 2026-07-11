/**
 * API 统一响应格式
 */
export type ApiResponse<T = unknown> = {
  code: number;
  msg: string;
  data: T | null;
};

/**
 * 会话状态
 */
export type SessionStatus = "pending" | "running" | "waiting" | "completed";

/**
 * 执行状态
 */
export type ExecutionStatus = "pending" | "running" | "completed" | "failed";

/**
 * 工具事件状态
 */
export type ToolEventStatus = "calling" | "called";

export type AgentMode = "react" | "research_team";

export type RunStatus =
  | "pending"
  | "planning"
  | "running"
  | "reviewing"
  | "synthesizing"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled"
  | "interrupted";

export type ResearchTaskStatus =
  | "pending"
  | "ready"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "cancelled"
  | "timed_out"
  | "interrupted";

export type EventCorrelation = {
  event_id?: string;
  created_at?: number;
  schema_version?: string;
  session_id?: string | null;
  run_id?: string | null;
  task_id?: string | null;
  attempt_id?: string | null;
  agent_id?: string | null;
  parent_event_id?: string | null;
  sequence_no?: number | null;
};

/**
 * MCP 传输类型
 */
export type MCPTransport = "stdio" | "sse" | "streamable_http";

// ==================== 配置模块类型 ====================

/**
 * LLM 配置
 */
export type LLMConfig = {
  base_url?: string;
  api_key?: string;
  model_name?: string;
  temperature?: number;
  max_tokens?: number;
  [key: string]: unknown;
};

/**
 * Agent 通用配置
 */
export type AgentConfig = {
  max_iterations?: number;
  max_retries?: number;
  max_search_results?: number;
  [key: string]: unknown;
};

/**
 * MCP 服务器列表项（GET 响应）
 */
export type ListMCPServerItem = {
  server_name: string;
  enabled: boolean;
  transport: MCPTransport;
  tools: string[];
};

/**
 * MCP 服务器列表响应
 */
export type MCPServersData = {
  mcp_servers: ListMCPServerItem[];
};

/**
 * MCP 服务器配置（POST 请求体中单个服务器的配置）
 */
export type MCPServerConfig = {
  transport?: MCPTransport;
  enabled?: boolean;
  description?: string | null;
  env?: Record<string, unknown> | null;
  command?: string | null;
  args?: string[] | null;
  url?: string | null;
  headers?: Record<string, unknown> | null;
  [key: string]: unknown;
};

/**
 * MCP 配置（POST 新增 MCP 服务的请求体）
 */
export type MCPConfig = {
  mcpServers: Record<string, MCPServerConfig>;
  [key: string]: unknown;
};

/**
 * A2A 服务器列表项（GET 响应）
 */
export type ListA2AServerItem = {
  id: string;
  name: string;
  description: string;
  input_modes: string[];
  output_modes: string[];
  streaming: boolean;
  push_notifications: boolean;
  enabled: boolean;
};

/**
 * A2A 服务器列表响应
 */
export type A2AServersData = {
  a2a_servers: ListA2AServerItem[];
};

/**
 * 新增 A2A 服务器请求参数
 */
export type CreateA2AServerParams = {
  base_url: string;
};

// ==================== 文件模块类型 ====================

/**
 * 文件信息
 */
export type FileInfo = {
  id: string;
  filename: string;
  filepath: string;
  key: string;
  extension: string;
  content_type: string;
  size: number;
  [key: string]: unknown;
};

/**
 * 文件上传请求参数
 */
export type FileUploadParams = {
  file: File;
  session_id?: string;
};

// ==================== 会话模块类型 ====================

/**
 * 会话信息
 */
export type Session = {
  session_id: string;
  title: string;
  latest_message: string;
  latest_message_at: string;
  status: SessionStatus;
  unread_message_count: number;
  [key: string]: unknown;
};

/**
 * 会话列表响应
 */
export type SessionsData = {
  sessions: Session[];
};

/**
 * 创建会话请求参数
 */
export type CreateSessionParams = {
  title?: string;
  [key: string]: unknown;
};

/**
 * 聊天消息
 */
export type ChatMessage = {
  role: "user" | "assistant" | "system";
  message: string;
  attachments?: Array<{
    file_id: string;
    filename: string;
    [key: string]: unknown;
  }>;
  [key: string]: unknown;
};

/**
 * 聊天请求参数
 * message 为空时用于流式拉取未完成任务的事件列表
 */
export type ChatParams = {
  message?: string;
  attachments?: string[];
  mode?: AgentMode;
  budget_profile?: "default";
  event_id?: string;
  [key: string]: unknown;
};

export type RunBudget = {
  max_workers: number;
  max_tasks: number;
  max_graph_depth: number;
  max_research_waves: number;
  max_attempts_per_task: number;
  task_timeout_seconds: number;
  run_timeout_seconds: number;
  max_llm_calls: number;
  max_tool_calls: number;
  max_total_tokens: number;
};

export type RunUsage = {
  llm_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  worker_attempts: number;
  elapsed_ms: number;
};

export type AgentRun = {
  id: string;
  session_id: string;
  mode: AgentMode;
  status: RunStatus;
  goal: string;
  plan_version?: number;
  budget_snapshot?: RunBudget;
  usage: RunUsage;
  error?: { type?: string; message?: string; [key: string]: unknown } | null;
  heartbeat_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type ResearchTask = {
  id: string;
  run_id: string;
  plan_version: number;
  task_key: string;
  description: string;
  objective: string;
  capability_profile: "research_readonly" | "analysis";
  dependency_ids: string[];
  acceptance_criteria: string[];
  source_requirements: Record<string, unknown>;
  required: boolean;
  priority: number;
  status: ResearchTaskStatus;
  assigned_agent_id: string | null;
  result_summary: string | null;
  error: { type?: string; message?: string; [key: string]: unknown } | null;
  attempt_count: number;
  created_at?: string;
  updated_at?: string;
};

export type ResearchSource = {
  id: string;
  run_id: string;
  canonical_url: string;
  original_url: string;
  title: string;
  domain: string;
  publisher: string | null;
  published_at: string | null;
  retrieved_at: string;
  content_type: string;
  content_hash: string;
  source_class: string;
  metadata: Record<string, unknown>;
};

export type ResearchReview = {
  approved: boolean;
  issues: string[];
  conflicts: string[];
  missing_questions: string[];
  repair_tasks: unknown[];
};

export type ResearchUsage = EventCorrelation & {
  budget: Partial<RunBudget>;
  usage: Partial<RunUsage>;
  remaining: Record<string, number>;
};

/**
 * 会话详情（含事件列表，与 chat 流式响应格式一致）
 */
export type SessionDetail = Session & {
  events?: SSEEventData[];
};

/**
 * 计划步骤
 */
export type PlanStep = {
  id: string;
  description: string;
  status: ExecutionStatus;
  [key: string]: unknown;
};

/**
 * 计划事件
 */
export type PlanEvent = {
  steps: PlanStep[];
  [key: string]: unknown;
};

/**
 * 步骤事件
 */
export type StepEvent = {
  id: string;
  status: ExecutionStatus;
  description: string;
  [key: string]: unknown;
};

/**
 * 工具调用事件
 */
export type ToolEvent = {
  tool_call_id?: string;
  name: string;
  function: string;
  args: Record<string, unknown>;
  content?: unknown;
  status?: ToolEventStatus;
  run_id?: string | null;
  task_id?: string | null;
  attempt_id?: string | null;
  agent_id?: string | null;
  sequence_no?: number | null;
  [key: string]: unknown;
};

/**
 * SSE 事件类型
 */
export type SSEEventType =
  | "message"
  | "title"
  | "plan"
  | "step"
  | "tool"
  | "wait"
  | "done"
  | "error"
  | "run"
  | "research_plan"
  | "research_task"
  | "research_source"
  | "research_review"
  | "research_usage";

/**
 * SSE 事件数据
 */
export type SSEEventData =
  | { type: "message"; data: ChatMessage }
  | { type: "title"; data: { title: string } }
  | { type: "plan"; data: PlanEvent }
  | { type: "step"; data: StepEvent }
  | { type: "tool"; data: ToolEvent }
  | { type: "wait"; data: Record<string, unknown> }
  | { type: "done"; data: Record<string, unknown> }
  | { type: "error"; data: EventCorrelation & { error: string; scope?: string } }
  | {
      type: "run";
      data: EventCorrelation & {
        status: RunStatus;
        goal: string;
        usage: Partial<RunUsage>;
        error?: AgentRun["error"];
      };
    }
  | {
      type: "research_plan";
      data: EventCorrelation & { plan: Record<string, unknown>; status: string };
    }
  | {
      type: "research_task";
      data: EventCorrelation & { task: ResearchTask; status: ResearchTaskStatus };
    }
  | {
      type: "research_source";
      data: EventCorrelation & { source: ResearchSource };
    }
  | {
      type: "research_review";
      data: EventCorrelation & { review: ResearchReview };
    }
  | { type: "research_usage"; data: ResearchUsage };

/**
 * SSE 事件处理器
 */
export type SSEEventHandler = (event: SSEEventData) => void;

/**
 * 会话文件信息
 */
export type SessionFile = {
  id: string;
  filename: string;
  filepath: string;
  key: string;
  extension: string;
  content_type: string;
  size: number;
  [key: string]: unknown;
};

/**
 * 查看文件内容请求参数
 */
export type ViewFileParams = {
  filepath: string;
  [key: string]: unknown;
};

/**
 * 查看 Shell 输出请求参数
 */
export type ViewShellParams = {
  shell_session_id: string;
  [key: string]: unknown;
};
