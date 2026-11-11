import { useMemo, useState } from 'react';
import { TaskDetail } from './TaskDetail';
import { TaskRow } from './TaskRow';
import { completeTask, deleteTask, updateTask, type Task } from './api';

function dateKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function scheduledMoment(t: Task): Date | null {
  const raw = t.remind_at ?? t.due_at;
  return raw ? new Date(raw) : null;
}

function isClosed(t: Task): boolean {
  return t.status === 'done' || t.status === 'cancelled';
}

// The day (and time) a task appears under in the calendar. Scheduled tasks
// use due_at/remind_at as always. A closed task with no schedule has no
// natural day otherwise - anchoring it to updated_at (bumped by
// complete_task's status write, so it doubles as "completed at") means it
// shows up and stays on the day it was actually finished, instead of never
// having a home or piling up forever in the undated bucket.
function taskAnchor(t: Task): Date | null {
  return scheduledMoment(t) ?? (isClosed(t) ? new Date(t.updated_at) : null);
}

function addOneDay(iso: string): string {
  const d = new Date(iso);
  d.setDate(d.getDate() + 1);
  return d.toISOString();
}

const WEEKDAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

export function Calendar({ tasks, onChange }: { tasks: Task[]; onChange: () => void }) {
  const today = new Date();
  const [monthCursor, setMonthCursor] = useState(new Date(today.getFullYear(), today.getMonth(), 1));
  const [selectedKey, setSelectedKey] = useState(dateKey(today));
  const [busy, setBusy] = useState(false);
  const [openTask, setOpenTask] = useState<Task | null>(null);

  // Same "don't silently eat a failed action" reasoning as Tasks.tsx's run().
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      onChange();
    } finally {
      setBusy(false);
    }
  }

  const tasksByDay = useMemo(() => {
    const map = new Map<string, Task[]>();
    const undated: Task[] = [];
    for (const t of tasks) {
      const moment = taskAnchor(t);
      if (!moment) {
        // Only genuinely-open, never-scheduled tasks land here now - a
        // closed task always has an anchor (updated_at) once isClosed.
        undated.push(t);
        continue;
      }
      const key = dateKey(moment);
      const list = map.get(key) ?? [];
      list.push(t);
      map.set(key, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => {
        const ma = taskAnchor(a)!.getTime();
        const mb = taskAnchor(b)!.getTime();
        return ma - mb;
      });
    }
    // Newest-created first - matches the bot's own list_tasks/
    // format_task_list ordering (Task.created_at.desc() in
    // app/core/tasks/service.py), so the same task list doesn't visibly
    // reverse itself between the chat and this calendar view.
    undated.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    return { map, undated };
  }, [tasks]);

  const cells = useMemo(() => {
    const year = monthCursor.getFullYear();
    const month = monthCursor.getMonth();
    const firstOfMonth = new Date(year, month, 1);
    // Monday-first grid: JS getDay() is 0=Sunday, shift so Monday=0.
    const leadingBlanks = (firstOfMonth.getDay() + 6) % 7;
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const result: (Date | null)[] = [];
    for (let i = 0; i < leadingBlanks; i++) result.push(null);
    for (let day = 1; day <= daysInMonth; day++) result.push(new Date(year, month, day));
    return result;
  }, [monthCursor]);

  const selectedTasks = tasksByDay.map.get(selectedKey) ?? [];

  return (
    <div className="calendar">
      <div className="calendar-header">
        <button
          onClick={() => {
            setMonthCursor(new Date(monthCursor.getFullYear(), monthCursor.getMonth() - 1, 1));
            setOpenTask(null);
          }}
        >
          ‹
        </button>
        <span>
          {monthCursor.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' })}
        </span>
        <button
          onClick={() => {
            setMonthCursor(new Date(monthCursor.getFullYear(), monthCursor.getMonth() + 1, 1));
            setOpenTask(null);
          }}
        >
          ›
        </button>
      </div>

      <div className="calendar-grid">
        {WEEKDAY_LABELS.map((w) => (
          <div key={w} className="calendar-weekday">{w}</div>
        ))}
        {cells.map((d, i) => {
          if (!d) return <div key={`blank-${i}`} />;
          const key = dateKey(d);
          const hasTasks = tasksByDay.map.has(key);
          const isToday = key === dateKey(today);
          const isSelected = key === selectedKey;
          return (
            <button
              key={key}
              className={`calendar-day${isToday ? ' today' : ''}${isSelected ? ' selected' : ''}`}
              onClick={() => {
                setSelectedKey(key);
                // Without this, a task-detail sheet opened for a task on
                // one day stayed open (rendered unconditionally below)
                // after selecting a different day entirely.
                setOpenTask(null);
              }}
            >
              {d.getDate()}
              {hasTasks && <span className="calendar-dot" />}
            </button>
          );
        })}
      </div>

      <div className="calendar-day-list">
        <h3>
          {new Date(...(selectedKey.split('-').map(Number) as [number, number, number])).toLocaleDateString('ru-RU', {
            day: 'numeric',
            month: 'long',
            weekday: 'long',
          })}
        </h3>
        {selectedTasks.length === 0 ? (
          <p className="empty">Задач нет.</p>
        ) : (
          <ul className="task-list" aria-busy={busy}>
            {selectedTasks.map((t) => (
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
                onOpen={setOpenTask}
              />
            ))}
          </ul>
        )}
      </div>

      {tasksByDay.undated.length > 0 && (
        <div className="calendar-undated">
          <h3>Без даты</h3>
          <ul className="task-list" aria-busy={busy}>
            {tasksByDay.undated.map((t) => (
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
                onOpen={setOpenTask}
              />
            ))}
          </ul>
        </div>
      )}

      {openTask && (
        <TaskDetail task={openTask} onClose={() => setOpenTask(null)} onSaved={onChange} />
      )}
    </div>
  );
}
