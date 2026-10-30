from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import TagOut
from ..services import tags as tags_svc

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(session: Annotated[Session, Depends(get_session)]):
    return tags_svc.list_with_counts(session)
