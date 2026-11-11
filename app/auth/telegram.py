"""Verification for Telegram Login Widget payloads (web login).

The bot channel does not use this: Telegram's Bot API already guarantees
the authenticity of `update.message.from_id`, so the bot handler identifies
users directly by telegram_id without this check. This module exists for
the future web client, where a user proves identity outside the bot.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InvalidTelegramAuth(Exception):
    pass


def verify_login_widget(data: dict, bot_token: str, max_age_seconds: int = 86400) -> None:
    """Raises InvalidTelegramAuth if the payload does not check out.

    `data` is the payload Telegram's Login Widget redirects back with
    (id, first_name, username, auth_date, hash, ...).
    """
    received_hash = data.get("hash")
    if not received_hash:
        raise InvalidTelegramAuth("missing hash")

    check_fields = {k: v for k, v in data.items() if k != "hash"}
    data_check_string = "\n".join(f"{k}={check_fields[k]}" for k in sorted(check_fields))

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InvalidTelegramAuth("hash mismatch")

    auth_date = int(data.get("auth_date", 0))
    if time.time() - auth_date > max_age_seconds:
        raise InvalidTelegramAuth("auth_date expired")


def verify_webapp_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    """Validates the `initData` string a Telegram Mini App gets from
    `window.Telegram.WebApp.initData` and returns the parsed `user` dict.

    Different signing scheme from the Login Widget above: the secret key is
    derived as HMAC-SHA256(key="WebAppData", msg=bot_token) rather than a
    plain SHA256 of the token - see
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app.
    """
    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InvalidTelegramAuth("missing hash")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InvalidTelegramAuth("hash mismatch")

    auth_date = int(pairs.get("auth_date", 0))
    if time.time() - auth_date > max_age_seconds:
        raise InvalidTelegramAuth("auth_date expired")

    user_raw = pairs.get("user")
    if not user_raw:
        raise InvalidTelegramAuth("missing user")
    try:
        return json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise InvalidTelegramAuth("malformed user field") from exc
