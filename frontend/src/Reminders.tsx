import { useEffect, useState } from 'react';
import { createRecurringReminder, deleteReminder, fetchReminders, type Reminder } from './api';

function describe(r: Reminder): string {
  if (r.recurrence === 'interval') {
    return `${r.payload.text} — каждые ${r.payload.interval_minutes} мин, ${r.payload.window_start}–${r.payload.window_end}`;
  }
  const label = r.recurrence === 'weekly' ? 'каждую неделю' : 'каждый день';
  return `${r.payload.text} — ${label}`;
}

export function Reminders() {
  const [reminders, setReminders] = useState<Reminder[] | null>(null);
  const [text, setText] = useState('');
  const [time, setTime] = useState('09:00');
  const [busy, setBusy] = useState(false);

  function reload() {
    fetchReminders().then(setReminders);
  }

  useEffect(reload, []);

  async function handleAdd() {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      await createRecurringReminder(trimmed, time, 'daily');
      setText('');
      reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="add-row">
        <input
          type="text"
          value={text}
          placeholder="Текст напоминания…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleAdd();
          }}
        />
        <input type="time" value={time} onChange={(e) => setTime(e.target.value)} className="time-input" />
        <button className="add-btn" onClick={handleAdd} disabled={!text.trim() || busy}>
          +
        </button>
      </div>
      <p className="hint">Ежедневное напоминание в указанное время. Интервальные напоминания настраиваются через чат.</p>

      {reminders === null ? (
        <p className="hint">Загрузка…</p>
      ) : reminders.length === 0 ? (
        <p className="empty">Активных напоминаний нет.</p>
      ) : (
        <ul className="task-list">
          {reminders.map((r) => (
            <li key={r.id} className="task-row">
              <div className="task-row-body">
                <span className="task-title">{describe(r)}</span>
              </div>
              <div className="task-row-actions">
                <button
                  className="icon-btn danger"
                  onClick={() => deleteReminder(r.id).then(reload)}
                  title="Отменить"
                >
                  ✕
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
