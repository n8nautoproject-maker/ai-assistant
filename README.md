# AI Assistant

Персональный проактивный ИИ-ассистент: Telegram-бот и Telegram Mini App, которые ведут задачи, напоминания, жизненные цели, заметки и контакты и сами пишут пользователю первыми (утренний план, проверка после фокус-сессии, вечерний обзор).

> **English summary.** A proactive personal AI assistant delivered as a Telegram bot plus a React Mini App. An LLM agent with ~40 function-calling tools (tasks, reminders, focus sessions, life goals, CRM, knowledge base, calendar, web search) sits behind a provider abstraction (Claude / any OpenAI-compatible model). Semantic memory and a knowledge base use a local multilingual embedding model with pgvector. Backend: FastAPI, async SQLAlchemy, PostgreSQL, APScheduler, aiogram 3. The documentation below is in Russian; `docs/ARCHITECTURE.md` is the full decision log.

Живой демо-бот: [@Minem_AI_Assistant_bot](https://t.me/Minem_AI_Assistant_bot)

## Что умеет

- **Диалог с действиями.** Пользователь пишет или наговаривает голосом: «напомни завтра в 10 позвонить поставщику», «перенеси задачу на пятницу», «запиши цель на год». Агент сам вызывает нужный инструмент.
- **Проактивность.** Планировщик раз в минуту поднимает отложенные задания из БД: утренний план от главной задачи, follow-up через N минут после старта фокус-сессии, вечерний обзор, напоминания, проверка просроченных задач и срока жизненных целей.
- **Модули (`app/core/`):** задачи и проекты, фокус-сессии, напоминания, жизненные цели и колесо баланса, память о пользователе, база знаний, CRM (контакты), привычки, расписание намаза (Aladhan), биллинг.
- **Интеграции:** Google Calendar, Drive и Gmail по OAuth (токены шифруются Fernet), веб-поиск, распознавание голоса, оплата подпиской через ЮKassa.
- **Mini App** внутри Telegram (React + TypeScript): задачи, календарь, фокус, цели, напоминания, база знаний, контакты, профиль, встроенная справка.

## Какие ИИ и инструменты применены

| Слой | Что используется | Зачем |
|---|---|---|
| LLM-агент | **Claude** (Anthropic API) и любые OpenAI-совместимые модели (в проде сейчас **DeepSeek `deepseek-chat`**; в журнале решений описано сравнение с YandexGPT, поддерживается и OpenRouter) через абстракцию `app/llm/` | Диалог, выбор и вызов инструментов (function calling), prompt caching у Claude |
| Эмбеддинги | **`paraphrase-multilingual-mpnet-base-v2`** (sentence-transformers, PyTorch CPU), запускается локально | Семантическая память и база знаний, без внешнего API и без ограничений по региону |
| Векторный поиск | **PostgreSQL + pgvector** | Поиск по памяти и знаниям |
| Голос | **Yandex SpeechKit** (+ `ffmpeg` для нарезки длинных голосовых) | Голосовые сообщения и микрофон в Mini App |
| Веб-поиск | **Serper.dev** (Google Search API) | Актуальные ответы агента |
| Бот | **aiogram 3** (long polling) | Основной канал, пишет первым |
| Планировщик | **APScheduler** + таблица `scheduled_jobs` | Проактивные сообщения |
| Backend | **FastAPI**, SQLAlchemy 2 (async), Alembic, PostgreSQL | API Mini App и OAuth-колбэки |
| Frontend | **React 19, TypeScript, Vite** | Telegram Mini App |
| Авторизация | Telegram Login Widget, Telegram WebApp `initData`, JWT | Вход в веб и Mini App |
| Платежи | **ЮKassa** | Подписка |
| Качество | pytest, регрессионный скрипт по реальным багам | См. ниже |
| Разработка | **Claude Code** | Написание и ревью кода |

## Как устроен агент

Основной цикл: `app/orchestrator/agent.py`. Несколько решений, которые выросли из реальных инцидентов (подробно в `docs/ARCHITECTURE.md`):

- **Маршрутизация по доменам без вызова LLM** (`domains.py`): в промпт и список инструментов попадают только релевантные модули (задачи, фокус, напоминания, память и поиск всегда, редкие вроде намаза, календаря, CRM и базы знаний только по теме сообщения). Это сокращает промпт и ошибки выбора инструмента. Выбор делает регулярка и один флаг из БД, а не второй вызов модели.
- **Цикл инструментов с лимитом** (`MAX_TOOL_ITERATIONS = 6`) и детерминированным порядком схем: префикс системного промпта и инструментов не меняется между раундами, поэтому кэш Claude его переиспользует.
- **Защита от ложных подтверждений**: если модель пишет «готово, перенёс задачу», а соответствующего вызова инструмента не было, ответ отклоняется и модель повторяет шаг с предупреждением. Аналогично для общих отписок, преждевременных вопросов-подтверждений и ложных «поиск недоступен».
- **Пакетные голосовые**: длинное голосовое с несколькими задачами разбирается отдельным извлечением (Claude) и превращается в список задач.
- **Проверка релевантности ответа** на уточняющие вопросы (устаревший вопрос не должен «захватывать» новую реплику).

## Структура репозитория

```
app/
  api/            FastAPI-маршруты (auth, tasks, projects, focus, agent, integrations, billing, admin ...)
  channels/       Telegram-бот (aiogram): хендлеры, клавиатуры, отправка
  core/           доменная логика: tasks, focus, reminders, life_goals, memory, knowledge, crm, ...
  orchestrator/   агент, реестр инструментов (~40 схем), маршрутизация по доменам
  llm/            провайдеры (Anthropic, OpenAI-совместимый) за общим интерфейсом
  embeddings/     локальный провайдер эмбеддингов
  integrations/   Google, ЮKassa, SpeechKit, веб-поиск, Aladhan
  scheduler/      APScheduler и обработчики проактивных заданий
  subscriptions/  единая проверка статуса подписки
migrations/       Alembic (29 миграций)
frontend/         React/TypeScript Mini App
scripts/          регрессионный тест LLM и health-check бота
tests/            pytest
docs/             ARCHITECTURE.md: журнал архитектурных решений
legal/            страницы политики и описания (для Telegram и Google OAuth)
```

## Запуск

Нужны Python 3.13, Node 20+, Docker (для Postgres и Redis) либо свой Postgres с расширением `pgvector`, а также `ffmpeg` для голосовых.

```bash
cp .env.example .env           # заполнить TELEGRAM_BOT_TOKEN, ключ LLM и остальное по комментариям
docker compose up -d           # postgres (порт 5434) + redis (порт 6380)

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt     # тянет CPU-сборку torch, ~1 ГБ
alembic upgrade head

python -m uvicorn app.main:app --reload   # HTTP API
python -m app.worker                      # бот + планировщик

cd frontend && npm ci && npm run dev      # Mini App
```

`app.main` и `app.worker` запускаются отдельно. Модель эмбеддингов (~1 ГБ) скачивается при первом старте. Выбор провайдера LLM: `LLM_PROVIDER=anthropic` (Claude) или `openai` с `LLM_BASE_URL` для DeepSeek, OpenRouter и других OpenAI-совместимых API.

## Тесты

```bash
pytest                                   # логика маршрутизации и защитных проверок агента
python scripts/regression_test_llm.py    # каждый кейс повторяет реальный баг из эксплуатации
```

Регрессионный скрипт ходит в настоящую модель (нужен ключ LLM в `.env`), `pytest` работает без внешних сервисов.

## Статус и ограничения

Проект работает у реальных пользователей, но это MVP: нет мобильного клиента, аналитика привычек минимальна. Эмбеддинги считаются на CPU внутри процессов API и воркера, поэтому серверу нужно порядка 2–3 ГБ RAM. Решения и ошибки, из которых они выросли, записаны в `docs/ARCHITECTURE.md`.

## Лицензия

MIT, см. `LICENSE`.
