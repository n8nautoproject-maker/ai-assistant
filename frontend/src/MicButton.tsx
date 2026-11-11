import { useRef, useState } from 'react';
import { transcribeAudio } from './api';

// Press-and-hold like Telegram's voice-message button, not click-to-toggle -
// a plain click used to start recording, requiring a second click to stop,
// which doesn't match how every user already expects a mic button to work
// from using Telegram itself. Pointer events (not touch/mouse separately)
// cover both mobile taps and desktop testing in one handler set.
export function MicButton({
  onTranscribed,
  onError,
  disabled,
}: {
  onTranscribed: (text: string) => void;
  onError: (message: string) => void;
  disabled?: boolean;
}) {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  // getUserMedia can take an arbitrary amount of time to resolve (first-
  // time permission prompt, a slow WebView) - if the user releases the
  // button before it resolves, stop() used to find recorderRef still null
  // and do nothing, then the recording started moments later with no way
  // left to stop it except the NEXT press, which would immediately close
  // it again instead of starting a new one the user actually wanted.
  const releasedBeforeStartRef = useRef(false);

  async function start() {
    if (recording || transcribing || disabled) return;
    releasedBeforeStartRef.current = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (releasedBeforeStartRef.current) {
        // Already released by the time permission/the stream came through -
        // don't start a recording nobody is holding the button for anymore.
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
        setTranscribing(true);
        try {
          const text = await transcribeAudio(blob);
          onTranscribed(text);
        } catch {
          onError('Не удалось распознать голос.');
        } finally {
          setTranscribing(false);
        }
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch {
      onError('Нет доступа к микрофону.');
    }
  }

  function stop() {
    releasedBeforeStartRef.current = true;
    if (!recorderRef.current) return;
    recorderRef.current.stop();
    recorderRef.current = null;
    setRecording(false);
  }

  return (
    <button
      type="button"
      className={`mic-btn${recording ? ' recording' : ''}`}
      disabled={disabled || transcribing}
      title={recording ? 'Отпустите, чтобы отправить' : 'Удерживайте, чтобы записать'}
      onPointerDown={(e) => {
        e.preventDefault();
        start();
      }}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()}
    >
      {transcribing ? (
        '…'
      ) : (
        <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true">
          <path d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-5a3.5 3.5 0 0 0-7 0v5A3.5 3.5 0 0 0 12 15Z" />
          <path d="M18.5 11.5a1 1 0 1 0-2 0 4.5 4.5 0 0 1-9 0 1 1 0 1 0-2 0 6.5 6.5 0 0 0 5.75 6.46V20H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-1.75v-2.04a6.5 6.5 0 0 0 5.75-6.46Z" />
        </svg>
      )}
    </button>
  );
}
