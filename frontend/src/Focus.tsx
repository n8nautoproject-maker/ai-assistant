import { useEffect, useState } from 'react';
import {
  abandonFocusSession,
  completeFocusSession,
  fetchOpenFocusSession,
  startFocusSession,
  type FocusSession,
  type Task,
} from './api';

export function Focus({ tasks }: { tasks: Task[] }) {
  const [session, setSession] = useState<FocusSession | null | 'loading'>('loading');
  const [busy, setBusy] = useState(false);
  const [taskId, setTaskId] = useState('');
  const [duration, setDuration] = useState(90);
  const [note, setNote] = useState('');

  const openTasks = tasks.filter((t) => t.status === 'pending' || t.status === 'in_progress');

  function reload() {
    fetchOpenFocusSession().then(setSession);
  }

  useEffect(reload, []);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      reload();
      setNote('');
    } finally {
      setBusy(false);
    }
  }

  if (session === 'loading') {
    return <div className="focus-screen">Загрузка…</div>;
  }

  const activeTask = session && tasks.find((t) => t.id === session.task_id);

  return (
    <div className="focus-screen">
      <h2>Фокус</h2>

      {session ? (
        <div className="focus-card">
          <p className="focus-card-title">{activeTask ? activeTask.title : 'Свободная сессия'}</p>
          <p className="focus-card-meta">
            {session.planned_duration_min} мин · начата{' '}
            {session.started_at &&
              new Date(session.started_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
          </p>
          <textarea
            placeholder="Что получилось / что помешало…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="focus-card-actions">
            <button
              className="primary-btn"
              disabled={busy || !note.trim()}
              onClick={() => run(() => completeFocusSession(note.trim()))}
            >
              Завершить
            </button>
            <button
              className="secondary-btn"
              disabled={busy || !note.trim()}
              onClick={() => run(() => abandonFocusSession(note.trim()))}
            >
              Сорвалась
            </button>
          </div>
        </div>
      ) : (
        <div className="focus-card">
          <label className="field-label">Задача (необязательно)</label>
          <select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
            <option value="">Без привязки к задаче</option>
            {openTasks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
          <label className="field-label">Длительность, мин</label>
          <input
            type="number"
            min={5}
            step={5}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
          />
          <button
            className="primary-btn"
            disabled={busy}
            onClick={() => run(() => startFocusSession(taskId || undefined, duration))}
          >
            Начать
          </button>
        </div>
      )}
    </div>
  );
}
