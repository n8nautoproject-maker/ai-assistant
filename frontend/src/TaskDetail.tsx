import { useState } from 'react';
import { updateTask, type Task } from './api';

function toLocalInputValue(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInputValue(local: string): string | null {
  if (!local) return null;
  return new Date(local).toISOString();
}

export function TaskDetail({
  task,
  onClose,
  onSaved,
}: {
  task: Task;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [note, setNote] = useState(task.description ?? '');
  // Whichever field already carried the task's date keeps being the one we
  // write back to - remind_at (wake-me-at-X) and due_at (deadline) are
  // different concepts elsewhere in the app, so editing here shouldn't
  // silently convert one into the other. A task with neither yet defaults
  // to due_at when a date is first set.
  const dateField: 'remind_at' | 'due_at' = task.remind_at ? 'remind_at' : 'due_at';
  const initialDate = toLocalInputValue(task.remind_at ?? task.due_at);
  const [dateValue, setDateValue] = useState(initialDate);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmedTitle = title.trim();
  const dirty = note !== (task.description ?? '') || dateValue !== initialDate || trimmedTitle !== task.title;

  async function handleSave() {
    if (!trimmedTitle) return;
    setSaving(true);
    setError(null);
    try {
      const patch: { title?: string; description: string; due_at?: string | null; remind_at?: string | null } = {
        description: note,
      };
      if (trimmedTitle !== task.title) {
        patch.title = trimmedTitle;
      }
      if (dateField === 'remind_at') {
        patch.remind_at = fromLocalInputValue(dateValue);
      } else {
        patch.due_at = fromLocalInputValue(dateValue);
      }
      await updateTask(task.id, patch);
      onSaved();
      onClose();
    } catch {
      setError('Не удалось сохранить. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet-card" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-header">
          <input
            className="task-title-input"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button className="icon-btn" title="Закрыть" onClick={onClose}>
            ✕
          </button>
        </div>

        <label className="field-label">Дата и время</label>
        <input
          type="datetime-local"
          value={dateValue}
          onChange={(e) => setDateValue(e.target.value)}
          onClick={(e) => e.currentTarget.showPicker?.()}
        />

        <label className="field-label">Заметка</label>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Например: договор отправлен, ждём подписи"
        />

        {error && <p className="voice-error">{error}</p>}

        <button className="primary-btn" onClick={handleSave} disabled={saving || !dirty || !trimmedTitle}>
          {saving ? 'Сохраняю…' : 'Сохранить'}
        </button>
      </div>
    </div>
  );
}
