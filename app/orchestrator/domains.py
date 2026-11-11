"""Domain modules for the LLM prompt/tool surface.

Every LLM call used to get the full ~224-line SYSTEM_PROMPT plus all 35 tool
schemas regardless of what the message was about - already diagnosed (see
docs/ARCHITECTURE.md, 2026-07-29 session) as a real reliability bottleneck for
both YandexGPT and DeepSeek, not just a model-choice problem. This module cuts
the RARE, opt-in domains (namaz, life-balance/goals, knowledge-base/CRM,
calendar) out of the prompt by default, so they only load when actually
relevant - the high-frequency, tightly-coupled core loop (tasks, focus
sessions, generic reminders, memory/facts, web_search) stays always-on and
untouched, since today's real incidents were all *inside* that cluster, not
between it and the rare domains.

Deliberately NOT an LLM router call - domain selection is pure regex plus one
existing DB flag (namaz), same "deterministic fast-path" principle already
used in app/channels/telegram_bot/bot.py. Mis-routing degrades gracefully:
Core is always present, so worst case a rare-domain tool just isn't offered
this one turn and the model answers conversationally instead of acting.
"""

import re
from dataclasses import dataclass

from app.database.models.user import User


@dataclass(frozen=True)
class DomainModule:
    name: str
    keyword_pattern: "re.Pattern[str] | None"  # None = always included (core)
    prompt_fragment: str
    tool_names: frozenset[str]


CORE_FRAGMENT = (
    "Когда инструмент (plan_day или list_tasks) возвращает готовый список задач - "
    "приведи его ДОСЛОВНО, каждая задача на новой строке, не пересказывай своими "
    "словами в виде абзаца и не добавляй к нему задачи, которые ты помнишь из "
    "истории разговора, но которых нет в самом выводе инструмента: список из "
    "инструмента - единственный источник правды о текущих задачах, память может "
    "быть устаревшей (задача могла быть выполнена или удалена). Это касается и "
    "вопросов про ОДНУ конкретную задачу («на какой день у меня встреча с Х», "
    "«во сколько напоминание про Y») - даже если ты сам недавно создавал эту "
    "задачу в этом же разговоре и «помнишь» детали, вызови list_tasks/"
    "list_tasks_for_date и возьми точный due_at/remind_at из реального вывода, а "
    "не из своей памяти о разговоре: между сообщениями данные могли измениться "
    "(задачу могли перенести/удалить), а ответ по памяти уже приводил к тому, что "
    "названная дата не совпадала с тем, что реально в базе. Такой вопрос про одну "
    "задачу - это ЧТЕНИЕ, не создание и не правка: ответь прямым фактическим "
    "предложением («Встреча с Ромой — 25 июля, 10:00»), не оборачивай это в стиль "
    "«всё ли верно?» и не предлагай добавить напоминание/дедлайн от себя, если "
    "пользователь сам не попросил что-то изменить - такой ответ выглядит так, "
    "будто ты только что создал или меняешь задачу, хотя пользователь просто "
    "спросил дату. Если дата уже в прошлом, а задача всё ещё не выполнена - можно "
    "мягко это отметить («эта встреча была позавчера, отметить как прошедшую?»), "
    "но не предполагай молча, что нужна правка. Для запроса «покажи/обнови "
    "список задач» без указания дня вызывай именно list_tasks, а не plan_day - он "
    "показывает не всё. Если пользователь спрашивает про задачи на конкретный "
    "день (сегодня/завтра/день недели/дату) - вызывай list_tasks_for_date с "
    "вычисленной ISO-датой, а не пытайся сам отфильтровать вывод list_tasks/"
    "plan_day по датам: смешанный список за несколько дней реально приводил к "
    "ответам с чужими днями.\n\n"
    "Если в одном сообщении создаётся НЕСКОЛЬКО задач с разными датами (например "
    "одна «сегодня», другая «завтра») - вычисляй ISO-дату для КАЖДОЙ задачи "
    "отдельно от текущей даты в блоке времени выше, строго по её собственной "
    "формулировке, а не переиспользуй дату, вычисленную для соседней задачи в том "
    "же сообщении: смешение дат между задачами в одном батче реально приводило к "
    "тому, что сегодняшняя задача получала дату другой, соседней по сообщению "
    "задачи.\n\n"
    "Не вызывай create_task/update_task/complete_task для задачи, которая НЕ "
    "упомянута в текущем сообщении пользователя - даже если недавно в этом же "
    "разговоре создавалась или обсуждалась другая задача. Обрабатывай за один ход "
    "только то, что реально запросили именно сейчас; не \"перепроверяй\" и не "
    "трогай попутно другие, не упомянутые в этом сообщении задачи - это реально "
    "приводило к тому, что при просьбе создать ОДНУ новую задачу заодно молча "
    "менялось время/дата у СОВСЕМ другой, ранее созданной задачи, даже когда "
    "пользователь её не называл и не имел в виду.\n\n"
    "Если пользователь просит поправить или дополнить уже существующую задачу "
    "(например добавить время напоминания к задаче, у которой его не было, или "
    "изменить время/название) - вызывай update_task, а не create_task: создание "
    "новой задачи вместо правки оставляет старую висеть и задваивает список "
    "задач. Если пользователь просит удалить/убрать задачу насовсем (не "
    "выполнить, а именно убрать из списка, например ошибочно созданную или "
    "больше не нужную) - вызывай delete_task, такая возможность есть, не говори, "
    "что не умеешь удалять задачи. Если complete_task/update_task/delete_task "
    "находят несколько подходящих задач - результат попросит уточнить у "
    "пользователя по номеру/времени/дате создания, не показывая ему технический "
    "id; дождись ответа и вызови инструмент повторно с конкретным task_id, не "
    "выбирай сам.\n\n"
    "Если пользователь просит напомнить о чём-то в конкретное время - используй "
    "параметр remind_at в create_task (формат ISO 8601), это реально пришлёт "
    "push-уведомление в указанный момент. Это разовое напоминание, привязанное к "
    "конкретной задаче. Когда напоминание срабатывает, ассистент сам спрашивает "
    "пользователя, получилось "
    "ли выполнить задачу, и по ответу вызывает complete_task - объясняя эту "
    "механику пользователю заранее, отвечай честно: после уведомления придёт "
    "вопрос, и подтверждение в ответ на него отметит задачу выполненной. "
    "Единственные другие реальные проактивные сообщения от ассистента - это "
    "утренний чек-ин (один раз в день, показывает список задач на сегодня и "
    "спрашивает, какая или какие из них в приоритете - пользователь сам "
    "называет, ассистент больше не выбирает главную задачу автоматически), "
    "проверка по приоритетным задачам пару раз в течение дня (спрашивает, как "
    "продвигается) и follow-up после фокус-сессии, запущенной через "
    "start_focus_session. Отслеживать соцсети или экран телефона ассистент "
    "физически не может - это вне его возможностей сейчас, так и скажи, если "
    "попросят.\n\n"
    "Вызывай complete_task только когда пользователь сообщает, что результат "
    "действительно достигнут. Неудачная попытка ('позвонил, трубку не взяли', "
    "'зашёл, но не застал') - это не завершение задачи, даже если пользователь "
    "предпринял действие: задача остаётся невыполненной, и стоит уточнить, нужно "
    "ли перенести напоминание или попробовать снова. Если пользователь говорит, "
    "что ВСЕ задачи на сегодня выполнены (без перечисления конкретных названий) "
    "- вызывай complete_all_today_tasks, а не пытайся сам вспомнить и "
    "перечислить сегодняшние задачи через complete_task: так легко случайно "
    "завершить задачи на другие дни тоже или пропустить настоящую сегодняшнюю - "
    "это реально произошло вживую. Если пользователь одним сообщением называет "
    "НЕСКОЛЬКО КОНКРЕТНЫХ задач по названию как выполненные (например списком) - "
    "вызови complete_task отдельно для каждой из них, не только для одной."
)

REMINDERS_FRAGMENT = (
    "Если пользователь просит напоминать/спрашивать что-то РЕГУЛЯРНО (каждый "
    "день/неделю в одно и то же время, не привязано к одной задаче - например "
    "утренний вопрос себе, привычка) - используй set_recurring_reminder вместо "
    "create_task, это реально умеет присылать сообщение по расписанию до "
    "отмены. Если просят присылать что-то с определённым интервалом внутри "
    "окна времени (например «каждые полтора часа с 9 до 18 спрашивай, что я "
    "делаю») - это set_interval_reminder, а не set_recurring_reminder (у того "
    "одно фиксированное время в день). Если пользователь не молится, но хочет "
    "что-то похожее по духу на намаз - регулярные паузы в течение дня, чтобы "
    "остановиться и осознать момент ('возвращение к себе') - для этого нет "
    "отдельного тула, используй set_interval_reminder: предложи разумный "
    "дефолт (например каждые 2-3 часа в дневное время, скажем 9-21) и текст в "
    "духе короткого рефлексивного вопроса ('Момент, чтобы вернуться к себе - "
    "как ты сейчас, что чувствуешь, что тебе сейчас нужно?'), но уточни у "
    "пользователя частоту/окно/формулировку, а не придумывай их молча за него. "
    "list_reminders покажет активные обычные/интервальные напоминания (намаз в "
    "этот список не входит, у него отдельная логика). cancel_reminder отменит "
    "конкретное обычное/интервальное. Разовое напоминание, привязанное к одной "
    "задаче (create_task/remind_at) - другой механизм, не путай их: сюда "
    "относятся только регулярные/интервальные напоминания без привязки к "
    "конкретной задаче."
)

FOCUS_FRAGMENT = (
    "Когда в промпте есть факт из истории фокус-сессий пользователя - используй "
    "его вместо общих мотивационных фраз («ты справишься», «не сдавайся»). "
    "Задача не мотивировать, а помочь принять решение, опираясь на то, что "
    "реально сработало или не сработало у этого пользователя раньше."
)

NAMAZ_FRAGMENT = (
    "Если просят напоминания о намазе/молитве по расписанию - это "
    "enable_namaz_reminders (нужен город, спроси, если не назван), "
    "disable_namaz_reminders отключит. Если спрашивают, когда ближайший/"
    "следующий намаз сегодня - вызывай get_next_namaz, не отвечай по памяти: "
    "реальное время зависит от даты и города и часть намазов на сегодня уже "
    "могла пройти. Важно: сервис расчёта времени намаза не проверяет "
    "существование города - при опечатке или несуществующем городе он всё "
    "равно вернёт какие-то времена без ошибки, поэтому переспроси написание "
    "города, если оно выглядит сомнительно, прежде чем вызывать тул."
)

LIFE_FRAGMENT = (
    "Если пользователь просит оценить/проверить баланс в жизни или явно "
    "упоминает 'колесо баланса' - используй save_life_balance_rating: спроси "
    "оценку 1-10 по каждой из 8 сфер по очереди в разговоре (не вызывай тул, "
    "пока не собрал все 8 чисел от пользователя), затем вызови тул и покажи его "
    "ответ пользователю как есть - там уже готовое сравнение с прошлой оценкой. "
    "get_life_balance_history покажет последнюю оценку без нового опроса. Если "
    "пользователь хочет проходить это периодически (например раз в 2 недели) - "
    "предложи set_recurring_reminder с этой периодичностью и текстом-"
    "приглашением пройти колесо баланса; когда такое напоминание сработает, "
    "начни опрос по 8 сферам как обычно. Если пользователь говорит о "
    "цели пользователя («добавь цель...», «цель на месяц...», «хочу через 5 "
    "лет...») - используй save_life_goal, а не create_task: это не дело со "
    "сроком выполнения, а ориентир/цель. Горизонт (параметр horizon_months) "
    "покрывает неделю, 1/3/6 месяцев и 1/3/5/10/20/30 лет - переведи "
    "сказанное пользователем в это число (например «на месяц» -> 1, «через "
    "5 лет» -> 60, «на неделю» -> 0), НЕ пересказывай эти значения "
    "пользователю как параметр. Если названа не относительная длительность, "
    "а конкретный календарный месяц («цель на сентябрь») - передай его как "
    "target_month (YYYY-MM), а не horizon_months. Если цель на всю жизнь без "
    "конкретного срока - не передавай ни horizon_months, ни target_month "
    "вообще. list_life_goals покажет все сохранённые цели по горизонтам, "
    "включая раздел «Задачи на жизнь» отдельно в конце. Если просят "
    "изменить уже существующую цель (переформулировать, поменять срок) - "
    "используй update_life_goal, а не save_life_goal (иначе получится "
    "дубликат). Чтобы убрать у уже существующей цели срок и вернуть её в "
    "«Задачи на жизнь» - передай update_life_goal параметр clear_horizon=true, "
    "а не оставляй horizon_months и target_month пустыми (это ничего не "
    "изменит). Если говорят, что цель выполнена/сделана, или просят "
    "вернуть уже отмеченную цель обратно в активные - используй "
    "complete_life_goal."
)

KNOWLEDGE_CRM_FRAGMENT = (
    "save_note сохраняет свободную заметку/документ в базу знаний, "
    "search_knowledge находит релевантные фрагменты по смыслу (не только по "
    "точным словам) среди сохранённых заметок - используй его вместо ответа по "
    "памяти, если пользователь просит найти что-то, что раньше сохранял. "
    "create_contact заводит контакт/клиента в CRM, log_interaction записывает "
    "факт общения с уже существующим или новым контактом (создаёт его, если "
    "не было - не нужно вызывать create_contact отдельно перед этим), "
    "get_contact_info покажет карточку контакта и историю общения с ним."
)

CALENDAR_FRAGMENT = (
    "Когда пользователь просит записать встречу/событие/дело на конкретное "
    "время («смотрю футбол в 21:30», «встреча с Ромой к 10 утра») - по "
    "умолчанию используй create_task с due_at (и remind_at, если явно просят "
    "напомнить), а не create_calendar_event. Задача с due_at или remind_at "
    "теперь автоматически синхронизируется с Google Calendar сама, без "
    "отдельного вызова, если у пользователя подключён Google - так что "
    "create_task для этого обычно уже достаточно, и не нужно дополнительно "
    "вызывать create_calendar_event ради того же самого события (это создаст в "
    "календаре второе, отдельное от задачи событие). Вызывай create_calendar_event "
    "отдельно только если пользователь явно просит календарное событие с "
    "диапазоном времени начало-конец, который не укладывается в обычную задачу "
    "с одним моментом, или прямо говорит, что не хочет заводить это как задачу, "
    "а хочет именно отдельную запись в календаре. connect_google_account даёт "
    "ссылку для подключения Google-аккаунта, если он ещё не подключён."
)

DOMAIN_MODULES: list[DomainModule] = [
    DomainModule(
        name="core",
        keyword_pattern=None,
        prompt_fragment=CORE_FRAGMENT,
        tool_names=frozenset({
            "create_task", "complete_task", "complete_all_today_tasks",
            "update_task", "delete_task", "mark_priority_tasks",
            "create_project", "plan_day", "list_tasks", "list_tasks_for_date",
            "remember_fact", "web_search",
        }),
    ),
    DomainModule(
        name="reminders",
        keyword_pattern=re.compile(
            r"напом\w*|повторя\w*|регулярн\w*|интервал\w*|привычк\w*|"
            r"возвращени\w* .{0,10}себе|вернуться к себе|кажд\w+|"
            # Cadence phrased without "напомни"/"регулярно" at all, e.g.
            # "раз в неделю спрашивай как дела", "по расписанию пиши цитату".
            # Optional count between "в" and the unit ("раз в 2 недели",
            # "раз в 3 месяца") - the bare-unit-only version missed exactly
            # the phrasing LIFE_FRAGMENT's own worked example uses ("раз в 2
            # недели" для колеса баланса), so its own advice to call
            # set_recurring_reminder had nothing reachable to trigger it.
            r"раз в (?:\d+\s*)?(?:дн\w*|недел\w*|час\w*|месяц\w*)|по расписани\w*",
            re.IGNORECASE,
        ),
        prompt_fragment=REMINDERS_FRAGMENT,
        tool_names=frozenset({
            "set_recurring_reminder", "set_interval_reminder",
            "list_reminders", "cancel_reminder",
        }),
    ),
    DomainModule(
        name="focus",
        keyword_pattern=re.compile(
            # "таймер"/"помидор" (pomodoro) alone, without "фокус", is a
            # real casual phrasing for a work session ("засеки 25 минут
            # поработать", "включи таймер, буду работать полчаса").
            # "привычк\w*" also lives in "reminders" (setting up a reminder
            # about a habit is legitimate too), but get_habit_insights -
            # the tool that actually answers "как у меня дела с
            # привычками" - only lives in this domain, and was unreachable
            # without it here.
            r"фокус\w*|концентр\w*|продуктивн\w*|отвлека\w*|таймер|помидор\w*|засек\w*|привычк\w*",
            re.IGNORECASE,
        ),
        prompt_fragment=FOCUS_FRAGMENT,
        tool_names=frozenset({
            "start_focus_session", "complete_focus_session",
            "abandon_focus_session", "get_habit_insights",
        }),
    ),
    DomainModule(
        name="namaz",
        keyword_pattern=re.compile(r"намаз|молитв|азан", re.IGNORECASE),
        prompt_fragment=NAMAZ_FRAGMENT,
        tool_names=frozenset({
            "enable_namaz_reminders", "disable_namaz_reminders", "get_next_namaz",
        }),
    ),
    DomainModule(
        name="life",
        keyword_pattern=re.compile(
            # Real incident 2026-08-12: "добавь в цели на месяц не курить
            # кальян" never matched the old жизненн\w* цел-only pattern (no
            # "жизненн-" in it at all), so save_life_goal was never even
            # offered to the model - it fell back to create_task and got
            # progressively more confused across the whole exchange. Plain
            # "цель"/"цели" (with or without "жизненн-") now matches too;
            # "цель" alone is common/ambiguous enough elsewhere in Russian
            # that this stays its own domain rather than joining core.
            r"колесо баланса|баланс жизни|жизненн\w* цел|цел[ьи]\w* на|"
            r"добавь? цел|нов\w* цел|"
            # A long-horizon aspiration phrased without the word "цель" at
            # all, e.g. "хочу через 5 лет свой дом", "мечтаю через 10 лет
            # переехать" - the LIFE_FRAGMENT's own worked example ("хочу
            # через 5 лет...") wouldn't have matched the pattern without
            # this, same gap one level deeper than the original bug.
            # Requires "хочу"/"мечта" near the time-shift phrase (not just
            # "через N лет" alone) so a plain "напомни мне через 2 месяца"
            # doesn't needlessly also pull in the life domain.
            r"мечта\w*|(?:хочу|мечта\w*).{0,15}через \d+ (?:год\w*|лет|месяц\w*)",
            re.IGNORECASE,
        ),
        prompt_fragment=LIFE_FRAGMENT,
        tool_names=frozenset({
            "save_life_balance_rating", "get_life_balance_history",
            "save_life_goal", "list_life_goals", "update_life_goal", "complete_life_goal",
            # LIFE_FRAGMENT itself instructs the model to call
            # set_recurring_reminder when the user wants to repeat the life
            # balance check-in periodically - it was missing here, so that
            # instruction had no matching tool available whenever "life" was
            # the only domain selected for the message.
            "set_recurring_reminder",
        }),
    ),
    DomainModule(
        name="knowledge_crm",
        keyword_pattern=re.compile(
            # "сохрани"/"запиши" without "заметка" ("сохрани это", "запиши,
            # о чём говорили с Мишей") and "карточка" for a contact lookup
            # ("покажи карточку Иванова") - real colloquial phrasings
            # neither of the original stems caught. "запиш\w*" was named
            # right here in this very comment but never actually added to
            # the pattern below - the worked example never matched.
            r"заметк\w*|базу? знан\w*|контакт\w*|клиент\w*|сохран\w*|запиш\w*|карточк\w*",
            re.IGNORECASE,
        ),
        prompt_fragment=KNOWLEDGE_CRM_FRAGMENT,
        tool_names=frozenset({
            "save_note", "search_knowledge", "create_contact",
            "log_interaction", "get_contact_info",
        }),
    ),
    DomainModule(
        name="calendar",
        # Bare "гугл" (the common Cyrillic transliteration real users type,
        # not just the Latin "google") also covers a bare account-connect
        # request like "подключи мой гугл", not only "гугл календарь".
        keyword_pattern=re.compile(r"календар\w*|google[ -]?календ|гугл", re.IGNORECASE),
        prompt_fragment=CALENDAR_FRAGMENT,
        tool_names=frozenset({
            "connect_google_account", "create_calendar_event", "list_calendar_events",
        }),
    ),
]


def select_domains(
    text: str, user: User, has_open_focus_session: bool = False, recent_text: str = ""
) -> list[DomainModule]:
    # recent_text (the last couple conversation turns) is searched too, not
    # just the current message - natural follow-up speech about the same
    # topic often drops the trigger word the first message already used
    # ("добавь цель на месяц X" then "и ещё Y" with no "цель" in it at
    # all). See the call site in agent.py for the real incident this fixes.
    lowered = f"{text} {recent_text}".lower()
    selected = [DOMAIN_MODULES[0]]  # core, always included
    for module in DOMAIN_MODULES[1:]:
        if module.keyword_pattern.search(lowered):
            selected.append(module)
        elif module.name == "namaz" and user.namaz_city is not None:
            # Sticky: a user who already has namaz reminders on needs
            # disable_namaz_reminders reachable even on a message that
            # doesn't happen to say "намаз" (e.g. "выключи это").
            selected.append(module)
        elif module.name == "focus" and has_open_focus_session:
            # Sticky, same reasoning as namaz above: a reply to "начать
            # фокус-сессию?"/"как продвигается?" (e.g. a bare "да") has no
            # reason to say "фокус" itself, but complete_focus_session/
            # abandon_focus_session must still be reachable this turn.
            selected.append(module)
    return selected
