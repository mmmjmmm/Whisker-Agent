import uuid
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.domain.models.agent_run import AgentMode, RunBudget, utc_now


class StartRunCommand(BaseModel):
    command_type: Literal["start"] = "start"
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    session_id: str
    mode: AgentMode
    message: str = Field(min_length=1)
    attachment_ids: list[str] = Field(default_factory=list)
    requested_at: datetime = Field(default_factory=utc_now)
    budget: RunBudget = Field(default_factory=RunBudget)


class CancelRunCommand(BaseModel):
    command_type: Literal["cancel"] = "cancel"
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    session_id: str
    requested_at: datetime = Field(default_factory=utc_now)


RunCommand = Annotated[
    Union[StartRunCommand, CancelRunCommand],
    Field(discriminator="command_type"),
]
