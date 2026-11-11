import { useEffect, useState } from 'react';
import { disableNamaz, enableNamaz, fetchNamazStatus, type NamazStatus } from './api';

export function Namaz() {
  const [status, setStatus] = useState<NamazStatus | null>(null);
  const [city, setCity] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    fetchNamazStatus().then(setStatus);
  }

  useEffect(reload, []);

  async function handleEnable() {
    const trimmed = city.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = await enableNamaz(trimmed);
      setStatus(next);
      setCity('');
    } catch {
      setError('Не получилось получить расписание для этого города — попробуй другое написание.');
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable() {
    setBusy(true);
    setError(null);
    try {
      const next = await disableNamaz();
      setStatus(next);
    } catch {
      setError('Не удалось отключить напоминания. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setBusy(false);
    }
  }

  if (status === null) {
    return <p className="hint">Загрузка…</p>;
  }

  return (
    <div>
      {status.enabled ? (
        <>
          <p>
            Включено для города <strong>{status.city}</strong>.
          </p>
          {status.next_prayer_name && status.next_prayer_time && (
            <p className="hint">
              Ближайший намаз: {status.next_prayer_name} в{' '}
              {new Date(status.next_prayer_time).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
            </p>
          )}
          <button className="secondary-btn" onClick={handleDisable} disabled={busy}>
            Отключить
          </button>
          {error && <p className="voice-error">{error}</p>}
        </>
      ) : (
        <>
          <p className="hint">Напоминания о намазе выключены.</p>
          <div className="add-row">
            <input
              type="text"
              value={city}
              placeholder="Город (например, Казань)"
              onChange={(e) => setCity(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleEnable();
              }}
            />
            <button className="add-btn" onClick={handleEnable} disabled={!city.trim() || busy}>
              +
            </button>
          </div>
          {error && <p className="voice-error">{error}</p>}
        </>
      )}
    </div>
  );
}
