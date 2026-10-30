import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import Item, ItemTag, Tag


def normalize(name: str) -> str:
    return " ".join(name.strip().split())[:100]


def get_or_create(session: Session, names: list[str]) -> list[Tag]:
    out: list[Tag] = []
    seen: set[str] = set()
    for raw in names:
        name = normalize(raw)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        tag = session.execute(
            sa.select(Tag).where(Tag.name == name)
        ).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name)
            session.add(tag)
            session.flush()
        out.append(tag)
    return out


def list_with_counts(session: Session) -> list[dict]:
    rows = session.execute(
        sa.select(Tag.id, Tag.name, sa.func.count(Item.id).label("count"))
        .outerjoin(ItemTag, ItemTag.tag_id == Tag.id)
        .outerjoin(Item, sa.and_(Item.id == ItemTag.item_id, Item.deleted_at.is_(None)))
        .group_by(Tag.id, Tag.name)
        .order_by(sa.text("count DESC"), Tag.name)
    ).all()
    return [{"id": r.id, "name": r.name, "count": r.count} for r in rows]
