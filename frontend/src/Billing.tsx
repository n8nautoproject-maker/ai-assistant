import { useEffect, useState } from 'react';
import { fetchSubscriptionStatus, startCheckout, type SubscriptionStatus } from './api';

const STATUS_RU: Record<SubscriptionStatus['subscription_status'], string> = {
  trialing: 'пробный период',
  active: 'активна',
  past_due: 'просрочена',
  canceled: 'отменена',
};

export function Billing() {
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSubscriptionStatus()
      .then(setStatus)
      .catch(() => setError('Не удалось загрузить статус подписки. Проверьте соединение и попробуйте ещё раз.'));
  }, []);

  async function handlePay() {
    setBusy(true);
    setError(null);
    try {
      const url = await startCheckout();
      // window.open() after an await isn't reliably recognized as
      // originating from the tap that triggered handlePay - Telegram's
      // in-app browser (where this is opened from in practice) and WebKit
      // both silently block it with no error shown at all. openLink is
      // Telegram's own documented way to open an external URL from a Mini
      // App and doesn't have this problem; window.open is only the
      // fallback for testing this outside Telegram.
      const openLink = window.Telegram?.WebApp?.openLink;
      if (openLink) {
        openLink(url);
      } else {
        window.open(url, '_blank');
      }
    } catch {
      setError('Не получилось создать ссылку на оплату — попробуйте ещё раз.');
    } finally {
      setBusy(false);
    }
  }

  if (status === null) {
    return error ? <p className="voice-error">{error}</p> : <p className="hint">Загрузка…</p>;
  }

  return (
    <div>
      <div className="life-balance-current">
        <div className="life-balance-row">
          <span className="life-balance-label">Статус</span>
          <span className="life-balance-score">{STATUS_RU[status.subscription_status]}</span>
        </div>
        {status.subscription_status === 'trialing' && status.trial_ends_at && (
          <div className="life-balance-row">
            <span className="life-balance-label">Пробный период до</span>
            <span className="life-balance-score">
              {new Date(status.trial_ends_at).toLocaleDateString('ru-RU')}
            </span>
          </div>
        )}
        {status.current_period_end && (
          <div className="life-balance-row">
            <span className="life-balance-label">Действует до</span>
            <span className="life-balance-score">
              {new Date(status.current_period_end).toLocaleDateString('ru-RU')}
            </span>
          </div>
        )}
        <div className="life-balance-row">
          <span className="life-balance-label">Стоимость</span>
          <span className="life-balance-score">{status.price_rub.replace('.00', '')} ₽ / мес</span>
        </div>
      </div>

      {status.subscription_status !== 'active' && (
        <button className="primary-btn" onClick={handlePay} disabled={busy}>
          {busy ? 'Открываем оплату…' : `Оплатить подписку — ${status.price_rub.replace('.00', '')} ₽`}
        </button>
      )}
      {error && <p className="voice-error">{error}</p>}
    </div>
  );
}
