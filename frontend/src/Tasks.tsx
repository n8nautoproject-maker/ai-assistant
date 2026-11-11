import { useMemo, useState } from 'react';
import { TaskDetail } from './TaskDetail';
import { TaskRow } from './TaskRow';
import { LifeGoals } from './LifeGoals';
import {
  completeTask,
  createTask,
  deleteTask,
  sendMessage,
  setPriorityTasks,
  unsetPriorityTask,
  updateTask,
  type Task,
} from './api';
import { MicButton } from './MicButton';

type SubTab = 'today' | 'tomorrow' | 'week' | 'all' | 'life' | 'done';

// "Завтра" was dropped 2026-08-29 in favor of "На неделю" (weekly life
// goals promoted out of "Жизнь"), then brought back 2026-09-23 alongside
// it - real feedback after a month without it: a quick day-ahead view
// was still wanted, it just needed to sit next to "На неделю", not be
// replaced by it.
const SUB_TABS: { id: SubTab; label: string }[] = [
  { id: 'today', label: 'Сегодня' },
  { id: 'tomorrow', label: 'Завтра' },
  { id: 'week', label: 'На неделю' },
  { id: 'all', label: 'Все' },
  { id: 'life', label: 'Жизнь' },
  { id: 'done', label: 'Выполненные' },
];

function isSameLocalDay(raw: string, offsetDays: number): boolean {
  const d = new Date(raw);
  const target = new Date();
  target.setDate(target.getDate() + offsetDays);
  return (
    d.getFullYear() === target.getFullYear() &&
    d.getMonth() === target.getMonth() &&
    d.getDate() === target.getDate()
  );
}

function dayOffset(t: Task, offsetDays: number): boolean {
  const raw = t.remind_at ?? t.due_at;
  return raw ? isSameLocalDay(raw, offsetDays) : false;
}

// updated_at doubles as "completed at" for a done task (bumped by
// complete_task's own status write - see TaskResponse's docstring in
// app/api/schemas.py), so this is how a task completed today is found
// regardless of what its original due_at/remind_at said.
function doneOn(t: Task, offsetDays: number): boolean {
  return t.status === 'done' && isSameLocalDay(t.updated_at, offsetDays);
}

function addOneDay(iso: string): string {
  const d = new Date(iso);
  d.setDate(d.getDate() + 1);
  return d.toISOString();
}

function toDateInputValue(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function Tasks({ tasks, onChange }: { tasks: Task[]; onChange: () => void }) {
  const [sub, setSub] = useState<SubTab>('today');
  const [busy, setBusy] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [voiceReply, setVoiceReply] = useState<string | null>(null);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [openTask, setOpenTask] = useState<Task | null>(null);
  const [dateOverride, setDateOverride] = useState('');
  const [timeOverride, setTimeOverride] = useState('');

  const visible = useMemo(() => {
    const open = tasks.filter((t) => t.status === 'pending' || t.status === 'in_progress');
    switch (sub) {
      case 'today': {
        // A task completed today stays visible here (struck through, not
        // hidden) instead of disappearing into Выполненные the instant
        // it's done - otherwise "Сегодня" can't answer "what did I
        // actually get done today", only "what's still left".
        const scheduled = open.filter((t) => dayOffset(t, 0));
        const doneToday = tasks.filter((t) => doneOn(t, 0));
        return [...scheduled, ...doneToday].sort((a, b) => {
          const aDone = a.status === 'done';
          const bDone = b.status === 'done';
          if (aDone !== bDone) return aDone ? 1 : -1;
          if (a.is_priority !== b.is_priority) return a.is_priority ? -1 : 1;
          return new Date(a.remind_at ?? a.due_at ?? 0).getTime() - new Date(b.remind_at ?? b.due_at ?? 0).getTime();
        });
      }
      case 'tomorrow':
        return open
          .filter((t) => dayOffset(t, 1))
          .sort((a, b) => new Date(a.remind_at ?? a.due_at ?? 0).getTime() - new Date(b.remind_at ?? b.due_at ?? 0).getTime());
      case 'all':
        // Everything not scheduled for today/tomorrow - undated backlog and
        // anything further out. Newest-created first, like the old plain list.
        // ("Жизнь" used to show this - split out so "Жизнь" could become
        // actual long-term life goals instead, a different domain entirely.)
        return open
          .filter((t) => !dayOffset(t, 0) && !dayOffset(t, 1))
          .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      case 'life':
      case 'week':
        return []; // rendered via <LifeGoals mode=.../> instead, not a Task[] view
      case 'done':
        return tasks
          .filter((t) => t.status === 'done')
          .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    }
  }, [tasks, sub]);

  // Used to have no catch at all - a failed request (flaky mobile network,
  // etc.) threw silently past this function with nothing shown to the
  // user, and handleAdd already blanked the input before the request even
  // resolved, so a failed add looked exactly like "nothing happened, my
  // task just vanished". Returns whether it actually succeeded so callers
  // that clear input state can wait for real success first.
  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      onChange();
      return true;
    } catch {
      setActionError('Не удалось выполнить действие. Проверьте соединение и попробуйте ещё раз.');
      return false;
    } finally {
      setBusy(false);
    }
  }

  // Quick-add used to always create an undated task regardless of which
  // sub-tab you were on - it looked like "Сегодня"/"Завтра" silently
  // swallowed the add (task landed in "Все" instead), when the create
  // itself was actually succeeding the whole time. Default the date to
  // match the active tab; an explicit date picker still overrides either way.
  function computeDueAt(): string | undefined {
    if (dateOverride) {
      return new Date(`${dateOverride}T${timeOverride || '12:00'}`).toISOString();
    }
    if (sub === 'today') {
      return new Date().toISOString();
    }
    if (sub === 'tomorrow') {
      const d = new Date();
      d.setDate(d.getDate() + 1);
      return d.toISOString();
    }
    return undefined;
  }

  // The date input used to stay blank on the "Завтра" tab even though
  // computeDueAt() already defaulted the actual submission to tomorrow -
  // so the field looked unset (дд.мм.гггг) and opening the native picker
  // started on today, not tomorrow, making it look like tomorrow's date
  // had to be picked by hand every time. Pre-filling it here makes the
  // field show the date that's actually about to be used.
  function handleSubClick(id: SubTab) {
    setSub(id);
    if (id === 'tomorrow') {
      const d = new Date();
      d.setDate(d.getDate() + 1);
      setDateOverride(toDateInputValue(d));
      setTimeOverride('');
    } else {
      // "today" deliberately stays unfilled: computeDueAt() falls back to
      // the actual current moment for it, which is more precise than a
      // fixed noon default would be. Every other tab starts from a clean
      // date field.
      setDateOverride('');
      setTimeOverride('');
    }
    // Without this, a task-detail sheet opened from one tab stayed open
    // (rendered unconditionally below, outside anything keyed on `sub`)
    // after switching to a completely different tab - easy to mistake for
    // "why is this the wrong task", or to edit/save a task you no longer
    // have any visual context for.
    setOpenTask(null);
  }

  async function handleAdd() {
    if (busy) return;
    const title = newTitle.trim();
    if (!title) return;
    const dueAt = computeDueAt();
    // Only clear the input once the create actually succeeded - clearing
    // it upfront meant a failed request silently ate the typed title, no
    // error, no way to retry without retyping.
    const ok = await run(() => createTask(title, dueAt));
    if (ok) {
      setNewTitle('');
      setDateOverride('');
      setTimeOverride('');
    }
  }

  // A dictated task goes to the assistant itself, not into the plain
  // title field - only the LLM can actually parse "напомни завтра в 9"
  // into a real due_at/remind_at, the field-fill path had no way to do
  // that and always left the date up to the (often empty) date picker.
  // Previously this also left a task "half added" until a separate tap
  // on + - sendMessage's tool call finishes the creation in one step.
  async function handleVoiceQuickAdd(spokenText: string) {
    setVoiceBusy(true);
    setActionError(null);
    setVoiceReply(null);
    try {
      const reply = await sendMessage(spokenText);
      setVoiceReply(reply);
      onChange();
    } catch {
      setActionError('Не удалось добавить задачу голосом. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setVoiceBusy(false);
    }
  }

  const emptyText: Record<SubTab, string> = {
    today: 'На сегодня задач нет.',
    tomorrow: 'На завтра задач нет.',
    week: '',
    all: 'Здесь пусто.',
    life: '',
    done: 'Пока ничего не выполнено.',
  };

  return (
    <div className="tasks-screen">
      <div className="tasks-sticky-header">
        <h2>Задачи</h2>

        {sub !== 'life' && sub !== 'week' && (
          <div className="add-row">
            <input
              type="text"
              value={newTitle}
              placeholder="Новая задача…"
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleAdd();
              }}
            />
            <MicButton onTranscribed={handleVoiceQuickAdd} onError={setActionError} disabled={voiceBusy} />
            <button className="add-btn" onClick={handleAdd} disabled={!newTitle.trim() || busy}>
              +
            </button>
          </div>
        )}

        {voiceBusy && <p className="hint">Добавляю задачу…</p>}
        {voiceReply && (
          <p className="hint voice-reply" onClick={() => setVoiceReply(null)}>
            {voiceReply}
          </p>
        )}

        {sub !== 'life' && sub !== 'week' && (
          <div className="add-date-row">
            <input
              type="date"
              value={dateOverride}
              onChange={(e) => setDateOverride(e.target.value)}
              onClick={(e) => e.currentTarget.showPicker?.()}
              title="Дата (необязательно) - иначе берётся по текущей вкладке"
            />
            {dateOverride && (
              <input
                type="time"
                value={timeOverride}
                onChange={(e) => setTimeOverride(e.target.value)}
                onClick={(e) => e.currentTarget.showPicker?.()}
                title="Время (по умолчанию 12:00)"
              />
            )}
            {dateOverride && (
              <button
                type="button"
                className="icon-btn"
                title="Убрать дату"
                onClick={() => {
                  setDateOverride('');
                  setTimeOverride('');
                }}
              >
                ✕
              </button>
            )}
          </div>
        )}

        {actionError && <p className="voice-error">{actionError}</p>}

        <div className="sub-tabs">
          {SUB_TABS.map((t) => (
            <button key={t.id} className={sub === t.id ? 'active' : ''} onClick={() => handleSubClick(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {sub === 'today' && visible.length > 0 && !visible.some((t) => t.is_priority) && (
        <p className="hint">Отметь звёздочкой, какая задача сегодня в приоритете.</p>
      )}

      {sub === 'life' || sub === 'week' ? (
        // key={sub} forces a fresh instance on every switch between "Жизнь"
        // and "На неделю" - without it React kept reusing the same
        // LifeGoals instance across both, so an open goal-detail sheet, an
        // unsaved add-row draft, and the horizon picker's selection all
        // leaked from one tab into the other.
        <LifeGoals key={sub} mode={sub} />
      ) : visible.length === 0 ? (
        <p className="empty">{emptyText[sub]}</p>
      ) : (
        <ul className="task-list" aria-busy={busy}>
          {visible.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              onComplete={(id) => run(() => completeTask(id))}
              onDelete={(id) => run(() => deleteTask(id))}
              onPostpone={(id) =>
                run(() =>
                  updateTask(id, {
                    due_at: t.due_at ? addOneDay(t.due_at) : undefined,
                    remind_at: t.remind_at ? addOneDay(t.remind_at) : undefined,
                  }),
                )
              }
              onTogglePriority={
                sub === 'today'
                  ? (id, next) => run(() => (next ? setPriorityTasks([id]) : unsetPriorityTask(id)))
                  : undefined
              }
              onOpen={setOpenTask}
              showFullDate={sub === 'all'}
            />
          ))}
        </ul>
      )}

      {openTask && (
        <TaskDetail task={openTask} onClose={() => setOpenTask(null)} onSaved={onChange} />
      )}
    </div>
  );
}
