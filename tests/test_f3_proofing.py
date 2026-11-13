"""F3 guest proofing on share links: guests pick, heart and comment on a
public page with no account. It opens a write path to unauthenticated
visitors, so most of this file is about what they must NOT be able to do:
reach items outside the share, write library data, post cross-site or
without the page's token, flood the link, or inject markup."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from kist import cli
from kist.config import Settings, get_settings
from kist.services import proofing
from kist.services import shares as shares_svc


@pytest.fixture(autouse=True)
def _fresh_limits():
    shares_svc.reset_attempts()
    proofing.reset_rates()
    yield
    proofing.reset_rates()


def guest(client) -> TestClient:
    """A visitor: no token, no cookies."""
    return TestClient(client.app, follow_redirects=False)


def proofed_share(client, upload, n=2, **share):
    folder = client.post("/api/folders", json={"name": "Mood"}).json()
    items = [upload(f"p{i}.png", folder_id=folder["id"])["item"] for i in range(n)]
    body = {"target_kind": "folder", "target_id": folder["id"], "title": "Mood", **share}
    s = client.post("/api/shares", json=body).json()
    res = client.patch(f"/api/shares/{s['id']}/proofing", json={"enabled": True})
    assert res.status_code == 200 and res.json()["enabled"] is True, res.text
    return s, folder, items


def open_page(v: TestClient, slug: str) -> str:
    """GET the gallery and return the page's CSRF token."""
    page = v.get(f"/s/{slug}")
    assert page.status_code == 200, page.text
    m = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert m, "no csrf token on the page"
    return m.group(1)


def act(v: TestClient, slug: str, csrf: str, **form):
    return v.post(f"/s/{slug}/proof", data={"csrf": csrf, "back": "1", **form})


def feedback(client, **params):
    res = client.get("/api/feedback", params=params)
    assert res.status_code == 200, res.text
    return res.json()


# ---------- defaults and the page ----------


def test_guest_feedback_is_off_by_default(client, upload):
    folder = client.post("/api/folders", json={"name": "F"}).json()
    item = upload("a.png", folder_id=folder["id"])["item"]
    s = client.post("/api/shares", json={"target_kind": "folder",
                                         "target_id": folder["id"]}).json()
    assert client.get(f"/api/shares/{s['id']}/proofing").json()["enabled"] is False
    v = guest(client)
    page = v.get(f"/s/{s['slug']}")
    assert "/proof" not in page.text and "set-cookie" not in page.headers
    res = v.post(f"/s/{s['slug']}/proof", data={"action": "pick", "item_id": item["id"]})
    assert res.status_code == 403
    assert v.get(f"/s/{s['slug']}/i/{item['id']}").status_code == 404
    assert feedback(client) == []


def test_settings_defaults():
    assert Settings.model_fields["proof_actions_per_ip"].default == 120
    assert Settings.model_fields["proof_actions_per_share"].default == 1500


def test_the_page_stays_script_free_and_hands_out_a_scoped_guest_cookie(client, upload):
    s, _, items = proofed_share(client, upload)
    v = guest(client)
    page = v.get(f"/s/{s['slug']}")
    assert page.status_code == 200
    assert "<script" not in page.text.lower()
    csp = page.headers["content-security-policy"]
    assert "allow-scripts" not in csp and "script-src" not in csp
    cookie = page.headers["set-cookie"]
    assert cookie.startswith("kist_guest=") and f"Path=/s/{s['slug']}" in cookie
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    for it in items:
        assert f'name="item_id" value="{it["id"]}"' in page.text
    # a returning guest keeps their cookie (no new one each visit)
    assert "set-cookie" not in v.get(f"/s/{s['slug']}").headers


# ---------- marks ----------


def test_pick_heart_reject_toggle_and_never_touch_the_library(client, upload):
    s, _, items = proofed_share(client, upload)
    it = items[0]
    before = client.get(f"/api/items/{it['id']}").json()
    v = guest(client)
    csrf = open_page(v, s["slug"])
    res = act(v, s["slug"], csrf, action="pick", item_id=it["id"])
    assert res.status_code == 303
    assert res.headers["location"] == f"/s/{s['slug']}#i-{it['id']}"
    assert act(v, s["slug"], csrf, action="heart", item_id=it["id"]).status_code == 303
    rows = feedback(client)
    assert len(rows) == 1 and rows[0]["verdict"] == "pick" and rows[0]["heart"] is True
    assert rows[0]["round"] == 1 and rows[0]["item_version"] == 1
    page = v.get(f"/s/{s['slug']}").text
    assert 'aria-pressed="true"' in page and "✓ Picked" in page
    # reject replaces the pick; pressing it again clears it
    act(v, s["slug"], csrf, action="reject", item_id=it["id"])
    assert feedback(client)[0]["verdict"] == "reject"
    act(v, s["slug"], csrf, action="reject", item_id=it["id"])
    act(v, s["slug"], csrf, action="heart", item_id=it["id"])
    assert feedback(client) == []  # nothing left: the mark is gone
    # the library never changed: no flag, no history, modified_at as it was
    after = client.get(f"/api/items/{it['id']}").json()
    assert after["flag"] is None and after["modified_at"] == before["modified_at"]
    hist = client.get(f"/api/items/{it['id']}/history").json()
    assert all(e.get("kind") != "edit" for e in hist)


def test_marks_record_the_version_the_guest_saw(client, upload):
    s, _, items = proofed_share(client, upload, n=1)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="pick", item_id=items[0]["id"])
    row = feedback(client)[0]
    assert row["item_version"] == 1 and row["has_newer_version"] is False
    from conftest import png_bytes

    res = client.post(f"/api/items/{items[0]['id']}/versions",
                      files={"file": ("v2.png", png_bytes((1, 2, 3)), "image/png")})
    assert res.status_code in (200, 201), res.text
    row = feedback(client)[0]
    assert row["has_newer_version"] is True and row["item_id"] == items[0]["id"]


# ---------- comments and escaping ----------


def test_comments_are_plain_text_escaped_and_limited(client, upload):
    s, _, items = proofed_share(client, upload)
    it = items[0]
    v = guest(client)
    csrf = open_page(v, s["slug"])
    evil = '<img src=x onerror=alert(1)><script>alert(2)</script>\x00\x07 "quoted" & more'
    res = act(v, s["slug"], csrf, action="comment", item_id=it["id"], text=evil, back="item")
    assert res.status_code == 303
    assert res.headers["location"] == f"/s/{s['slug']}/i/{it['id']}#comments"
    stored = feedback(client)[0]["text"]
    assert "\x00" not in stored and "\x07" not in stored and stored.startswith("<img")
    page = v.get(f"/s/{s['slug']}/i/{it['id']}").text
    assert "<img src=x" not in page and "<script>alert" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert act(v, s["slug"], csrf, action="comment", item_id=it["id"],
               text="x" * (proofing.COMMENT_MAX + 1)).status_code == 400
    assert act(v, s["slug"], csrf, action="comment", item_id=it["id"],
               text="  \n ").status_code == 400
    # a body far over the limit is refused before it is read
    assert v.post(f"/s/{s['slug']}/proof", content=b"a=" + b"x" * 20000,
                  headers={"content-type": "application/x-www-form-urlencoded"}
                  ).status_code == 413


def test_guest_name_is_kept_in_the_cookie_cleaned_and_escaped(client, upload):
    s, _, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="pick", item_id=items[0]["id"])
    res = act(v, s["slug"], csrf, action="name", name='Ann <b>"x"</b>\x00')
    assert res.status_code == 303 and "kist_guest=" in res.headers["set-cookie"]
    page = v.get(f"/s/{s['slug']}").text
    assert 'value="Ann &lt;b&gt;&quot;x&quot;&lt;/b&gt;"' in page
    assert feedback(client)[0]["guest_name"] == 'Ann <b>"x"</b>'  # renamed in this round
    assert act(v, s["slug"], csrf, action="name",
               name="n" * (proofing.NAME_MAX + 1)).status_code == 400
    # the same guest id: the CSRF token is still good after a rename
    assert act(v, s["slug"], csrf, action="heart", item_id=items[0]["id"]).status_code == 303


def test_guests_see_their_own_feedback_unless_show_all(client, upload):
    s, _, items = proofed_share(client, upload)
    it = items[0]
    a, b = guest(client), guest(client)
    ca, cb = open_page(a, s["slug"]), open_page(b, s["slug"])
    act(a, s["slug"], ca, action="comment", item_id=it["id"], text="secret from A")
    act(a, s["slug"], ca, action="pick", item_id=it["id"])
    assert "secret from A" not in b.get(f"/s/{s['slug']}/i/{it['id']}").text
    assert "secret from A" in a.get(f"/s/{s['slug']}/i/{it['id']}").text
    assert "1 picked" not in b.get(f"/s/{s['slug']}").text
    client.patch(f"/api/shares/{s['id']}/proofing", json={"show_all": True})
    assert "secret from A" in b.get(f"/s/{s['slug']}/i/{it['id']}").text
    assert "1 picked" in b.get(f"/s/{s['slug']}").text
    # B can't delete A's comment, even with show_all on
    cid = next(r["id"] for r in feedback(client) if r["kind"] == "comment")
    assert act(b, s["slug"], cb, action="delete_comment", comment_id=cid,
               back="item", item_id=it["id"]).status_code == 404
    assert act(a, s["slug"], ca, action="delete_comment", comment_id=cid,
               back="item", item_id=it["id"]).status_code == 303
    assert all(r["kind"] != "comment" for r in feedback(client))


# ---------- scope ----------


def test_a_guest_can_only_touch_items_in_the_share(client, upload):
    s, folder, items = proofed_share(client, upload)
    outside = upload("private.png")["item"]
    v = guest(client)
    csrf = open_page(v, s["slug"])
    for action, extra in (("pick", {}), ("heart", {}), ("comment", {"text": "hi"})):
        res = act(v, s["slug"], csrf, action=action, item_id=outside["id"], **extra)
        assert res.status_code == 404, action
    assert v.get(f"/s/{s['slug']}/i/{outside['id']}").status_code == 404
    assert outside["id"] not in v.get(f"/s/{s['slug']}").text
    # a junk id, a path-ish id
    for bad in ("../../etc", "", "x" * 300):
        assert act(v, s["slug"], csrf, action="pick", item_id=bad).status_code in (404, 400)
    # filed out of the shared folder: no longer reachable
    client.patch(f"/api/items/{items[0]['id']}", json={"folder_ids": []})
    assert act(v, s["slug"], csrf, action="pick", item_id=items[0]["id"]).status_code == 404
    # trashed: gone too
    client.delete(f"/api/items/{items[1]['id']}")
    assert act(v, s["slug"], csrf, action="pick", item_id=items[1]["id"]).status_code == 404
    assert feedback(client) == []


def test_a_guest_of_one_share_cant_use_its_cookie_on_another(client, upload):
    s1, _, items1 = proofed_share(client, upload)
    folder2 = client.post("/api/folders", json={"name": "Other"}).json()
    it2 = upload("o.png", folder_id=folder2["id"])["item"]
    s2 = client.post("/api/shares", json={"target_kind": "folder",
                                          "target_id": folder2["id"]}).json()
    client.patch(f"/api/shares/{s2['id']}/proofing", json={"enabled": True})
    v = guest(client)
    csrf1 = open_page(v, s1["slug"])
    # the cookie is path-scoped, and the token is tied to share 1's slug
    res = v.post(f"/s/{s2['slug']}/proof", data={"csrf": csrf1, "action": "pick",
                                                  "item_id": it2["id"]},
                 cookies={"kist_guest": v.cookies.get("kist_guest")})
    assert res.status_code == 403
    # and share 1's item through share 2's slug: not in share 2
    csrf2 = open_page(v, s2["slug"])
    assert act(v, s2["slug"], csrf2, action="pick", item_id=items1[0]["id"]).status_code == 404


def test_password_revoked_and_expired_links_take_no_feedback(client, upload):
    s, _, items = proofed_share(client, upload, password="hunter22")
    v = guest(client)
    assert v.get(f"/s/{s['slug']}").status_code == 401
    res = v.post(f"/s/{s['slug']}/proof", data={"action": "pick", "item_id": items[0]["id"]})
    assert res.status_code == 401
    assert v.get(f"/s/{s['slug']}/i/{items[0]['id']}").status_code == 401
    client.post(f"/api/shares/{s['id']}/revoke")
    assert v.post(f"/s/{s['slug']}/proof", data={}).status_code == 410
    assert v.get(f"/s/{s['slug']}/i/{items[0]['id']}").status_code == 410


# ---------- CSRF and origin ----------


def test_forms_need_the_pages_token_and_the_same_site(client, upload):
    s, _, items = proofed_share(client, upload)
    it = items[0]
    v = guest(client)
    csrf = open_page(v, s["slug"])
    assert act(v, s["slug"], "", action="pick", item_id=it["id"]).status_code == 403
    assert act(v, s["slug"], "0" * 40, action="pick", item_id=it["id"]).status_code == 403
    # someone else's token doesn't work with this cookie
    other = guest(client)
    other_csrf = open_page(other, s["slug"])
    assert act(v, s["slug"], other_csrf, action="pick", item_id=it["id"]).status_code == 403
    # no cookie at all (a forged form from a page that never loaded ours)
    bare = guest(client)
    assert act(bare, s["slug"], csrf, action="pick", item_id=it["id"]).status_code == 403
    # a forged cookie
    bare.cookies.set("kist_guest", "abcdefghijklmnop.QQ.9999999999.deadbeef")
    assert act(bare, s["slug"], csrf, action="pick", item_id=it["id"]).status_code == 403
    # cross-site
    res = v.post(f"/s/{s['slug']}/proof", data={"csrf": csrf, "action": "pick",
                                                 "item_id": it["id"]},
                 headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403
    res = v.post(f"/s/{s['slug']}/proof", data={"csrf": csrf, "action": "pick",
                                                 "item_id": it["id"]},
                 headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    assert feedback(client) == []
    # no-referrer pages send Origin: null on their own forms: that's fine
    res = v.post(f"/s/{s['slug']}/proof", data={"csrf": csrf, "action": "pick",
                                                 "item_id": it["id"]},
                 headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    assert res.status_code == 303


def test_the_back_target_cant_redirect_off_site(client, upload):
    s, _, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    for back in ("https://evil.example", "//evil.example", "item", "999999"):
        res = act(v, s["slug"], csrf, action="heart", item_id=items[0]["id"], back=back)
        assert res.status_code == 303
        assert res.headers["location"].startswith(f"/s/{s['slug']}"), back


# ---------- rate limits and caps ----------


def test_rate_limits_per_address_and_per_link(client, upload, monkeypatch):
    s, _, items = proofed_share(client, upload)
    monkeypatch.setattr(get_settings(), "proof_actions_per_ip", 3)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    codes = [act(v, s["slug"], csrf, action="heart", item_id=items[0]["id"]).status_code
             for _ in range(4)]
    assert codes == [303, 303, 303, 429]
    res = act(v, s["slug"], csrf, action="heart", item_id=items[0]["id"])
    assert res.status_code == 429 and int(res.headers["retry-after"]) > 0
    proofing.reset_rates()
    monkeypatch.setattr(get_settings(), "proof_actions_per_ip", 1000)
    monkeypatch.setattr(get_settings(), "proof_actions_per_share", 2)
    codes = [act(v, s["slug"], csrf, action="heart", item_id=items[0]["id"]).status_code
             for _ in range(3)]
    assert codes == [303, 303, 429]


def test_comment_caps_per_guest_round(client, upload, monkeypatch):
    s, _, items = proofed_share(client, upload)
    monkeypatch.setattr(proofing, "COMMENTS_PER_GUEST_ROUND", 2)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    codes = [act(v, s["slug"], csrf, action="comment", item_id=items[0]["id"],
                 text=f"c{i}").status_code for i in range(3)]
    assert codes == [303, 303, 429]


# ---------- rounds ----------


def test_rounds_close_reopen_and_start_fresh(client, upload):
    s, _, items = proofed_share(client, upload)
    it = items[0]
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="pick", item_id=it["id"])
    res = client.patch(f"/api/shares/{s['id']}/proofing", json={"round_open": False})
    assert res.json()["round_open"] is False and res.json()["rounds"][0]["closed_at"]
    page = v.get(f"/s/{s['slug']}").text
    assert "Feedback for round 1 is closed" in page and 'name="action"' not in page
    assert "You picked this" in page
    assert act(v, s["slug"], csrf, action="heart", item_id=it["id"]).status_code == 403
    out = client.post(f"/api/shares/{s['id']}/proofing/rounds").json()
    assert out["round"] == 2 and out["round_open"] is True and len(out["rounds"]) == 2
    page = v.get(f"/s/{s['slug']}").text
    assert "round 2" in page and "✓ Picked" not in page  # a fresh round
    act(v, s["slug"], csrf, action="reject", item_id=it["id"])
    rounds = sorted((r["round"], r["verdict"]) for r in feedback(client))
    assert rounds == [(1, "pick"), (2, "reject")]
    assert [r["verdict"] for r in feedback(client, round=1)] == ["pick"]
    # turning feedback off stops it and hides the controls; on again resumes
    client.patch(f"/api/shares/{s['id']}/proofing", json={"enabled": False})
    assert "/proof" not in v.get(f"/s/{s['slug']}").text
    assert act(v, s["slug"], csrf, action="pick", item_id=it["id"]).status_code == 403


# ---------- the owner's side ----------


def test_unseen_counts_and_mark_seen(client, upload):
    s, _, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="pick", item_id=items[0]["id"])
    act(v, s["slug"], csrf, action="comment", item_id=items[1]["id"], text="nice")
    summary = client.get("/api/feedback/summary").json()
    assert summary["unseen"] == 2 and summary["shares"][0]["share_id"] == s["id"]
    counts = client.get("/api/feedback/counts").json()
    assert counts[items[0]["id"]]["picks"] == 1 and counts[items[1]["id"]]["comments"] == 1
    assert len(feedback(client, unseen=True)) == 2
    assert client.post("/api/feedback/seen", json={}).json()["marked"] == 1
    assert client.get("/api/feedback/summary").json()["unseen"] == 0
    assert feedback(client, unseen=True) == []
    act(v, s["slug"], csrf, action="heart", item_id=items[1]["id"])
    assert client.get("/api/feedback/summary").json()["unseen"] == 1


def test_accept_is_explicit_one_batch_and_undoable(client, upload):
    s, _, items = proofed_share(client, upload)
    a, b = items
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="name", name="Ann")
    act(v, s["slug"], csrf, action="pick", item_id=a["id"])
    act(v, s["slug"], csrf, action="heart", item_id=b["id"])
    act(v, s["slug"], csrf, action="comment", item_id=a["id"], text="warmer please")
    rows = feedback(client)
    mark_a = next(r for r in rows if r["kind"] == "mark" and r["item_id"] == a["id"])
    mark_b = next(r for r in rows if r["kind"] == "mark" and r["item_id"] == b["id"])
    com = next(r for r in rows if r["kind"] == "comment")
    res = client.post("/api/feedback/accept", json={
        "mark_ids": [mark_a["id"]], "comment_ids": [com["id"]]})
    assert res.status_code == 200, res.text
    out = res.json()
    assert sorted(out["accepted"]) == sorted([mark_a["id"], com["id"]]) and out["batch_id"]
    assert client.get(f"/api/items/{a['id']}").json()["flag"] == "pick"
    anns = client.get(f"/api/items/{a['id']}/annotations").json()
    assert len(anns) == 1 and anns[0]["author"] == "Guest: Ann via Mood"
    assert "warmer please" in anns[0]["text"]
    rows = {r["id"]: r for r in feedback(client)}
    assert rows[mark_a["id"]]["accepted"] and rows[com["id"]]["accepted"]
    # a heart accepted as a label
    out2 = client.post("/api/feedback/accept", json={"mark_ids": [mark_b["id"]],
                                                     "as": "label:red"}).json()
    assert client.get(f"/api/items/{b['id']}").json()["label"] == "red"
    assert client.post("/api/feedback/accept", json={"mark_ids": [mark_b["id"]],
                                                     "as": "label:<b>"}).status_code == 422
    # undo the first batch: flag and annotation gone, the feedback is open again
    assert client.post(f"/api/batches/{out['batch_id']}/undo").status_code == 200
    assert client.get(f"/api/items/{a['id']}").json()["flag"] is None
    assert client.get(f"/api/items/{a['id']}/annotations").json() == []
    rows = {r["id"]: r for r in feedback(client)}
    assert not rows[mark_a["id"]]["accepted"] and not rows[com["id"]]["accepted"]
    assert rows[mark_b["id"]]["accepted"]
    client.post(f"/api/batches/{out2['batch_id']}/undo")
    assert client.get(f"/api/items/{b['id']}").json()["label"] is None
    # trashed items are skipped, not resurrected
    client.delete(f"/api/items/{a['id']}")
    out3 = client.post("/api/feedback/accept", json={"mark_ids": [mark_a["id"]]}).json()
    assert out3["accepted"] == [] and out3["skipped"][0]["id"] == mark_a["id"]
    assert client.post("/api/feedback/accept", json={}).status_code == 422


def test_owner_deletes_a_guest_comment(client, upload):
    s, _, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="comment", item_id=items[0]["id"], text="rude")
    cid = feedback(client)[0]["id"]
    assert client.delete(f"/api/feedback/comments/{cid}").status_code == 200
    assert feedback(client) == []
    assert client.delete(f"/api/feedback/comments/{cid}").status_code == 404
    assert "rude" not in v.get(f"/s/{s['slug']}/i/{items[0]['id']}").text


def test_read_tokens_never_see_guest_feedback(client, upload):
    from test_tokens import as_token, make_token

    s, folder, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="comment", item_id=items[0]["id"], text="hi")
    reader = as_token(client, make_token(client, "R", ["read"])["token"])
    limited = as_token(client, make_token(client, "L", ["read"],
                                          folder_ids=[folder["id"]])["token"])
    for c, want in ((reader, 403), (limited, 404)):
        for path in ("/api/feedback", "/api/feedback/summary", "/api/feedback/counts",
                     f"/api/shares/{s['id']}/proofing"):
            assert c.get(path).status_code == want, path
        assert c.post("/api/feedback/accept", json={"mark_ids": ["x"]}).status_code == want
        assert c.patch(f"/api/shares/{s['id']}/proofing",
                       json={"enabled": False}).status_code == want


def test_mcp_tools_list_and_accept(client, upload):
    from test_tokens import as_token, make_token, mcp_result

    s, _, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="pick", item_id=items[0]["id"])
    agent = as_token(client, make_token(client, "A", ["agent"])["token"])
    rows = json.loads(mcp_result(agent, "guest_feedback", {})["content"][0]["text"])
    rows = rows if isinstance(rows, list) else [rows]
    assert rows[0]["verdict"] == "pick"
    out = mcp_result(agent, "accept_guest_feedback", {"mark_ids": [rows[0]["id"]]})
    assert json.loads(out["content"][0]["text"])["accepted"] == [rows[0]["id"]]
    assert client.get(f"/api/items/{items[0]['id']}").json()["flag"] == "pick"
    reader = as_token(client, make_token(client, "R", ["read"])["token"])
    res = mcp_result(reader, "guest_feedback", {})
    assert res.get("isError")


# ---------- durability ----------


def test_feedback_survives_rebuild_index_and_verify_checks_it(client, upload):
    s, _, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="name", name="Bo")
    act(v, s["slug"], csrf, action="pick", item_id=items[0]["id"])
    act(v, s["slug"], csrf, action="comment", item_id=items[1]["id"], text="ok")
    client.post(f"/api/shares/{s['id']}/proofing/rounds")
    client.patch(f"/api/shares/{s['id']}/proofing", json={"show_all": True})
    client.post("/api/feedback/seen", json={})
    act(v, s["slug"], csrf, action="heart", item_id=items[1]["id"])
    mark = next(r for r in feedback(client) if r["round"] == 1 and r["kind"] == "mark")
    client.post("/api/feedback/accept", json={"mark_ids": [mark["id"]]})
    path = get_settings().state_dir / "shares" / s["id"] / "feedback.json"
    assert path.is_file()

    def snapshot():
        return (client.get(f"/api/shares/{s['id']}/proofing").json(),
                sorted((r["id"], r["kind"], r["round"], r.get("verdict"), r.get("heart"),
                        r.get("text"), r["guest_name"], r["accepted"])
                       for r in feedback(client)))

    before = snapshot()
    assert cli.verify() == 0
    assert cli.rebuild_index(wipe=True) == 0
    assert snapshot() == before
    assert cli.verify() == 0
    # the guest's cookie still works after the rebuild
    assert act(v, s["slug"], csrf, action="pick", item_id=items[1]["id"]).status_code == 303
    # verify notices a file that drifted
    doc = json.loads(path.read_text())
    doc["comments"] = []
    path.write_text(json.dumps(doc))
    from kist.db import SessionLocal

    with SessionLocal() as session:
        assert any("feedback.json differs" in p for p in proofing.check_files(session))
        assert proofing.sync_file(session, s["id"])
        assert proofing.check_files(session) == []


def test_removing_a_share_takes_its_feedback_file(client, upload):
    s, _, items = proofed_share(client, upload)
    v = guest(client)
    csrf = open_page(v, s["slug"])
    act(v, s["slug"], csrf, action="pick", item_id=items[0]["id"])
    path = get_settings().state_dir / "shares" / s["id"] / "feedback.json"
    assert path.is_file()
    client.post(f"/api/shares/{s['id']}/revoke")
    assert client.delete(f"/api/shares/{s['id']}").status_code == 200
    assert not path.exists()
    assert feedback(client) == []
    assert cli.rebuild_index(wipe=False) == 0
    assert feedback(client) == []


def test_unit_text_cleaning():
    assert proofing.clean_name("  a \t b\u200b ") == "a b"
    assert proofing.clean_comment("a\r\n\r\n\r\n\r\nb\x00") == "a\n\nb"
    with pytest.raises(ValueError):
        proofing.clean_comment("")
    assert proofing.guest_label("", "abcdefgh") == "Guest abcd"
