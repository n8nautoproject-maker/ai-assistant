"""Regression test suite for the LLM currently configured in .env
(app.llm.factory.get_llm_provider - whatever LLM_PROVIDER/OPENAI_MODEL/
LLM_BASE_URL currently point at).

Each case here reproduces a REAL bug found in production (dated in the
comment), not a hypothetical - see the 2026-07-29 session that introduced
this file for the original incidents and the DeepSeek-vs-YandexGPT
comparison that motivated the model switch. Uses the app's actual current
SYSTEM_PROMPT and tool schemas (imported live, not copy-pasted), so this
file stays correct as those evolve - only the fixture conversations here
need updating if a case goes stale.

Run manually after any LLM provider/model change, or after touching
SYSTEM_PROMPT/tools_registry.py, to catch a regression before real users
do:

    python scripts/regression_test_llm.py

Deliberately NOT pytest-based - this project has no test framework
dependency yet, and every other verification in this codebase's history
has been a plain runnable script (see git log). Each case calls the LLM
directly with a reconstructed conversation and inspects the raw tool_calls
- it does not execute tools against a real database, so it checks *what
the model decides to call*, not the full dispatch pipeline.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMProvider  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.orchestrator.agent import SYSTEM_PROMPT  # noqa: E402
from app.orchestrator.domains import DOMAIN_MODULES  # noqa: E402
from app.orchestrator.tools_registry import get_tool_schemas  # noqa: E402

# All five cases below are Core-domain (task management) - real production
# traffic for these always gets SYSTEM_PROMPT + the core domain's prompt
# fragment concatenated together (see app.orchestrator.agent.handle_message's
# select_domains() call), not SYSTEM_PROMPT alone. Since the 2026-07-30
# domain-split refactor moved most task-specific instructions out of
# SYSTEM_PROMPT into domains.CORE_FRAGMENT, testing against SYSTEM_PROMPT
# alone would silently test a prompt real users never actually see.
CORE_FRAGMENT = next(m for m in DOMAIN_MODULES if m.name == "core").prompt_fragment
TOOL_SCHEMAS = get_tool_schemas()
TIME_CONTEXT = (
    "Текущие дата и время: 2026-07-29 12:00 (среда), "
    "часовой пояс пользователя: Europe/Moscow."
)


def system(extra: str = "") -> dict:
    content = f"{SYSTEM_PROMPT}\n\n{CORE_FRAGMENT}\n\n{TIME_CONTEXT}"
    if extra:
        content += f"\n\n{extra}"
    return {"role": "system", "content": content}


class Case:
    def __init__(self, name: str, messages: list[dict], check):
        self.name = name
        self.messages = messages
        self.check = check  # (tool_calls: list[dict], text: str|None) -> (bool, str)


def _tc_names(tool_calls) -> list[str]:
    return [tc.name for tc in tool_calls]


def _tc_args(tool_calls, name: str) -> dict | None:
    for tc in tool_calls:
        if tc.name == name:
            return tc.arguments
    return None


CASES: list[Case] = [
    # 2026-07-28: "Задача на сегодня протереть пыль" then, minutes later,
    # "Эта задача выполнена" - YandexGPT in prod completed an unrelated
    # older task ("Зашить спорт футболку") instead of the just-created one.
    Case(
        name="ambiguous-reference-just-created-task",
        messages=[
            system(),
            {"role": "user", "content": "Задача на сегодня протереть пыль"},
            {
                "role": "assistant",
                "content": "Задача «Протереть пыль» успешно создана и запланирована на сегодня.",
            },
            {"role": "user", "content": "Эта задача выполнена"},
        ],
        check=lambda tc, text: (
            (len(tc) == 1 and tc[0].name == "complete_task" and "пыль" in json.dumps(tc[0].arguments, ensure_ascii=False).lower())
            or (len(tc) == 1 and tc[0].name in ("list_tasks", "list_tasks_for_date")),
            "expected complete_task targeting 'протереть пыль', or a list_tasks/list_tasks_for_date verification call first",
        ),
    ),
    # 2026-07-28: user names exactly one of 7 evening-review tasks as done
    # ("уточнить что там с тг парсером"). YandexGPT in prod completed a
    # DIFFERENT, unlisted task ("Написать пост в тг канал...") instead.
    Case(
        name="evening-review-exact-match-not-substring-confusion",
        messages=[
            system(
                "Пользователю только что прислали вечерний обзор незавершённых "
                "задач за сегодня: «Написать пост в тг канал про эвент "
                "приложение» (id=11111111-1111-1111-1111-111111111111); "
                "«Протереть пыль» (id=22222222-2222-2222-2222-222222222222); "
                "«уточнить что там с тг парсером» "
                "(id=33333333-3333-3333-3333-333333333333). Если сообщение "
                "называет конкретные из них как выполненные - вызови "
                "complete_task с соответствующим task_id для каждой названной."
            ),
            {"role": "user", "content": "Уточнить что там с тг парсером выполнено"},
        ],
        check=lambda tc, text: (
            any(
                t.name == "complete_task"
                and t.arguments.get("task_id") == "33333333-3333-3333-3333-333333333333"
                for t in tc
            )
            and not any(
                t.name == "complete_task"
                and t.arguments.get("task_id") == "11111111-1111-1111-1111-111111111111"
                for t in tc
            ),
            "expected complete_task on the 'тг парсером' id specifically, not the unrelated 'тг канал' task",
        ),
    ),
    # 2026-07-29: stacked, unanswered task_reminder pushes + a bare "Да"
    # already got a clarification request once (hint fired-once, cleared).
    # A second bare "Да" with NO anchor left - YandexGPT in prod confidently
    # rescheduled all 5 unrelated tasks with no grounding for that action.
    Case(
        name="bare-ambiguous-reply-no-anchor-must-not-confabulate",
        messages=[
            system(),
            {"role": "assistant", "content": "⏰ По поводу оплаты Аленберг — получилось сделать?"},
            {"role": "assistant", "content": "⏰ Позвонить в Салават — получилось сделать?"},
            {"role": "assistant", "content": "⏰ Попросить оплату за музи — получилось сделать?"},
            {"role": "user", "content": "Да"},
            {
                "role": "assistant",
                "content": "Уточните, пожалуйста, название задачи, которую вы хотите отметить как выполненную.",
            },
            {"role": "user", "content": "Да"},
        ],
        check=lambda tc, text: (
            len(tc) == 0,
            "expected NO tool call (should ask for clarification again, not act on an ungrounded guess)",
        ),
    ),
    # 2026-07-19: duplicate-titled tasks, user gives one explicit combined
    # instruction (move one, delete the other). Real bug: bare "Да, всё
    # верно" with nothing actually done.
    Case(
        name="dual-instruction-move-one-delete-other",
        messages=[
            system(),
            {"role": "user", "content": "Протереть пыль - задача на завтра"},
            {
                "role": "assistant",
                "content": (
                    "Вспомним, когда была создана каждая задача:\n"
                    "1. «Прибраться дома, протереть пыль» — создана 26 июля\n"
                    "2. «Протереть пыль» — создана 28 июля\n"
                    "Уточню: какая из них та, что была запланирована на "
                    "сегодня (29 июля)?"
                ),
            },
            {
                "role": "user",
                "content": "«Прибраться дома, протереть пыль» - эту переносим на завтра, а «Протереть пыль» - удаляем",
            },
        ],
        check=lambda tc, text: (
            len(tc) >= 1
            and all(t.name in ("update_task", "delete_task", "list_tasks", "list_tasks_for_date") for t in tc),
            "expected real update_task/delete_task calls (or a list_tasks verification first), not a bare text confirmation",
        ),
    ),
    # Verbatim-relay check: the tool's own output is already chronologically
    # sorted - the model must not silently reorder it while relaying.
    Case(
        name="verbatim-relay-preserves-order",
        messages=[
            system(),
            {"role": "user", "content": "Какие у меня задачи на завтра?"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_tasks_for_date", "arguments": '{"date": "2026-07-30"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "- забрать трафарет (09:00)\n- позвонить в Салават (09:05)\n- выезжать к парикмахеру (14:00)",
            },
        ],
        check=lambda tc, text: (
            text is not None
            and 0
            <= (t := text.lower()).find("забрать трафарет")
            < t.find("позвонить в салават")
            < t.find("выезжать к парикмахеру"),
            "expected the three tasks relayed in the same order the tool returned them",
        ),
    ),
]


async def run_case(llm: LLMProvider, case: Case) -> tuple[bool, str]:
    response = await llm.chat(case.messages, tools=TOOL_SCHEMAS)
    tool_calls = response.tool_calls or []
    ok, expectation = case.check(tool_calls, response.content)
    detail = (
        f"tool_calls={[(t.name, t.arguments) for t in tool_calls]}"
        if tool_calls
        else f"text={response.content!r}"
    )
    return ok, f"{expectation} | got: {detail}"


async def main() -> None:
    llm = get_llm_provider()
    print(f"Testing against: {llm}\n")
    failures = 0
    for case in CASES:
        try:
            ok, detail = await run_case(llm, case)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"raised {exc!r}"
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{status}] {case.name}")
        print(f"       {detail}\n")

    print(f"{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
