from collections.abc import Callable

from app.domain.models.team import AgentMode
from app.domain.services.flows.base import BaseFlow


class FlowRouter:
    def __init__(
        self,
        react_flow: BaseFlow,
        team_flow_factory: Callable[[], BaseFlow],
    ):
        self._react_flow = react_flow
        self._team_flow_factory = team_flow_factory

    def resolve(self, mode: AgentMode) -> BaseFlow:
        if mode is AgentMode.REACT:
            return self._react_flow
        if mode is AgentMode.TEAM:
            return self._team_flow_factory()
        raise ValueError(f"不支持的 Agent mode: {mode}")
