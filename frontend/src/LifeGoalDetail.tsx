import { useState } from 'react';
import {
  completeLifeGoal,
  formatHorizonOption,
  LIFE_GOAL_HORIZONS_MONTHS,
  monthInputToLastDay,
  type LifeGoal,
  uncompleteLifeGoal,
  updateLifeGoal,
  WEEK_HORIZON,
} from './api';

function addDaysToIsoDate(iso: string, days: number): string {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function LifeGoalDetail({
  goal,
  onClose,
  onSaved,
}: {
  goal: LifeGoal;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(goal.title);
  const [horizon, setHorizon] = useState<number | null>(goal.horizon_months);
  const initialMonth = goal.horizon_months === null && goal.target_date ? goal.target_date.slice(0, 7) : '';
  const [specificMonth, setSpecificMonth] = useState(initialMonth);
  const [comment, setComment] = useState(goal.description ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmedTitle = title.trim();
  const dirty =
    trimmedTitle !== goal.title ||
    horizon !== goal.horizon_months ||
    specificMonth !== initialMonth ||
    comment !== (goal.description ?? '');

  async function handleSave() {
    if (!trimmedTitle) return;
    setSaving(true);
    setError(null);
    try {
      // horizon_months/target_date used to be sent on EVERY save, even one
      // that only touched the title or comment. The backend distinguishes
      // "field omitted" from "field explicitly sent" (see update_goal's
      // Ellipsis-sentinel docstring) - a present-but-unchanged target_date
      // still counted as "explicit", so update_goal recomputed the
      // calendar anchor from TODAY on every save, silently discarding
      // whatever real deadline the goal actually had (including one
      // carry_forward_life_goals had already rolled forward). Only send
      // these two when the period itself actually changed - undefined
      // here is dropped by JSON.stringify, matching "not provided".
      const periodChanged = horizon !== goal.horizon_months || specificMonth !== initialMonth;
      await updateLifeGoal(goal.id, {
        title: trimmedTitle,
        horizon_months: periodChanged ? (specificMonth ? null : horizon) : undefined,
        description: comment.trim() || null,
        target_date: periodChanged ? (specificMonth ? monthInputToLastDay(specificMonth) : null) : undefined,
      });
      onSaved();
      onClose();
    } catch {
      setError('Не удалось сохранить. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setSaving(false);
    }
  }

  async function toggleDone() {
    setSaving(true);
    setError(null);
    try {
      await (goal.is_done ? uncompleteLifeGoal(goal.id) : completeLifeGoal(goal.id));
      onSaved();
      onClose();
    } catch {
      setError('Не удалось сохранить. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setSaving(false);
    }
  }

  // Immediate action, same shape as toggleDone above - moves a week-horizon
  // goal's target_date exactly 7 days forward and saves right away, rather
  // than requiring "Период" to be re-selected (that select's onChange only
  // offers the fixed bucket types, not a way to pick a specific week).
  async function moveToNextWeek() {
    setSaving(true);
    setError(null);
    try {
      const current = goal.target_date ?? new Date().toISOString().slice(0, 10);
      await updateLifeGoal(goal.id, { target_date: addDaysToIsoDate(current, 7) });
      onSaved();
      onClose();
    } catch {
      setError('Не удалось перенести. Проверьте соединение и попробуйте ещё раз.');
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

        <label className="field-label">Период</label>
        <select
          value={specificMonth ? 'month' : horizon === null ? 'none' : String(horizon)}
          onChange={(e) => {
            if (e.target.value === 'month') {
              setSpecificMonth((prev) => prev || new Date().toISOString().slice(0, 7));
              return;
            }
            setSpecificMonth('');
            setHorizon(e.target.value === 'none' ? null : Number(e.target.value));
          }}
        >
          {LIFE_GOAL_HORIZONS_MONTHS.map((m) => (
            <option key={m} value={m}>
              {formatHorizonOption(m)}
            </option>
          ))}
          <option value="none">На всю жизнь</option>
          <option value="month">Конкретный месяц…</option>
        </select>
        {specificMonth && (
          <input
            type="month"
            value={specificMonth}
            onChange={(e) => setSpecificMonth(e.target.value)}
            onClick={(e) => e.currentTarget.showPicker?.()}
          />
        )}

        <label className="field-label">Комментарий</label>
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Например: почему эта цель важна, план шагов"
        />

        {error && <p className="voice-error">{error}</p>}

        <button className="primary-btn" onClick={handleSave} disabled={saving || !dirty || !trimmedTitle}>
          {saving ? 'Сохраняю…' : 'Сохранить'}
        </button>
        <button className="secondary-btn" onClick={toggleDone} disabled={saving}>
          {goal.is_done ? 'Вернуть в активные' : 'Отметить выполненной'}
        </button>
        {goal.horizon_months === WEEK_HORIZON && !goal.is_done && (
          <button className="secondary-btn" onClick={moveToNextWeek} disabled={saving}>
            Перенести на следующую неделю →
          </button>
        )}
      </div>
    </div>
  );
}
