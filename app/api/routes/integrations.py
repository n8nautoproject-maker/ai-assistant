from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.integrations.service import handle_oauth_callback
from app.database.session import async_session_factory

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/google/callback", response_class=HTMLResponse)
async def google_callback(state: str, code: str | None = None, error: str | None = None) -> str:
    # No JWT here - the browser hitting this route is Google's redirect, not
    # an authenticated API call. The state token (created in
    # create_connect_url) is what identifies which user this is for.
    if error or code is None:
        return "<p>Не удалось подключить Google Calendar. Можно закрыть окно и попробовать ещё раз в боте.</p>"

    async with async_session_factory() as session:  # type: AsyncSession
        user_id = await handle_oauth_callback(session, state=state, code=code)

    if user_id is None:
        return "<p>Ссылка устарела или уже использована. Запроси новую ссылку в боте.</p>"
    return "<p>Google Calendar подключён. Можно закрыть это окно и вернуться в Telegram.</p>"
