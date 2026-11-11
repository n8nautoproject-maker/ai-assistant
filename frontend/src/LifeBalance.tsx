import { useEffect, useState } from 'react';
import {
  createLifeBalanceRating,
  fetchLifeBalance,
  LIFE_BALANCE_DOMAINS,
  type LifeBalanceSnapshot,
} from './api';

const DEFAULT_SCORES: Record<string, number> = Object.fromEntries(
  LIFE_BALANCE_DOMAINS.map((d) => [d.key, 5]),
);

export function LifeBalance() {
  const [history, setHistory] = useState<LifeBalanceSnapshot[] | null>(null);
  const [scores, setScores] = useState<Record<string, number>>(DEFAULT_SCORES);
  const [busy, setBusy] = useState(false);

  function reload() {
    fetchLifeBalance().then(setHistory);
  }

  useEffect(reload, []);

  async function handleSave() {
    if (busy) return;
    setBusy(true);
    try {
      await createLifeBalanceRating(scores);
      reload();
    } finally {
      setBusy(false);
    }
  }

  const latest = history?.[0];
  const previous = history?.[1];

  return (
    <div>
      {latest && (
        <div className="life-balance-current">
          <h3>Текущая оценка</h3>
          {LIFE_BALANCE_DOMAINS.map((d) => {
            const value = latest.ratings[d.key];
            const prevValue = previous?.ratings[d.key];
            const delta = prevValue !== undefined ? value - prevValue : null;
            return (
              <div key={d.key} className="life-balance-row">
                <span className="life-balance-label">{d.label}</span>
                <span className="life-balance-score">
                  {value}/10
                  {delta ? (
                    <span className={delta > 0 ? 'life-balance-up' : 'life-balance-down'}>
                      {' '}
                      ({delta > 0 ? '+' : ''}
                      {delta})
                    </span>
                  ) : null}
                </span>
              </div>
            );
          })}
        </div>
      )}

      <h3>Новая оценка</h3>
      {LIFE_BALANCE_DOMAINS.map((d) => (
        <div key={d.key} className="life-balance-input-row">
          <span className="life-balance-label">{d.label}</span>
          <input
            type="range"
            min={1}
            max={10}
            value={scores[d.key]}
            onChange={(e) => setScores((prev) => ({ ...prev, [d.key]: Number(e.target.value) }))}
          />
          <span className="life-balance-value">{scores[d.key]}</span>
        </div>
      ))}
      <button className="primary-btn" onClick={handleSave} disabled={busy}>
        Сохранить оценку
      </button>
    </div>
  );
}
