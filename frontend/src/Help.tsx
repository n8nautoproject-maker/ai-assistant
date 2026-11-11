import { useState, type ReactNode } from 'react';

// Real feedback: some users don't realize this is a chat-first assistant
// (they only tap around the tabs and never talk to it), others can't find
// a specific screen (life goals, focus sessions, reminders) because it's
// one level deep inside "Ещё". This is a static reference for both -
// grouped by "как пользоваться" vs "где что искать" rather than a flat
// feature list, since those are the two different questions people
// actually have.
const SECTIONS: { question: string; answer: ReactNode }[] = [
  {
    question: 'Как этим вообще пользоваться?',
    answer: (
      <>
        <p>
          Это ассистент — с ним можно просто разговаривать, как с человеком, во вкладке{' '}
          <strong>Чат</strong>. Напишите или наговорите голосом (удерживайте кнопку микрофона),
          что нужно сделать — он сам поймёт и создаст задачу, поставит напоминание, сохранит
          заметку и т.д.
        </p>
        <p>Например, можно написать:</p>
        <ul>
          <li>«напомни завтра в 9 утра позвонить врачу»</li>
          <li>«добавь задачу купить билеты»</li>
          <li>«поставь цель выучить испанский за полгода»</li>
          <li>«запиши, что договорился встретиться с Аней в четверг»</li>
          <li>«какие у меня задачи на сегодня?»</li>
        </ul>
        <p>
          Всё, что можно сказать ассистенту, можно и сделать руками в соответствующей вкладке —
          чат для этого не обязателен, это просто более быстрый способ.
        </p>
      </>
    ),
  },
  {
    question: 'Где мои задачи?',
    answer: (
      <>
        <p>
          Вкладка <strong>Задачи</strong>, вверху пять разделов:
        </p>
        <ul>
          <li><strong>Сегодня</strong> — что запланировано на сегодня (и уже выполненное сегодня)</li>
          <li><strong>На неделю</strong> — короткие ближайшие задачи-цели на неделю</li>
          <li><strong>Все</strong> — всё остальное, включая задачи без даты</li>
          <li><strong>Жизнь</strong> — долгосрочные цели (на месяц, на год и дальше)</li>
          <li><strong>Выполненные</strong> — история закрытых задач</li>
        </ul>
        <p>
          Задачу можно отметить выполненной (✓), перенести на завтра (→) или удалить (✕) прямо
          в списке; тап по задаче открывает карточку с деталями.
        </p>
      </>
    ),
  },
  {
    question: 'Где посмотреть задачи по дням / в календаре?',
    answer: (
      <p>
        Вкладка <strong>Планер</strong> — календарь на месяц. Точка под числом означает, что на
        этот день что-то запланировано; тап по дню показывает список задач на него.
      </p>
    ),
  },
  {
    question: 'А где фокус-сессии, напоминания, намаз, баланс жизни, заметки, контакты?',
    answer: (
      <p>
        Всё это во вкладке <strong>Ещё</strong>, по под-вкладкам вверху: <strong>Фокус</strong>{' '}
        (таймер на рабочий отрезок), <strong>Напоминания</strong> (повторяющиеся или интервальные,
        не привязанные к конкретной задаче), <strong>Намаз</strong> (время намаза по городу),{' '}
        <strong>Баланс</strong> (колесо баланса жизни по 8 сферам), <strong>Заметки</strong> (база
        знаний с поиском по смыслу) и <strong>Контакты</strong> (мини-CRM: кому и что нужно
        сказать/сделать).
      </p>
    ),
  },
  {
    question: 'Где посмотреть/оплатить подписку?',
    answer: (
      <p>
        Вкладка <strong>Профиль</strong> → раздел «Подписка». Там же можно переключить тёмную/
        светлую тему.
      </p>
    ),
  },
  {
    question: 'Голосовые сообщения не распознаются или зависают',
    answer: (
      <p>
        Кнопка микрофона работает по принципу «нажал и держи, как в Telegram» — отпускать нужно,
        когда договорили. Если запись не начинается, проверьте, что приложению разрешён доступ к
        микрофону. Если распознавание не отвечает дольше минуты — попробуйте ещё раз, обычно это
        временная задержка на стороне сервера.
      </p>
    ),
  },
];

export function Help() {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className="secondary-btn" onClick={() => setOpen(true)}>
        Как пользоваться ассистентом
      </button>
    );
  }

  return (
    <div className="sheet-overlay" onClick={() => setOpen(false)}>
      <div className="sheet-card help-card" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-header">
          <h3>Как пользоваться</h3>
          <button className="icon-btn" title="Закрыть" onClick={() => setOpen(false)}>
            ✕
          </button>
        </div>

        {SECTIONS.map((s) => (
          <details key={s.question} className="help-section">
            <summary>{s.question}</summary>
            <div className="help-answer">{s.answer}</div>
          </details>
        ))}
      </div>
    </div>
  );
}
