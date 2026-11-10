"""Milestone 2 "organise" endpoints: vocabulary, tag cleanup and merge,
duplicates, stacks, suggested collections and people.

    GET    /api/vocabulary                          aliases, implications, namespaces
    POST   /api/vocabulary/aliases                  {alias, tag}: rewrites items (one batch)
    DELETE /api/vocabulary/aliases?alias=
    POST   /api/vocabulary/implies                  {tag, implied}: loops are 422
    DELETE /api/vocabulary/implies?tag=&implied=
    GET    /api/tags/cleanup?unused_since=          suggestions {kind, tags, counts, proposal}
    POST   /api/tags/merge?dry_run=1                {from: [..], into, keep_alias?}
    POST   /api/tags/rename?dry_run=1               {from, to}
    GET    /api/duplicates/groups?kind=             exact | same_image | similar
    POST   /api/duplicates/resolve                  {keep, others, merge}
    GET    /api/stacks/suggestions
    POST   /api/stacks                              {item_ids, cover}
    GET    /api/stacks/{id}
    PATCH  /api/stacks/{id}                         {cover?, add?, remove?}
    DELETE /api/stacks/{id}                         unstack
    GET    /api/collections/suggested?refresh=1
    POST   /api/collections/suggested/{id}/accept   {name, kind?, parent_id?}
    GET    /api/people
    PATCH  /api/people/{id}                         {name?, cover_face_id?}
    POST   /api/people/merge                        {from: [..], into}
    PATCH  /api/faces/{id}                          {person_id}
    GET    /api/faces/{id}/thumb

Scopes follow access.py (reads need read; changes write, agent or admin).
A folder-limited reader reaches only GET /api/people, /api/stacks/{id},
/api/items/{id}/faces and /api/faces/{id}/thumb, each filtered to the items
it can see; the curation screens (duplicates, collections, cleanup,
suggestions) are 404 for it, like /api/tags.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import ItemOut
from ..services import collections as collections_svc
from ..services import duplicates as duplicates_svc
from ..services import faces as faces_svc
from ..services import stacks as stacks_svc
from ..services import tags as tags_svc
from ..services import vocabulary

router = APIRouter(tags=["organise"])


# ---------- vocabulary ----------


class AliasIn(BaseModel):
    alias: str
    tag: str


class ImpliesIn(BaseModel):
    tag: str
    implied: str


@router.get("/vocabulary")
def get_vocabulary(session: Annotated[Session, Depends(get_session)]) -> dict:
    """The preferred terms: aliases (variant -> tag), implied tags and the
    namespaces in use. Agents read this before tagging."""
    return vocabulary.get(session)


@router.post("/vocabulary/aliases", status_code=201)
def add_alias(body: AliasIn, session: Annotated[Session, Depends(get_session)]) -> dict:
    """Make `alias` a variant of `tag`. Items carrying the alias are
    rewritten to the tag now (tag events, one batch: undo it to put them
    back), and new uses are rewritten on the way in."""
    return vocabulary.add_alias(session, body.alias, body.tag)


@router.delete("/vocabulary/aliases")
def remove_alias(alias: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    return {"removed": vocabulary.remove_alias(session, alias)}


@router.post("/vocabulary/implies", status_code=201)
def add_implies(body: ImpliesIn, session: Annotated[Session, Depends(get_session)]) -> dict:
    """`tag` implies `implied`: searching the implied tag finds items with
    `tag`. Nothing is written to items. A loop is refused (422)."""
    return vocabulary.add_implies(session, body.tag, body.implied)


@router.delete("/vocabulary/implies")
def remove_implies(tag: str, implied: str,
                   session: Annotated[Session, Depends(get_session)]) -> dict:
    return {"removed": vocabulary.remove_implies(session, tag, implied)}


# ---------- tag cleanup ----------


class MergeIn(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    into: str
    keep_alias: bool = False  # leave the old names as aliases of `into`


class RenameIn(BaseModel):
    from_: str = Field(alias="from")
    to: str


@router.get("/tags/cleanup")
def tag_cleanup(
    session: Annotated[Session, Depends(get_session)],
    unused_since: datetime | None = None,
) -> list[dict]:
    """Suggestions: one_off, clash (case/spacing), near (misspelling or
    same stem), unused (since `unused_since`, default a year ago) and
    smart_folder (refers to a missing tag or folder)."""
    return tags_svc.cleanup(session, unused_since)


@router.post("/tags/merge")
def merge_tags(body: MergeIn, session: Annotated[Session, Depends(get_session)],
               dry_run: bool = False) -> dict:
    """Move every item from the `from` tags to `into`, as one batch
    (undoable). dry_run=1 only counts the items affected."""
    return tags_svc.merge(session, body.from_, body.into, dry_run=dry_run,
                          keep_alias=body.keep_alias)


@router.post("/tags/rename")
def rename_tag(body: RenameIn, session: Annotated[Session, Depends(get_session)],
               dry_run: bool = False) -> dict:
    """Rename a tag; renaming onto an existing tag merges them."""
    return tags_svc.rename(session, body.from_, body.to, dry_run=dry_run)


# ---------- duplicates ----------


class MergeOptions(BaseModel):
    tags: bool = True
    folders: bool = True
    notes: bool = True
    rating: Literal["max", "keep"] = "max"
    source_url: bool = True


class ResolveIn(BaseModel):
    keep: str
    others: list[str] = Field(min_length=1)
    merge: MergeOptions = MergeOptions()


@router.get("/duplicates/groups")
def duplicate_groups(
    session: Annotated[Session, Depends(get_session)],
    kind: Literal["exact", "same_image", "similar"] = "same_image",
    limit: Annotated[int, Query(ge=1, le=duplicates_svc.MAX_GROUPS)] = 100,
) -> list[dict]:
    """Groups of duplicates with a suggested keeper (largest, then oldest,
    then with a source URL). not_duplicate links keep a pair apart."""
    return duplicates_svc.groups(session, kind, limit)


@router.post("/duplicates/resolve")
def resolve_duplicates(body: ResolveIn, session: Annotated[Session, Depends(get_session)]):
    """Merge the others' tags, folders, notes, best rating and source URL
    into `keep`, then trash the others: one batch, undoable."""
    out = duplicates_svc.resolve(session, body.keep, body.others, body.merge.model_dump())
    return {**out, "keep": ItemOut.from_item(out["keep"])}


# ---------- stacks ----------


class StackIn(BaseModel):
    item_ids: list[str] = Field(min_length=2)
    cover: str | None = None


class StackPatch(BaseModel):
    cover: str | None = None
    add: list[str] = []
    remove: list[str] = []


@router.get("/stacks/suggestions")
def stack_suggestions(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[dict]:
    """RAW+JPEG pairs, bursts (same camera, within 2 s) and version_of
    links worth stacking. Nothing is stacked until you POST /api/stacks."""
    return stacks_svc.suggestions(session, limit)


@router.post("/stacks", status_code=201)
def create_stack(body: StackIn, session: Annotated[Session, Depends(get_session)]) -> dict:
    stack = stacks_svc.create(session, body.item_ids, body.cover)
    return stacks_svc.stack_out(session, stack)


@router.get("/stacks/{stack_id}")
def get_stack(stack_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    return stacks_svc.stack_out(session, stacks_svc.get(session, stack_id))


@router.patch("/stacks/{stack_id}")
def patch_stack(stack_id: str, body: StackPatch,
                session: Annotated[Session, Depends(get_session)]) -> dict:
    stack = stacks_svc.update(session, stack_id, cover=body.cover, add=body.add,
                              remove=body.remove)
    if session.get(type(stack), stack_id) is None:
        return {"id": stack_id, "dissolved": True}
    return stacks_svc.stack_out(session, stack)


@router.delete("/stacks/{stack_id}")
def delete_stack(stack_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    return {"unstacked": stacks_svc.unstack(session, stack_id)}


# ---------- suggested collections ----------


class AcceptIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["folder", "smart_folder"] = "folder"
    parent_id: str | None = None


@router.get("/collections/suggested")
def suggested_collections(session: Annotated[Session, Depends(get_session)],
                          refresh: bool = False) -> dict:
    """Clusters of 8+ look-alike items not already in one folder:
    {computed_at, clusters: [{id, size, sample_item_ids, label_suggestion,
    ...}]}. Cached; refresh=1 recomputes now."""
    return collections_svc.suggested(session, refresh)


@router.post("/collections/suggested/{cid}/accept", status_code=201)
def accept_collection(cid: str, body: AcceptIn,
                      session: Annotated[Session, Depends(get_session)]) -> dict:
    return collections_svc.accept(session, cid, body.name, body.kind, body.parent_id)


# ---------- people ----------


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, max_length=faces_svc.NAME_MAX)
    cover_face_id: int | None = None


class PeopleMerge(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    into: str


class FacePatch(BaseModel):
    person_id: str | None = None


@router.get("/people")
def list_people(session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    """People (named first), each with face and item counts and a cover face."""
    return faces_svc.list_people(session)


@router.patch("/people/{person_id}")
def patch_person(person_id: str, body: PersonPatch,
                 session: Annotated[Session, Depends(get_session)]) -> dict:
    fields: dict[str, Any] = body.model_dump(exclude_unset=True)
    return faces_svc.update_person(
        session, person_id,
        name=fields["name"] if "name" in fields else ...,
        cover_face_id=fields.get("cover_face_id"),
    )


@router.post("/people/merge")
def merge_people(body: PeopleMerge, session: Annotated[Session, Depends(get_session)]) -> dict:
    return faces_svc.merge_people(session, body.from_, body.into)


@router.patch("/faces/{face_id}")
def patch_face(face_id: int, body: FacePatch,
               session: Annotated[Session, Depends(get_session)]) -> dict:
    """Move one face to another person, or to nobody (person_id null)."""
    return faces_svc.set_face_person(session, face_id, body.person_id)


@router.get("/faces/{face_id}/thumb")
def face_thumb(face_id: int, session: Annotated[Session, Depends(get_session)],
               size: Annotated[int, Query(ge=32, le=512)] = 160):
    return Response(faces_svc.face_thumb(session, face_id, size), media_type="image/webp",
                    headers={"Cache-Control": "private, no-cache"})
