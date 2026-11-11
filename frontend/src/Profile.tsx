import { useState } from 'react';
import { Admin } from './Admin';
import { Billing } from './Billing';
import { Help } from './Help';
import { applyTheme, getTheme, type Theme } from './theme';

export function Profile() {
  const [theme, setTheme] = useState<Theme>(getTheme());

  function choose(next: Theme) {
    applyTheme(next);
    setTheme(next);
  }

  return (
    <div className="profile-screen">
      <h2>Профиль</h2>

      <div className="life-balance-current">
        <h3>Помощь</h3>
        <Help />
      </div>

      <div className="life-balance-current">
        <h3>Оформление</h3>
        <div className="theme-toggle">
          <button className={theme === 'dark' ? 'active' : ''} onClick={() => choose('dark')}>
            Тёмная
          </button>
          <button className={theme === 'light' ? 'active' : ''} onClick={() => choose('light')}>
            Светлая
          </button>
        </div>
      </div>

      <div className="life-balance-current">
        <h3>Подписка</h3>
        <Billing />
      </div>

      <Admin />
    </div>
  );
}
