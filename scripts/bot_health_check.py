"""Consolidated health-check for the bot as it stands today (2026-08-02).

Answers "how well does it actually work right now" by running four different
kinds of checks in one report, instead of trusting any single one:

1. Deterministic fast-path unit tests (app.channels.telegram_bot.bot regexes) -
   instant, no DB/LLM - these bypass the LLM entirely in production, so they
   need their own direct tests, not just LLM-fixture cases.
2. Domain-routing unit tests (app.orchestrator.domains.select_domains) -
   instant, no DB/LLM - confirms the 2026-07-30 prompt/tool-split routes
   correctly.
3. LLM tool-calling fixture cases - reuses scripts/regression_test_llm.py's
   existing CASES (real past incidents) rather than duplicating them.
4. Live end-to-end cases through app.orchestrator.agent.handle_message against
   the real dev DB (docker-compose, port 5434) with a throwaway negative
   telegram_id, deleted after - reproduces the exact real incidents fixed this
   session (stale evening-review hint hijacking an unrelated new task; "покажи
   все мои задачи" fabricating an unrelated reply) and confirms they stay
   fixed, not just that isolated fixtures pass.

Deliberately NOT pytest - matches this project's established plain-script
convention (see regression_test_llm.py's own docstring).

Run: python scripts/bot_health_check.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete  # noqa: E402

from app.auth.service import get_or_create_user  # noqa: E402
from app.channels.telegram_bot.bot import (  # noqa: E402
    _resolve_day_query_date,
    _resolve_list_all_tasks,
    _resolve_new_task_title,
)
from app.core.tasks.service import create_task, mark_awaiting_confirmation  # noqa: E402
from app.database.models.conversation_message import ConversationMessage  # noqa: E402
from app.database.models.task import Task  # noqa: E402
from app.database.models.user import User  # noqa: E402
from app.database.session import async_session_factory  # noqa: E402
from app.llm.factory import get_llm_provider  # noqa: E402
from app.orchestrator.agent import handle_message  # noqa: E402
from app.orchestrator.domains import select_domains  # noqa: E402

FAKE_TG_ID = -998855

results: list[tuple[str, bool, str]] = []


def check(section: str, name: str, ok: bool, detail: str) -> None:
    results.append((f"{section}: {name}", ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {section}: {name}\n       {detail}\n")


# ---------------------------------------------------------------------------
# 1. Deterministic fast-path unit tests (bot.py) - real incidents this session
# ---------------------------------------------------------------------------
def run_fastpath_checks() -> None:
    cases = [
        ("Задача починить стул на кухне", "new_task", "починить стул на кухне"),
        ("задача купить подушку", "new_task", "купить подушку"),
        ("купить новый комплект белья", "none", None),  # bare, no prefix - LLM path
        ("задачу выполнил", "none", None),  # completion word - must NOT create
        ("задача купить хлеб завтра", "none", None),  # has date word - LLM path
        ("покажи все мои задачи", "list_all", None),
        ("какие у меня задачи", "list_all", None),
        ("какие у меня задачи на сегодня", "day_query", None),  # date wins over list_all
        ("удали все задачи", "none", None),  # action word - must NOT list
    ]
    for text, expected_kind, expected_title in cases:
        new_task = _resolve_new_task_title(text)
        day_query = _resolve_day_query_date(text, "Europe/Moscow")
        list_all = _resolve_list_all_tasks(text)
        if expected_kind == "new_task":
            ok = new_task == expected_title
            detail = f"expected new_task title={expected_title!r} | got new_task={new_task!r}"
        elif expected_kind == "day_query":
            ok = day_query is not None
            detail = f"expected a resolved date | got day_query={day_query}"
        elif expected_kind == "list_all":
            ok = list_all is True
            detail = f"expected list_all=True | got list_all={list_all}"
        else:
            ok = new_task is None and day_query is None and not list_all
            detail = (
                f"expected none of the fast paths to fire | got new_task={new_task!r}, "
                f"day_query={day_query}, list_all={list_all}"
            )
        check("fast-path", text, ok, detail)


# ---------------------------------------------------------------------------
# 2. Domain-routing unit tests (domains.py)
# ---------------------------------------------------------------------------
def run_domain_checks(user: User) -> None:
    cases = [
        ("Отключи намаз", {"core", "namaz"}),
        ("Задача помыть посуду", {"core"}),
        ("покажи колесо баланса", {"core", "life"}),
        ("создай событие в google календаре завтра в 18:00", {"core", "calendar"}),
        ("сохрани заметку про клиента Иванова", {"core", "knowledge_crm"}),
    ]
    for text, expected in cases:
        got = {m.name for m in select_domains(text, user)}
        check("domain-routing", text, got == expected, f"expected {expected} | got {got}")


# ---------------------------------------------------------------------------
# 3. LLM tool-calling fixture cases - reuse regression_test_llm.py's CASES
# ---------------------------------------------------------------------------
async def run_llm_fixture_checks(llm) -> None:
    from scripts.regression_test_llm import CASES, run_case

    for case in CASES:
        try:
            ok, detail = await run_case(llm, case)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"raised {exc!r}"
        check("llm-fixture", case.name, ok, detail)


# ---------------------------------------------------------------------------
# 4. Live end-to-end cases against the real dev DB - the actual incidents
# ---------------------------------------------------------------------------
async def run_live_checks(session, user: User, llm) -> None:
    # 4a. The exact "Задача починить стул на кухне" incident: evening-review
    # style awaiting_confirmation state must NOT swallow a genuinely new,
    # unrelated task-creation message.
    t1, _ = await create_task(session=session, user_id=user.id, title="Купить санокс")
    t2, _ = await create_task(session=session, user_id=user.id, title="Сделать дырки в ремне")
    await mark_awaiting_confirmation(session, t1)
    await mark_awaiting_confirmation(session, t2)
    reply = await handle_message(session, user, llm, "Задача починить стул на кухне")
    created = await session.execute(
        Task.__table__.select().where(Task.user_id == user.id, Task.title.ilike("%стул%"))
    )
    stul_created = created.first() is not None
    check(
        "live e2e",
        "stale evening-review hint does not swallow new task",
        stul_created,
        f"expected the 'стул' task to exist in DB after the message | reply={reply!r}",
    )

    # 4b. Regression check: a message that genuinely answers a pending
    # confirmation must still work (the relevance gate must not overcorrect).
    t3, _ = await create_task(session=session, user_id=user.id, title="Полить цветы")
    await mark_awaiting_confirmation(session, t3)
    reply2 = await handle_message(session, user, llm, "полить цветы сделал")
    await session.refresh(t3)
    check(
        "live e2e",
        "genuine pending-confirmation reply still completes the task",
        t3.status == "done",
        f"expected status=done | got status={t3.status!r} | reply={reply2!r}",
    )

    # 4c. Domain tool-schema availability sanity check (no live external call -
    # namaz/calendar need real network/OAuth this fake user doesn't have).
    reply3 = await handle_message(session, user, llm, "хочу пройти колесо баланса")
    check(
        "live e2e",
        "life-balance domain reachable end-to-end",
        "балл" in reply3.lower() or "сфер" in reply3.lower() or "оцен" in reply3.lower(),
        f"expected the model to start the 8-sphere questionnaire | reply={reply3!r}",
    )


async def main() -> None:
    llm = get_llm_provider()
    print(f"Testing against: {llm}\n")

    run_fastpath_checks()

    async with async_session_factory() as session:
        user = await get_or_create_user(
            session, telegram_id=FAKE_TG_ID, username="healthcheck", first_name="Health",
        )
        run_domain_checks(user)
        await run_llm_fixture_checks(llm)
        await run_live_checks(session, user, llm)

        # cleanup - throwaway user only
        await session.execute(delete(Task).where(Task.user_id == user.id))
        await session.execute(delete(ConversationMessage).where(ConversationMessage.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"{'=' * 60}\n{passed}/{total} passed\n{'=' * 60}")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")
    sys.exit(1 if passed < total else 0)


if __name__ == "__main__":
    asyncio.run(main())
