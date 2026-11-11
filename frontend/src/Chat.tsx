import { useEffect, useRef, useState } from 'react';
import { fetchChatHistory, sendMessage, type ConversationMessage } from './api';
import { MicButton } from './MicButton';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

function toChatMessage(m: ConversationMessage): ChatMessage {
  return { role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content };
}

export function Chat() {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchChatHistory()
      .then((history) => setMessages(history.map(toChatMessage)))
      .catch(() => setMessages([]));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages, sending]);

  // Accepts an explicit override so a voice message can send itself right
  // after transcription, without waiting for the `text` state update to
  // land first (setText is async) or for a separate tap on ↑.
  async function handleSend(override?: string) {
    const trimmed = (override ?? text).trim();
    if (!trimmed || sending) return;
    setText('');
    setError(null);
    setMessages((prev) => [...(prev ?? []), { role: 'user', content: trimmed }]);
    setSending(true);
    try {
      const reply = await sendMessage(trimmed);
      setMessages((prev) => [...(prev ?? []), { role: 'assistant', content: reply }]);
    } catch {
      setError('Не получилось отправить сообщение — попробуйте ещё раз.');
      // Roll back the optimistic bubble added above - it was never
      // actually sent, so leaving it in place makes a failed message look
      // identical to a real one until the next reload silently drops it.
      // Restoring the text means the user isn't left retyping a message
      // from memory after a network blip.
      setMessages((prev) => (prev ?? []).slice(0, -1));
      setText(trimmed);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat-screen">
      <h2>Чат</h2>

      <div className="chat-messages">
        {messages === null ? (
          <p className="hint">Загрузка…</p>
        ) : messages.length === 0 ? (
          <p className="empty">Напишите ассистенту что-нибудь — например, «какие у меня задачи на сегодня».</p>
        ) : (
          messages.map((m, i) => (
            <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
              {m.content}
            </div>
          ))
        )}
        {sending && <div className="chat-bubble chat-bubble-assistant chat-bubble-pending">…</div>}
        <div ref={bottomRef} />
      </div>

      {error && <p className="voice-error">{error}</p>}

      <div className="add-row chat-input-row">
        <input
          type="text"
          value={text}
          placeholder="Сообщение…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSend();
          }}
          disabled={sending}
        />
        <MicButton
          // Sends immediately on transcription - previously this only
          // filled the text field, requiring a separate tap on ↑ to
          // actually send a dictated message.
          onTranscribed={(transcribed) => {
            const combined = text.trim() ? `${text.trim()} ${transcribed}` : transcribed;
            handleSend(combined);
          }}
          onError={setError}
          disabled={sending}
        />
        <button className="add-btn" onClick={() => handleSend()} disabled={!text.trim() || sending}>
          ↑
        </button>
      </div>
    </div>
  );
}
