"""F3 guest proofing on share links.

A share link can let its visitors give feedback: pick or reject, heart and
comment on the items it shows. It is off by default (per link, "Let guests
pick and comment"). Guests have no account: a random guest id and an
optional display name ride in a signed HttpOnly cookie scoped to the link's
own path (kist_guest), so nothing about them is kept but what they typed.

Rules that keep this safe (tests/test_f3_proofing.py checks each):
- guests never write library data. Their marks and comments live in their
  own tables and library/.kist/shares/<share_id>/feedback.json; only the
  owner's explicit Accept turns them into a pick, label or annotation,
  as an ordinary undoable batch in the item history;
- a guest can only touch items the share covers right now
  (shares.item_in_share, the same check that serves the files);
- feedback is plain text, length-limited and HTML-escaped wherever it is
  shown; control characters are dropped;
- every guest action is rate-limited per visitor address and per link (in
  memory), needs a CSRF token tied to the guest cookie, and is refused for
  cross-site requests;
- feedback is grouped in numbered rounds; the owner starts a new round,
  closes the current one or turns guest feedback off. Each mark and comment
  keeps the item version (sha256 and position in its version chain) the
  guest saw.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import unicodedata
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..config import get_settings
from ..ids import new_ulid
from ..models import Item, ItemLink, ProofComment, ProofMark, Share, ShareProofing
from . import events, validation
from . import meta as meta_svc
from . import shares as shares_svc

log = logging.getLogger("kist.proofing")

DIR = "shares"
FILE = "feedback.json"
NAME_MAX = 60
COMMENT_MAX = 2000
MARKS_MAX_PER_SHARE = 20_000
COMMENTS_MAX_PER_SHARE = 5_000
COMMENTS_PER_GUEST_ROUND = 200
RATE_WINDOW = 600  # seconds
GUEST_COOKIE = "kist_guest"
GUEST_COOKIE_DAYS = 180
GUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,32}$")
MARK_ACTIONS = ("pick", "reject", "unpick", "heart", "unheart")
LIST_MAX = 500


class NotFound(shares_svc.NotFound):
    """Answered 404 by the app's shares.NotFound handler."""

    def __init__(self, message: str):
        Exception.__init__(self, message)


class Closed(Exception):
    """Guest feedback is off, or the round is closed (HTTP 403 / a page)."""


class TooMany(Exception):
    """Rate limit or a size cap reached (HTTP 429)."""

    def __init__(self, message: str, wait: int = 60):
        super().__init__(message)
        self.wait = wait


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# ---------- text ----------


def _strip_controls(text: str, keep_newlines: bool) -> str:
    out = []
    for ch in text:
        if ch in "\n\t" and keep_newlines:
            out.append(ch)
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def clean_name(value) -> str:
    name = " ".join(_strip_controls(str(value or ""), False).split())
    if len(name) > NAME_MAX:
        raise validation.ValidationError(f"names are at most {NAME_MAX} characters")
    return name


def clean_comment(value) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_controls(text, True).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        raise validation.ValidationError("the comment is empty")
    if len(text) > COMMENT_MAX:
        raise validation.ValidationError(f"comments are at most {COMMENT_MAX} characters")
    return text


def guest_label(name: str, guest_id: str) -> str:
    return name or f"Guest {guest_id[:4]}"


def actor_label(name: str, guest_id: str, share: Share | None) -> str:
    """How an accepted comment's annotation names its author."""
    where = (share.title if share is not None and share.title else "a share link")
    return f"Guest: {guest_label(name, guest_id)} via {where}"


# ---------- the guest's cookie and CSRF token ----------


def _key(purpose: bytes) -> bytes:
    # tied to the owner token like the share cookie: rotating it signs out
    return hashlib.sha256(b"kist-proof-" + purpose + b"\0"
                          + get_settings().token.encode()).digest()


def _sign(msg: str, purpose: bytes) -> str:
    return hmac.new(_key(purpose), msg.encode(), hashlib.sha256).hexdigest()


def new_guest_id() -> str:
    return secrets.token_urlsafe(12)


def make_guest_cookie(share: Share, guest_id: str, name: str) -> tuple[str, int]:
    max_age = GUEST_COOKIE_DAYS * 86400
    if share.expires_at is not None:
        left = int((share.expires_at - _now()).total_seconds())
        max_age = max(1, min(max_age, left))
    until = int(time.time()) + max_age
    name_b64 = base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")
    body = f"{guest_id}.{name_b64}.{until}"
    return f"{body}.{_sign(share.slug + '|' + body, b'cookie')}", max_age


def read_guest_cookie(share: Share, value: str | None) -> tuple[str, str] | None:
    """(guest_id, name) from a valid cookie, else None."""
    if not value or len(value) > 400:
        return None
    parts = value.split(".")
    if len(parts) != 4:
        return None
    guest_id, name_b64, until, sig = parts
    if not GUEST_ID_RE.match(guest_id) or not until.isdigit() or int(until) < time.time():
        return None
    body = f"{guest_id}.{name_b64}.{until}"
    if not hmac.compare_digest(_sign(share.slug + "|" + body, b"cookie"), sig):
        return None
    try:
        name = base64.urlsafe_b64decode(name_b64 + "=" * (-len(name_b64) % 4)).decode()
        return guest_id, clean_name(name)
    except (ValueError, validation.ValidationError):
        return None


def csrf_token(share: Share, guest_id: str) -> str:
    return _sign(f"{share.slug}|{guest_id}", b"csrf")[:40]


def csrf_ok(share: Share, guest_id: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(share, guest_id), str(token)[:80])


# ---------- rate limits (in memory; addresses are hashed, never stored) ----------

_hits: dict[str, deque] = {}
_hits_lock = threading.Lock()
_salt = secrets.token_bytes(16)


def _take(key: str, limit: int, now: float) -> int | None:
    q = _hits.setdefault(key, deque())
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= limit:
        return int(RATE_WINDOW - (now - q[0])) + 1
    return None


def rate_check(address: str | None, share_id: str) -> None:
    """Count one guest action; TooMany when the visitor's address or the
    link is over its limit (KIST_PROOF_ACTIONS_PER_IP / _PER_SHARE)."""
    s = get_settings()
    ip_key = "ip:" + hashlib.sha256(_salt + (address or "?").encode()).hexdigest()[:24]
    share_key = "share:" + share_id
    now = time.monotonic()
    with _hits_lock:
        for key, limit in ((ip_key, s.proof_actions_per_ip),
                           (share_key, s.proof_actions_per_share)):
            wait = _take(key, max(1, limit), now)
            if wait:
                raise TooMany("Too much feedback at once. Try again in a few minutes.", wait)
        _hits[ip_key].append(now)
        _hits[share_key].append(now)
        if len(_hits) > 20_000:  # bounded memory under an address-spraying attack
            for key in list(_hits)[:10_000]:
                _hits.pop(key, None)


def reset_rates() -> None:
    with _hits_lock:
        _hits.clear()


# ---------- settings and rounds ----------


def get_settings_row(session: Session, share_id: str) -> ShareProofing | None:
    return session.get(ShareProofing, share_id)


def enabled_for(session: Session, share: Share) -> ShareProofing | None:
    sp = session.get(ShareProofing, share.id)
    return sp if sp is not None and sp.enabled else None


def settings_out(session: Session, share: Share, sp: ShareProofing | None) -> dict:
    counts = {"marks": 0, "comments": 0, "unseen": 0}
    if sp is not None:
        counts = _share_counts(session, share.id, sp)
    return {
        "share_id": share.id,
        "enabled": bool(sp and sp.enabled),
        "show_all": bool(sp and sp.show_all),
        "round": sp.round if sp else 1,
        "round_open": bool(sp.round_open) if sp else True,
        "rounds": list(sp.rounds or []) if sp else [],
        "seen_at": sp.seen_at if sp else None,
        **counts,
    }


def _share_counts(session: Session, share_id: str, sp: ShareProofing) -> dict:
    seen = sp.seen_at
    m_new = sa.func.count().filter(ProofMark.updated_at > seen) if seen else sa.func.count()
    c_new = sa.func.count().filter(ProofComment.created_at > seen) if seen else sa.func.count()
    marks, m_unseen = session.execute(
        sa.select(sa.func.count(), m_new).where(ProofMark.share_id == share_id)
    ).one()
    comments, c_unseen = session.execute(
        sa.select(sa.func.count(), c_new).where(ProofComment.share_id == share_id)
    ).one()
    return {"marks": marks, "comments": comments, "unseen": m_unseen + c_unseen}


def _share(session: Session, share_id: str) -> Share:
    share = session.get(Share, share_id)
    if share is None:
        raise NotFound(f"share {share_id} not found")
    return share


def _ensure(session: Session, share_id: str) -> ShareProofing:
    sp = session.get(ShareProofing, share_id)
    if sp is None:
        sp = ShareProofing(share_id=share_id, enabled=False, show_all=False, round=1,
                           round_open=True,
                           rounds=[{"n": 1, "started_at": _iso(_now()), "closed_at": None}],
                           updated_at=_now())
        session.add(sp)
        session.flush()
    return sp


def update_settings(session: Session, share_id: str, fields: dict) -> ShareProofing:
    """enabled, show_all and round_open (closing/reopening the current round)."""
    _share(session, share_id)
    sp = _ensure(session, share_id)
    if fields.get("enabled") is not None:
        sp.enabled = bool(fields["enabled"])
    if fields.get("show_all") is not None:
        sp.show_all = bool(fields["show_all"])
    if fields.get("round_open") is not None and bool(fields["round_open"]) != sp.round_open:
        sp.round_open = bool(fields["round_open"])
        rounds = [dict(r) for r in (sp.rounds or [])]
        for r in rounds:
            if r.get("n") == sp.round:
                r["closed_at"] = None if sp.round_open else _iso(_now())
        sp.rounds = rounds
    sp.updated_at = _now()
    _commit(session, share_id)
    return sp


def start_round(session: Session, share_id: str) -> ShareProofing:
    """Close the current round and open the next one (number + 1)."""
    _share(session, share_id)
    sp = _ensure(session, share_id)
    now = _iso(_now())
    rounds = [dict(r) for r in (sp.rounds or [])]
    for r in rounds:
        if r.get("n") == sp.round and not r.get("closed_at"):
            r["closed_at"] = now
    sp.round += 1
    sp.round_open = True
    rounds.append({"n": sp.round, "started_at": now, "closed_at": None})
    sp.rounds = rounds[-200:]
    sp.updated_at = _now()
    _commit(session, share_id)
    return sp


def mark_seen(session: Session, share_id: str | None = None) -> int:
    """The owner has looked: the unseen count drops to 0 (one share or all)."""
    q = sa.select(ShareProofing)
    if share_id:
        q = q.where(ShareProofing.share_id == share_id)
    rows = list(session.execute(q).scalars())
    now = _now()
    for sp in rows:
        sp.seen_at = now
    session.commit()
    for sp in rows:
        sync_file(session, sp.share_id)
    return len(rows)


# ---------- the guest's side ----------


def _open_round(session: Session, share: Share) -> ShareProofing:
    sp = enabled_for(session, share)
    if sp is None:
        raise Closed("Feedback is turned off for this link.")
    if not sp.round_open:
        raise Closed(f"Feedback for round {sp.round} is closed.")
    return sp


def _version(session: Session, item: Item) -> int:
    """The item's place in its version chain (1 = the first, or no chain)."""
    linked = session.execute(
        sa.select(ItemLink.src_id).where(
            ItemLink.kind == "version_of",
            sa.or_(ItemLink.src_id == item.id, ItemLink.dst_id == item.id),
        ).limit(1)
    ).first()
    if not linked:
        return 1
    from . import versions

    try:
        chain = [i.id for i in versions.chain(session, item.id)]
        return chain.index(item.id) + 1
    except Exception:  # a broken chain must not stop feedback
        return 1


def set_mark(session: Session, share: Share, item: Item, guest_id: str, name: str,
             action: str) -> ProofMark | None:
    """A guest's pick / reject / unpick / heart / unheart on an item the
    share covers (the caller checked item_in_share). Returns the mark, or
    None when it is cleared."""
    if action not in MARK_ACTIONS:
        raise validation.ValidationError("unknown action")
    sp = _open_round(session, share)
    meta_svc.advisory_lock(session, f"proofing:{share.id}")
    mark = session.execute(sa.select(ProofMark).where(
        ProofMark.share_id == share.id, ProofMark.round == sp.round,
        ProofMark.guest_id == guest_id, ProofMark.item_id == item.id,
    )).scalar_one_or_none()
    if mark is None:
        if action in ("unpick", "unheart"):
            session.commit()
            return None
        total = session.execute(sa.select(sa.func.count()).select_from(ProofMark)
                                .where(ProofMark.share_id == share.id)).scalar_one()
        if total >= MARKS_MAX_PER_SHARE:
            session.rollback()
            raise TooMany("This link has had as much feedback as it can take.", 3600)
        mark = ProofMark(id=new_ulid(), share_id=share.id, round=sp.round, guest_id=guest_id,
                         guest_name=name, item_id=item.id, item_sha256=item.sha256,
                         item_version=_version(session, item), verdict=None, heart=False,
                         created_at=_now())
        session.add(mark)
    if action == "pick":
        mark.verdict = None if mark.verdict == "pick" else "pick"  # a second tap clears
    elif action == "reject":
        mark.verdict = None if mark.verdict == "reject" else "reject"
    elif action == "unpick":
        mark.verdict = None
    elif action == "heart":
        mark.heart = not mark.heart
    elif action == "unheart":
        mark.heart = False
    mark.guest_name = name
    mark.item_sha256, mark.updated_at = item.sha256, _now()
    result: ProofMark | None = mark
    if mark.verdict is None and not mark.heart:
        session.delete(mark)
        result = None
    _commit(session, share.id)
    return result


def add_comment(session: Session, share: Share, item: Item, guest_id: str, name: str,
                text: str) -> ProofComment:
    text = clean_comment(text)
    sp = _open_round(session, share)
    meta_svc.advisory_lock(session, f"proofing:{share.id}")
    total, mine = session.execute(
        sa.select(sa.func.count(),
                  sa.func.count().filter(ProofComment.guest_id == guest_id,
                                         ProofComment.round == sp.round))
        .where(ProofComment.share_id == share.id)
    ).one()
    if total >= COMMENTS_MAX_PER_SHARE:
        session.rollback()
        raise TooMany("This link has had as many comments as it can take.", 3600)
    if mine >= COMMENTS_PER_GUEST_ROUND:
        session.rollback()
        raise TooMany("That's a lot of comments for one round. Thank you!", 3600)
    c = ProofComment(id=new_ulid(), share_id=share.id, round=sp.round, guest_id=guest_id,
                     guest_name=name, item_id=item.id, item_sha256=item.sha256,
                     item_version=_version(session, item), text=text, created_at=_now())
    session.add(c)
    _commit(session, share.id)
    return c


def guest_delete_comment(session: Session, share: Share, guest_id: str, comment_id: str) -> None:
    """A guest takes back their own comment in the open round."""
    sp = _open_round(session, share)
    c = session.get(ProofComment, comment_id or "")
    if (c is None or c.share_id != share.id or c.guest_id != guest_id
            or c.round != sp.round or c.accepted_at is not None):
        raise NotFound("comment")
    session.delete(c)
    _commit(session, share.id)


def rename_guest(session: Session, share: Share, guest_id: str, name: str) -> None:
    """A guest changed their display name: their feedback in the current
    round follows (older rounds keep the name they gave then)."""
    sp = enabled_for(session, share)
    if sp is None:
        return
    changed = 0
    for model in (ProofMark, ProofComment):
        changed += session.execute(
            sa.update(model).where(model.share_id == share.id, model.round == sp.round,
                                   model.guest_id == guest_id, model.guest_name != name)
            .values(guest_name=name)
        ).rowcount or 0
    if changed:
        _commit(session, share.id)
    else:
        session.commit()


def guest_view(session: Session, share: Share, sp: ShareProofing, guest_id: str | None,
               item_ids: list[str]) -> dict[str, dict]:
    """What a guest sees per item in the current round: their own mark and
    comments, plus everyone's when the owner turned show_all on."""
    out: dict[str, dict] = {
        i: {"verdict": None, "heart": False, "picks": 0, "rejects": 0, "hearts": 0,
            "comments": []}
        for i in item_ids
    }
    if not item_ids:
        return out
    marks = session.execute(sa.select(ProofMark).where(
        ProofMark.share_id == share.id, ProofMark.round == sp.round,
        ProofMark.item_id.in_(item_ids),
        *([] if sp.show_all else [ProofMark.guest_id == (guest_id or "")]),
    )).scalars()
    for m in marks:
        o = out[m.item_id]
        if m.guest_id == guest_id:
            o["verdict"], o["heart"] = m.verdict, m.heart
        o["picks"] += m.verdict == "pick"
        o["rejects"] += m.verdict == "reject"
        o["hearts"] += bool(m.heart)
    comments = session.execute(sa.select(ProofComment).where(
        ProofComment.share_id == share.id, ProofComment.round == sp.round,
        ProofComment.item_id.in_(item_ids),
        *([] if sp.show_all else [ProofComment.guest_id == (guest_id or "")]),
    ).order_by(ProofComment.created_at, ProofComment.id)).scalars()
    for c in comments:
        out[c.item_id]["comments"].append({
            "id": c.id, "name": guest_label(c.guest_name, c.guest_id), "text": c.text,
            "mine": c.guest_id == guest_id, "at": c.created_at,
        })
    return out


# ---------- the owner's side ----------


def _newer_versions(session: Session, item_ids: set[str]) -> set[str]:
    """Items that have a newer version (something is version_of them)."""
    if not item_ids:
        return set()
    return set(session.execute(sa.select(ItemLink.dst_id).where(
        ItemLink.kind == "version_of", ItemLink.dst_id.in_(item_ids)
    )).scalars())


def _items(session: Session, item_ids: set[str]) -> dict[str, Item]:
    if not item_ids:
        return {}
    return {i.id: i for i in session.execute(
        sa.select(Item).where(Item.id.in_(item_ids))).scalars()}


def _mark_accepted(m: ProofMark, item: Item | None) -> bool:
    if not m.accepted_batch or not m.accepted_as or item is None:
        return False
    if m.accepted_as.startswith("label:"):
        return item.label == m.accepted_as[6:]
    return item.flag == m.accepted_as


def _comment_accepted(c: ProofComment, annotations: set[str]) -> bool:
    return bool(c.annotation_id) and c.annotation_id in annotations


def _visible_item_ids(item_ids: set[str]) -> set[str]:
    """Owner/write tokens see everything; kept as the one place to narrow."""
    return item_ids


def feedback_rows(session: Session, *, share_id: str | None = None, round: int | None = None,
                  item_id: str | None = None, unseen: bool = False,
                  limit: int = 200) -> list[dict]:
    """Marks and comments, newest first, as the Feedback view lists them."""
    from ..models import Annotation

    limit = max(1, min(int(limit or 200), LIST_MAX))
    seen_by_share = {sp.share_id: sp.seen_at for sp in session.execute(
        sa.select(ShareProofing)).scalars()}
    rows: list[tuple[datetime, str, Any]] = []
    for model, at_col in ((ProofMark, ProofMark.updated_at),
                          (ProofComment, ProofComment.created_at)):
        q = sa.select(model).order_by(at_col.desc(), model.id.desc()).limit(limit)
        if share_id:
            q = q.where(model.share_id == share_id)
        if round is not None:
            q = q.where(model.round == round)
        if item_id:
            q = q.where(model.item_id == item_id)
        for r in session.execute(q).scalars():
            at = r.updated_at if model is ProofMark else r.created_at
            if unseen:
                seen = seen_by_share.get(r.share_id)
                if seen is not None and at <= seen:
                    continue
            rows.append((at, "mark" if model is ProofMark else "comment", r))
    rows.sort(key=lambda t: (t[0], t[2].id), reverse=True)
    rows = rows[:limit]
    ids = {r.item_id for _, _, r in rows}
    items = _items(session, ids)
    newer = _newer_versions(session, ids)
    shares = {s.id: s for s in session.execute(
        sa.select(Share).where(Share.id.in_({r.share_id for _, _, r in rows}))).scalars()}
    ann_ids = {r.annotation_id for _, k, r in rows if k == "comment" and r.annotation_id}
    live_anns = set(session.execute(
        sa.select(Annotation.id).where(Annotation.id.in_(ann_ids))).scalars()) if ann_ids else set()
    out = []
    for at, kind, r in rows:
        item = items.get(r.item_id)
        share = shares.get(r.share_id)
        seen = seen_by_share.get(r.share_id)
        base = {
            "id": r.id, "kind": kind, "share_id": r.share_id,
            "share_title": share.title if share else "", "round": r.round,
            "guest_id": r.guest_id, "guest_name": guest_label(r.guest_name, r.guest_id),
            "item_id": r.item_id, "item_filename": item.filename if item else None,
            "item_state": ("purged" if item is None
                           else "trashed" if item.deleted_at is not None else "active"),
            "item_version": r.item_version,
            "seen_version_changed": bool(item and item.sha256 != r.item_sha256),
            "has_newer_version": r.item_id in newer,
            "at": at, "unseen": seen is None or at > seen,
        }
        if kind == "mark":
            base.update(verdict=r.verdict, heart=r.heart, accepted_as=r.accepted_as,
                        accepted=_mark_accepted(r, item))
        else:
            base.update(text=r.text, annotation_id=r.annotation_id,
                        accepted=_comment_accepted(r, live_anns))
        out.append(base)
    return out


def item_counts(session: Session, item_ids: list[str] | None = None) -> dict[str, dict]:
    """Guest picks per item for tiles and the info panel: the current round
    of every share with guest feedback on (or the given items only)."""
    cur = sa.select(ShareProofing.share_id, ShareProofing.round).subquery()
    q = (sa.select(ProofMark.item_id,
                   sa.func.count().filter(ProofMark.verdict == "pick"),
                   sa.func.count().filter(ProofMark.verdict == "reject"),
                   sa.func.count().filter(ProofMark.heart.is_(True)))
         .join(cur, sa.and_(cur.c.share_id == ProofMark.share_id, cur.c.round == ProofMark.round))
         .group_by(ProofMark.item_id).limit(10_000))
    cq = (sa.select(ProofComment.item_id, sa.func.count())
          .join(cur, sa.and_(cur.c.share_id == ProofComment.share_id,
                             cur.c.round == ProofComment.round))
          .group_by(ProofComment.item_id).limit(10_000))
    if item_ids is not None:
        q = q.where(ProofMark.item_id.in_(item_ids))
        cq = cq.where(ProofComment.item_id.in_(item_ids))
    out: dict[str, dict] = {}
    for item_id, picks, rejects, hearts in session.execute(q).all():
        out[item_id] = {"picks": picks, "rejects": rejects, "hearts": hearts, "comments": 0}
    for item_id, n in session.execute(cq).all():
        out.setdefault(item_id, {"picks": 0, "rejects": 0, "hearts": 0, "comments": 0})
        out[item_id]["comments"] = n
    return out


def summary(session: Session) -> dict:
    """Sidebar and Feedback view header: per share with feedback on or any
    feedback, and the total unseen count."""
    shares = {s.id: s for s in session.execute(sa.select(Share)).scalars()}
    rows = []
    total = 0
    for sp in session.execute(sa.select(ShareProofing)).scalars():
        share = shares.get(sp.share_id)
        if share is None:
            continue
        out = settings_out(session, share, sp)
        if not out["enabled"] and not out["marks"] and not out["comments"]:
            continue
        out.update(title=share.title, status=shares_svc.status(share), slug=share.slug)
        total += out["unseen"]
        rows.append(out)
    rows.sort(key=lambda r: (-r["unseen"], r["title"] or ""))
    return {"unseen": total, "shares": rows}


def delete_comment(session: Session, comment_id: str) -> None:
    """The owner removes a guest comment (an accepted comment's annotation stays)."""
    c = session.get(ProofComment, comment_id)
    if c is None:
        raise NotFound(f"comment {comment_id} not found")
    share_id = c.share_id
    session.delete(c)
    _commit(session, share_id)


ACCEPT_AS = ("pick", "reject")


def _accept_value(as_: str | None, mark: ProofMark) -> str:
    value = as_ or mark.verdict or "pick"
    if value in ACCEPT_AS:
        return value
    if value.startswith("label:") and value[6:] in validation.LABELS:
        return value
    raise validation.ValidationError(
        "accept as pick, reject or label:<colour> (" + ", ".join(validation.LABELS) + ")"
    )


def accept(session: Session, *, mark_ids: list[str] | None = None,
           comment_ids: list[str] | None = None, as_: str | None = None) -> dict:
    """The owner's Accept: guest marks become the item's pick/reject flag
    (or a colour label), guest comments become annotations on the whole
    image authored "Guest: <name> via <share>". One batch, so one Undo
    reverts it all; an item that is gone or trashed is skipped."""
    from . import annotations as annotations_svc
    from . import items as items_svc

    mark_ids = list(dict.fromkeys(mark_ids or []))[:LIST_MAX]
    comment_ids = list(dict.fromkeys(comment_ids or []))[:LIST_MAX]
    if not mark_ids and not comment_ids:
        raise validation.ValidationError("nothing to accept")
    if as_ is not None:
        _accept_value(as_, ProofMark(verdict=None))  # validate once up front
    accepted, skipped = [], []
    touched_shares: set[str] = set()
    with events.new_batch() as ctx:
        marks = [m for m in session.execute(
            sa.select(ProofMark).where(ProofMark.id.in_(mark_ids))).scalars()] if mark_ids else []
        found = {m.id for m in marks}
        skipped += [{"id": i, "reason": "not found"} for i in mark_ids if i not in found]
        for m in marks:
            value = _accept_value(as_, m)
            item = session.get(Item, m.item_id)
            if item is None or item.deleted_at is not None:
                skipped.append({"id": m.id, "reason": "the item is gone or in the trash"})
                continue
            field = {"label": value[6:]} if value.startswith("label:") else {"flag": value}
            items_svc.update_item(session, m.item_id, fields=field)
            m = session.get(ProofMark, m.id)
            m.accepted_at, m.accepted_as, m.accepted_batch = _now(), value, ctx.batch_id
            touched_shares.add(m.share_id)
            session.commit()
            accepted.append(m.id)
        comments = [c for c in session.execute(
            sa.select(ProofComment).where(ProofComment.id.in_(comment_ids))).scalars()
        ] if comment_ids else []
        found = {c.id for c in comments}
        skipped += [{"id": i, "reason": "not found"} for i in comment_ids if i not in found]
        shares = {s.id: s for s in session.execute(sa.select(Share).where(
            Share.id.in_({c.share_id for c in comments}))).scalars()} if comments else {}
        for c in comments:
            item = session.get(Item, c.item_id)
            if item is None or item.deleted_at is not None:
                skipped.append({"id": c.id, "reason": "the item is gone or in the trash"})
                continue
            author = actor_label(c.guest_name, c.guest_id, shares.get(c.share_id))
            a = annotations_svc.create(
                session, c.item_id,
                {"kind": "box", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0,
                 "text": f"{c.text}\n\n({author}, round {c.round})"[:annotations_svc.TEXT_MAX]},
                author=author[:200],
            )
            c = session.get(ProofComment, c.id)
            c.accepted_at, c.annotation_id, c.accepted_batch = _now(), a.id, ctx.batch_id
            touched_shares.add(c.share_id)
            session.commit()
            accepted.append(c.id)
        batch_id = ctx.batch_id
    for share_id in touched_shares:
        sync_file(session, share_id)
    return {"batch_id": batch_id if accepted else None, "accepted": accepted, "skipped": skipped}


# ---------- the durable copy: library/.kist/shares/<id>/feedback.json ----------


def path_for(share_id: str) -> Path:
    return get_settings().state_dir / DIR / share_id / FILE


def _mark_row(m: ProofMark) -> dict:
    return {
        "id": m.id, "round": m.round, "guest_id": m.guest_id, "guest_name": m.guest_name,
        "item_id": m.item_id, "item_sha256": m.item_sha256, "item_version": m.item_version,
        "verdict": m.verdict, "heart": bool(m.heart), "created_at": _iso(m.created_at),
        "updated_at": _iso(m.updated_at), "accepted_at": _iso(m.accepted_at),
        "accepted_as": m.accepted_as, "accepted_batch": m.accepted_batch,
    }


def _comment_row(c: ProofComment) -> dict:
    return {
        "id": c.id, "round": c.round, "guest_id": c.guest_id, "guest_name": c.guest_name,
        "item_id": c.item_id, "item_sha256": c.item_sha256, "item_version": c.item_version,
        "text": c.text, "created_at": _iso(c.created_at), "accepted_at": _iso(c.accepted_at),
        "annotation_id": c.annotation_id, "accepted_batch": c.accepted_batch,
    }


def _settings_row(sp: ShareProofing) -> dict:
    return {
        "enabled": bool(sp.enabled), "show_all": bool(sp.show_all), "round": sp.round,
        "round_open": bool(sp.round_open), "rounds": list(sp.rounds or []),
        "seen_at": _iso(sp.seen_at),
    }


def file_doc(session: Session, share_id: str) -> dict | None:
    sp = session.get(ShareProofing, share_id)
    if sp is None:
        return None
    marks = session.execute(sa.select(ProofMark).where(ProofMark.share_id == share_id)
                            .order_by(ProofMark.id)).scalars()
    comments = session.execute(sa.select(ProofComment).where(ProofComment.share_id == share_id)
                               .order_by(ProofComment.id)).scalars()
    return {"version": 1, "share_id": share_id, "settings": _settings_row(sp),
            "marks": [_mark_row(m) for m in marks],
            "comments": [_comment_row(c) for c in comments]}


def _commit(session: Session, share_id: str) -> None:
    session.commit()
    sync_file(session, share_id)


def sync_file(session: Session, share_id: str) -> bool:
    """Write feedback.json from the committed rows (never raises; verify
    reports a failed write and `kist rewrite-meta` redoes it)."""
    try:
        meta_svc.advisory_lock(session, f"proofing-file:{share_id}")
        doc = file_doc(session, share_id)
        if doc is not None:
            meta_svc.atomic_write_text(path_for(share_id),
                                       json.dumps(doc, indent=1, ensure_ascii=False))
        session.commit()
        return True
    except Exception:
        session.rollback()
        log.exception("feedback.json for share %s not written", share_id)
        meta_svc.note_pending(f"proofing:{share_id}")
        return False


def sync_all(session: Session) -> bool:
    ok = True
    for share_id in session.execute(sa.select(ShareProofing.share_id)).scalars().all():
        ok = sync_file(session, share_id) and ok
    return ok


def on_share_deleted(share_id: str) -> None:
    """A share removed from the list takes its feedback file with it (the
    rows go by ON DELETE CASCADE), so rebuild-index can't bring it back."""
    p = path_for(share_id)
    try:
        p.unlink(missing_ok=True)
        p.parent.rmdir()
    except OSError:
        pass


def restore(session: Session) -> tuple[int, int]:
    """rebuild-index: settings, marks and comments from every feedback.json
    whose share exists. Returns (rows restored, unreadable files)."""
    base = get_settings().state_dir / DIR
    if not base.is_dir():
        return 0, 0
    restored = bad = 0
    for p in sorted(base.glob(f"*/{FILE}")):
        share_id = p.parent.name
        if session.get(Share, share_id) is None:
            continue
        try:
            doc = meta_svc.read_json(p)
            st = doc["settings"]
            sp = session.get(ShareProofing, share_id)
            if sp is None:
                session.add(ShareProofing(
                    share_id=share_id, enabled=bool(st.get("enabled")),
                    show_all=bool(st.get("show_all")), round=int(st.get("round") or 1),
                    round_open=bool(st.get("round_open", True)),
                    rounds=list(st.get("rounds") or []), seen_at=_parse(st.get("seen_at")),
                    updated_at=_now(),
                ))
                session.flush()
                restored += 1
            for r in doc.get("marks") or []:
                if session.get(ProofMark, r["id"]) is not None:
                    continue
                if r.get("verdict") not in (None, "pick", "reject"):
                    continue
                session.add(ProofMark(
                    id=r["id"], share_id=share_id, round=int(r["round"]),
                    guest_id=str(r["guest_id"]), guest_name=clean_name(r.get("guest_name")),
                    item_id=str(r["item_id"]), item_sha256=str(r.get("item_sha256") or ""),
                    item_version=int(r.get("item_version") or 1), verdict=r.get("verdict"),
                    heart=bool(r.get("heart")),
                    created_at=_parse(r.get("created_at")) or _now(),
                    updated_at=_parse(r.get("updated_at")) or _now(),
                    accepted_at=_parse(r.get("accepted_at")), accepted_as=r.get("accepted_as"),
                    accepted_batch=r.get("accepted_batch"),
                ))
                restored += 1
            for r in doc.get("comments") or []:
                if session.get(ProofComment, r["id"]) is not None:
                    continue
                session.add(ProofComment(
                    id=r["id"], share_id=share_id, round=int(r["round"]),
                    guest_id=str(r["guest_id"]), guest_name=clean_name(r.get("guest_name")),
                    item_id=str(r["item_id"]), item_sha256=str(r.get("item_sha256") or ""),
                    item_version=int(r.get("item_version") or 1),
                    text=clean_comment(r.get("text")),
                    created_at=_parse(r.get("created_at")) or _now(),
                    accepted_at=_parse(r.get("accepted_at")),
                    annotation_id=r.get("annotation_id"), accepted_batch=r.get("accepted_batch"),
                ))
                restored += 1
            session.commit()
        except Exception:
            session.rollback()
            log.exception("%s unreadable", p)
            bad += 1
    return restored, bad


def check_files(session: Session) -> list[str]:
    """verify: every share's feedback.json matches the database."""
    problems: list[str] = []
    base = get_settings().state_dir / DIR
    on_disk = {p.parent.name for p in base.glob(f"*/{FILE}")} if base.is_dir() else set()
    in_db = set(session.execute(sa.select(ShareProofing.share_id)).scalars())
    for share_id in sorted(in_db | on_disk):
        want = file_doc(session, share_id)
        p = path_for(share_id)
        if want is None:
            if session.get(Share, share_id) is not None:
                problems.append(f"shares/{share_id}/{FILE}: not in the database")
            continue
        if not p.is_file():
            problems.append(f"shares/{share_id}/{FILE} missing")
            continue
        try:
            disk = meta_svc.read_json(p)
        except (OSError, ValueError):
            problems.append(f"shares/{share_id}/{FILE} unreadable")
            continue
        if _norm(disk) != _norm(json.loads(json.dumps(want))):
            problems.append(f"shares/{share_id}/{FILE} differs from the database")
    return problems


def _norm(doc: dict) -> dict:
    """Compare instants, not their spelling."""
    def fix(row: dict) -> dict:
        return {k: (_parse(v) if k.endswith("_at") and isinstance(v, str) else v)
                for k, v in row.items()}

    try:
        return {
            "settings": fix(doc.get("settings") or {}),
            "marks": sorted((fix(r) for r in doc.get("marks") or []), key=lambda r: r["id"]),
            "comments": sorted((fix(r) for r in doc.get("comments") or []),
                               key=lambda r: r["id"]),
        }
    except (TypeError, KeyError, ValueError, AttributeError):
        return {"bad": True}
