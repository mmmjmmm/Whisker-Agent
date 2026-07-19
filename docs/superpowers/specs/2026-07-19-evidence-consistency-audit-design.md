# 资料一致性核验平台设计

日期：2026-07-19

## 背景

本设计面向一个异步资料一致性核验平台。用户上传多份资料文件和一个最终大文件，系统只在本次上传的封闭资料包内抽取可核验的显性事实声明，并输出异常项：冲突、证据不足、资料间冲突、需人工判断。系统不做开放式法规合规判定，不自动修改最终文件，不把模型常识或互联网内容作为通过依据。

当前代码库是 FastAPI + SQLAlchemy + PostgreSQL + Redis + OSS + Next.js 的 Agent 平台，已有文件上传/下载、会话任务、Redis Stream、Trace 和配置管理。现有文件能力只记录文件元信息和对象存储位置，没有文档解析、证据块、索引、原子声明、审计任务和人工复核状态。因此该能力应作为新的 `audit` 领域模块接入，而不是塞入现有聊天会话或通用文件服务。

## 调研依据

主流严肃系统的共同模式不是“把文件全部塞进大模型后直接给审计结论”，而是先建立可追溯资料集，再进行检索、标注、引用、复核和导出。

- Microsoft Purview eDiscovery 使用 case/review set 工作流，将资料加入 review set 后支持索引、搜索、筛选、标记、分析和导出。这说明审计类系统需要固定资料快照和 review 工作区。
- Relativity aiR for Review、eBrevia、Kira 等法律/合同审查系统强调文档审阅、提取、解释、引用来源和专家复核，而不是完全自动替代审计人员。
- Azure AI Search、Amazon Bedrock Knowledge Bases、Google Vertex AI RAG Engine、OpenAI File Search 体现了 RAG 主流工程路径：解析、chunk、embedding、向量/混合检索、重排、引用来源。
- Azure Document Intelligence、Amazon Textract、Google Document AI 说明复杂文档的 OCR、表格、布局、坐标和 confidence 是证据可信度的核心输入。
- RAGAS、TruLens、LangSmith 等评测工具把检索命中、上下文相关性、groundedness 等拆开评估。本平台还需要额外评估冲突召回率、资料间冲突发现率和“证据不足误判为通过”的比例。

公开参考：

- Microsoft Purview eDiscovery workflow: https://learn.microsoft.com/en-us/purview/edisc-workflow
- Microsoft Purview review set: https://learn.microsoft.com/en-us/purview/edisc-review-set-manage
- Azure AI Search RAG: https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview
- Amazon Bedrock Knowledge Bases: https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html
- OpenAI File Search: https://developers.openai.com/api/docs/guides/tools-file-search
- Azure Document Intelligence Layout: https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout
- Amazon Textract AnalyzeDocument: https://docs.aws.amazon.com/textract/latest/APIReference/API_AnalyzeDocument.html
- Google Document AI response handling: https://docs.cloud.google.com/document-ai/docs/handle-response
- RAGAS metrics: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- TruLens RAG Triad: https://www.trulens.org/getting_started/core_concepts/rag_triad/
- LangSmith RAG evaluation: https://docs.langchain.com/langsmith/evaluate-rag-tutorial

## 推荐方案

采用“自管证据库 + 混合检索 + 严格事实比对 + 人工复核”的架构。

RAG 在该系统中只承担证据召回和定位，不承担最终审计裁判。最终判定由结构化事实比对、资料优先级规则、模型辅助判断和人工复核共同完成。托管 File Search 或 Knowledge Base 可以作为后续 adapter，但不能成为唯一事实层，因为首版必须保存页码、段落、表格单元格、解析置信度、资料版本和复核状态。

## 首版范围

首版必须实现：

- 异步审计任务创建、状态查询和报告查询。
- 资料文件与最终文件的角色区分。
- 文件解析结果的结构化持久化，至少保留文本块、来源文件、页码或 sheet、段落或表格坐标、解析置信度。
- 证据块构建，用于检索和引用。
- 最终文件的原子声明抽取。
- 对原子声明执行资料包内检索和严格比对。
- 只输出异常项：`conflict`、`insufficient_evidence`、`source_conflict`、`needs_human_judgment`。
- 报告覆盖率摘要：抽取声明数、核验声明数、异常数、通过数、未核验数、失败解析数。
- 人工复核状态：未处理、确认异常、误报、已处理。
- 原始资料留存/删除机制的领域接口和数据状态，不默认用于训练。

首版不做：

- 法规/制度合规判定。
- 互联网或外部知识补证。
- 自动修改最终文件。
- 对抽象评价和行业判断做强行自动判定。
- 没有证据来源的高置信通过结论。
- 把未输出异常解释为全文件完全通过。

## 领域模型

### AuditTask

审计任务聚合根。

核心字段：

- `id`
- `title`
- `status`: `created`、`parsing`、`indexing`、`extracting_claims`、`verifying`、`completed`、`failed`、`cancelled`
- `created_at`
- `updated_at`
- `completed_at`
- `failure_reason`
- `coverage_summary`

接口约束：

- 任务必须至少包含一个资料文件和一个最终文件才能启动。
- 完成态任务的报告内容不可被后台任务自动重写，只能通过重新运行生成新任务或新版本。

### AuditFile

审计任务内的文件快照。

核心字段：

- `id`
- `task_id`
- `file_id`
- `role`: `source` 或 `target`
- `filename`
- `mime_type`
- `size`
- `authority_level`
- `document_date`
- `version_label`
- `parse_status`
- `parse_confidence`

接口约束：

- `source` 文件作为证据来源。
- `target` 文件用于抽取待核验声明。
- 资料间冲突时优先按 `authority_level`、`document_date`、`version_label` 解释；规则不足时输出 `source_conflict`。

### ParsedBlock

文档解析后的最小来源块。

核心字段：

- `id`
- `task_id`
- `audit_file_id`
- `kind`: `paragraph`、`table_cell`、`table_row`、`heading`、`ocr_text`
- `text`
- `page_number`
- `sheet_name`
- `row_index`
- `column_index`
- `bbox`
- `confidence`
- `order_index`

接口约束：

- 块必须能回溯到原文件位置。
- 低解析置信度块不能作为 `supported` 结论的唯一证据。

### EvidenceBlock

用于检索和引用的证据块。

核心字段：

- `id`
- `task_id`
- `audit_file_id`
- `parsed_block_ids`
- `text`
- `metadata`
- `lexical_terms`
- `embedding_ref`

接口约束：

- EvidenceBlock 可以由一个或多个 ParsedBlock 合并而来。
- 合并不得丢失原始坐标。
- 检索返回给比对模块的证据必须包含来源坐标和解析置信度。

### AtomicClaim

从最终大文件抽取的原子声明。

核心字段：

- `id`
- `task_id`
- `source_block_id`
- `text`
- `claim_type`: `amount`、`date`、`entity`、`quantity`、`status`、`obligation`、`reference`、`other_fact`、`judgment`
- `subject`
- `predicate`
- `object_value`
- `unit`
- `time_scope`
- `scope_qualifier`
- `extract_confidence`

接口约束：

- 一句话包含多个事实时必须拆成多个 AtomicClaim。
- 判断性、抽象性、行业评价性内容归为 `judgment`，默认进入人工判断，不强行自动判通过。

### VerificationFinding

异常项。

核心字段：

- `id`
- `task_id`
- `claim_id`
- `finding_type`: `conflict`、`insufficient_evidence`、`source_conflict`、`needs_human_judgment`
- `severity`: `low`、`medium`、`high`
- `reason`
- `key_difference`
- `evidence_refs`
- `suggested_action`
- `evidence_confidence`
- `judgment_confidence`
- `parse_confidence`
- `review_status`: `open`、`confirmed`、`false_positive`、`resolved`

接口约束：

- 异常项必须绑定原子声明和来源位置。
- `conflict` 必须有冲突证据。
- `insufficient_evidence` 可以没有支持证据，但必须记录检索范围和失败原因。
- `source_conflict` 必须列出至少两个互相冲突的资料证据。

## 核心数据流

1. 用户创建 AuditTask。
2. 用户上传资料文件和最终文件，并在任务内声明文件角色。
3. 系统启动异步任务，状态进入 `parsing`。
4. DocumentParser 下载 OSS 文件，解析文本 PDF、Word、Excel。扫描件或图片保留 OCR confidence，低置信度区域进入复核风险。
5. ParsedBlock 写入数据库。
6. EvidenceBuilder 将资料文件的 ParsedBlock 合并成 EvidenceBlock。
7. RetrieverIndexer 为 EvidenceBlock 建立关键词索引和向量索引。
8. ClaimExtractor 从最终文件的 ParsedBlock 抽取 AtomicClaim。
9. Verifier 对每条 AtomicClaim 执行检索、重排、资料间冲突检测和严格比对。
10. FindingWriter 只持久化异常项，并更新覆盖率摘要。
11. 用户在前端查看异常报告、证据来源和复核状态。

## 检索与比对

检索采用混合策略：

- 关键词检索用于主体、编号、金额、日期、合同号等精确字段。
- 向量检索用于语义近似资料片段召回。
- 元数据过滤用于文件角色、权威级别、日期、版本、页码、sheet。
- 重排用于候选证据排序。

比对采用严格标准：

- 金额、比例、数量、日期、主体、范围、状态、义务语气必须精确匹配或满足明确容差。
- “预计完成”与“已完成”、“应当”与“可以”、“约 100 万”与“超过 120 万”不能因语义相近而判通过。
- 检索不到证据时输出 `insufficient_evidence`，不输出 `conflict`。
- 多个资料互相矛盾时按优先级规则处理；无法裁决时输出 `source_conflict`。

## 异步任务与状态

首版使用应用内后台任务或现有 Redis Stream 任务机制承载审计任务。接口层只负责创建任务、启动任务、查询状态、查询报告、更新复核状态。

状态进度：

- `created`: 文件已登记但未启动。
- `parsing`: 正在解析文件。
- `indexing`: 正在构建证据块和索引。
- `extracting_claims`: 正在抽取最终文件声明。
- `verifying`: 正在核验证据。
- `completed`: 报告已生成。
- `failed`: 任务失败，保留失败阶段和原因。
- `cancelled`: 用户取消。

失败处理：

- 单个文件解析失败不应导致所有任务静默通过。
- 如果最终文件解析失败，任务失败。
- 如果部分资料解析失败，继续处理可解析资料，但覆盖率摘要必须记录失败文件和未覆盖风险。
- OCR 或表格解析低置信度时，相应证据不得单独支撑通过结论。

## 接口草案

后端新增 `/api/audit-tasks` 路由。

- `POST /api/audit-tasks`: 创建任务。
- `POST /api/audit-tasks/{task_id}/files`: 绑定已上传文件到任务，声明 `source` 或 `target`。
- `POST /api/audit-tasks/{task_id}/start`: 启动异步核验。
- `GET /api/audit-tasks/{task_id}`: 查询任务状态和覆盖率摘要。
- `GET /api/audit-tasks/{task_id}/findings`: 查询异常项列表。
- `GET /api/audit-tasks/{task_id}/findings/{finding_id}`: 查询单个异常详情和证据。
- `POST /api/audit-tasks/{task_id}/findings/{finding_id}/review`: 更新人工复核状态。
- `POST /api/audit-tasks/{task_id}/delete-source-files`: 删除原始资料并保留报告元数据。

## 前端首版

首版新增一个“资料核验”页面。

页面能力：

- 创建任务。
- 选择或上传资料文件。
- 选择或上传最终文件。
- 启动任务并展示异步阶段进度。
- 展示覆盖率摘要。
- 默认只展示异常项。
- 异常项详情展示原文声明、判定类型、关键差异、证据来源、建议处理动作和复核状态。

前端不在首版实现复杂证据原文 PDF 高亮预览；后端必须先返回足够坐标，后续再做定位预览。

## 模块设计

新增 `api/app/domain/models/audit.py` 存放审计领域模型。

新增仓储接口：

- `AuditTaskRepository`
- `AuditFileRepository`
- `ParsedBlockRepository`
- `EvidenceBlockRepository`
- `AtomicClaimRepository`
- `VerificationFindingRepository`

新增应用服务：

- `AuditTaskService`: 面向接口层的深模块，封装任务创建、文件绑定、启动、查询、复核。
- `AuditPipelineRunner`: 面向后台任务的深模块，封装解析、索引、声明抽取、核验和报告汇总。

内部实现模块：

- `DocumentParser`
- `EvidenceBuilder`
- `EvidenceIndexer`
- `ClaimExtractor`
- `EvidenceRetriever`
- `ClaimVerifier`
- `ReportBuilder`

外部 seam：

- `DocumentParser` 后续可替换为本地解析、Azure Document Intelligence、Textract 或 Document AI adapter。
- `EvidenceIndexer` 后续可替换为 PostgreSQL 全文 + pgvector、OpenSearch、Elasticsearch、Azure AI Search 或托管 File Search adapter。
- `LLM` 复用现有 OpenAI 兼容接口，但结构化输出必须通过 Pydantic schema 校验。

## 数据库与索引

首版建议使用 PostgreSQL 保存所有审计领域数据。向量索引的实现可以分两步：

1. Phase 1 使用 PostgreSQL 表保存 EvidenceBlock，先实现关键词召回和元数据过滤。
2. Phase 2 接入 pgvector 或独立搜索引擎，实现向量召回、混合排序和重排。

这样可以先验证领域模型、任务流和报告，而不是一开始被向量库选型拖住。

## 测试与评测

单元测试：

- 文件角色约束。
- 任务状态流转。
- 原子声明 schema 校验。
- Finding 类型与证据约束。
- 资料优先级冲突规则。

集成测试：

- 创建任务、绑定文件、启动、完成、查询 findings。
- 部分资料解析失败时覆盖率摘要正确。
- 低置信度 OCR 证据不能支撑通过。

评测集：

- 先准备 20 到 50 组真实资料包和最终文件。
- 人工标注 AtomicClaim、异常类型和黄金证据。
- 指标包括冲突召回率、证据命中率、资料间冲突发现率、证据不足误判为通过比例、人工复核节省时间。

## 分阶段实现

Phase 0：规格与数据模型

- 新增审计领域模型和 schema。
- 新增数据库迁移。
- 新增任务创建、文件绑定、状态查询和异常查询接口。
- 使用假 runner 或最小 runner 验证完整任务生命周期。

Phase 1：解析与异常报告骨架

- 支持文本 PDF、Word、Excel 的基础解析。
- 建立 ParsedBlock 和 EvidenceBlock。
- 支持手动或简单规则生成 AtomicClaim。
- 生成只含异常项的报告。

Phase 2：混合检索与严格比对

- 实现关键词召回、元数据过滤、向量检索和候选重排。
- 实现金额、日期、主体、状态、义务的严格比对。
- 实现资料间冲突检测。

Phase 3：复核与评测

- 完成人工复核状态流。
- 引入真实评测集。
- 输出指标报表。
- 根据评测结果调整 chunk、召回和比对策略。

## 风险

- 文档解析质量会直接决定证据可信度，尤其是扫描件、复杂表格和图片。
- 只输出异常项容易让用户误解为未列出内容全部通过，因此覆盖率摘要和责任边界必须始终展示。
- 向量检索召回失败会造成漏审，必须用评测集持续检查。
- 模型抽取原子声明可能漏项，需要对大文件抽取覆盖率做抽样复核。
- 商业托管 RAG 组件可能无法暴露足够细的来源坐标，不能作为唯一事实层。

## 验收标准

- 用户可以创建一个审计任务，绑定至少一份资料文件和一个最终文件。
- 任务可以异步运行并进入完成或失败状态。
- 完成任务返回覆盖率摘要。
- 报告默认只返回异常项。
- 每个异常项包含原文声明、异常类型、原因、关键差异、证据来源、建议处理动作和复核状态。
- 无证据的声明不会被判为通过。
- 资料间冲突不会由模型自行裁判。
- 不修改 `.env` 或本地运行配置文件。
