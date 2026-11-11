import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.contact import Contact
from app.database.models.contact_interaction import ContactInteraction

INTERACTION_HISTORY_LIMIT = 10


async def find_contact_by_name(
    session: AsyncSession, user_id: uuid.UUID, name: str
) -> Contact | None:
    result = await session.execute(
        select(Contact)
        .where(Contact.user_id == user_id, func.lower(Contact.name) == name.lower())
        # Without an explicit order, which of several same-name contacts
        # .first() returns is whatever physical row order Postgres happens
        # to hand back - not guaranteed stable, and not tied to which one
        # the caller actually meant. Oldest first at least makes it
        # deterministic and matches "the contact that's been here longest
        # is probably the real match" more often than an arbitrary order.
        .order_by(Contact.created_at)
    )
    return result.scalars().first()


async def create_contact(
    session: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    company: str | None = None,
    notes: str | None = None,
) -> tuple[Contact, bool]:
    """Reuses an existing contact with the same name instead of always
    inserting a new row - log_interaction already did this dedup check,
    but create_contact didn't, so re-invoking it for the same name (e.g.
    the model re-calling the tool after the user restates the request)
    created a second, "ghost" Contact row that later name-based lookups
    (find_contact_by_name's .first()) would silently miss depending on
    insertion order, splitting phone/email/notes/history across the two.

    Returns (contact, was_created). was_created=False means an EXISTING
    contact was matched and updated instead of a new one being made - the
    caller should say so, since two different real people can share a
    name: a merge silently keeps whichever field was already filled in and
    drops the new value for that field, which is real, permanent data
    loss for the "actually a different person" case if nobody is told a
    merge happened at all.
    """
    existing = await find_contact_by_name(session, user_id, name)
    if existing is not None:
        existing.phone = existing.phone or phone
        existing.email = existing.email or email
        existing.company = existing.company or company
        existing.notes = existing.notes or notes
        await session.commit()
        await session.refresh(existing)
        return existing, False

    contact = Contact(
        user_id=user_id, name=name, phone=phone, email=email, company=company, notes=notes
    )
    session.add(contact)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race against a concurrent create_contact for the same
        # name (duplicate Telegram update delivery is the realistic
        # trigger) - the unique index on (user_id, lower(name)) is what
        # actually makes this impossible now; this just means the loser
        # merges into the winner instead of crashing with a raw 500.
        await session.rollback()
        winner = await find_contact_by_name(session, user_id, name)
        assert winner is not None  # the row that just violated our own insert must exist
        winner.phone = winner.phone or phone
        winner.email = winner.email or email
        winner.company = winner.company or company
        winner.notes = winner.notes or notes
        await session.commit()
        await session.refresh(winner)
        return winner, False
    await session.refresh(contact)
    return contact, True


async def log_interaction(
    session: AsyncSession,
    user_id: uuid.UUID,
    contact_name: str,
    note: str,
    company: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> ContactInteraction:
    contact = await find_contact_by_name(session, user_id, contact_name)
    if contact is None:
        contact = Contact(
            user_id=user_id, name=contact_name, company=company, phone=phone, email=email
        )
        session.add(contact)
        await session.flush()
    else:
        # Fill in any details the model picked up this time that weren't
        # known before - never overwrite an existing value.
        contact.company = contact.company or company
        contact.phone = contact.phone or phone
        contact.email = contact.email or email

    interaction = ContactInteraction(contact_id=contact.id, user_id=user_id, note=note)
    session.add(interaction)
    await session.commit()
    await session.refresh(interaction)
    return interaction


async def get_contact_history(session: AsyncSession, contact_id: uuid.UUID) -> list[ContactInteraction]:
    result = await session.execute(
        select(ContactInteraction)
        .where(ContactInteraction.contact_id == contact_id)
        .order_by(ContactInteraction.created_at.desc())
        .limit(INTERACTION_HISTORY_LIMIT)
    )
    return list(result.scalars().all())


async def get_contact_with_history(
    session: AsyncSession, user_id: uuid.UUID, name: str
) -> tuple[Contact, list[ContactInteraction]] | None:
    contact = await find_contact_by_name(session, user_id, name)
    if contact is None:
        return None
    return contact, await get_contact_history(session, contact.id)


async def log_interaction_for_contact(
    session: AsyncSession, contact: Contact, note: str
) -> ContactInteraction:
    """Same job as log_interaction below, but for a caller that already
    holds the exact Contact row (by id, ownership already confirmed) -
    skips the name re-lookup entirely, so it can't land on a different
    contact that merely shares a (case-insensitive) name. Used by the REST
    routes in api/routes/crm.py, which resolve by id via _get_owned_contact
    first; log_interaction itself still has to search by name since a name
    is all the LLM tool path ever has to identify the contact by.
    """
    interaction = ContactInteraction(contact_id=contact.id, user_id=contact.user_id, note=note)
    session.add(interaction)
    await session.commit()
    await session.refresh(interaction)
    return interaction


async def list_contacts(session: AsyncSession, user_id: uuid.UUID) -> list[Contact]:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user_id).order_by(Contact.name)
    )
    return list(result.scalars().all())
