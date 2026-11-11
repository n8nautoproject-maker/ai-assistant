import { useEffect, useState } from 'react';
import {
  createContact,
  fetchContact,
  fetchContacts,
  logContactInteraction,
  type Contact,
  type ContactWithHistory,
} from './api';

export function Contacts() {
  const [contacts, setContacts] = useState<Contact[] | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<ContactWithHistory | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);

  function reload() {
    fetchContacts().then(setContacts);
  }

  useEffect(reload, []);

  async function handleAdd() {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      await createContact(trimmed);
      setName('');
      reload();
    } finally {
      setBusy(false);
    }
  }

  // note is reset here (not just on a successful log) - without it, an
  // unsaved draft typed for one contact reappeared, pre-filled, in the
  // next contact's own note field, and could get logged onto the wrong
  // contact's history entirely if the tap on "+" landed before noticing.
  async function openContact(id: string) {
    setError(null);
    setNote('');
    try {
      setSelected(await fetchContact(id));
    } catch {
      setError('Не удалось открыть контакт. Проверьте соединение и попробуйте ещё раз.');
    }
  }

  async function handleLogInteraction() {
    if (!selected || !note.trim()) return;
    setError(null);
    try {
      const updated = await logContactInteraction(selected.contact.id, note.trim());
      setSelected(updated);
      setNote('');
    } catch {
      setError('Не удалось сохранить запись. Проверьте соединение и попробуйте ещё раз.');
    }
  }

  if (selected) {
    return (
      <div>
        <button
          className="secondary-btn"
          onClick={() => {
            setSelected(null);
            setNote('');
          }}
        >
          ← Назад к списку
        </button>
        <h3>{selected.contact.name}</h3>
        {selected.contact.phone && <p className="hint">Тел: {selected.contact.phone}</p>}
        {selected.contact.company && <p className="hint">Компания: {selected.contact.company}</p>}

        <div className="add-row">
          <input
            type="text"
            value={note}
            placeholder="Записать общение…"
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleLogInteraction();
            }}
          />
          <button className="add-btn" onClick={handleLogInteraction} disabled={!note.trim()}>
            +
          </button>
        </div>
        {error && <p className="voice-error">{error}</p>}

        {selected.interactions.length === 0 ? (
          <p className="empty">Пока нет записей.</p>
        ) : (
          <ul className="task-list">
            {selected.interactions.map((i) => (
              <li key={i.id} className="task-row">
                <div className="task-row-body">
                  <span className="task-title">{i.note}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div>
      <div className="add-row">
        <input
          type="text"
          value={name}
          placeholder="Имя контакта…"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleAdd();
          }}
        />
        <button className="add-btn" onClick={handleAdd} disabled={!name.trim() || busy}>
          +
        </button>
      </div>
      {error && <p className="voice-error">{error}</p>}

      {contacts === null ? (
        <p className="hint">Загрузка…</p>
      ) : contacts.length === 0 ? (
        <p className="empty">Контактов пока нет.</p>
      ) : (
        <ul className="task-list">
          {contacts.map((c) => (
            <li key={c.id} className="task-row contacts-row" onClick={() => openContact(c.id)}>
              <div className="task-row-body">
                <span className="task-title">{c.name}</span>
                {c.company && <span className="task-time">{c.company}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
