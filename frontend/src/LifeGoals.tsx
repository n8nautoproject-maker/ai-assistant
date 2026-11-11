import { useEffect, useMemo, useState } from 'react';
import {
  completeLifeGoal,
  createLifeGoal,
  fetchLifeGoals,
  formatHorizonLabel,
  formatHorizonOption,
  formatMonthYear,
  LIFE_GOAL_HORIZONS_MONTHS,
  monthInputToLastDay,
  uncompleteLifeGoal,
  WEEK_HORIZON,
  type LifeGoal,
} from './api';
import { LifeGoalDetail } from './LifeGoalDetail';

// Distinct group keys for goals pinned to a specific calendar slot -
// "month:" for a chosen month (horizon_months null + target_date), "week:"
// for a week-horizon goal (grouped by its exact target_date, since two
// week-goals only share a group when they share the same target week's
// end date - see moveToNextWeek in LifeGoalDetail.tsx, which is the only
// thing that ever changes a week-goal's target_date away from "this week").
// Without this, every week-horizon goal collapsed into one bucket keyed
// only by horizon_months (=== WEEK_HORIZON for all of them) regardless of
// which week it actually targeted, so moving a goal to "next week" left it
// visually stuck under the same "На неделю (this week's dates)" header.
type GroupKey = number | null | `month:${string}` | `week:${string}` | 'carried_over';

// A goal server-side carry_forward_life_goals just rolled onto its next
// period (see app/core/life_goals/service.py) gets pulled into its own
// group regardless of horizon, instead of blending back into a
// fresh-looking bucket - explicit request from a user 2026-08-12: show
// these below everything else, under their own "перенесённые" header.
function groupKeyFor(g: LifeGoal): GroupKey {
  if (g.carried_over) return 'carried_over';
  if (g.horizon_months === WEEK_HORIZON && g.target_date) return `week:${g.target_date}`;
  if (g.horizon_months === null && g.target_date) return `month:${g.target_date.slice(0, 7)}`;
  return g.horizon_months;
}

function groupLabel(key: GroupKey, items: LifeGoal[]): string {
  if (key === 'carried_over') return 'Перенесённые с прошлого месяца';
  if (typeof key === 'string' && key.startsWith('month:')) return formatMonthYear(items[0].target_date!);
  if (typeof key === 'string' && key.startsWith('week:')) return formatHorizonLabel(WEEK_HORIZON, items[0].target_date);
  return formatHorizonLabel(key as number | null, items[0]?.target_date);
}

// "week" renders only horizon=WEEK_HORIZON goals with a simple title-only
// add row (no picker - the horizon is implicit) - used by Tasks.tsx's own
// "На неделю" tab, promoted out of "Жизнь" so weekly goals sit alongside
// Сегодня/Все instead of one level deeper. "life" renders everything else
// and drops "Неделя" from its own picker, since a week goal is now created
// from the dedicated tab instead. Real request, 2026-08-29: "вытащи из
// блока задачи на жизнь недельные задачи и перетащи их в основное меню".
export function LifeGoals({ mode = 'life' }: { mode?: 'life' | 'week' }) {
  const [goals, setGoals] = useState<LifeGoal[] | null>(null);
  const [title, setTitle] = useState('');
  // Defaults to the shortest real horizon, not "1 месяц" - a month default
  // made it look like a month was the smallest period available, when a
  // week is actually shorter and already supported. Real feedback:
  // "кажется что наименьший расчетный период у нас месяц, а по факту
  // неделя". But WEEK_HORIZON only means that in "life" mode's own
  // picker - in "week" mode the horizon is implicit and this state isn't
  // even read (handleAdd hardcodes WEEK_HORIZON directly below), while in
  // "life" mode the picker explicitly EXCLUDES WEEK_HORIZON from its
  // options (see below) - defaulting to it there left the select showing
  // no matching option, and adding a goal without touching the select at
  // all silently created a "На неделю" goal instead of a "life" one,
  // which then just vanished from this very tab after reload().
  const [horizon, setHorizon] = useState<number | null>(mode === 'week' ? WEEK_HORIZON : 1);
  const [specificMonth, setSpecificMonth] = useState('');
  const [busy, setBusy] = useState(false);
  const [openGoal, setOpenGoal] = useState<LifeGoal | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Tracks which goal's done-button is mid-request, so that button (and
  // only that one) can show an immediate, unmistakable visual response to
  // the tap - previously there was zero feedback between click and the
  // list eventually re-rendering, so a slow/failed request looked
  // identical to "the tap did nothing at all".
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set());

  function reload() {
    fetchLifeGoals().then(setGoals);
  }

  useEffect(reload, []);

  const scoped = useMemo(() => {
    if (!goals) return null;
    return goals.filter((g) => (mode === 'week' ? g.horizon_months === WEEK_HORIZON : g.horizon_months !== WEEK_HORIZON));
  }, [goals, mode]);

  const grouped = useMemo(() => {
    if (!scoped) return [];
    const byKey = new Map<GroupKey, LifeGoal[]>();
    for (const g of scoped) {
      const key = groupKeyFor(g);
      const list = byKey.get(key) ?? [];
      list.push(g);
      byKey.set(key, list);
    }
    for (const items of byKey.values()) {
      // Open goals first, done ones trail behind (still visible, just
      // de-emphasized) - matches how the Задачи tab already sorts.
      items.sort((a, b) => Number(a.is_done) - Number(b.is_done));
    }
    // Any group pinned to a real calendar date (week: or month:) sorts by
    // that date, nearest first - this naturally puts "next week" ahead of
    // "September" and behind "this week" with no separate case for week
    // vs. month, and is why a week-goal moved forward lands in the right
    // place instead of needing its own hardcoded rank. Undated relative
    // horizons (1/3/6/... months without a specific pin) sort by length
    // after every dated group; the open-ended lifetime bucket (null)
    // always sorts last.
    const dateOf = (key: GroupKey): string | null =>
      typeof key === 'string' && key !== 'carried_over' ? byKey.get(key)![0].target_date : null;
    const sortedKeys = [...byKey.keys()].sort((a, b) => {
      // "Перенесённые с прошлого месяца" always sorts last, below even
      // the open-ended lifetime bucket - explicit request from Сергея.
      if (a === 'carried_over') return 1;
      if (b === 'carried_over') return -1;
      const dateA = dateOf(a);
      const dateB = dateOf(b);
      if (dateA !== null && dateB !== null) return dateA.localeCompare(dateB);
      if (dateA !== null) return -1;
      if (dateB !== null) return 1;
      if (a === null) return 1;
      if (b === null) return -1;
      return (a as number) - (b as number);
    });
    return sortedKeys.map((key) => ({
      key,
      label: groupLabel(key, byKey.get(key)!),
      items: byKey.get(key)!,
    }));
  }, [scoped]);

  async function handleAdd() {
    const trimmed = title.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      if (mode === 'week') {
        await createLifeGoal(trimmed, WEEK_HORIZON);
      } else {
        const targetDate = specificMonth ? monthInputToLastDay(specificMonth) : undefined;
        await createLifeGoal(trimmed, specificMonth ? null : horizon, undefined, targetDate);
      }
      setTitle('');
      setSpecificMonth('');
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function toggleDone(g: LifeGoal) {
    setActionError(null);
    setTogglingIds((prev) => new Set(prev).add(g.id));
    // Optimistic flip first - the list re-renders with the new state (and
    // CSS strikethrough) immediately, instead of waiting on a round trip
    // plus a full reload() to reflect what the click just did. Reverted
    // in the catch branch if the request actually failed.
    setGoals((prev) => prev && prev.map((x) => (x.id === g.id ? { ...x, is_done: !x.is_done } : x)));
    try {
      await (g.is_done ? uncompleteLifeGoal(g.id) : completeLifeGoal(g.id));
      reload();
    } catch {
      setGoals((prev) => prev && prev.map((x) => (x.id === g.id ? { ...x, is_done: g.is_done } : x)));
      setActionError('Не удалось изменить статус цели. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setTogglingIds((prev) => {
        const next = new Set(prev);
        next.delete(g.id);
        return next;
      });
    }
  }

  return (
    <div className="life-goals">
      <div className="add-row">
        <input
          type="text"
          value={title}
          placeholder={mode === 'week' ? 'Новая задача на неделю…' : 'Новая цель…'}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleAdd();
          }}
        />
        <button className="add-btn" onClick={handleAdd} disabled={!title.trim() || busy}>
          +
        </button>
      </div>

      {/* The horizon/month picker only makes sense in "life" mode - "week"
          mode's horizon is implicit (always WEEK_HORIZON), same reasoning
          as Сегодня/Завтра's add-row never showing a date picker either. */}
      {mode === 'life' && (
        <div className="life-goal-add-row">
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
            {LIFE_GOAL_HORIZONS_MONTHS.filter((m) => m !== WEEK_HORIZON).map((m) => (
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
        </div>
      )}

      {actionError && <p className="voice-error">{actionError}</p>}

      {scoped === null ? (
        <p className="hint">Загрузка…</p>
      ) : grouped.length === 0 ? (
        <p className="empty">{mode === 'week' ? 'На неделю задач нет.' : 'Целей пока нет.'}</p>
      ) : (
        grouped.map(({ key, label, items }) => (
          <div key={key ?? 'none'} className="life-goal-group">
            <h3>{label}</h3>
            <ul className="task-list">
              {items.map((g) => (
                <li key={g.id} className={`task-row${g.is_done ? ' life-goal-done' : ''}`}>
                  <button
                    type="button"
                    className="icon-btn ok life-goal-done-btn"
                    title={g.is_done ? 'Вернуть в активные' : 'Выполнено'}
                    disabled={togglingIds.has(g.id)}
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      toggleDone(g);
                    }}
                  >
                    {togglingIds.has(g.id) ? '…' : '✓'}
                  </button>
                  <div className="task-row-body" onClick={() => setOpenGoal(g)}>
                    <span className="task-title">{g.title}</span>
                    {g.is_done && (
                      <span className="task-time">
                        выполнено {new Date(g.updated_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}

      {openGoal && (
        <LifeGoalDetail goal={openGoal} onClose={() => setOpenGoal(null)} onSaved={reload} />
      )}
    </div>
  );
}
