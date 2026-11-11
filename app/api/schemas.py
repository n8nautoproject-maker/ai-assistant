import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class AdminUserResponse(BaseModel):
    id: uuid.UUID
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    created_at: datetime
    message_count: int
    last_active_at: datetime | None


class TelegramLoginPayload(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    # Telegram includes this in the widget's callback payload whenever the
    # user has a profile photo, and factors it into the hash it computes -
    # dropping it here (Pydantic silently ignores unknown fields by default)
    # made verify_login_widget build a data_check_string missing a field
    # Telegram's own hash accounts for, so every login from a user WITH a
    # profile photo failed with "hash mismatch" while photo-less users
    # logged in fine, masking the bug.
    photo_url: str | None = None
    auth_date: int
    hash: str


class TelegramWebAppAuthRequest(BaseModel):
    init_data: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TaskCreateRequest(BaseModel):
    title: str
    description: str | None = None
    due_at: datetime | None = None
    project_id: uuid.UUID | None = None


class TaskResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: str
    due_at: datetime | None
    remind_at: datetime | None
    project_id: uuid.UUID | None
    created_at: datetime
    # Used by the Mini App calendar to anchor a *closed* undated task (no
    # due_at/remind_at) to the day it was actually completed on, instead of
    # either hiding it or leaving it in an ever-growing undated bucket -
    # updated_at is bumped by complete_task's status write, so it doubles
    # as "completed at" for done/cancelled tasks in practice.
    updated_at: datetime
    is_priority: bool

    model_config = {"from_attributes": True}


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    due_at: datetime | None = None
    remind_at: datetime | None = None
    description: str | None = None


class TaskPriorityRequest(BaseModel):
    task_ids: list[uuid.UUID]


class FocusStartRequest(BaseModel):
    task_id: uuid.UUID | None = None
    planned_duration_min: int | None = None


class FocusCompleteRequest(BaseModel):
    outcome_note: str


class FocusAbandonRequest(BaseModel):
    interruption_reason: str

    model_config = {"from_attributes": True}


class ProjectCreateRequest(BaseModel):
    name: str
    description: str | None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentMessageRequest(BaseModel):
    text: str


class AgentMessageResponse(BaseModel):
    reply: str


class TranscribeResponse(BaseModel):
    text: str


class ConversationMessageResponse(BaseModel):
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LifeGoalCreateRequest(BaseModel):
    title: str
    horizon_months: int | None = None
    description: str | None = None
    # Set only for a "specific month" goal (e.g. September 2026) - the
    # client sends that month's last day. Auto-computed instead when left
    # unset for week/year-scale horizons - see target_date_for_horizon in
    # core/life_goals/service.py.
    target_date: date | None = None


class LifeGoalUpdateRequest(BaseModel):
    title: str | None = None
    horizon_months: int | None = None
    description: str | None = None
    target_date: date | None = None

    # For horizon_months/description/target_date, None is itself
    # meaningful (clears the horizon back to "Задачи на жизнь" / clears
    # the description / drops a manually-picked month back to
    # auto-computed) and must be distinguished from "the client didn't
    # send this field at all". model_fields_set (not the field's value) is
    # what the route layer checks to tell those two cases apart - see
    # routes/life_goals.py.


class LifeGoalResponse(BaseModel):
    id: uuid.UUID
    horizon_months: int | None
    title: str
    description: str | None
    is_done: bool
    target_date: date | None
    carried_over: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecurringReminderRequest(BaseModel):
    text: str
    time: str
    recurrence: str = "daily"


class IntervalReminderRequest(BaseModel):
    text: str
    interval_minutes: int
    window_start: str
    window_end: str


class ReminderResponse(BaseModel):
    id: uuid.UUID
    recurrence: str | None
    next_run_at: datetime
    payload: dict

    model_config = {"from_attributes": True}


class NamazEnableRequest(BaseModel):
    city: str
    country: str = "Russia"


class NamazStatusResponse(BaseModel):
    enabled: bool
    city: str | None = None
    country: str | None = None
    next_prayer_name: str | None = None
    next_prayer_time: datetime | None = None


class LifeBalanceRatingRequest(BaseModel):
    health: int
    career: int
    finance: int
    relationships: int
    personal_growth: int
    recreation: int
    environment: int
    meaning: int


class LifeBalanceSnapshotResponse(BaseModel):
    id: uuid.UUID
    ratings: dict[str, int]
    created_at: datetime

    model_config = {"from_attributes": True}


class NoteCreateRequest(BaseModel):
    title: str
    # Matches MAX_CONTENT_LENGTH in app/core/knowledge/service.py - kept in
    # sync manually since importing the service layer into the schema
    # module would invert the usual dependency direction.
    content: str = Field(min_length=1, max_length=100_000)


class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeSearchResultResponse(BaseModel):
    document_id: uuid.UUID
    content: str

    model_config = {"from_attributes": True}


class ContactCreateRequest(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    company: str | None = None
    notes: str | None = None


class ContactResponse(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    company: str | None
    notes: str | None
    created_at: datetime
    # True only on the POST /crm/contacts response, and only when the name
    # matched an existing contact (create_contact merged into it instead of
    # inserting a new row) - lets the client warn "похоже, такой контакт
    # уже есть" instead of silently discarding any field the existing
    # contact already had a value for. Always False elsewhere (not stored
    # on the Contact model itself, set explicitly by the route).
    merged: bool = False

    model_config = {"from_attributes": True}


class ContactInteractionRequest(BaseModel):
    note: str


class ContactInteractionResponse(BaseModel):
    id: uuid.UUID
    note: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ContactWithHistoryResponse(BaseModel):
    contact: ContactResponse
    interactions: list[ContactInteractionResponse]


class SubscriptionStatusResponse(BaseModel):
    subscription_plan: str
    subscription_status: str
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    price_rub: str


class CheckoutResponse(BaseModel):
    confirmation_url: str


class FocusSessionResponse(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    status: str
    planned_duration_min: int
    started_at: datetime | None
    ended_at: datetime | None
    outcome_note: str | None
    interruption_reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
