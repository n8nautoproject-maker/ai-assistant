export interface Task {
  id: string;
  title: string;
  description: string | null;
  status: 'pending' | 'in_progress' | 'done' | 'cancelled';
  due_at: string | null;
  remind_at: string | null;
  project_id: string | null;
  created_at: string;
  updated_at: string;
  is_priority: boolean;
}

export interface ConversationMessage {
  role: string;
  content: string;
  created_at: string;
}

export interface FocusSession {
  id: string;
  task_id: string | null;
  status: 'planned' | 'active' | 'awaiting_followup' | 'completed' | 'abandoned';
  planned_duration_min: number;
  started_at: string | null;
  ended_at: string | null;
  outcome_note: string | null;
  interruption_reason: string | null;
  created_at: string;
}

export interface LifeGoal {
  id: string;
  horizon_months: number | null;
  title: string;
  description: string | null;
  is_done: boolean;
  target_date: string | null;
  carried_over: boolean;
  created_at: string;
  updated_at: string;
}

// Stored in months (not years) so the short horizons (1/3/6 месяцев) are
// exact integers instead of awkward year fractions - see
// app/database/models/life_goal.py for the backend side of this.
// 0 is a sentinel for "На неделю" (not a real month count) - reusing this
// slot avoids a second discriminator field for what's still fundamentally
// "which relative bucket is this goal in".
export const WEEK_HORIZON = 0;
export const LIFE_GOAL_HORIZONS_MONTHS = [WEEK_HORIZON, 1, 3, 6, 12, 36, 60, 120, 240, 360] as const;

function monthWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'месяц';
  if ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) return 'месяца';
  return 'месяцев';
}

function yearWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'год';
  if ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) return 'года';
  return 'лет';
}

// Bare "N единиц" phrase, e.g. for a <select> option ("3 месяца", "5 лет").
export function formatHorizonOption(months: number): string {
  if (months === WEEK_HORIZON) return 'Неделя';
  if (months === 6) return 'Полгода';
  if (months < 12) return `${months} ${monthWord(months)}`;
  const years = months / 12;
  return `${years} ${yearWord(years)}`;
}

// "На N единиц" phrase for group headers, or the null-horizon bucket label.
// targetDate (when known) appends the real calendar anchor so "На год"
// reads as "На год (до 31.12.2026)" instead of a vague relative bucket -
// see target_date_for_horizon on the backend for how that date is derived.
export function formatHorizonLabel(months: number | null, targetDate?: string | null): string {
  if (months === null) return 'Задачи на жизнь';
  if (months === WEEK_HORIZON && targetDate) return `На неделю (${formatWeekRange(targetDate)})`;
  const base = `На ${formatHorizonOption(months).toLowerCase()}`;
  if (targetDate && months >= 12) return `${base} (до ${formatRuDate(targetDate)})`;
  return base;
}

function formatRuDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

// "10–16 авг" style range for the calendar week a date (Mon-Sun) falls in.
export function formatWeekRange(iso: string): string {
  const end = new Date(iso);
  const start = new Date(end);
  start.setDate(end.getDate() - 6);
  const sameMonth = start.getMonth() === end.getMonth();
  const startStr = start.toLocaleDateString('ru-RU', { day: 'numeric', month: sameMonth ? undefined : 'short' });
  const endStr = end.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  return `${startStr}–${endStr}`;
}

// "Сентябрь 2026" for a specific-month goal's group header.
export function formatMonthYear(iso: string): string {
  const d = new Date(iso);
  const label = d.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
  return label.charAt(0).toUpperCase() + label.slice(1);
}

// Converts an <input type="month"> value ("2026-09") to that month's last
// day as an ISO date string, which is what the backend's target_date
// column stores.
export function monthInputToLastDay(monthValue: string): string {
  const [y, m] = monthValue.split('-').map(Number);
  const lastDay = new Date(y, m, 0).getDate();
  return `${y}-${String(m).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`;
}

export interface Reminder {
  id: string;
  recurrence: string | null;
  next_run_at: string;
  payload: { text?: string; interval_minutes?: number; window_start?: string; window_end?: string };
}

export interface NamazStatus {
  enabled: boolean;
  city: string | null;
  country: string | null;
  next_prayer_name: string | null;
  next_prayer_time: string | null;
}

export const LIFE_BALANCE_DOMAINS: { key: string; label: string }[] = [
  { key: 'health', label: 'Здоровье' },
  { key: 'career', label: 'Карьера/работа' },
  { key: 'finance', label: 'Финансы' },
  { key: 'relationships', label: 'Отношения/семья' },
  { key: 'personal_growth', label: 'Личностный рост' },
  { key: 'recreation', label: 'Отдых/развлечения' },
  { key: 'environment', label: 'Окружение/дом' },
  { key: 'meaning', label: 'Смысл/духовность' },
];

export interface LifeBalanceSnapshot {
  id: string;
  ratings: Record<string, number>;
  created_at: string;
}

export interface Document {
  id: string;
  title: string;
  created_at: string;
}

export interface KnowledgeSearchResult {
  document_id: string;
  content: string;
}

export interface Contact {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  company: string | null;
  notes: string | null;
  created_at: string;
}

export interface ContactInteraction {
  id: string;
  note: string;
  created_at: string;
}

export interface ContactWithHistory {
  contact: Contact;
  interactions: ContactInteraction[];
}

export interface SubscriptionStatus {
  subscription_plan: string;
  subscription_status: 'trialing' | 'active' | 'past_due' | 'canceled';
  trial_ends_at: string | null;
  current_period_end: string | null;
  price_rub: string;
}

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

// No request in this file had a timeout before - a hung backend (ASR queue
// stuck, LLM call stalled, network stall that doesn't actually drop the
// connection) left the caller's busy/loading state true forever, with no
// way to recover short of reloading the whole app. 45s covers the slowest
// real calls here (voice transcription, chat) without making a genuinely
// stuck request invisible for minutes.
const REQUEST_TIMEOUT_MS = 45_000;

// True when running inside Telegram's own WebView (Mini App) - the only
// place window.Telegram.WebApp.initData is ever populated. False when the
// same build is opened as a plain website, which needs the Login Widget
// path (loginWithWidget below) instead.
export function isTelegramWebApp(): boolean {
  return !!window.Telegram?.WebApp?.initData;
}

async function authenticate(): Promise<string> {
  const initData = window.Telegram?.WebApp?.initData;
  if (!initData) {
    throw new ApiError('Открой это приложение из Telegram-бота, а не напрямую в браузере.');
  }
  const res = await fetch(`${API_BASE}/auth/telegram-webapp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ init_data: initData }),
  });
  if (!res.ok) {
    throw new ApiError('Не удалось подтвердить личность через Telegram.');
  }
  const data = await res.json();
  return data.access_token as string;
}

// Cached across calls within the session - every button tap would otherwise
// re-authenticate before doing the actual action, doubling every request.
let tokenPromise: Promise<string> | null = null;

function getToken(): Promise<string> {
  if (!tokenPromise) {
    tokenPromise = authenticate().catch((err) => {
      tokenPromise = null;
      throw err;
    });
  }
  return tokenPromise;
}

export interface TelegramLoginWidgetUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  auth_date: number;
  hash: string;
}

// Outside Telegram there's no initData to trade for a token - this takes the
// Telegram Login Widget's own callback payload instead (same shape as the
// classic Login Widget everywhere else) and primes the same tokenPromise
// cache getToken()/request() already read from, so every other api.ts
// function works completely unchanged after this resolves.
export async function loginWithWidget(user: TelegramLoginWidgetUser): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/telegram-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(user),
  });
  if (!res.ok) {
    throw new ApiError('Не удалось подтвердить личность через Telegram.');
  }
  const data = await res.json();
  const token = data.access_token as string;
  tokenPromise = Promise.resolve(token);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getToken();
  const isFormData = init?.body instanceof FormData;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(init?.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError('Сервер не отвечает — проверьте соединение и попробуйте ещё раз.');
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
  if (!res.ok) {
    if (res.status === 401) {
      // The cached token was rejected (expired, revoked, or the server's
      // signing secret rotated) - clear it so the NEXT call re-
      // authenticates instead of retrying with the same dead token for
      // the rest of the session, which previously never recovered on its
      // own short of a full page reload.
      tokenPromise = null;
    }
    throw new ApiError(`Запрос не выполнен (${res.status}).`, res.status);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

export function fetchTasks(): Promise<Task[]> {
  return request('/tasks');
}

export function createTask(title: string, dueAt?: string): Promise<Task> {
  return request('/tasks', {
    method: 'POST',
    body: JSON.stringify({ title, due_at: dueAt ?? null }),
  });
}

export function updateTask(
  id: string,
  patch: { title?: string; due_at?: string | null; remind_at?: string | null; description?: string | null },
): Promise<Task> {
  return request(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(patch) });
}

export function completeTask(id: string): Promise<Task> {
  return request(`/tasks/${id}/complete`, { method: 'POST' });
}

export function deleteTask(id: string): Promise<void> {
  return request(`/tasks/${id}`, { method: 'DELETE' });
}

export function setPriorityTasks(taskIds: string[]): Promise<Task[]> {
  return request('/tasks/priority', {
    method: 'POST',
    body: JSON.stringify({ task_ids: taskIds }),
  });
}

export function unsetPriorityTask(id: string): Promise<Task> {
  return request(`/tasks/${id}/priority`, { method: 'DELETE' });
}

export async function transcribeAudio(blob: Blob): Promise<string> {
  const form = new FormData();
  form.append('file', blob, 'voice.webm');
  const { text } = await request<{ text: string }>('/agent/transcribe', {
    method: 'POST',
    body: form,
  });
  return text;
}

export function fetchChatHistory(): Promise<ConversationMessage[]> {
  return request('/agent/messages');
}

export async function sendMessage(text: string): Promise<string> {
  const { reply } = await request<{ reply: string }>('/agent/message', {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
  return reply;
}

export function fetchOpenFocusSession(): Promise<FocusSession | null> {
  return request('/focus/sessions/open');
}

export function startFocusSession(taskId?: string, plannedDurationMin?: number): Promise<FocusSession> {
  return request('/focus/sessions/start', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId ?? null, planned_duration_min: plannedDurationMin ?? null }),
  });
}

export function completeFocusSession(outcomeNote: string): Promise<FocusSession> {
  return request('/focus/sessions/complete', {
    method: 'POST',
    body: JSON.stringify({ outcome_note: outcomeNote }),
  });
}

export function abandonFocusSession(interruptionReason: string): Promise<FocusSession> {
  return request('/focus/sessions/abandon', {
    method: 'POST',
    body: JSON.stringify({ interruption_reason: interruptionReason }),
  });
}

export function fetchLifeGoals(): Promise<LifeGoal[]> {
  return request('/life-goals');
}

export function createLifeGoal(
  title: string,
  horizonMonths: number | null,
  description?: string,
  targetDate?: string | null,
): Promise<LifeGoal> {
  return request('/life-goals', {
    method: 'POST',
    body: JSON.stringify({
      title,
      horizon_months: horizonMonths,
      description: description ?? null,
      target_date: targetDate ?? null,
    }),
  });
}

export function updateLifeGoal(
  id: string,
  patch: { title?: string; horizon_months?: number | null; description?: string | null; target_date?: string | null },
): Promise<LifeGoal> {
  return request(`/life-goals/${id}`, { method: 'PATCH', body: JSON.stringify(patch) });
}

export function completeLifeGoal(id: string): Promise<LifeGoal> {
  return request(`/life-goals/${id}/complete`, { method: 'POST' });
}

export function uncompleteLifeGoal(id: string): Promise<LifeGoal> {
  return request(`/life-goals/${id}/uncomplete`, { method: 'POST' });
}

export function fetchReminders(): Promise<Reminder[]> {
  return request('/reminders');
}

export function createRecurringReminder(text: string, time: string, recurrence: 'daily' | 'weekly'): Promise<Reminder> {
  return request('/reminders/recurring', {
    method: 'POST',
    body: JSON.stringify({ text, time, recurrence }),
  });
}

export function createIntervalReminder(
  text: string,
  intervalMinutes: number,
  windowStart: string,
  windowEnd: string,
): Promise<Reminder> {
  return request('/reminders/interval', {
    method: 'POST',
    body: JSON.stringify({
      text,
      interval_minutes: intervalMinutes,
      window_start: windowStart,
      window_end: windowEnd,
    }),
  });
}

export function deleteReminder(id: string): Promise<void> {
  return request(`/reminders/${id}`, { method: 'DELETE' });
}

export function fetchNamazStatus(): Promise<NamazStatus> {
  return request('/namaz');
}

export function enableNamaz(city: string, country?: string): Promise<NamazStatus> {
  return request('/namaz/enable', {
    method: 'POST',
    body: JSON.stringify({ city, country: country ?? 'Russia' }),
  });
}

export function disableNamaz(): Promise<NamazStatus> {
  return request('/namaz/disable', { method: 'POST' });
}

export function fetchLifeBalance(): Promise<LifeBalanceSnapshot[]> {
  return request('/life-balance');
}

export function createLifeBalanceRating(ratings: Record<string, number>): Promise<LifeBalanceSnapshot> {
  return request('/life-balance', { method: 'POST', body: JSON.stringify(ratings) });
}

export function fetchNotes(): Promise<Document[]> {
  return request('/knowledge');
}

export function createNote(title: string, content: string): Promise<Document> {
  return request('/knowledge', { method: 'POST', body: JSON.stringify({ title, content }) });
}

export function searchNotes(query: string): Promise<KnowledgeSearchResult[]> {
  return request(`/knowledge/search?q=${encodeURIComponent(query)}`);
}

export function fetchContacts(): Promise<Contact[]> {
  return request('/crm/contacts');
}

export function createContact(
  name: string,
  fields?: { phone?: string; email?: string; company?: string; notes?: string },
): Promise<Contact> {
  return request('/crm/contacts', { method: 'POST', body: JSON.stringify({ name, ...fields }) });
}

export function fetchContact(id: string): Promise<ContactWithHistory> {
  return request(`/crm/contacts/${id}`);
}

export function logContactInteraction(id: string, note: string): Promise<ContactWithHistory> {
  return request(`/crm/contacts/${id}/interactions`, { method: 'POST', body: JSON.stringify({ note }) });
}

export function fetchSubscriptionStatus(): Promise<SubscriptionStatus> {
  return request('/billing/status');
}

export async function startCheckout(): Promise<string> {
  const { confirmation_url } = await request<{ confirmation_url: string }>('/billing/checkout', {
    method: 'POST',
  });
  return confirmation_url;
}

export interface AdminUser {
  id: string;
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  created_at: string;
  message_count: number;
  last_active_at: string | null;
}

// 403s for every user except the one hardcoded admin (ADMIN_TELEGRAM_ID on
// the backend) - Profile.tsx calls this for every user and just hides the
// section on failure, so there's nothing here that needs a separate
// "am I admin" check of its own.
export function fetchAdminUsers(): Promise<AdminUser[]> {
  return request('/admin/users');
}
