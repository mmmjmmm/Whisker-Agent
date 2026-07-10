from collections.abc import Callable

from app.domain.models.agent_run import AgentMode
from app.domain.services.flows.base import (
    BaseFlow,
    FlowResourceRequirements,
)


class UnsupportedAgentMode(ValueError):
    pass


class FlowRouter:
    def __init__(
            self,
            react_factory: Callable[[], BaseFlow],
            research_factory: Callable[[], BaseFlow],
    ) -> None:
        self._factories = {
            AgentMode.REACT: react_factory,
            AgentMode.RESEARCH_TEAM: research_factory,
        }
        self._requirements = {
            AgentMode.REACT: FlowResourceRequirements(
                sandbox=True,
                browser=True,
                mcp=True,
                a2a=True,
            ),
            AgentMode.RESEARCH_TEAM: FlowResourceRequirements(),
        }

    @staticmethod
    def mode_for(mode: AgentMode | str | None) -> AgentMode:
        if mode is None:
            return AgentMode.REACT
        try:
            return mode if isinstance(mode, AgentMode) else AgentMode(mode)
        except ValueError as exc:
            raise UnsupportedAgentMode(f"unsupported agent mode: {mode}") from exc

    def create(self, mode: AgentMode | str | None) -> BaseFlow:
        resolved = self.mode_for(mode)
        return self._factories[resolved]()

    def requirements_for(
            self,
            mode: AgentMode | str | None,
    ) -> FlowResourceRequirements:
        return self._requirements[self.mode_for(mode)].model_copy(deep=True)
