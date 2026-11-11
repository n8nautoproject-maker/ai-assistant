"""domains.py keyword_pattern regexes, tested against real example
phrasings pulled from the domain fragments' own worked examples and from
incidents this project has already hit once. No DB/LLM needed - this is
exactly the class of bug (a domain's own worked example not matching its
own regex) that's bitten this file at least three times before a human
happened to notice in production.
"""
from app.orchestrator import domains

REAL_PHRASES = {
    "reminders": [
        "напомни мне выпить воды",
        "раз в 2 недели проверяй баланс",
        "каждый день в 9 утра спрашивай как дела",
    ],
    "focus": [
        "как у меня дела с привычками в последнее время?",
        "включи таймер на 25 минут",
        "засеки 25 минут поработать",
    ],
    "namaz": ["включи напоминания о намазе"],
    "life": [
        "добавь цель на месяц выучить испанский",
        "хочу через 5 лет свой дом",
        "хочу проходить колесо баланса раз в 2 недели",
    ],
    "knowledge_crm": [
        "запиши, о чём говорили с Мишей",
        "сохрани это",
        "покажи карточку Иванова",
    ],
    "calendar": ["подключи мой гугл", "добавь в google календарь встречу"],
}


def _by_name():
    return {m.name: m for m in domains.DOMAIN_MODULES}


def test_domain_regex_matches_its_own_worked_examples():
    modules = _by_name()
    for domain_name, phrases in REAL_PHRASES.items():
        module = modules[domain_name]
        assert module.keyword_pattern is not None
        for phrase in phrases:
            assert module.keyword_pattern.search(phrase.lower()), (
                f'domain "{domain_name}" regex did not match {phrase!r}'
            )


def test_life_fragment_worked_example_is_reachable_via_reminders_too():
    # LIFE_FRAGMENT tells the model to offer set_recurring_reminder for a
    # cadence like "раз в 2 недели" - that phrase must ALSO select the
    # "reminders" domain (where the tool is actually registered), not just
    # "life", otherwise the model is told to do something it can't reach
    # this turn unless "life" also carries the tool (see the domains.py
    # comment on why it now does, belt-and-suspenders with this check).
    modules = _by_name()
    text = "хочу проходить колесо баланса раз в 2 недели"
    assert modules["reminders"].keyword_pattern.search(text.lower())
    assert modules["life"].keyword_pattern.search(text.lower())
