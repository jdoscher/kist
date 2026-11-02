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


def test_folder_includes_subfolders(client, upload):
    top = client.post("/api/folders", json={"name": "Renders"}).json()
    mid = client.post("/api/folders", json={"name": "cobalt", "parent_id": top["id"]}).json()
    leaf = client.post("/api/folders", json={"name": "Stand", "parent_id": mid["id"]}).json()
    other = client.post("/api/folders", json={"name": "Elsewhere"}).json()

    deep = upload(folder_id=leaf["id"])["item"]
    shallow = upload(folder_id=top["id"])["item"]
    upload(folder_id=other["id"])
    # filed twice inside the same subtree: still one item
    client.put(f"/api/items/{deep['id']}/folders", json={"folder_ids": [leaf["id"], mid["id"]]})

    def ids(folder_id):
        return sorted(i["id"] for i in client.get(f"/api/items?folder={folder_id}").json()["items"])

    assert ids(top["id"]) == sorted([deep["id"], shallow["id"]])
    assert ids(mid["id"]) == [deep["id"]]
    assert ids(leaf["id"]) == [deep["id"]]
    assert client.get(f"/api/items?folder={top['id']}").json()["total"] == 2

    counts = {f["id"]: f["item_count"] for f in client.get("/api/folders").json()}
    assert counts[top["id"]] == 2
    assert counts[mid["id"]] == 1
    assert counts[leaf["id"]] == 1
    assert counts[other["id"]] == 1

    # trashed items drop out of the counts and the listing
    client.delete(f"/api/items/{deep['id']}")
    counts = {f["id"]: f["item_count"] for f in client.get("/api/folders").json()}
    assert counts[top["id"]] == 1 and counts[leaf["id"]] == 0
    assert ids(top["id"]) == [shallow["id"]]
