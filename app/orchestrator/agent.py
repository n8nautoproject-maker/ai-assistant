import asyncio
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.focus.service import get_open_session
from app.core.habits.service import format_coach_hint, get_focus_stats
from app.core.memory.service import (
    append_message,
    get_recent_messages,
    list_facts,
    maybe_summarize,
)
from app.core.reminders.service import pop_pending_reminder_prompt
from app.core.tasks.service import clear_awaiting_confirmation, get_awaiting_confirmation_tasks
from app.database.models.focus_session import FocusSession, FocusSessionStatus
from app.database.models.task import Task, TaskStatus
from app.database.models.user import User
from app.llm.base import LLMProvider
from app.orchestrator.domains import select_domains
from app.orchestrator.tools_registry import dispatch_tool_call, get_tool_schemas

SYSTEM_PROMPT = (
    "Ты — «AI Assistant Core», персональный ассистент пользователя. "
    "Ты помогаешь организовать задачи, защищаешь фокус и помнишь контекст. "
    "Отвечай кратко, по-русски, и используй доступные инструменты, когда "
    "пользователь просит что-то сделать (например, создать задачу), "
    "а не только рассказываешь, как это сделать.\n\n"
    "Важно: не обещай того, что инструменты не умеют делать на самом деле, "
    "и не обещай самостоятельно исправить баг или техническую проблему "
    "(например неправильное отображение списка) - у тебя нет доступа к "
    "своему собственному коду, ты можешь только вызывать существующие "
    "инструменты. Если что-то отображается некорректно - честно скажи, "
    "что это похоже на баг, и предложи то, что реально в твоих силах "
    "(например update_task), не обещая цикл технического исправления. "
    "Если пользователь просит «будильник» - у тебя нет доступа к системным "
    "будильникам телефона, только push-уведомление в Telegram (remind_at/"
    "set_recurring_reminder). Создай его, но СНАЧАЛА одной фразой уточни, "
    "что это Telegram-уведомление, а не системный будильник (если звук "
    "выключен или уведомления отключены, ничего не зазвонит), и только "
    "ПОСЛЕ этого подтверди время - не называй это «будильником» и не "
    "формулируй как оговорку в конце.\n\n"
    "Голосовые сообщения уже поддерживаются: они автоматически распознаются "
    "в текст перед тем, как попасть к тебе, так что ты уже читаешь их "
    "содержание как обычный текст. Если пользователь спрашивает, можешь ли "
    "ты принимать голосовые - отвечай, что да, уже можешь, а не что не "
    "умеешь работать с аудио.\n\n"
    "Текстовый ответ вида «всё сделано»/«все отмечены выполненными» без "
    "реальных вызовов инструмента - это вводящая в заблуждение выдумка, а "
    "не результат. Это касается любых действий (create_task, update_task, "
    "set_recurring_reminder и т.д.): никогда не подтверждай пользователю, "
    "что что-то сделано, если ты это не сделал вызовом соответствующего "
    "инструмента.\n\n"
    "Если у тебя уже достаточно данных, чтобы вызвать create_task/"
    "update_task/complete_task/delete_task/set_recurring_reminder и т.п. "
    "(понятно название и, если оно нужно, время/дата) - вызывай инструмент "
    "сразу, не спрашивая заранее что-то вроде «всё верно?»/«сохранить?». "
    "Подтверждай уже post-factum, результатом самого вызова. Уточняющий "
    "вопрос ДО вызова уместен, только если реально не хватает обязательной "
    "информации или есть настоящая неоднозначность (например несколько "
    "подходящих задач) - лишний вопрос там, где всё и так понятно из "
    "сообщения, выглядит как будто ассистент разучился действовать сразу.\n\n"
    "Если запрос пользователя подразумевал ОДНО конкретное действие "
    "(создать/изменить/завершить/удалить одну задачу и т.п.) - подтверди "
    "именно этим одним действием и не вызывай следом list_tasks/"
    "list_tasks_for_date/plan_day в том же ходу, если пользователь явно не "
    "попросил заодно показать список: раньше это приводило к тому, что "
    "подтверждение создания задачи подменялось полным дампом текущего "
    "списка задач, и было непонятно, создалась ли задача вообще. Это "
    "касается и обратного случая - нельзя заменять результат вызова "
    "инструмента общей отвлечённой фразой вроде «если у вас есть ещё "
    "вопросы, сообщите» без упоминания того, что реально произошло: "
    "финальный ответ должен явно называть, что именно сделано (название "
    "задачи и её время/дата, если есть), а не только вежливое заключение.\n\n"
    "Когда в промпте есть факт из истории фокус-сессий пользователя - "
    "используй его вместо общих мотивационных фраз («ты справишься», "
    "«не сдавайся»). Задача не мотивировать, а помочь принять решение, "
    "опираясь на то, что реально сработало или не сработало у этого "
    "пользователя раньше.\n\n"
    "Если пользователь хочет поговорить о переживаниях, эмоциональных "
    "трудностях или личных проблемах - поддержи разговор в тёплом, "
    "сочувствующем тоне, можешь поделиться своим взглядом как рассуждение "
    "собеседника, а не профессиональную рекомендацию. В начале такого "
    "разговора один раз явно скажи: ты не лицензированный психолог, твои "
    "слова - не медицинская и не психологическая рекомендация. Если в "
    "сообщениях пользователя есть признаки острого кризиса (мысли о "
    "причинении себе вреда, суицидальные мысли) - вне зависимости от "
    "остального контекста мягко, но прямо порекомендуй бесплатную "
    "круглосуточную горячую линию психологической помощи 8-800-2000-122 "
    "(по всей России) или очного специалиста, и не пытайся самостоятельно "
    "разрешить кризисную ситуацию разговором.\n\n"
    "Ты не ограничен только темой задач/фокуса/заметок - отвечай на любые "
    "вопросы пользователя (общие знания, объяснения, мнение, помощь с "
    "текстом, просто разговор на отвлечённую тему и т.п.) так же свободно, "
    "как это делает любая большая языковая модель (ChatGPT, DeepSeek). Не "
    "отказывайся и не уводи разговор обратно к задачам только потому, что "
    "вопрос не про них - если для ответа не нужен ни один инструмент, "
    "просто ответь по существу обычным текстом. web_search доступен, если "
    "вопросу нужны свежие факты (новости, погода и т.п.)."
)

# Must match app/scheduler/handlers.py's producer-side constants of the
# same name - duplicated rather than imported to avoid a circular import
# (scheduler/handlers.py -> channels/telegram_bot/sender.py ->
# channels/telegram_bot/bot.py -> orchestrator/agent.py).
MORNING_PRIORITY_MARKER = "MORNING_PRIORITY_QUESTION::"
PRIORITY_PROGRESS_MARKER = "PRIORITY_PROGRESS_CHECKIN::"

# Real incidents (documented earlier in this project, reproduced again
# 2026-08-02: "перенеси покупку лампочек на завтра" hit the generic fallback
# text) show the model occasionally looping through tool calls (often
# re-verifying via list_tasks) without ever settling on a final text answer
# within 4 iterations - not an infra timeout, confirmed via logs previously.
# Bumping to 6 is a cheap mitigation (more room before the fallback message)
# while the actual cause (tool-surface/prompt bloat) gets addressed
# structurally - see the 2026-07-30 domain-split work.
MAX_TOOL_ITERATIONS = 6
COACH_STATS_WINDOW_DAYS = 30

# Exact-phrase matching against the fixed templates each tool handler
# returns (tools_registry.py) turned out too narrow: the model doesn't
# copy those templates verbatim when it hallucinates a confirmation, it
# writes its own sentence - "Задача «ГИБДД» ... успешно создана и
# добавлена в ваш Google Calendar" doesn't contain the literal substring
# "задача создана" (words not adjacent), so the old fingerprint list
# missed this real incident even though "не подтверждай, если не вызвал
# инструмент" was already in the prompt. Matching word STEMS instead
# catches any phrasing built around the same completion verbs, at the
# cost of also matching negated statements ("не могу удалить") - which
# _NEGATION_PATTERN filters back out by checking a short window before
# each stem match.
_CONFIRMATION_STEM_PATTERN = re.compile(
    r"\b(созда\w*|добавле\w*|добавил\w*|отмет\w*|отмеч\w*|обновил\w*|"
    r"обновлен\w*|удали\w*|удален\w*|отмени\w*|отменен\w*|включ\w*|отключ\w*|"
    r"перенос\w*|перенош\w*|перенесе\w*|перенёс\w*|перенес\w*|"
    r"сохранил\w*|сохранен\w*|отправил\w*|"
    r"отправлен\w*|запланирова\w*|записал\w*|запомнил\w*|буду присыл\w*|"
    r"напомн\w*|поставил\w*|заверш\w*|выполн\w*)\b",
    re.IGNORECASE,
)
_NEGATION_PATTERN = re.compile(
    r"\b(не|нельзя|невозможно|не удалось|не получилось|не могу|не умею)\b",
    re.IGNORECASE,
)
_NEGATION_WINDOW_CHARS = 20

# Which real tool(s) would back up each claimed stem - checked against the
# tools actually dispatched THIS turn, not just "was any tool called at
# all". That coarser check missed a real incident: the model called some
# unrelated tool earlier in the turn (any_tool_called=True), then still
# fabricated an unrelated "Задача «Пообедать» успешно создана" with no
# create_task call behind it - the task never existed in the DB. Per-claim
# matching against the actual call set catches that; a blanket "some tool
# ran" check can't.
_CLAIM_STEM_TOOLS: dict[str, frozenset[str]] = {
    "созда": frozenset({"create_task", "create_project", "create_contact", "create_calendar_event"}),
    "добавле": frozenset({"create_task", "create_contact"}),
    "добавил": frozenset({"create_task", "create_contact"}),
    "отмет": frozenset({"complete_task", "complete_all_today_tasks"}),
    "отмеч": frozenset({"complete_task", "complete_all_today_tasks"}),
    "обновил": frozenset({"update_task"}),
    "обновлен": frozenset({"update_task"}),
    "удали": frozenset({"delete_task"}),
    # Passive participle forms ("удалена"/"отменена") use a different root
    # vowel (-ен- not -и-) than the active/imperative forms above, so they
    # need their own stem entries - real incident, 2026-08-02: "Задача
    # «Купить лампочки» удалена. ✅" slipped past this detector entirely
    # (delete_task was never actually called) because "удали\w*" doesn't
    # match "удалена", then a follow-up "Переношу её на завтра" claimed the
    # same task was still active - neither claim was ever checked. Same gap
    # for "включена/включены" (enable_namaz_reminders) and "отключена/
    # отключены" (disable_namaz_reminders) - a real "намаз включён ✅" claim
    # the same day had no stem to catch it under either.
    "удален": frozenset({"delete_task"}),
    "отмени": frozenset({"cancel_reminder", "abandon_focus_session"}),
    "отменен": frozenset({"cancel_reminder", "abandon_focus_session"}),
    "включ": frozenset({"enable_namaz_reminders"}),
    "отключ": frozenset({"disable_namaz_reminders"}),
    "перенос": frozenset({"update_task"}),
    "перенош": frozenset({"update_task"}),
    "перенесе": frozenset({"update_task"}),
    # "Перенёс"/"перенес" (past tense, first person - "Перенёс «Купить
    # лампочки» на завтра") is the single most natural way to phrase this
    # confirmation, and used a different 6th letter (е vs о) than
    # перенос\w*/перенош\w* - real incident, this exact phrasing slipped
    # past the detector entirely with zero backing update_task call.
    "перенёс": frozenset({"update_task"}),
    "перенес": frozenset({"update_task"}),
    "сохранил": frozenset({"save_note", "save_life_balance_rating"}),
    "сохранен": frozenset({"save_note", "save_life_balance_rating"}),
    # send_email is registered nowhere (tools_registry.py deletes it from
    # TOOLS outright) and appears in no domain's tool_names, so it can never
    # be in called_tools - keeping it as the sole expected tool here made
    # _claims_unconfirmed_action fire unconditionally on ANY use of the
    # "отправил" stem (e.g. "отправил тебе ссылку" from
    # connect_google_account), a guaranteed false positive on an unrelated,
    # already-correct response. connect_google_account's own reply text is
    # the realistic real-world source of this stem now.
    "отправил": frozenset({"connect_google_account", "create_calendar_event"}),
    "отправлен": frozenset({"connect_google_account", "create_calendar_event"}),
    "запланирова": frozenset({"create_calendar_event", "create_task"}),
    "записал": frozenset({"log_interaction", "save_note"}),
    "запомнил": frozenset({"remember_fact"}),
    # "Поставил напоминание/задачу" - as natural a confirmation phrasing as
    # "добавил"/"создал", just missing from the original stem list.
    "поставил": frozenset({"create_task", "set_recurring_reminder", "set_interval_reminder"}),
    # "Завершил фокус-сессию" / "задача завершена" / "я всё выполнил" -
    # same gap: real completion-claim verbs the model naturally reaches
    # for that had no stem entry at all, so a hallucinated claim using
    # either of them passed through unbacked and unnoticed.
    "заверш": frozenset({"complete_task", "complete_all_today_tasks", "complete_focus_session"}),
    "выполн": frozenset({"complete_task", "complete_all_today_tasks"}),
    # enable_namaz_reminders' own success message ("Готово, буду присылать
    # напоминания о времени намаза...") reuses this exact phrasing - without
    # it here, a real enable_namaz_reminders call got flagged as an
    # unconfirmed claim (false positive), the opposite problem from the
    # missing-stem gaps above.
    "буду присыл": frozenset({
        "set_recurring_reminder", "set_interval_reminder", "enable_namaz_reminders",
    }),
    # Real incident 2026-08-27: "Готово! ✅\n\nНапомню завтра (28 августа), "
    # в 09:00 — выложить пост... Придёт push-уведомлением в Telegram." had
    # no create_task/reminder call behind it at all (confirmed empty in
    # both the tasks and scheduled_jobs tables) - "Напомню X — Y" is the
    # bot's own standard confirmation template for a newly set reminder
    # throughout this codebase, but no stem here matched it, same "wrote
    # its own sentence, no listed stem covers it" gap this detector was
    # already built to catch for other phrasings.
    "напомн": frozenset({"create_task", "set_recurring_reminder", "set_interval_reminder"}),
}


def _claims_unconfirmed_action(text: str, called_tools: frozenset[str]) -> bool:
    lowered = text.lower()
    for match in _CONFIRMATION_STEM_PATTERN.finditer(lowered):
        preceding = lowered[max(0, match.start() - _NEGATION_WINDOW_CHARS) : match.start()]
        if _NEGATION_PATTERN.search(preceding):
            continue
        matched_word = match.group(0)
        expected_tools = next(
            (tools for stem, tools in _CLAIM_STEM_TOOLS.items() if matched_word.startswith(stem)),
            None,
        )
        if expected_tools is None or not (called_tools & expected_tools):
            return True
    return False


# Real incident, 2026-07-28: three separate messages describing new tasks
# ("задача на сегодня получить загранник", a 3-item batch, "сходить сегодня
# на тренировку") each got the exact same hollow reply - "Если у вас есть
# ещё какие-либо задачи или вопросы, пожалуйста, сообщите." - regardless of
# what actually happened underneath: the batch message genuinely created 3
# tasks (confirmed in the DB, due_at set correctly), one message created a
# task but with due_at left NULL despite saying "на сегодня" (so it silently
# never showed up in any date-filtered list), and the third never called
# create_task at all - the task was never persisted anywhere. This exact
# phrase is already named in SYSTEM_PROMPT as the specific thing not to say
# ("нельзя заменять результат вызова инструмента общей отвлечённой фразой
# вроде «если у вас есть ещё вопросы, сообщите»") - the prompt instruction
# alone isn't holding, same recurring lesson as _claims_unconfirmed_action/
# _asks_premature_confirmation above needing code-level backup. Requiring
# the absence of any _CONFIRMATION_STEM_PATTERN match keeps this narrow: a
# reply that already names a concrete result and just tacks on harmless
# trailing filler isn't flagged, only a reply that is PURELY the deflection.
_GENERIC_DEFLECTION_PATTERN = re.compile(
    r"если (?:у (?:вас|тебя) )?(?:есть|появ\w*|возникн\w*) .{0,30}"
    r"(?:вопрос\w*|задач\w*|дел\w*).{0,30}"
    r"(?:сообщ\w*|дай\w* знать|напиш\w*|скаж\w*|обраща\w*)",
    re.IGNORECASE,
)


def _is_generic_deflection(text: str) -> bool:
    lowered = text.lower()
    return bool(_GENERIC_DEFLECTION_PATTERN.search(lowered)) and not _CONFIRMATION_STEM_PATTERN.search(lowered)


GENERIC_DEFLECTION_NOTICE = (
    "Технический контроль: твой предыдущий ответ - это ничего не значащая "
    "вежливая отговорка («если у вас есть ещё вопросы, сообщите» и "
    "подобное) без единого конкретного факта. Перечитай исходное сообщение "
    "пользователя из этого хода и реальный результат вызванных инструментов "
    "(если они были) и ответь заново: если что-то реально было создано/"
    "изменено/завершено в этом ходу - назови это прямо (название задачи и "
    "её дата/время, если есть); если пользователь описал дело, событие или "
    "план на конкретное время, а ты ещё не вызвал для этого инструмент - "
    "вызови его сейчас (обычно create_task, с due_at, если в сообщении "
    "названа дата), а не откладывай на «сообщите, если что»; если это был "
    "настоящий вопрос - ответь на него по существу. Важно: этот текст - "
    "служебная подсказка ДЛЯ ТЕБЯ, твой следующий ответ уйдёт напрямую "
    "пользователю в чат - не упоминай эту проверку и не объясняй "
    "пользователю, что сработал внутренний контроль."
)


# Mirror problem to _claims_unconfirmed_action: instead of claiming an
# unbacked action happened, the model asks the user to verify details it
# already has in full, before calling any tool at all - the exact "Всё ли
# верно с задачей..." regression a prompt-only instruction failed to
# suppress reliably in live use (confirmed twice: create_task never ran
# either time, not just an extra sentence tacked onto a real creation).
# Same fix shape as the hallucination detector above: a narrow regex on the
# fixed handful of phrasings this model actually uses for it, forcing one
# corrective retry rather than trusting the system prompt alone to hold.
_PREMATURE_CONFIRMATION_PATTERN = re.compile(
    r"вс[её] ли верно|верно ли (?:я|это)|прав(?:ильно)? ли я (?:понял|поняла)|"
    r"подтверди(?:те)?,? что|сохранить (?:эту |её )?задачу\?",
    re.IGNORECASE,
)


def _asks_premature_confirmation(text: str) -> bool:
    return bool(_PREMATURE_CONFIRMATION_PATTERN.search(text.lower()))


# Real incident, 2026-08-14: web_search was genuinely broken (Tavily
# region-blocked) for two days, during which several "поиск не отвечает"
# replies landed in conversation_messages. Once the integration was fixed
# (switched to Serper), the model kept repeating the same refusal on the
# very next turn WITHOUT calling web_search at all - confirmed via prod
# logs showing a single deepseek.com completion call and zero requests to
# google.serper.dev for that turn. It was anchoring on its own prior
# (by-then-false) statements still sitting in the history window rather
# than actually retrying the tool. Same fix shape as the two detectors
# above: don't trust the prompt alone to stop this, force a corrective
# retry whenever the model claims search is unavailable without having
# just tried it.
_SEARCH_UNAVAILABLE_PATTERN = re.compile(
    r"поиск.{0,60}(?:не отвечает|недоступ\w*|не работа\w*)|"
    r"не (?:получилось|могу) (?:выполнить поиск|получить (?:данны|информаци|актуальны|точны))",
    re.IGNORECASE,
)


def _claims_search_unavailable(text: str, called_tools: frozenset[str]) -> bool:
    return "web_search" not in called_tools and bool(_SEARCH_UNAVAILABLE_PATTERN.search(text.lower()))


SEARCH_UNAVAILABLE_NOTICE = (
    "Технический контроль: в твоём предыдущем ответе ты написал, что поиск в "
    "интернете не работает/недоступен, но не вызвал инструмент web_search в "
    "этом ходу вообще. web_search сейчас исправен - если в истории диалога "
    "есть более ранние сообщения о его неполадках, они устарели, не "
    "полагайся на них и не повторяй их формулировки. Вызови web_search "
    "прямо сейчас по теме исходного вопроса пользователя и ответь на основе "
    "реального результата. Важно: этот текст - служебная подсказка ДЛЯ "
    "ТЕБЯ, твой следующий ответ уйдёт напрямую пользователю в чат - не "
    "упоминай эту проверку и не объясняй пользователю, что сработал "
    "внутренний контроль."
)


PREMATURE_CONFIRMATION_NOTICE = (
    "Технический контроль: в твоём предыдущем ответе ты спрашиваешь у "
    "пользователя подтверждение («всё ли верно» и т.п.), не вызвав ни "
    "одного инструмента. Разберись, что на самом деле просил пользователь "
    "в исходном сообщении, и ответь заново: если это была просьба что-то "
    "СДЕЛАТЬ (создать/изменить/завершить/удалить и т.п.) и у тебя уже есть "
    "все нужные детали (иначе ты не смог бы сформулировать вопрос с ними) "
    "- вызови нужный инструмент прямо сейчас, не спрашивая снова. Если "
    "исходное сообщение было ВОПРОСОМ о уже существующих данных (например "
    "«когда у меня встреча с X», «во сколько напоминание про Y») - не "
    "вызывай никакой изменяющий инструмент, а ответь на вопрос прямым "
    "фактическим предложением по данным, которые у тебя уже есть в этом "
    "разговоре или которые вернёт list_tasks/list_tasks_for_date. Важно: "
    "этот текст - служебная подсказка ДЛЯ ТЕБЯ, твой следующий ответ уйдёт "
    "напрямую пользователю в чат - не упоминай эту проверку и не объясняй "
    "пользователю, что сработал внутренний контроль или что ты передумал."
)


async def _retry_with_notice(
    messages: list[dict],
    session: AsyncSession,
    user: User,
    llm: LLMProvider,
    tool_schemas: list[dict],
    final_content: str,
    notice: str,
    called_tools: set[str],
    force_tool_call: bool = False,
) -> str:
    """Shared by both correction paths below: append the flagged reply plus
    a corrective system note, retry once, and execute any tool call the
    retry makes. Falls back to the retry's own text if it still declines
    to call anything - never loops more than this one extra round.

    force_tool_call: live testing showed the premature-confirmation notice
    alone isn't reliable - the model repeated the exact same text-only
    "verify?" reply even after being told explicitly to call a tool
    instead. Passing force_tool_call=True there makes the retry call
    tool_choice="required" on the provider, which removes the model's
    option to reply with plain text at all - it still picks which tool
    (the notice's text tells it to pick a read-only one if the original
    message was a question, not necessarily create_task specifically).
    Not used for the claims-unconfirmed-action notice: there, the model
    might legitimately need to just retract/correct a hallucinated claim
    in text rather than being forced into calling something.
    """
    messages.append({"role": "assistant", "content": final_content})
    messages.append({"role": "system", "content": notice})
    retry = await llm.chat(messages, tools=tool_schemas, force_tool_call=force_tool_call)
    if retry.tool_calls:
        called_tools.update(tc.name for tc in retry.tool_calls)
        await _execute_tool_calls(messages, session, user, retry.tool_calls)
        wrap_up = await llm.chat(messages, tools=tool_schemas)
        return wrap_up.content or final_content
    return retry.content or final_content


CONFIRMATION_MISMATCH_NOTICE = (
    "Технический контроль: в твоём предыдущем ответе есть формулировка, "
    "похожая на подтверждение выполненного действия (создание/изменение/"
    "отмена чего-либо), но она не подкреплена вызовом нужного инструмента "
    "в этом ходу диалога. Если пользователь на самом деле просил что-то "
    "сделать - вызови соответствующий инструмент прямо сейчас, не "
    "переспрашивая пользователя заново. Если для этого не хватает "
    "деталей (например не указано время) - задай пользователю обычный, "
    "естественный уточняющий вопрос напрямую, КАК ЕСЛИ БЫ ты отвечал ему "
    "в первый раз. Важно: этот текст - служебная подсказка ДЛЯ ТЕБЯ, "
    "твой следующий ответ уйдёт напрямую пользователю в чат - не пиши "
    "объяснений о своём внутреннем процессе в третьем лице («я не вызвал "
    "инструмент, потому что пользователь...», «технический контроль "
    "показал...») - пользователь не должен видеть отсылок к этой "
    "проверке или к тому, что и почему ты (не) вызвал."
)


async def _execute_tool_calls(messages: list[dict], session: AsyncSession, user: User, tool_calls) -> None:
    messages.append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": str(tc.arguments)},
                }
                for tc in tool_calls
            ],
        }
    )
    for tc in tool_calls:
        result = await dispatch_tool_call(session, user, tc.name, tc.arguments)
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})


async def _focus_session_hint(session: AsyncSession, focus_session: FocusSession) -> str:
    # There's no persisted conversation history yet (Memory module isn't
    # built), so each incoming message is handled with zero context about
    # what the assistant asked previously. The open focus_sessions row *is*
    # that missing context - it's re-read from the DB on every message and
    # folded into the prompt, so the model can still tell "да, начинаем"
    # apart from an unrelated message even though it never sees the prior
    # turn that prompted it.
    if focus_session.status == FocusSessionStatus.PLANNED.value:
        hint = (
            "У пользователя есть предложенная, но ещё не начатая фокус-сессия "
            f"(id={focus_session.id}). Если следующее сообщение - согласие начать, "
            "вызови start_focus_session. Если пользователь явно просит закрыть, "
            "отменить или отклонить её (в том числе если говорит, что не помнит "
            "такого предложения или что уже поработал сам по себе) - вызови "
            "abandon_focus_session с коротким interruption_reason, а не "
            "complete_focus_session (её нельзя завершить как выполненную, она "
            "не была начата). Если сообщение вообще не про сессию - просто "
            "ответь по существу, не вызывая инструмент."
        )
        # Coach: ground any encouragement in the user's own history instead
        # of generic motivational phrases, if there's enough history to say
        # something real (format_coach_hint returns None otherwise).
        since = datetime.now(timezone.utc) - timedelta(days=COACH_STATS_WINDOW_DAYS)
        stats = await get_focus_stats(session, focus_session.user_id, since)
        coach_hint = format_coach_hint(stats)
        if coach_hint:
            hint += (
                f" Если уместно, можешь опереться на такой факт из истории "
                f"пользователя (не выдумывай другие цифры): {coach_hint}"
            )
        return hint
    if focus_session.status == FocusSessionStatus.ACTIVE.value:
        # Follow-up hasn't fired yet at this point - don't claim otherwise, or
        # the model treats an unrelated message as an answer to a question
        # that was never asked and produces a confused reply.
        return (
            f"У пользователя сейчас идёт фокус-сессия (id={focus_session.id}), "
            "follow-up ещё не отправлялся. Отвечай на сообщение по существу и "
            "не завершай сессию просто так - вызывай complete_focus_session или "
            "abandon_focus_session только если пользователь сам явно говорит, "
            "что уже закончил работу раньше срока или что сессия сорвалась "
            "прямо сейчас."
        )
    return (
        "У пользователя сейчас открыта фокус-сессия, и его недавно спросили, что "
        f"удалось сделать (id={focus_session.id}). Если сообщение описывает "
        "выполненную работу - вызови complete_focus_session с кратким outcome_note. "
        "Если сообщение говорит, что не получилось поработать, или объясняет, что "
        "помешало - вызови abandon_focus_session с interruption_reason. Если "
        "непонятно, что произошло, уточни вопросом «Что помешало?», не вызывая "
        "инструмент."
    )


WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
]


def _current_time_context(user: User) -> str:
    # Without this, the model has no anchor to resolve relative time
    # expressions ("через 2 минуты", "завтра в 9") into the absolute ISO
    # timestamps create_task/create_calendar_event expect - it would either
    # guess wrong or (observed in practice) invent an excuse to refuse
    # instead of admitting it doesn't know the current time.
    now_local = datetime.now(timezone.utc).astimezone(ZoneInfo(user.timezone))
    weekday = WEEKDAYS_RU[now_local.weekday()]
    # A named-weekday request ("напомни в пятницу") used to make the model
    # compute the date itself from just today's weekday - real incident
    # 2026-08-25: asked on a Tuesday for "пятница", it stored the reminder
    # for Saturday (one day off). Spelling out each of the next 7 days'
    # actual dates removes the arithmetic entirely - a lookup instead of a
    # calculation the model can get wrong.
    upcoming = ", ".join(
        f"{WEEKDAYS_RU[(now_local.weekday() + i) % 7]} — {(now_local + timedelta(days=i)):%Y-%m-%d}"
        for i in range(1, 8)
    )
    return (
        f"Текущие дата и время: {now_local:%Y-%m-%d %H:%M} ({weekday}), "
        f"часовой пояс пользователя: {user.timezone}. Ближайшие дни: {upcoming}."
    )


async def _task_followup_hint(
    session: AsyncSession, user: User
) -> tuple[str, list[Task]] | None:
    tasks = await get_awaiting_confirmation_tasks(session, user.id)
    if not tasks:
        return None
    # Fire-once: clear immediately so this hint doesn't linger and get
    # misapplied to a later, unrelated message.
    for t in tasks:
        await clear_awaiting_confirmation(session, t)

    if len(tasks) == 1:
        task = tasks[0]
        hint = (
            f"Пользователя только что спросили, получилось ли сделать задачу "
            f"«{task.title}» (id={task.id}). Если сообщение подтверждает "
            f"выполнение - вызови complete_task с task_id={task.id}. Если "
            "сообщение говорит, что не получилось - вызови update_task с "
            f"task_id={task.id}, сдвинув due_at/remind_at на следующий день "
            "в то же время (бери точную текущую дату из блока времени выше, "
            "не вычисляй на глаз), и скажи, что перенёс. Если сообщение не "
            "связано с задачей - не вызывай ни один из этих инструментов, "
            "просто ответь по существу."
        )
        return hint, tasks

    # evening_review marks several at once - the user's reply is free text
    # that may name some by title ("первые две сделал") without listing
    # every id, so both directions (complete vs reschedule) need the id
    # visible per task, not just the title.
    listing = "; ".join(f"«{t.title}» (id={t.id})" for t in tasks)
    hint = (
        f"Пользователю только что прислали вечерний обзор незавершённых "
        f"задач за сегодня: {listing}. Если сообщение называет конкретные "
        "из них как выполненные - вызови complete_task с соответствующим "
        "task_id для каждой названной. Для остальных из этого списка, "
        "которые пользователь не подтвердил выполненными и явно не "
        "попросил оставить как есть - вызови update_task для каждой, "
        "сдвинув due_at/remind_at на следующий день в то же время (бери "
        "точную текущую дату из блока времени выше, не вычисляй на глаз), "
        "и упомяни, что перенёс остальные на завтра. ВАЖНО: обработай "
        f"КАЖДУЮ из перечисленных {len(tasks)} задач по отдельности, ни одну "
        "не пропускай - живая ошибка показала, что при нескольких задачах в "
        "списке одна из них может остаться без вызова инструмента вообще, "
        "если торопиться с ответом. Такие сообщения часто идут без знаков "
        "препинания (голосовой ввод) - «задача1 выполнено задача2 не "
        "выполнено» означает, что «выполнено»/«не выполнено» относится к "
        "НАЗВАНИЮ ПЕРЕД НИМ, а не ко всему сообщению разом: разбирай "
        "по очереди, к какой конкретно задаче относится каждая пометка "
        "статуса, не смешивай статус одной с соседней. Если сообщение "
        "вообще не связано с этими задачами - не вызывай ни один из этих "
        "инструментов, просто ответь по существу."
    )
    return hint, tasks


# Below this word count a message is very unlikely to be a run-on voice
# transcription naming several separate tasks - skips the extra LLM call
# (and its latency/cost) for the common case of a short one-liner.
_MULTI_TASK_MIN_WORDS = 8


# Claude Sonnet 5, not the configured LLM_PROVIDER (DeepSeek/Yandex) - this
# one extraction step is specifically the place those models proved
# unreliable (garbled/dropped items in a run-on voice batch), and the task
# is narrow/cheap enough that a stronger model here doesn't meaningfully
# change per-message cost. Empty ANTHROPIC_API_KEY degrades to skipping this
# step entirely (see _extract_multi_task_items below), not an error - the
# main DeepSeek/Yandex call still runs as it always did.
_ANTHROPIC_EXTRACTION_MODEL = "claude-sonnet-5"


async def _extract_multi_task_items(text: str) -> list[str] | None:
    """Isolated, cheap extraction call - same "sidestep the overloaded main
    prompt" pattern as _is_reply_relevant just below. Real incident,
    2026-08 (see CLAUDE.md item #5): a single voice message describing
    several tasks in one unpunctuated run-on sentence reliably lost or
    garbled some of them - the main call has to both parse an ambiguous
    list AND decide/place tool calls in the same breath, competing with an
    already-large system prompt. Extracting the list first, in its own tiny
    prompt with nothing else to attend to, hands the main call an explicit,
    already-disambiguated checklist instead.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=_ANTHROPIC_EXTRACTION_MODEL,
            max_tokens=500,
            thinking={"type": "disabled"},
            system=(
                "Определи, описывает ли сообщение пользователя НЕСКОЛЬКО "
                "(два или больше) отдельных дел/задач для добавления в "
                "список дел - типичный случай: голосовое сообщение без "
                "знаков препинания, где несколько дел названы подряд. Если "
                "да - перечисли каждое отдельной строкой, коротко и по "
                "существу, без нумерации и лишних слов, сохранив срок/дату/"
                "время каждого пункта, если она названа в исходном "
                "сообщении. Если сообщение - это ОДНА задача, вопрос или "
                "что-то ещё не про список из нескольких новых задач - "
                "ответь ровно одним словом: НЕТ."
            ),
            messages=[{"role": "user", "content": text}],
        )
    except Exception:
        return None

    content = "".join(block.text for block in response.content if block.type == "text").strip()
    if not content or content.upper().startswith("НЕТ"):
        return None
    items = [line.strip(" -•\t") for line in content.splitlines() if line.strip()]
    # A single line back means the model didn't actually find a batch (or
    # just echoed the message) - only a genuine 2+ item split is useful.
    return items if len(items) >= 2 else None


def _multi_task_hint(items: list[str]) -> str:
    listing = "; ".join(f"«{item}»" for item in items)
    return (
        f"Технический разбор входящего сообщения показал, что в нём "
        f"названы {len(items)} отдельных новых дела/задачи одним списком: "
        f"{listing}. Вызови create_task ОТДЕЛЬНО для КАЖДОГО из них по "
        "очереди, ни один пункт не пропускай - живая ошибка показала, что "
        "при нескольких задачах в одном голосовом сообщении без знаков "
        "препинания часть из них может остаться без вызова инструмента "
        "вообще. Вычисляй дату/время для КАЖДОГО пункта отдельно, строго "
        "по его собственной формулировке из списка выше (бери точную "
        "текущую дату из блока времени выше, не вычисляй на глаз), не "
        "переиспользуй дату/время, вычисленные для соседнего пункта."
    )


async def _is_reply_relevant(llm: LLMProvider, topic: str, user_message: str) -> bool:
    """Cheap, isolated yes/no classification call, deliberately kept OUTSIDE
    the main system prompt/tool loop - the actual root cause of the
    "Задача починить стул на кухне" / "задача купить подушку" incidents
    (2026-07-29/30) wasn't tool selection, it was a stale pending-question
    hint dominating an unrelated new message because the escape clause
    ("если не связано - просто ответь по существу") was buried under many
    lines of specific procedural instructions in the same bloated prompt.
    Deciding relevance here, in a tiny isolated prompt with nothing else
    competing for the model's attention, sidesteps that entirely instead of
    hoping the big call reads the escape clause correctly. If this call
    itself fails, default to True (old behaviour: inject the hint) rather
    than silently dropping state on an infra hiccup.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "Ты определяешь, отвечает ли новое сообщение пользователя "
                "ПО СУЩЕСТВУ на конкретный вопрос/сообщение, которое ему "
                "только что прислали, или это отдельное сообщение не по "
                "теме - даже если оно на ту же общую тему в широком "
                "смысле. Например: пользователю прислали вопрос про уже "
                "существующие дела/задачи (сделал ли он их) - если новое "
                "сообщение называет СОВЕРШЕННО ДРУГОЕ, ранее не "
                "упомянутое дело, это НЕ ответ по теме, даже если оно "
                "тоже про задачи в общем смысле: пользователь просто "
                "пишет что-то новое, не отвечая на заданный вопрос. "
                "Реальный ответ по теме подтверждает/опровергает/"
                "уточняет именно то, что было названо в присланном "
                "сообщении. Ответь одним словом: да или нет."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Пользователю только что прислали: «{topic}»\n\n"
                f"Новое сообщение пользователя: «{user_message}»\n\n"
                "Это ответ по теме присланного (да) или отдельное новое "
                "сообщение не по теме (нет)?"
            ),
        },
    ]
    try:
        response = await llm.chat(messages)
    except Exception:
        return True
    return (response.content or "").strip().lower().startswith("да")


async def _reminder_reply_hint(session: AsyncSession, user: User) -> tuple[str, str] | None:
    # Any scheduler-sent proactive push that asks something but has no
    # dedicated state table of its own (custom_reminder pushes, and the
    # morning check-in's "no task with a due date - what's today's main
    # thing?" branch) goes out via the scheduler, not handle_message, so it
    # lands in conversation history as a bare assistant turn with no user
    # turn before it - the same "no anchor for the next reply" gap
    # _focus_session_hint and _task_followup_hint solve for their own
    # flows. Without an explicit hint here, a reply has nothing telling the
    # model what it's a reply to, and it can fall back to just chatting (or,
    # for the reminder case, re-confirming the reminder setup) instead of
    # acting on it - see the "работают, все ок" bug and the morning
    # check-in "сделать парсер" bug this was written for.
    text = await pop_pending_reminder_prompt(session, user)
    if text is None:
        return None

    if text.startswith(MORNING_PRIORITY_MARKER):
        question = text[len(MORNING_PRIORITY_MARKER):]
        topic = (
            f"утренний список задач на сегодня, с вопросом, какая или "
            f"какие из них в приоритете:\n{question}"
        )
        hint = (
            f"Пользователю только что прислали утренний список задач на "
            f"сегодня и спросили, какая или какие из них в приоритете:\n"
            f"{question}\n\n"
            "Если следующее сообщение называет одну или несколько задач из "
            "этого списка - вызови mark_priority_tasks с их названиями "
            "(titles), не создавай новую задачу. Если сообщение вообще не "
            "про приоритет - просто ответь по существу, не вызывая "
            "инструмент."
        )
        return topic, hint
    if text.startswith(PRIORITY_PROGRESS_MARKER):
        listing = text[len(PRIORITY_PROGRESS_MARKER):]
        topic = f"проверка по приоритетным задачам на сегодня: {listing}"
        hint = (
            f"Пользователю только что прислали проверку по приоритетным "
            f"задачам на сегодня: {listing}. Если сообщение говорит, что "
            "какая-то из них сделана - вызови complete_task для неё. Если "
            "говорит, что не сделана или объясняет, что помешало - не "
            "меняй её статус молча, просто ответь по-человечески "
            "(например можно предложить перенести время). Если сообщение "
            "вообще не про эти задачи - просто ответь по существу, не "
            "вызывая инструмент."
        )
        return topic, hint

    topic = text
    hint = (
        f"Пользователю только что проактивно прислали сообщение-вопрос: "
        f"«{text}». Следующее сообщение, скорее всего, отвечает на него по "
        "смыслу. Если оно называет что-то, что нужно сделать (например это "
        "был вопрос про главную задачу на сегодня, и ответ называет "
        "занятие/дело) - создай задачу через create_task с этим названием "
        "(due_at сегодня, если не назвали другое время), а не просто "
        "прими к сведению. Если это ответ на напоминание-вопрос "
        "(set_interval_reminder/set_recurring_reminder) - ответь по "
        "существу и по-человечески, коротко, не вызывай "
        "set_interval_reminder/set_recurring_reminder заново и не "
        "повторяй подтверждение о том, что напоминание установлено - оно "
        "уже установлено, если только пользователь явно не просит изменить "
        "его параметры (частоту, окно, текст) или отменить его."
    )
    return topic, hint


async def _memory_context(session: AsyncSession, user: User) -> str:
    parts = []
    if user.memory_summary:
        parts.append(f"Справка о предыдущих разговорах с пользователем:\n{user.memory_summary}")
    facts = await list_facts(session, user.id)
    if facts:
        facts_text = "\n".join(f"- {f.key}: {f.value}" for f in facts)
        parts.append(f"Известные факты о пользователе:\n{facts_text}")
    return "\n\n".join(parts)


async def handle_message(
    session: AsyncSession,
    user: User,
    llm: LLMProvider,
    text: str,
    reply_to_text: str | None = None,
) -> str:
    system_prompt = f"{SYSTEM_PROMPT}\n\n{_current_time_context(user)}"
    if reply_to_text:
        # Telegram's reply-to relationship - without this the model has no
        # way to resolve "поменяй вот это сообщение"/"почему тут так"
        # style references to a specific earlier message (real incident,
        # 2026-07-28: user replied to a task-list message asking to change
        # its format, got an unrelated answer).
        system_prompt = (
            f"{system_prompt}\n\nПользователь ответил (через функцию "
            f"Telegram «Ответить») именно на это более раннее сообщение "
            f'в чате: "{reply_to_text}". Учитывай это как объект, к '
            "которому относится текущее сообщение пользователя, если оно "
            "по смыслу похоже на реакцию/уточнение/просьбу об изменении "
            "этого конкретного более раннего сообщения."
        )
    # _task_followup_hint/_reminder_reply_hint have DB side effects that
    # must run unconditionally on every message (clearing
    # awaiting_confirmation, popping pending_reminder_prompt - both
    # fire-once) - those stay sequential. The three _is_reply_relevant
    # calls that decide whether to actually SURFACE each hint are pure LLM
    # reads with no side effects of their own, so they're batched into one
    # asyncio.gather instead of three sequential round-trips - a message
    # could otherwise cost 3 extra sequential LLM calls on top of the main
    # call (+ possible _retry_with_notice + maybe_summarize), which adds up
    # fast on a provider already flagged as slower/less stable than before.
    open_session = await get_open_session(session, user.id)
    followup_result = await _task_followup_hint(session, user)
    reminder_hint_result = await _reminder_reply_hint(session, user)

    # Scheduled (not awaited) here so it runs concurrently with the
    # relevance_checks gather right below, same reasoning as that batch -
    # one more potential LLM round-trip per message would add up otherwise.
    multi_task_future = (
        asyncio.ensure_future(_extract_multi_task_items(text))
        if len(text.split()) >= _MULTI_TASK_MIN_WORDS
        else None
    )

    relevance_checks = {}
    if open_session is not None and open_session.status != FocusSessionStatus.ACTIVE.value:
        focus_topic = (
            "предложенная, ещё не начатая фокус-сессия - нужно "
            "подтвердить начало или отклонить"
            if open_session.status == FocusSessionStatus.PLANNED.value
            else "вопрос, что удалось сделать во время фокус-сессии"
        )
        relevance_checks["focus"] = _is_reply_relevant(llm, focus_topic, text)
    if followup_result:
        _, tasks = followup_result
        followup_topic = (
            f"вопрос, получилось ли сделать задачу «{tasks[0].title}»"
            if len(tasks) == 1
            else "вечерний обзор невыполненных сегодня задач, с вопросом, что из них сделано"
        )
        relevance_checks["followup"] = _is_reply_relevant(llm, followup_topic, text)
    if reminder_hint_result:
        reminder_topic, _ = reminder_hint_result
        relevance_checks["reminder"] = _is_reply_relevant(llm, reminder_topic, text)

    relevant = {}
    if relevance_checks:
        results = await asyncio.gather(*relevance_checks.values())
        relevant = dict(zip(relevance_checks.keys(), results))

    if open_session is not None:
        if open_session.status == FocusSessionStatus.ACTIVE.value:
            # No question has been asked yet in this state (follow-up
            # hasn't fired) - there's nothing to check relevance against,
            # this hint only guards against accidental complete/abandon.
            system_prompt = f"{system_prompt}\n\n{await _focus_session_hint(session, open_session)}"
        elif relevant.get("focus"):
            system_prompt = f"{system_prompt}\n\n{await _focus_session_hint(session, open_session)}"

    followup_tasks: list[Task] = []
    if followup_result and relevant.get("followup"):
        followup_hint, tasks = followup_result
        system_prompt = f"{system_prompt}\n\n{followup_hint}"
        followup_tasks = tasks
        # else: awaiting_confirmation was already cleared inside
        # _task_followup_hint (fire-once) - nothing further to undo, this
        # turn just doesn't get forced to engage with those tasks.

    if reminder_hint_result and relevant.get("reminder"):
        _, reminder_hint = reminder_hint_result
        system_prompt = f"{system_prompt}\n\n{reminder_hint}"

    if multi_task_future is not None:
        multi_task_items = await multi_task_future
        if multi_task_items:
            system_prompt = f"{system_prompt}\n\n{_multi_task_hint(multi_task_items)}"

    memory_context = await _memory_context(session, user)
    if memory_context:
        system_prompt = f"{system_prompt}\n\n{memory_context}"

    history = await get_recent_messages(session, user.id)

    # Domain split: core (tasks/focus/reminders/memory/web_search) is always
    # included; namaz/life-balance/knowledge-CRM/calendar only load their
    # prompt fragment + tool schemas when the message (or, for namaz, a
    # sticky per-user flag) actually suggests they're relevant. See
    # app/orchestrator/domains.py - this is the fix for the "one ~800-line
    # prompt + 35 tool schemas on every single call" bottleneck already
    # diagnosed as a real reliability ceiling for both YandexGPT and DeepSeek.
    # recent_text (the last couple turns, fetched above) is searched too,
    # not just the current message - real incident 2026-08-12: a user's
    # first voice message said "добавь цели на месяц не курить кальян"
    # (matched), a second one right after just said "...работать надо
    # сам" with no trigger word at all (natural elliptical speech
    # continuing the same topic) and lost the life-goal tools entirely for
    # that turn, which is most of why that exchange fell apart.
    recent_text = " ".join(m.content for m in history[-2:])
    domains = select_domains(
        text, user, has_open_focus_session=open_session is not None, recent_text=recent_text
    )
    for module in domains:
        system_prompt = f"{system_prompt}\n\n{module.prompt_fragment}"
    domain_tool_names = frozenset().union(*(m.tool_names for m in domains))

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *({"role": m.role, "content": m.content} for m in history),
        {"role": "user", "content": text},
    ]
    tool_schemas = get_tool_schemas(domain_tool_names)

    final_content = "Не получилось довести до конца — попробуй переформулировать запрос."
    called_tools: set[str] = set()
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await llm.chat(messages, tools=tool_schemas)

        if not response.tool_calls:
            final_content = response.content or ""
            break

        called_tools.update(tc.name for tc in response.tool_calls)
        await _execute_tool_calls(messages, session, user, response.tool_calls)

    if _claims_unconfirmed_action(final_content, frozenset(called_tools)):
        final_content = await _retry_with_notice(
            messages, session, user, llm, tool_schemas,
            final_content, CONFIRMATION_MISMATCH_NOTICE, called_tools,
        )
    elif not called_tools and _asks_premature_confirmation(final_content):
        final_content = await _retry_with_notice(
            messages, session, user, llm, tool_schemas,
            final_content, PREMATURE_CONFIRMATION_NOTICE, called_tools,
            force_tool_call=True,
        )
    elif _claims_search_unavailable(final_content, frozenset(called_tools)):
        final_content = await _retry_with_notice(
            messages, session, user, llm, tool_schemas,
            final_content, SEARCH_UNAVAILABLE_NOTICE, called_tools,
            force_tool_call=True,
        )
    elif _is_generic_deflection(final_content):
        final_content = await _retry_with_notice(
            messages, session, user, llm, tool_schemas,
            final_content, GENERIC_DEFLECTION_NOTICE, called_tools,
        )

    if len(followup_tasks) > 1:
        # Real incident: a user named 4 tasks' status in one reply to the
        # evening_review batch hint - the model correctly handled 3 (2
        # completed, 1 rescheduled) and silently dropped the 4th (no
        # complete_task, no update_task, no mention in the reply at all).
        # Comparing before/after state per task_id catches this
        # deterministically instead of trusting the model noticed its own
        # omission - it demonstrably doesn't reliably.
        missed = []
        for before in followup_tasks:
            after = await session.get(Task, before.id)
            if (
                after is not None
                and after.status == TaskStatus.PENDING.value
                and after.due_at == before.due_at
                and after.remind_at == before.remind_at
            ):
                missed.append(after)
        if missed:
            missed_list = "; ".join(f"«{t.title}» (id={t.id})" for t in missed)
            notice = (
                "Технический контроль: из вечернего обзора эти задачи остались "
                f"необработанными - {missed_list}. Перечитай ДОСЛОВНО, что "
                "пользователь написал именно про КАЖДУЮ из них конкретно (не "
                "предполагай и не угадывай по общему тону сообщения) - если "
                "явно назвал именно эту задачу выполненной, вызови complete_task; "
                "если явно назвал именно её невыполненной, или сообщение вообще "
                "не касалось именно её - вызови update_task, сдвинув due_at/"
                "remind_at на следующий день в то же время. Реальная ошибка: "
                "в похожей ситуации модель перепутала направление и отметила "
                "явно невыполненную задачу как выполненную - перепроверь "
                "формулировку по каждой задаче отдельно, а не по инерции."
            )
            final_content = await _retry_with_notice(
                messages, session, user, llm, tool_schemas,
                final_content, notice, called_tools, force_tool_call=True,
            )

    await append_message(session, user, role="user", content=text)
    await append_message(session, user, role="assistant", content=final_content)
    await maybe_summarize(session, user, llm)

    return final_content
