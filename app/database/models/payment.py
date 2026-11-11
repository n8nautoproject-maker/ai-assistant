import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class Payment(UUIDPKMixin, TimestampMixin, Base):
    """Audit trail of ЮKassa payments (checkout + recurring charges), not
    the source of truth for subscription state - that's users.subscription_
    status/current_period_end, updated by app/core/billing/service.py once
    a payment is confirmed. Exists so a disputed/failed charge can be traced
    without calling the ЮKassa API again.
    """

    __tablename__ = "payments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    yookassa_payment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(32))
