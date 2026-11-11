import json
import logging

import anthropic

from app.llm.base import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# The Messages API requires this, unlike OpenAI's Chat Completions where it's
# optional - generous enough for the longest real reply this app produces
# (a full task list relay) without being an unbounded cost risk per call.
_MAX_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    """Anthropic's Messages API is not OpenAI-wire-compatible (unlike
    DeepSeek/Yandex, which both speak the OpenAI Chat Completions format
    OpenAIProvider already handles) - different system-prompt placement,
    different tool schema shape, different tool-call/tool-result message
    shapes. This class is the translation layer so every other module
    (agent.py, tools_registry.py) keeps building/reading messages and tool
    schemas in the one OpenAI-shaped format they already use; only this
    provider needs to know Anthropic's wire format exists at all - see
    LLMProvider's own docstring, which named "Anthropic" as an expected
    future provider from day one.
    """

    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        force_tool_call: bool = False,
    ) -> LLMResponse:
        system, converted = _convert_messages(messages)
        # Cache breakpoint on the system block - Anthropic renders tools
        # before system, so this covers both. Within one multi-round
        # tool-calling turn (app/orchestrator/agent.py's MAX_TOOL_ITERATIONS
        # loop), system+tools are byte-identical on every round; only
        # `messages` grows as tool_use/tool_result pairs get appended. Real
        # incident that surfaced this: one Telegram message turned into 6
        # rounds (a stale-question classifier wrongly pulled in 6 unrelated
        # tasks), and every round re-billed the full system+tools at full
        # price - ~52k input tokens for what should have been a cheap
        # exchange. Cache reads cost ~10% of input price within the 5-
        # minute TTL. get_tool_schemas() sorts by name specifically so this
        # prefix stays byte-identical across calls with the same domain
        # selection - an unsorted tool order would silently defeat this.
        system_param = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if system
            else anthropic.NOT_GIVEN
        )
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=system_param,
            messages=converted,
            tools=_convert_tools(tools) if tools else anthropic.NOT_GIVEN,
            tool_choice=(
                {"type": "any"} if force_tool_call and tools else anthropic.NOT_GIVEN
            ),
        )

        if response.stop_reason == "max_tokens":
            # The response (text and/or a tool_use block) may be truncated
            # mid-word or mid-JSON - silently treating it as a normal
            # completion risks passing a half-finished tool call to
            # dispatch_tool_call, or a reply that just stops mid-sentence
            # with nothing telling the caller anything went wrong.
            logger.warning(
                "anthropic response truncated at max_tokens=%d - content may be incomplete",
                _MAX_TOKENS,
            )

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
        return LLMResponse(content="".join(text_parts) or None, tool_calls=tool_calls)


def _convert_tools(tools: list[dict]) -> list[dict]:
    """OpenAI {"type":"function","function":{"name","description","parameters"}}
    -> Anthropic {"name","description","input_schema"} - same schema content,
    different envelope. tools_registry.get_tool_schemas() is the single
    source of truth for the schema itself; only the wrapper shape differs
    per provider, so no changes needed there.
    """
    converted = []
    for t in tools:
        fn = t["function"]
        converted.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return converted


def _convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-shaped messages (as built throughout agent.py/tools_registry.py)
    -> (system_prompt, Anthropic-shaped messages list).

    Shapes that need translating, everything else (plain {"role","content"}
    text turns) passes through unchanged since Anthropic accepts a bare
    string for `content` same as OpenAI does:
    - role="system" AT INDEX 0 -> pulled out entirely, Anthropic takes it as
      a top-level `system` param, not a message. app/orchestrator/agent.py
      always puts exactly one main system prompt at the very start.
    - role="system" ANYWHERE ELSE -> Anthropic has no per-message system
      role at all, and unlike the index-0 case this one is positional (e.g.
      _retry_with_notice's corrective note, meant to read as "about your
      last reply specifically"). Hoisting it into the top-level `system`
      param like the first one would silently move it out of the actual
      turn sequence and bundle it with the unrelated main instructions
      instead - converted to a plain user turn in place instead, which
      keeps its position and reads naturally regardless of nominal role.
    - role="assistant" with "tool_calls" (no plain "content") -> becomes an
      assistant message whose content is a list of tool_use blocks.
    - role="tool" (OpenAI's tool-result message, keyed by "tool_call_id")
      -> becomes a user message whose content is a list of tool_result
      blocks - Anthropic has no separate "tool" role, tool results are
      user-turn content. When one assistant turn calls several tools in
      parallel, agent.py appends one role="tool" message per call in a row;
      those are batched into a SINGLE user message with multiple
      tool_result blocks (not one user message per call) - Anthropic
      expects exactly that shape for a parallel tool_use turn, and sending
      them as separate consecutive user messages is a documented anti-
      pattern that trains the model to stop making parallel calls.
    """
    system_parts = []
    converted: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            converted.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "tool":
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            })
            continue
        flush_tool_results()
        if role == "system":
            if i == 0:
                system_parts.append(m["content"])
            else:
                converted.append({"role": "user", "content": m["content"]})
        elif role == "assistant" and "tool_calls" in m:
            converted.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        # agent.py stores this as str(tc.arguments) (a
                        # Python dict repr, not JSON) when building the
                        # OpenAI-shaped history - json.loads would choke on
                        # single-quoted repr syntax, so this round-trips
                        # through the same eval-free parser dispatch_tool_call
                        # never needed here: arguments already came from a
                        # real ToolCall.arguments dict this same turn, so a
                        # literal Python-repr parse is safe and exact.
                        "input": _parse_arguments(tc["function"]["arguments"]),
                    }
                    for tc in m["tool_calls"]
                ],
            })
        else:
            converted.append(m)
    flush_tool_results()
    return "\n\n".join(system_parts), converted


def _parse_arguments(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # str(dict) repr (single quotes, Python None/True/False) - only
        # reachable if some future caller builds the OpenAI-shaped history
        # with Python repr instead of JSON, same fallback either way is
        # cheap insurance since this only ever runs on our own prior turn's
        # own tool-call arguments, never untrusted input.
        import ast
        return ast.literal_eval(raw)
