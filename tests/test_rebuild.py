from kist import cli


def test_rebuild_index_restores_everything_from_disk(client, upload):
    folder = client.post("/api/folders", json={"name": "Keep"}).json()
    client.post("/api/smart-folders", json={"name": "Reds", "query": {"tags": ["red"]}})
    item = upload(
        "keep.png", folder_id=folder["id"], tags="red", notes="n",
        created_at="2018-03-04T05:06:07Z",
    )["item"]
    trashed = upload("trash.png", deleted_at="2021-01-01T00:00:00Z")["item"]

    assert cli.rebuild_index(wipe=True) == 0

    got = client.get(f"/api/items/{item['id']}").json()
    assert got["tags"] == ["red"]
    assert got["folder_ids"] == [folder["id"]]
    assert got["notes"] == "n"
    assert got["created_at"].startswith("2018-03-04")
    assert client.get(f"/api/items/{trashed['id']}").json()["deleted_at"] is not None
    assert [s["name"] for s in client.get("/api/smart-folders").json()] == ["Reds"]
