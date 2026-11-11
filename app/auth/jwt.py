import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

settings = get_settings()

ALGORITHM = "HS256"


def create_access_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    return uuid.UUID(payload["sub"])
