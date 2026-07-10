from app.domain.models.agent_run import AgentMode
from app.domain.services.flows.base import FlowResourceRequirements
from app.domain.services.flows.flow_router import FlowRouter, UnsupportedAgentMode


def test_router_keeps_react_as_default() -> None:
    router = FlowRouter(
        react_factory=lambda: "react-flow",
        research_factory=lambda: "research-flow",
    )

    assert router.mode_for(None) == AgentMode.REACT
    assert router.create(None) == "react-flow"


def test_research_flow_requires_no_sandbox_resources() -> None:
    router = FlowRouter(
        react_factory=lambda: "react-flow",
        research_factory=lambda: "research-flow",
    )

    requirements = router.requirements_for(AgentMode.RESEARCH_TEAM)

    assert requirements == FlowResourceRequirements()


def test_unknown_mode_is_rejected() -> None:
    router = FlowRouter(
        react_factory=lambda: "react-flow",
        research_factory=lambda: "research-flow",
    )

    try:
        router.mode_for("swarm")
    except UnsupportedAgentMode as exc:
        assert "swarm" in str(exc)
    else:
        raise AssertionError("unknown mode was accepted")
