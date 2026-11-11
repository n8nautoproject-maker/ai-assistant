import { useEffect, useState } from 'react';
import { createNote, fetchNotes, searchNotes, type Document, type KnowledgeSearchResult } from './api';

export function Knowledge() {
  const [notes, setNotes] = useState<Document[] | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<KnowledgeSearchResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [searching, setSearching] = useState(false);

  function reload() {
    fetchNotes().then(setNotes);
  }

  useEffect(reload, []);

  async function handleAdd() {
    if (!title.trim() || !content.trim() || busy) return;
    setBusy(true);
    try {
      await createNote(title.trim(), content.trim());
      setTitle('');
      setContent('');
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function handleSearch() {
    if (!query.trim() || searching) return;
    setSearching(true);
    try {
      setResults(await searchNotes(query.trim()));
    } finally {
      setSearching(false);
    }
  }

  return (
    <div>
      <div className="add-row">
        <input
          type="text"
          value={query}
          placeholder="Поиск по заметкам…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSearch();
          }}
        />
        <button className="add-btn" onClick={handleSearch} disabled={!query.trim() || searching}>
          🔍
        </button>
      </div>
      {results !== null && (
        <div className="knowledge-search-results">
          {results.length === 0 ? (
            <p className="empty">Ничего не найдено.</p>
          ) : (
            <ul className="task-list">
              {results.map((r, i) => (
                <li key={i} className="task-row">
                  <div className="task-row-body">
                    <span className="task-title">{r.content}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <h3>Новая заметка</h3>
      <input
        type="text"
        value={title}
        placeholder="Название"
        onChange={(e) => setTitle(e.target.value)}
        className="knowledge-title-input"
      />
      <textarea value={content} placeholder="Текст заметки…" onChange={(e) => setContent(e.target.value)} />
      <button className="primary-btn" onClick={handleAdd} disabled={!title.trim() || !content.trim() || busy}>
        Сохранить
      </button>

      <h3>Все заметки</h3>
      {notes === null ? (
        <p className="hint">Загрузка…</p>
      ) : notes.length === 0 ? (
        <p className="empty">Заметок пока нет.</p>
      ) : (
        <ul className="task-list">
          {notes.map((n) => (
            <li key={n.id} className="task-row">
              <div className="task-row-body">
                <span className="task-title">{n.title}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
