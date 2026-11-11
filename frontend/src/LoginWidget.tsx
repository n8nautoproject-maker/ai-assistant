import { useEffect, useRef, useState } from 'react';
import { loginWithWidget } from './api';

const BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME as string | undefined;

// Shown only when isTelegramWebApp() is false (App.tsx) - i.e. the site was
// opened directly in a browser instead of from inside the Telegram Mini App,
// the first concrete step toward a standalone app that isn't Telegram-only.
export function LoginWidget({ onSuccess }: { onSuccess: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!BOT_USERNAME) return;

    window.onTelegramAuth = async (user) => {
      try {
        await loginWithWidget(user);
        onSuccess();
      } catch {
        setError('Не удалось войти через Telegram — попробуйте ещё раз.');
      }
    };

    const container = containerRef.current;
    // React 19 StrictMode runs effects twice in dev (mount/cleanup/remount) -
    // without clearing the slot first, that produced two duplicate widget
    // iframes side by side (caught live while verifying this screen).
    if (container) container.innerHTML = '';

    const script = document.createElement('script');
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.async = true;
    script.setAttribute('data-telegram-login', BOT_USERNAME);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    container?.appendChild(script);

    return () => {
      delete window.onTelegramAuth;
      if (container) container.innerHTML = '';
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="centered login-screen">
      <div>
        <h2>AI-ассистент</h2>
        <p className="hint">Войдите через Telegram, чтобы продолжить.</p>
        {BOT_USERNAME ? (
          <div ref={containerRef} className="login-widget-slot" />
        ) : (
          <p className="voice-error">Вход не настроен (нет VITE_TELEGRAM_BOT_USERNAME).</p>
        )}
        {error && <p className="voice-error">{error}</p>}
      </div>
    </div>
  );
}
