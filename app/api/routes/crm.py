import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ContactCreateRequest,
    ContactInteractionRequest,
    ContactResponse,
    ContactWithHistoryResponse,
)
from app.auth.dependencies import require_active_subscription
from app.core.crm.service import (
    create_contact,
    get_contact_history,
    list_contacts,
    log_interaction_for_contact,
)
from app.database.models.contact import Contact
from app.database.models.user import User
from app.database.session import get_db

router = APIRouter(prefix="/crm", tags=["crm"])


async def _get_owned_contact(db: AsyncSession, user_id: uuid.UUID, contact_id: uuid.UUID) -> Contact | None:
    contact = await db.get(Contact, contact_id)
    if contact is None or contact.user_id != user_id:
        return None
    return contact


@router.get("/contacts", response_model=list[ContactResponse])
async def list_crm_contacts(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[ContactResponse]:
    contacts = await list_contacts(db, user.id)
    return [ContactResponse.model_validate(c) for c in contacts]


@router.post("/contacts", response_model=ContactResponse)
async def create_crm_contact(
    payload: ContactCreateRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> ContactResponse:
    contact, was_created = await create_contact(
        db,
        user.id,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        company=payload.company,
        notes=payload.notes,
    )
    return ContactResponse.model_validate(contact).model_copy(update={"merged": not was_created})


@router.get("/contacts/{contact_id}", response_model=ContactWithHistoryResponse)
async def get_crm_contact(
    contact_id: uuid.UUID,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> ContactWithHistoryResponse:
    contact = await _get_owned_contact(db, user.id, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Контакт не найден")
    # By id directly, not by name - a re-lookup by contact.name here used to
    # be able to resolve to a DIFFERENT contact than the one just confirmed
    # owned above whenever two contacts share a (case-insensitive) name,
    # since find_contact_by_name's .first() has no way to know which one
    # this specific request meant.
    interactions = await get_contact_history(db, contact.id)
    return ContactWithHistoryResponse(
        contact=ContactResponse.model_validate(contact),
        interactions=list(interactions),
    )


@router.post("/contacts/{contact_id}/interactions", response_model=ContactWithHistoryResponse)
async def log_crm_interaction(
    contact_id: uuid.UUID,
    payload: ContactInteractionRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> ContactWithHistoryResponse:
    contact = await _get_owned_contact(db, user.id, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Контакт не найден")
    # Same "operate on the exact confirmed row, not a name re-lookup" fix as
    # get_crm_contact above - a name-based log_interaction here could write
    # the new note onto a DIFFERENT same-named contact than the one this
    # request's contact_id actually points to.
    await log_interaction_for_contact(db, contact, note=payload.note)
    interactions = await get_contact_history(db, contact.id)
    return ContactWithHistoryResponse(
        contact=ContactResponse.model_validate(contact),
        interactions=list(interactions),
    )
