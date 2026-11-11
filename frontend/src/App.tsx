import { useEffect, useRef, useState } from 'react';
import { fetchTasks, isTelegramWebApp, type Task } from './api';
import { Calendar } from './Calendar';
import { Chat } from './Chat';
import { Tasks } from './Tasks';
import { More } from './More';
import { Profile } from './Profile';
import { LoginWidget } from './LoginWidget';
import './App.css';

type Tab = 'chat' | 'tasks' | 'calendar' | 'more' | 'profile';

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; tasks: Task[] };

const TABS: { id: Tab; label: string }[] = [
  { id: 'tasks', label: 'Задачи' },
  { id: 'chat', label: 'Чат' },
  { id: 'calendar', label: 'Планер' },
  { id: 'more', label: 'Ещё' },
  // A sub-tab inside Ещё first, but that row scrolls horizontally with no
  // visible scrollbar and Профиль went unnoticed at the end of 8 entries -
  // promoted to a top-level tab so it's always on-screen, no scrolling.
  { id: 'profile', label: 'Профиль' },
];

function App() {
  // Outside Telegram there's no initData to authenticate with at all - show
  // the Login Widget first and don't even attempt fetchTasks() until that
  // resolves, instead of surfacing api.ts's Telegram-only error message as
  // if it were a generic failure. Inside Telegram this is always true and
  // the app behaves exactly as before.
  const [authed, setAuthed] = useState(isTelegramWebApp());
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [tab, setTab] = useState<Tab>('tasks');

  // Guards against out-of-order responses: a slow initial mount fetch (or a
  // second reload() fired while an earlier one is still in flight - e.g. a
  // quick add right after opening) could otherwise resolve AFTER a newer
  // one and silently overwrite fresh state with stale data - the exact
  // symptom of "task doesn't appear until I switch tabs and back" reported
  // live, since switching tabs and back doesn't refetch, it just re-renders
  // whatever state was last (wrongly) applied.
  const reloadSeq = useRef(0);
  const reload = () => {
    const seq = ++reloadSeq.current;
    fetchTasks()
      .then((tasks) => {
        if (seq === reloadSeq.current) setState({ status: 'ready', tasks });
      })
      .catch((err: Error) => {
        if (seq === reloadSeq.current) setState({ status: 'error', message: err.message });
      });
  };

  useEffect(() => {
    window.Telegram?.WebApp?.ready();
    window.Telegram?.WebApp?.expand();
    // Header/background color for the current theme is already set by
    // applyTheme() in main.tsx before this ever mounts - see theme.ts.
    if (authed) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed]);

  if (!authed) {
    return <LoginWidget onSuccess={() => setAuthed(true)} />;
  }
  if (state.status === 'loading') {
    return <div className="centered">Загрузка…</div>;
  }
  if (state.status === 'error') {
    // reload() also fires from every mutation elsewhere in the app (Tasks/
    // Calendar's onChange), so any transient failure - a 502 during a
    // routine backend deploy, one dropped request - used to leave the
    // whole app stuck on this screen with nothing but the raw error text:
    // no way back to Chat/Профиль, no way to retry, short of closing and
    // reopening the Mini App entirely.
    return (
      <div className="centered error">
        <p>{state.message}</p>
        <button className="secondary-btn" onClick={reload}>
          Повторить
        </button>
      </div>
    );
  }

  return (
    <div className="app">
      <div className="screen">
        {tab === 'chat' && <Chat />}
        {tab === 'tasks' && <Tasks tasks={state.tasks} onChange={reload} />}
        {tab === 'calendar' && <Calendar tasks={state.tasks} onChange={reload} />}
        {tab === 'more' && <More tasks={state.tasks} />}
        {tab === 'profile' && <Profile />}
      </div>
      <nav className="tabbar">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}

export default App;
