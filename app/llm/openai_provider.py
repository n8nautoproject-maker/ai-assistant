import json

from openai import AsyncOpenAI

from app.llm.base import LLMProvider, LLMResponse, ToolCall


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        # extra_headers: some OpenAI-wire-compatible providers need more than
        # a bearer token - e.g. Yandex Cloud AI Studio requires x-folder-id
        # on every request. Generic passthrough here keeps this class
        # provider-agnostic; provider-specific header construction belongs
        # in factory.py, not here.
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url or None, default_headers=extra_headers
        )
        self._model = model

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        force_tool_call: bool = False,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools or None,
            tool_choice="required" if force_tool_call and tools else None,
        )
        choice = response.choices[0].message

        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments))
            for tc in (choice.tool_calls or [])
        ]
        return LLMResponse(content=choice.content, tool_calls=tool_calls)
