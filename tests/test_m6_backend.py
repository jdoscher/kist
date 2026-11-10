"""Milestone 6 backend: date buckets (B1-B3), unfiled count (B6)."""

from datetime import UTC, datetime

import sqlalchemy as sa


def _dates(rows: dict[str, datetime], col: str = "created_at") -> None:
    from kist.db import SessionLocal
    from kist.models import Item

    with SessionLocal() as s:
        for item_id, when in rows.items():
            s.execute(sa.update(Item).where(Item.id == item_id).values({col: when}))
        s.commit()


def _dt(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def _library(upload):
    """9 items over four months, with ties and items exactly on month starts."""
    ids = [upload(tags="m6" if i % 2 else "")["item"]["id"] for i in range(9)]
    when = {
        ids[0]: _dt(2024, 7, 1),  # exactly on July's start: July
        ids[1]: _dt(2024, 6, 30, 23, 59, 59),
        ids[2]: _dt(2024, 6, 15, 12),
        ids[3]: _dt(2024, 6, 15, 12),  # a tie with ids[2]
        ids[4]: _dt(2024, 6, 1),  # exactly on June's start: June
        ids[5]: _dt(2024, 5, 31, 23, 59, 59),
        ids[6]: _dt(2024, 5, 2),
        ids[7]: _dt(2023, 12, 31, 23),
        ids[8]: _dt(2023, 12, 1, 0, 0, 1),
    }
    _dates(when)
    return ids, when


def _all_pages(client, params: dict, limit: int) -> list[str]:
    out, cursor = [], params.pop("cursor", None)
    while True:
        q = {**params, "limit": limit, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/items", params=q).json()
        out += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            return out


def test_buckets_newest_first_with_cursors_that_start_each_month(client, upload):
    ids, when = _library(upload)
    res = client.get("/api/items/buckets", params={"sort": "created", "order": "desc"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert (data["sort"], data["order"], data["item_order"]) == ("created", "desc", "-created")
    assert data["total"] == 9
    assert [(b["key"], b["count"]) for b in data["buckets"]] == [
        ("2024-07", 1), ("2024-06", 4), ("2024-05", 2), ("2023-12", 2)]

    full = _all_pages(client, {"order": "-created"}, 100)
    assert full == sorted(ids, key=lambda i: (when[i], i), reverse=True)
    for bucket in data["buckets"]:
        month = [i for i in full if when[i].strftime("%Y-%m") == bucket["key"]]
        # B2: GET /api/items takes the cursor unchanged; the month's items come
        # first, and paging (2 at a time, across the tie) runs on into the next
        paged = _all_pages(client, {"order": "-created", "cursor": bucket["cursor"]}, 2)
        assert paged[:len(month)] == month, bucket["key"]
        assert paged == full[full.index(month[0]):], bucket["key"]


def test_buckets_oldest_first(client, upload):
    ids, when = _library(upload)
    data = client.get("/api/items/buckets", params={"sort": "created", "order": "asc"}).json()
    assert [(b["key"], b["count"]) for b in data["buckets"]] == [
        ("2023-12", 2), ("2024-05", 2), ("2024-06", 4), ("2024-07", 1)]
    assert data["item_order"] == "created"
    full = _all_pages(client, {"order": "created"}, 100)
    june = data["buckets"][2]
    paged = _all_pages(client, {"order": "created", "cursor": june["cursor"]}, 3)
    assert paged == full[full.index(ids[4]):]  # June starts with the item on June 1 00:00
    assert paged[:4] == [i for i in full if when[i].strftime("%Y-%m") == "2024-06"]


def test_buckets_take_the_list_order_key_and_every_filter(client, upload):
    ids, when = _library(upload)
    # the taken sort: a date taken moves an item to its month
    _dates({ids[0]: _dt(2019, 3, 4)}, col="taken_at")
    data = client.get("/api/items/buckets", params={"order": "-taken"}).json()
    assert (data["sort"], data["order"], data["item_order"]) == ("taken", "desc", "-taken")
    assert [b["key"] for b in data["buckets"]] == ["2024-06", "2024-05", "2023-12", "2019-03"]
    paged = _all_pages(client, {"order": "-taken", "cursor": data["buckets"][-1]["cursor"]}, 5)
    assert paged == [ids[0]]
    # modified, both ways
    assert client.get("/api/items/buckets", params={"sort": "modified", "order": "asc"}
                      ).json()["item_order"] == "modified"
    assert client.get("/api/items", params={"order": "modified"}).status_code == 200
    # filters: the same as GET /api/items
    tagged = client.get("/api/items/buckets", params={"tags": "m6"}).json()
    listed = client.get("/api/items", params={"tags": "m6"}).json()
    assert tagged["total"] == listed["total"] == 4
    unfiled = client.get("/api/items/buckets", params={"view": "unfiled"}).json()
    assert unfiled["total"] == 5
    assert client.get("/api/items/buckets", params={"type": "pdf"}).json() == {
        "sort": "created", "order": "desc", "item_order": "-created", "total": 0, "buckets": []}
    # not a date sort, or nonsense
    for params in ({"sort": "filename"}, {"order": "-size"}, {"order": "sideways"},
                   {"sort": "taken", "order": "up"}):
        res = client.get("/api/items/buckets", params=params)
        assert res.status_code == 422, (params, res.text)


def test_buckets_respect_a_folder_limited_token(client, upload):
    ids, _ = _library(upload)
    folder = client.post("/api/folders", json={"name": "Shared"}).json()
    client.patch(f"/api/items/{ids[2]}/folders", json={"add": [folder["id"]]})
    secret = client.post("/api/tokens", json={
        "name": "reader", "scopes": ["read"], "folder_ids": [folder["id"]]}).json()["token"]
    res = client.get("/api/items/buckets", headers={"Authorization": f"Bearer {secret}"})
    assert res.status_code == 200, res.text
    assert [(b["key"], b["count"]) for b in res.json()["buckets"]] == [("2024-06", 1)]


def test_mcp_item_buckets(client, upload):
    from test_mcp import call, init

    _library(upload)
    init(client)
    import json

    res = call(client, "item_buckets", {"sort": "created", "order": "asc", "tags": ["m6"]})
    assert not res.get("isError"), res
    data = json.loads(res["content"][0]["text"])
    assert data["item_order"] == "created" and data["total"] == 4
    rest = call(client, "item_buckets", {"sort": "filename"}, id_=11)
    assert rest["isError"] and "date sort" in rest["content"][0]["text"]
    first = data["buckets"][0]
    page = json.loads(call(client, "search_items", {
        "order": "created", "cursor": first["cursor"], "tags": ["m6"], "limit": 100,
    }, id_=12)["content"][0]["text"])
    assert len(page["items"]) == 4


def test_stats_counts_unfiled(client, upload):
    a = upload()["item"]
    upload(tags="x")
    folder = client.post("/api/folders", json={"name": "F"}).json()
    upload(folder_id=folder["id"])
    trashed = upload()["item"]
    assert client.get("/api/stats").json()["unfiled"] == 2
    client.delete(f"/api/items/{trashed['id']}")
    stats = client.get("/api/stats").json()
    assert stats["unfiled"] == 1
    assert stats["unfiled"] == client.get("/api/items", params={"view": "unfiled"}).json()["total"]
    client.patch(f"/api/items/{a['id']}/tags", json={"add": ["done"]})
    assert client.get("/api/stats").json()["unfiled"] == 0
