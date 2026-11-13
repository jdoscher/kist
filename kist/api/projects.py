"""Projects, pins and custom fields (Kist 2.0 F6; services/projects.py).

    GET    /api/projects?status=active|archived|all
    POST   /api/projects                         {name, client, job_number, phase, notes, status}
    GET    /api/projects/{id}                    members resolved (missing ones flagged)
    PATCH  /api/projects/{id}                    any of the above
    DELETE /api/projects/{id}                    its pins go, its field values leave the items
    POST   /api/projects/{id}/members            {kind, id}
    DELETE /api/projects/{id}/members/{kind}/{member_id}
    POST   /api/projects/{id}/fields             {name, type, choices?}
    PATCH  /api/projects/{id}/fields/{field_id}  {name?, choices?, position?}
    DELETE /api/projects/{id}/fields/{field_id}
    GET    /api/projects-of/{kind}/{member_id}   the projects holding a folder, board ...
    GET    /api/fields                           every field key in use (filter pickers)
    GET    /api/items/{id}/fields                the item's projects with fields + values
    PATCH  /api/items/{id}/fields                {values: {field id: value | null}}
    POST   /api/fields/bulk                      {item_ids, values}; one batch
    GET    /api/pins                             in order, names resolved
    POST   /api/pins                             {kind, ref, name?, query?}
    DELETE /api/pins?kind=&ref=
    PUT    /api/pins/order                       {pins: [{kind, ref}]}
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import ItemOut
from ..services import projects as svc

router = APIRouter(tags=["projects"])

SessionDep = Annotated[Session, Depends(get_session)]


class ProjectBody(BaseModel):
    name: str | None = Field(default=None, max_length=1000)
    client: str | None = Field(default=None, max_length=1000)
    job_number: str | None = Field(default=None, max_length=1000)
    phase: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=50_000)
    status: str | None = None


class MemberBody(BaseModel):
    kind: str
    id: str


class FieldBody(BaseModel):
    name: str | None = Field(default=None, max_length=1000)
    type: str | None = None
    choices: list[str] | None = None
    position: int | None = None


class ValuesBody(BaseModel):
    values: dict[str, Any]


class BulkValuesBody(BaseModel):
    item_ids: list[str]
    values: dict[str, Any]


class PinBody(BaseModel):
    kind: str
    ref: str | None = None
    name: str | None = None
    query: str | None = None


class PinOrder(BaseModel):
    pins: list[dict[str, str]]


@router.get("/projects")
def list_projects(session: SessionDep, status: str = "active"):
    return svc.list_projects(session, status)


@router.post("/projects", status_code=201)
def create_project(body: ProjectBody, session: SessionDep):
    return svc.create(session, body.model_dump(exclude_unset=True))


@router.get("/projects/{project_id}")
def get_project(project_id: str, session: SessionDep):
    return svc.get(session, project_id)


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectBody, session: SessionDep):
    return svc.update(session, project_id, body.model_dump(exclude_unset=True))


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, session: SessionDep):
    return svc.delete(session, project_id)


@router.post("/projects/{project_id}/members")
def add_member(project_id: str, body: MemberBody, session: SessionDep):
    return svc.add_member(session, project_id, body.kind, body.id)


@router.delete("/projects/{project_id}/members/{kind}/{member_id}")
def remove_member(project_id: str, kind: str, member_id: str, session: SessionDep):
    return svc.remove_member(session, project_id, kind, member_id)


@router.post("/projects/{project_id}/fields", status_code=201)
def add_field(project_id: str, body: FieldBody, session: SessionDep):
    return svc.add_field(session, project_id, body.model_dump(exclude_unset=True))


@router.patch("/projects/{project_id}/fields/{field_id}")
def patch_field(project_id: str, field_id: str, body: FieldBody, session: SessionDep):
    return svc.update_field(session, project_id, field_id, body.model_dump(exclude_unset=True))


@router.delete("/projects/{project_id}/fields/{field_id}")
def delete_field(project_id: str, field_id: str, session: SessionDep):
    return svc.delete_field(session, project_id, field_id)


@router.get("/projects-of/{kind}/{member_id}")
def projects_of(kind: str, member_id: str, session: SessionDep):
    return svc.projects_of(session, kind, member_id)


@router.get("/fields")
def fields_index(session: SessionDep):
    return svc.fields_index(session)


@router.get("/items/{item_id}/fields")
def item_fields(item_id: str, session: SessionDep):
    return svc.item_fields(session, item_id)


@router.patch("/items/{item_id}/fields")
def set_item_fields(item_id: str, body: ValuesBody, session: SessionDep):
    item = svc.set_values(session, item_id, body.values)
    return {"item": ItemOut.from_item(item), "projects": svc.item_fields(session, item_id)}


@router.post("/fields/bulk")
def bulk_fields(body: BulkValuesBody, session: SessionDep):
    return svc.bulk_set(session, body.item_ids, body.values)


@router.get("/pins")
def list_pins(session: SessionDep):
    return svc.list_pins(session)


@router.post("/pins")
def add_pin(body: PinBody, session: SessionDep):
    return svc.add_pin(session, body.model_dump(exclude_none=True))


@router.delete("/pins")
def remove_pin(kind: str, ref: str, session: SessionDep):
    return svc.remove_pin(session, kind, ref)


@router.put("/pins/order")
def reorder_pins(body: PinOrder, session: SessionDep):
    return svc.reorder_pins(session, body.pins)

