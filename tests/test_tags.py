def test_prune_unused_tags_keeps_live_and_trashed(client, upload):
    live = upload(tags='["Keep"]')["item"]
    trashed = upload(tags='["InTrash"]')["item"]
    gone = upload(tags='["Gone"]')["item"]
    client.delete(f"/api/items/{trashed['id']}")
    client.put(f"/api/items/{gone['id']}/tags", json={"tags": ["Keep"]})

    res = client.delete("/api/tags/unused")
    assert res.status_code == 200
    assert res.json() == {"deleted": ["Gone"]}

    names = {t["name"] for t in client.get("/api/tags").json()}
    assert names == {"Keep", "InTrash"}
    assert client.post(f"/api/items/{trashed['id']}/restore").status_code == 200
    assert client.get(f"/api/items/{trashed['id']}").json()["tags"] == ["InTrash"]
    assert live["tags"] == ["Keep"]

    assert client.delete("/api/tags/unused").json() == {"deleted": []}
