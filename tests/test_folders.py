def test_nested_folders_and_multi_membership(client, upload):
    parent = client.post("/api/folders", json={"name": "Work"}).json()
    child = client.post("/api/folders", json={"name": "Logos", "parent_id": parent["id"]}).json()
    other = client.post("/api/folders", json={"name": "Inspiration"}).json()
    assert child["parent_id"] == parent["id"]

    item = upload()["item"]
    res = client.put(
        f"/api/items/{item['id']}/folders", json={"folder_ids": [child["id"], other["id"]]}
    )
    assert sorted(res.json()["folder_ids"]) == sorted([child["id"], other["id"]])

    in_child = client.get(f"/api/items?folder={child['id']}").json()["items"]
    in_other = client.get(f"/api/items?folder={other['id']}").json()["items"]
    unfiled = client.get("/api/items?folder=none").json()["items"]
    assert [i["id"] for i in in_child] == [item["id"]] == [i["id"] for i in in_other]
    assert unfiled == []

    counts = {f["id"]: f["item_count"] for f in client.get("/api/folders").json()}
    assert counts[child["id"]] == 1 and counts[other["id"]] == 1


def test_folder_cannot_become_its_own_descendant(client):
    a = client.post("/api/folders", json={"name": "A"}).json()
    b = client.post("/api/folders", json={"name": "B", "parent_id": a["id"]}).json()
    res = client.patch(f"/api/folders/{a['id']}", json={"parent_id": b["id"]})
    assert res.status_code == 400


def test_deleting_a_folder_keeps_its_items(client, upload):
    folder = client.post("/api/folders", json={"name": "Temp"}).json()
    item = upload(folder_id=folder["id"])["item"]
    assert item["folder_ids"] == [folder["id"]]
    client.delete(f"/api/folders/{folder['id']}")
    assert client.get(f"/api/items/{item['id']}").json()["folder_ids"] == []


def test_smart_folder_crud(client):
    query = {"tags_any": ["cats", "dogs"], "type": "image"}
    made = client.post("/api/smart-folders", json={"name": "Pets", "query": query}).json()
    assert made["query"]["tags_any"] == ["cats", "dogs"]
    client.patch(f"/api/smart-folders/{made['id']}", json={"name": "Animals"})
    listed = client.get("/api/smart-folders").json()
    assert [s["name"] for s in listed] == ["Animals"]
    client.delete(f"/api/smart-folders/{made['id']}")
    assert client.get("/api/smart-folders").json() == []
