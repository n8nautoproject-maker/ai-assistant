import type { Task } from './api';

const STATUS_MARK: Record<Task['status'], string> = {
  pending: '○',
  in_progress: '◐',
  done: '✓',
  cancelled: '✕',
};

function moment(t: Task): Date | null {
  const raw = t.remind_at ?? t.due_at;
  return raw ? new Date(raw) : null;
}

export function TaskRow({
  task,
  onComplete,
  onPostpone,
  onDelete,
  onTogglePriority,
  onOpen,
  showFullDate,
}: {
  task: Task;
  onComplete: (id: string) => void;
  onPostpone: (id: string) => void;
  onDelete: (id: string) => void;
  onTogglePriority?: (id: string, next: boolean) => void;
  onOpen?: (task: Task) => void;
  // "Все"/"Выполненные" mix tasks from many different days with no day
  // header to anchor them (unlike Сегодня/Завтра/the Планер day-list,
  // where the tab/selected-day already says which day it is) - a bare
  // "10:00" there doesn't say which day 10:00 is. Real feedback: "у этих
  // задач непонятна дата".
  showFullDate?: boolean;
}) {
  const m = moment(task);
  const isOpen = task.status === 'pending' || task.status === 'in_progress';
  const isDone = task.status === 'done';

  return (
    <li className={`task-row${isDone ? ' row-done' : ''}`} onClick={onOpen ? () => onOpen(task) : undefined}>
      <span className="task-mark">{STATUS_MARK[task.status]}</span>
      <div className="task-row-body">
        <span className="task-title">{task.title}</span>
        {isDone ? (
          <span className="task-time">
            выполнено{' '}
            {new Date(task.updated_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}
            {', '}
            {new Date(task.updated_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
          </span>
        ) : (
          m && (
            <span className="task-time">
              {showFullDate && `${m.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}, `}
              {m.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
            </span>
          )
        )}
      </div>
      {isOpen && (
        <div className="task-row-actions" onClick={(e) => e.stopPropagation()}>
          {onTogglePriority && (
            <button
              className={`icon-btn${task.is_priority ? ' active' : ''}`}
              title="В приоритете сегодня"
              onClick={() => onTogglePriority(task.id, !task.is_priority)}
            >
              {task.is_priority ? '★' : '☆'}
            </button>
          )}
          <button
            className="icon-btn"
            title={m ? 'Перенести на завтра' : 'У задачи нет даты - нечего переносить'}
            disabled={!m}
            onClick={() => onPostpone(task.id)}
          >
            →
          </button>
          <button className="icon-btn ok" title="Выполнено" onClick={() => onComplete(task.id)}>
            ✓
          </button>
          <button className="icon-btn danger" title="Удалить" onClick={() => onDelete(task.id)}>
            ✕
          </button>
        </div>
      )}
    </li>
  );
}
