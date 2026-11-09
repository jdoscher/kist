import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Item, ItemTag, Tag
from . import validation


def normalize(name: str) -> str:
    """Whitespace collapsed; used to match names (e.g. for removal)."""
    return " ".join(str(name).split())[: validation.TAG_MAX]


def get_or_create(session: Session, names: list[str], *, validate: bool = True) -> list[Tag]:
    """The Tag rows for `names`, creating missing ones.

    Safe against a concurrent writer creating the same tag: the insert is an
    upsert, so neither side gets a unique-key error. With validate=False
    (rebuild-index restoring old data) names are only cleaned, not refused.
    """
    out: list[Tag] = []
    seen: set[str] = set()
    for raw in names:
        if validate:
            name = validation.tag_name(raw)
        else:
            name = normalize(raw)
            if not name:
                continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        session.execute(
            pg_insert(Tag).values(name=name).on_conflict_do_nothing(index_elements=["name"])
        )
        out.append(session.execute(sa.select(Tag).where(Tag.name == name)).scalar_one())
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


def prune_unused(session: Session) -> list[str]:
    """Delete tags that no item uses, trashed items included (so a restore keeps
    its tags). Returns the deleted names, sorted."""
    used = sa.select(ItemTag.tag_id)
    names = session.execute(
        sa.select(Tag.name).where(Tag.id.not_in(used)).order_by(Tag.name)
    ).scalars().all()
    if names:
        session.execute(sa.delete(Tag).where(Tag.id.not_in(used)))
        session.commit()
    return list(names)
