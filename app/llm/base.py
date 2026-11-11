from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    """Every provider (OpenAI, Anthropic, local) implements this contract.

    Swapping the model behind the assistant means writing one new class
    here - nothing in the orchestrator or core modules changes.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        force_tool_call: bool = False,
    ) -> LLMResponse:
        """force_tool_call requires the model to call one of `tools` instead
        of returning plain text - used to make a corrective retry actually
        bite (see app/orchestrator/agent.py's _retry_with_notice) instead of
        hoping the model complies with a system-prompt instruction a second
        time, which live testing showed it doesn't reliably do."""
        ...
