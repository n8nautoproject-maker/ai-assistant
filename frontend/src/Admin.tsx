import { useEffect, useState } from 'react';
import { fetchAdminUsers, type AdminUser } from './api';

// "Last active" is the closest thing to a real activity signal for a user
// who only taps around the Mini App (Tasks/Focus/Life Goals) and never
// sends a chat message - conversation_messages never sees that usage at
// all, message_count alone can't tell you if they're still around. See
// app/auth/dependencies.py (get_current_user) and app/auth/service.py
// (get_or_create_user) for where this timestamp gets touched.
function formatLastActive(iso: string | null): string {
  if (!iso) return '—';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return 'сегодня';
  if (days === 1) return 'вчера';
  if (days < 7) return `${days} дн назад`;
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' });
}

// Rendered unconditionally by Profile.tsx for every user - the backend 403s
// this fetch for everyone except ADMIN_TELEGRAM_ID, and that failure just
// means "render nothing", so this is invisible to non-admin users without
// Profile.tsx needing any "am I admin" check of its own.
export function Admin() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);

  useEffect(() => {
    fetchAdminUsers()
      .then(setUsers)
      .catch(() => setUsers(null));
  }, []);

  if (!users) return null;

  return (
    <div className="life-balance-current">
      <h3>Пользователи ({users.length})</h3>
      <table className="admin-users-table">
        <thead>
          <tr>
            <th>Пользователь</th>
            <th>Регистрация</th>
            <th>Актив.</th>
            <th>Сообщ.</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const name = [u.first_name, u.last_name].filter(Boolean).join(' ');
            return (
              <tr key={u.id}>
                <td className="admin-users-name-cell">
                  {name && <span className="admin-users-name">{name}</span>}
                  {u.username && (
                    <a
                      className="admin-users-username"
                      href={`https://t.me/${u.username}`}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      @{u.username}
                    </a>
                  )}
                  {!name && !u.username && '—'}
                </td>
                <td>{new Date(u.created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })}</td>
                <td>{formatLastActive(u.last_active_at)}</td>
                <td>{u.message_count}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
