"""_claims_unconfirmed_action - the hallucination detector for agent.py.
Pure function, no DB/LLM needed. Every case here is either a real incident
this project already hit once (see the comments in agent.py itself) or the
exact fix verified in this session's audit - a regression test suite this
detector never had before, despite being rewritten under fire at least
five separate times.
"""
from app.orchestrator.agent import _claims_unconfirmed_action


def test_flags_task_move_confirmation_with_no_backing_call():
    text = "Перенёс «Купить лампочки» на завтра, в 09:00."
    assert _claims_unconfirmed_action(text, frozenset()) is True


def test_does_not_flag_task_move_confirmation_backed_by_real_call():
    text = "Перенёс «Купить лампочки» на завтра, в 09:00."
    assert _claims_unconfirmed_action(text, frozenset({"update_task"})) is False


def test_flags_reminder_set_confirmation_with_no_backing_call():
    text = "Поставил напоминание на 9 утра."
    assert _claims_unconfirmed_action(text, frozenset()) is True


def test_does_not_flag_reminder_set_confirmation_backed_by_real_call():
    text = "Поставил напоминание на 9 утра."
    assert _claims_unconfirmed_action(text, frozenset({"create_task"})) is False


def test_flags_focus_completion_claim_with_no_backing_call():
    text = "Завершил фокус-сессию, можно отдыхать."
    assert _claims_unconfirmed_action(text, frozenset()) is True


def test_does_not_flag_task_completion_backed_by_real_call():
    text = "Задача выполнена."
    assert _claims_unconfirmed_action(text, frozenset({"complete_task"})) is False


def test_google_connect_link_does_not_false_positive_on_send_email():
    # Regression for the bug where "отправил" pointed only at the deleted
    # send_email tool - any unrelated use of that stem (e.g. this one, from
    # connect_google_account's own reply text) used to be flagged
    # unconditionally, since send_email could never appear in called_tools.
    text = "Я уже отправил тебе ссылку для подключения Google: https://..."
    assert _claims_unconfirmed_action(text, frozenset({"connect_google_account"})) is False


def test_namaz_enable_confirmation_is_not_a_false_positive():
    text = "Готово, буду присылать напоминания о времени намаза для Казани."
    assert _claims_unconfirmed_action(text, frozenset({"enable_namaz_reminders"})) is False


def test_negated_claim_is_not_flagged():
    text = "Не могу удалить эту задачу - похожих несколько, уточни какую."
    assert _claims_unconfirmed_action(text, frozenset()) is False


def test_plain_answer_with_no_claim_stem_is_not_flagged():
    text = "Сегодня у тебя три задачи: сходить в магазин, позвонить маме, купить билеты."
    assert _claims_unconfirmed_action(text, frozenset()) is False
