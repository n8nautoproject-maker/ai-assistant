from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import DocumentResponse, KnowledgeSearchResultResponse, NoteCreateRequest
from app.auth.dependencies import require_active_subscription
from app.core.knowledge.service import list_documents, save_document, search
from app.database.models.user import User
from app.database.session import get_db
from app.embeddings.factory import get_embedding_provider

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=list[DocumentResponse])
async def list_notes(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    documents = await list_documents(db, user.id)
    return [DocumentResponse.model_validate(d) for d in documents]


@router.post("", response_model=DocumentResponse)
async def create_note(
    payload: NoteCreateRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    document = await save_document(
        db, get_embedding_provider(), user_id=user.id, title=payload.title, content=payload.content
    )
    return DocumentResponse.model_validate(document)


@router.get("/search", response_model=list[KnowledgeSearchResultResponse])
async def search_notes(
    q: str,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeSearchResultResponse]:
    chunks = await search(db, get_embedding_provider(), user_id=user.id, query=q)
    return [KnowledgeSearchResultResponse.model_validate(c) for c in chunks]
