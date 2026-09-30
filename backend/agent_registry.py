"""
Agent Registry: a centralized, extensible definition and lookup system for
the specialized AI agents in the Intelligenzit workforce platform.

This module is purely additive metadata. It does not change how the
existing LangGraph nodes in agents.py execute — graph.py/agents.py keep
calling call_llm/call_llm_json directly and the 7-stage workflow is
untouched. The registry exists so agents can be discovered, described, and
validated in one place, as groundwork for a future AI Executive / dynamic
orchestration layer (not implemented here).

`default_registry` is pre-populated with the 7 agents that already make up
the live pipeline (backend/agents.py), one entry per LangGraph node.
"""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from . import llm as llm_module

_AGENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VALID_PROVIDERS = {"ollama", "anthropic"}


class AgentStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"


class RegistryError(Exception):
    """Base class for agent registry errors."""


class AgentValidationError(RegistryError):
    """Raised when an agent definition fails validation."""


class AgentNotFoundError(RegistryError):
    """Raised when looking up or removing an agent_id that isn't registered."""


class DuplicateAgentError(RegistryError):
    """Raised when registering an agent_id that already exists (without replace=True)."""


@dataclass(frozen=True)
class ModelConfig:
    """Model/provider configuration for an agent.

    provider/model left as None means "use backend.llm's active default"
    (LLM_PROVIDER / OLLAMA_MODEL / ANTHROPIC_MODEL env vars), so registry
    entries stay in sync with the real runtime configuration instead of
    hardcoding a second copy of it.
    """
    provider: Optional[str] = None
    model: Optional[str] = None
    max_tokens: int = 1500

    def resolve(self) -> "ModelConfig":
        """Return a copy with provider/model filled in from backend.llm's current config."""
        provider = self.provider or llm_module._provider()
        model = self.model or llm_module._default_model(provider)
        return ModelConfig(provider=provider, model=model, max_tokens=self.max_tokens)


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    department: str
    role: str
    description: str
    capabilities: tuple = field(default_factory=tuple)
    tools: tuple = field(default_factory=tuple)
    model: ModelConfig = field(default_factory=ModelConfig)
    status: AgentStatus = AgentStatus.ACTIVE

    def __post_init__(self):
        # Accept any iterable (e.g. a list literal) at construction time but
        # store as immutable tuples, matching the frozen dataclass contract.
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "tools", tuple(self.tools))


def validate_agent(definition: AgentDefinition) -> None:
    """Raise AgentValidationError if `definition` violates the registry's schema."""
    errors = []

    if not isinstance(definition.agent_id, str) or not _AGENT_ID_PATTERN.match(definition.agent_id):
        errors.append(
            "agent_id must be a non-empty lowercase slug (letters, digits, '_' or '-', "
            "starting with a letter or digit)"
        )

    for field_name in ("name", "department", "role", "description"):
        value = getattr(definition, field_name, None)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} must be a non-empty string")

    if not isinstance(definition.capabilities, tuple) or not all(
        isinstance(c, str) and c.strip() for c in definition.capabilities
    ):
        errors.append("capabilities must be a list/tuple of non-empty strings")

    if not isinstance(definition.tools, tuple) or not all(
        isinstance(t, str) and t.strip() for t in definition.tools
    ):
        errors.append("tools must be a list/tuple of non-empty strings")

    if not isinstance(definition.model, ModelConfig):
        errors.append("model must be a ModelConfig instance")
    else:
        if definition.model.provider is not None and definition.model.provider not in _VALID_PROVIDERS:
            errors.append(f"model.provider must be one of {sorted(_VALID_PROVIDERS)} or None")
        if not isinstance(definition.model.max_tokens, int) or definition.model.max_tokens <= 0:
            errors.append("model.max_tokens must be a positive integer")

    if not isinstance(definition.status, AgentStatus):
        errors.append("status must be an AgentStatus value")

    if errors:
        raise AgentValidationError(
            f"Invalid agent definition '{getattr(definition, 'agent_id', '?')}': " + "; ".join(errors)
        )


class AgentRegistry:
    """In-memory registry of agent definitions. Validates on every registration."""

    def __init__(self):
        self._agents: dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition, *, replace: bool = False) -> AgentDefinition:
        validate_agent(definition)
        if definition.agent_id in self._agents and not replace:
            raise DuplicateAgentError(
                f"Agent '{definition.agent_id}' is already registered "
                "(pass replace=True to overwrite)."
            )
        self._agents[definition.agent_id] = definition
        return definition

    def unregister(self, agent_id: str) -> None:
        if agent_id not in self._agents:
            raise AgentNotFoundError(f"No agent registered with id '{agent_id}'")
        del self._agents[agent_id]

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise AgentNotFoundError(f"No agent registered with id '{agent_id}'") from None

    def get_or_none(self, agent_id: str) -> Optional[AgentDefinition]:
        return self._agents.get(agent_id)

    def list_agents(
        self,
        *,
        department: Optional[str] = None,
        status: Optional[AgentStatus] = None,
        capability: Optional[str] = None,
    ) -> list:
        results: Iterable[AgentDefinition] = self._agents.values()
        if department is not None:
            results = (a for a in results if a.department == department)
        if status is not None:
            results = (a for a in results if a.status == status)
        if capability is not None:
            results = (a for a in results if capability in a.capabilities)
        return sorted(results, key=lambda a: a.agent_id)

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents


def build_default_registry() -> AgentRegistry:
    """Registry pre-populated with the 7 agents already running in backend/agents.py.

    agent_id matches the LangGraph node name in backend/graph.py for each, so
    entries can be cross-referenced with the live pipeline unambiguously.
    """
    registry = AgentRegistry()

    registry.register(AgentDefinition(
        agent_id="orchestrator",
        name="Orchestrator Agent",
        department="Workflow Orchestration",
        role="Request classifier and execution planner",
        description=(
            "Classifies the incoming business/client requirement and produces "
            "the execution plan naming which downstream stages are needed."
        ),
        capabilities=["request_classification", "execution_planning"],
        tools=[],
        status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="research",
        name="Research Agent",
        department="Research & Intelligence",
        role="Background research",
        description=(
            "Gathers and organises relevant background information, prior art, "
            "and constraints for the requirement, without proposing a solution."
        ),
        capabilities=["background_research", "prior_art_analysis"],
        tools=[],
        status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="requirements",
        name="Requirements Agent",
        department="Business Analysis",
        role="Requirements structuring",
        description=(
            "Converts the raw request and research findings into a structured "
            "list of functional and non-functional requirements."
        ),
        capabilities=["requirements_elicitation", "requirements_structuring"],
        tools=[],
        status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="analysis",
        name="Analysis Agent",
        department="Business Analysis",
        role="Options and risk analysis",
        description=(
            "Given the requirements, identifies viable options, trade-offs, "
            "risks, and constraints without picking a final answer."
        ),
        capabilities=["options_analysis", "risk_assessment"],
        tools=[],
        status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="solution",
        name="Solution Agent",
        department="Solution Delivery",
        role="Technical recommendation",
        description=(
            "Proposes a specific, justified technical recommendation based on "
            "the analysis, and revises it when the Review agent sends it back."
        ),
        capabilities=["technical_recommendation", "solution_design"],
        tools=[],
        status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="review",
        name="Review/Critic Agent",
        department="Quality Assurance",
        role="Solution review gate",
        description=(
            "Checks the proposed solution against requirements for "
            "completeness and consistency, approving it or sending it back "
            "for rework (capped) with notes."
        ),
        capabilities=["solution_review", "quality_gate"],
        tools=[],
        status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="report",
        name="Report Agent",
        department="Reporting",
        role="Final deliverable generation",
        description=(
            "Combines the requirements, analysis, and approved solution into "
            "a final, client-ready structured deliverable."
        ),
        capabilities=["deliverable_generation", "client_reporting"],
        tools=[],
        status=AgentStatus.ACTIVE,
    ))

    return registry


default_registry = build_default_registry()
