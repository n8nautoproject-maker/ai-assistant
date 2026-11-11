import { useState } from 'react';
import { Reminders } from './Reminders';
import { Namaz } from './Namaz';
import { LifeBalance } from './LifeBalance';
import { Knowledge } from './Knowledge';
import { Contacts } from './Contacts';
import { Focus } from './Focus';
import type { Task } from './api';

// Подписка moved to Профиль (its own top-level tab now) - keeping it here
// too would just be the same duplication bug Профиль itself already had.
type MoreSubTab = 'focus' | 'reminders' | 'namaz' | 'balance' | 'knowledge' | 'contacts';

const SUB_TABS: { id: MoreSubTab; label: string }[] = [
  { id: 'focus', label: 'Фокус' },
  { id: 'reminders', label: 'Напоминания' },
  { id: 'namaz', label: 'Намаз' },
  { id: 'balance', label: 'Баланс' },
  { id: 'knowledge', label: 'Заметки' },
  { id: 'contacts', label: 'Контакты' },
];

export function More({ tasks }: { tasks: Task[] }) {
  const [sub, setSub] = useState<MoreSubTab>('focus');

  return (
    <div className="tasks-screen">
      <h2>Ещё</h2>

      <div className="sub-tabs">
        {SUB_TABS.map((t) => (
          <button key={t.id} className={sub === t.id ? 'active' : ''} onClick={() => setSub(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {sub === 'focus' && <Focus tasks={tasks} />}
      {sub === 'reminders' && <Reminders />}
      {sub === 'namaz' && <Namaz />}
      {sub === 'balance' && <LifeBalance />}
      {sub === 'knowledge' && <Knowledge />}
      {sub === 'contacts' && <Contacts />}
    </div>
  );
}
