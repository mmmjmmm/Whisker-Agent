from enum import Enum


class ResearchErrorCode(str, Enum):
    VALIDATION = "ValidationError"
    POLICY_VIOLATION = "PolicyViolation"
    BUDGET_EXCEEDED = "BudgetExceeded"
    MODEL_RATE_LIMITED = "ModelRateLimited"
    MODEL_TIMEOUT = "ModelTimeout"
    MODEL_OUTPUT_INVALID = "ModelOutputInvalid"
    TOOL_TRANSIENT = "ToolTransientError"
    TOOL_PERMANENT = "ToolPermanentError"
    TASK_TIMEOUT = "TaskTimeout"
    RUN_CANCELLED = "RunCancelled"
    INFRASTRUCTURE_UNAVAILABLE = "InfrastructureUnavailable"
    PROCESS_INTERRUPTED = "ProcessInterrupted"


class ResearchExecutionError(RuntimeError):
    def __init__(
            self,
            code: ResearchErrorCode,
            message: str,
            retryable: bool,
            scope: str,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.scope = scope
        super().__init__(message)

