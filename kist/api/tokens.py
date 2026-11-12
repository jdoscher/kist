from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import access
from ..db import get_session
from ..services import tokens as tokens_svc

# BearerTokenMiddleware already refuses non-admin tokens here; the
# dependency repeats the rule next to the routes it protects
router = APIRouter(tags=["tokens"])


def admin_only() -> None:
    access.require(access.Action.TOKENS)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=tokens_svc.NAME_MAX)
    scopes: list[str]
    folder_ids: list[str] = []
    expires_at: datetime | None = None


class TokenOut(BaseModel):
    id: str
    name: str
    scopes: list[str]
    folder_ids: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    active: bool


class TokenCreated(TokenOut):
    token: str  # the plaintext: shown this once, never stored


class MeOut(BaseModel):
    name: str
    scopes: list[str]
    folder_ids: list[str]
    expires_at: datetime | None


@router.get("/me", response_model=MeOut)
def me():
    p = access.current() or access.OWNER
    return MeOut(
        name=p.name, scopes=sorted(p.scopes), folder_ids=list(p.folder_ids),
        expires_at=p.expires_at,
    )


@router.get("/tokens", response_model=list[TokenOut], dependencies=[Depends(admin_only)])
def list_tokens(session: Annotated[Session, Depends(get_session)]):
    return [tokens_svc.token_out(t) for t in tokens_svc.list_tokens(session)]


@router.post(
    "/tokens", response_model=TokenCreated, status_code=201, dependencies=[Depends(admin_only)]
)
def create_token(body: TokenCreate, session: Annotated[Session, Depends(get_session)]):
    token, plaintext = tokens_svc.create(
        session, body.name, body.scopes, body.folder_ids, body.expires_at
    )
    return {**tokens_svc.token_out(token), "token": plaintext}


@router.delete("/tokens/{token_id}", response_model=TokenOut, dependencies=[Depends(admin_only)])
def revoke_token(token_id: str, session: Annotated[Session, Depends(get_session)]):
    return tokens_svc.token_out(tokens_svc.revoke(session, token_id))


@router.delete(
    "/tokens/{token_id}/entry", status_code=204, dependencies=[Depends(admin_only)]
)
def remove_token_entry(token_id: str, session: Annotated[Session, Depends(get_session)]):
    """Remove a revoked or expired token from the list (409 while it still works)."""
    tokens_svc.remove(session, token_id)
