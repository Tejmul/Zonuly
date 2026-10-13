"""Reply tracking — poll Gmail threads for responses and classify them.

Classification drives the funnel board and the notification, so it errs toward
NEUTRAL rather than guessing POSITIVE: a false "they said yes!" is worse than a
reply the user reads themselves.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

from sqlmodel import col, func, select

from jobhunter.db import Email, Reply, get_session, init_db, utcnow
from jobhunter.outreach import gmail

log = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """You classify replies to a job-seeker's cold referral request.
You are conservative: if the reply is ambiguous, automated, or you are unsure, choose NEUTRAL.
JSON only."""

CLASSIFY_PROMPT = """The candidate asked this person for a referral. Here is their reply:

FROM: {sender}
---
{body}
---

Classify it:
- "positive": they agreed to refer, offered a call, asked for a resume/details, or made an intro
- "negative": they declined, said they can't help, or said the candidate isn't a fit
- "closed": the role is filled, hiring is frozen, they've left the company, or it's an
  auto-reply saying they're unreachable
- "neutral": out-of-office, a bounce, a mailing-list bot, a holding reply, or anything unclear

Reply with exactly:
{{"sentiment": "positive"|"negative"|"closed"|"neutral", "reason": "<one short sentence>", "action": "<what the candidate should do next, one short sentence>"}}"""

_QUOTE = re.compile(
    r"\n\s*(On .{0,80}wrote:|-{2,}\s*Original Message|_{5,}|From:\s.+\nSent:)", re.I
)


def _decode(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — malformed part
        return ""


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk the MIME tree, preferring text/plain."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}

    if mime == "text/plain" and body.get("data"):
        return _decode(body["data"])

    parts = payload.get("parts") or []
    for part in parts:                                  # plain text first
        if part.get("mimeType") == "text/plain":
            text = _extract_body(part)
            if text:
                return text
    for part in parts:                                  # then anything nested
        text = _extract_body(part)
        if text:
            return text

    if mime == "text/html" and body.get("data"):
        from jobhunter.scrapers.base import html_to_text

        return html_to_text(_decode(body["data"]))
    return ""


def _strip_quoted(text: str) -> str:
    """Drop the quoted original so the classifier judges only what they wrote."""
    m = _QUOTE.search(text)
    if m:
        text = text[: m.start()]
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(">")]
    return "\n".join(lines).strip()


def classify(body: str, sender: str) -> dict:
    from jobhunter import llm

    data = llm.chat_json(
        CLASSIFY_PROMPT.format(sender=sender, body=body[:3000]),
        CLASSIFY_SYSTEM,
        temperature=0.0,
        num_predict=200,
        alias="cheap",
        purpose="reply-classify",
        default=None,
    )
    valid = {"positive", "negative", "closed", "neutral"}
    if not isinstance(data, dict) or data.get("sentiment") not in valid:
        return {"sentiment": "neutral", "reason": "could not classify automatically", "action": "read it yourself"}
    return data


def record_reply(email_id: int, body: str, *, sender: str, message_id: str | None = None) -> dict:
    """Store one inbound reply, classify it, and — if it carries a yes with a time, a link
    or a deadline — put it on the calendar path. Used by the Gmail poll and by the
    operator's "record a reply" action (a pasted reply, or a dry-run rehearsal)."""
    import uuid

    from jobhunter.outreach import suppress

    init_db()
    # "unsubscribe", "please don't email me": suppressed forever, whatever the classifier says
    stop = suppress.is_opt_out(_strip_quoted(body) or body)
    if stop:
        verdict = {"sentiment": "negative", "reason": f"asked not to be contacted again (\"{stop}\")",
                   "action": "nothing — they and their company are suppressed"}
    else:
        verdict = classify(body, sender)
    sentiment = verdict["sentiment"]
    with get_session() as session:
        reply = Reply(
            email_id=email_id,
            gmail_message_id=message_id or f"manual:{uuid.uuid4().hex[:12]}",
            from_addr=sender,
            body=body[:8000],
            sentiment=sentiment,
            sentiment_reason=f"{verdict.get('reason', '')} | next: {verdict.get('action', '')}"[:400],
            received_at=utcnow(),
        )
        session.add(reply)
        email = session.get(Email, email_id)
        if email and email.status == "sent":
            email.status = "replied"
            session.add(email)
        # a reply to a follow-up is a reply to the ask it follows: the cold mail is answered
        if email and email.kind != "cold" and email.parent_email_id:
            parent = session.get(Email, email.parent_email_id)
            if parent and parent.status == "sent":
                parent.status = "replied"
                session.add(parent)
        suppressed = suppress.opt_out(session, email, phrase=stop, sender=sender) if (stop and email) else None
        session.commit()
        session.refresh(reply)
        reply_id = reply.id

    out = {"reply_id": reply_id, "sentiment": sentiment, "reason": verdict.get("reason"), "action": verdict.get("action")}
    if suppressed:
        out["opted_out"] = suppressed
    if sentiment == "positive":
        from jobhunter.outreach import schedule

        out["events"] = schedule.events_from_reply(reply_id)
    return out


def poll(limit: int = 400) -> dict:
    """Check every sent thread for new inbound messages."""
    init_db()
    stats = {"threads_checked": 0, "new_replies": 0, "bounced": 0, "positive": 0, "negative": 0, "closed": 0, "neutral": 0}

    if gmail.AUTH == "app_password":
        return _poll_imap(stats, limit)

    try:
        svc = gmail.service()
        me = svc.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
    except gmail.GmailNotConfigured as e:
        return {**stats, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {**stats, "error": f"Gmail unavailable: {e}"}

    with get_session() as session:
        sent = session.exec(
            select(Email).where(
                col(Email.status).in_(["sent", "replied"]), col(Email.gmail_thread_id).is_not(None)
            ).order_by(col(Email.sent_at).desc()).limit(limit)
        ).all()
        threads = [(e.id, e.gmail_thread_id, e.to_email) for e in sent]
        known = {
            r.gmail_message_id for r in session.exec(
                select(Reply).where(col(Reply.gmail_message_id).is_not(None))
            ).all()
        }

    for email_id, thread_id, to_email in threads:
        if str(thread_id).startswith("dryrun:"):
            continue   # never left the machine; nothing to poll
        stats["threads_checked"] += 1
        try:
            thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        except Exception as e:  # noqa: BLE001 — deleted thread, transient API error
            log.debug("thread %s unreadable: %s", thread_id, e)
            continue

        for msg in thread.get("messages") or []:
            msg_id = msg.get("id")
            if not msg_id or msg_id in known:
                continue
            headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}
            sender = (headers.get("from") or "").lower()
            if me and me in sender:
                continue  # our own message in the thread

            body = _strip_quoted(_extract_body(msg.get("payload") or {}))
            if not body:
                continue

            sentiment = record_reply(email_id, body, sender=headers.get("from") or to_email, message_id=msg_id)["sentiment"]
            known.add(msg_id)
            stats["new_replies"] += 1
            stats[sentiment] += 1
            log.info("reply on thread %s from %s -> %s", thread_id, sender, sentiment)

    log.info("reply poll: %s", stats)
    return stats


def _poll_imap(stats: dict, limit: int = 400) -> dict:
    """The same walk over IMAP: every sent thread, every message in it that is not ours.
    A bounce marks the email failed (the address is dead) and is never classified —
    a mailer-daemon is not a person saying no."""
    if not gmail.configured():
        return {**stats, "error": gmail.status()["hint"]}

    with get_session() as session:
        sent = session.exec(
            select(Email).where(
                col(Email.status).in_(["sent", "replied"]), col(Email.gmail_thread_id).is_not(None)
            ).order_by(col(Email.sent_at).desc()).limit(limit)
        ).all()
        threads = [(e.id, e.gmail_thread_id, e.to_email, e.gmail_message_id) for e in sent]
        known = {
            r.gmail_message_id for r in session.exec(
                select(Reply).where(col(Reply.gmail_message_id).is_not(None))
            ).all()
        }

    for email_id, thread_id, to_email, message_id in threads:
        if str(thread_id).startswith("dryrun:"):
            continue
        stats["threads_checked"] += 1
        try:
            messages = gmail.thread_messages(str(thread_id), message_id=message_id)
        except gmail.GmailNotConfigured as e:
            return {**stats, "error": str(e)}
        except Exception as e:  # noqa: BLE001 — transient IMAP error; next poll retries
            log.debug("thread %s unreadable: %s", thread_id, e)
            continue

        for msg in messages:
            if msg["is_ours"] or msg["id"] in known:
                continue
            if msg["is_bounce"]:
                from jobhunter.outreach import suppress

                with get_session() as session:
                    email = session.get(Email, email_id)
                    if email and email.status in ("sent", "replied"):
                        # the address is dead: the contact loses it, nobody retries it
                        suppress.mark_bounced(session, email, msg["text"])
                        session.commit()
                known.add(msg["id"])
                stats["bounced"] += 1
                log.warning("bounce on thread %s (%s)", thread_id, to_email)
                continue
            body = _strip_quoted(msg["text"])
            if not body:
                continue
            sentiment = record_reply(email_id, body, sender=msg["from"] or to_email, message_id=msg["id"])["sentiment"]
            known.add(msg["id"])
            stats["new_replies"] += 1
            stats[sentiment] += 1
            log.info("reply on thread %s from %s -> %s", thread_id, msg["from"], sentiment)

    log.info("reply poll: %s", stats)
    return stats


def funnel() -> dict:
    """Counts for the Overview funnel: drafted -> approved -> sent -> replied -> positive."""
    init_db()
    with get_session() as session:
        def count_emails(*where) -> int:
            return session.exec(select(func.count()).select_from(Email).where(*where)).one()

        def count_replies(sentiment: str) -> int:
            return session.exec(
                select(func.count()).select_from(Reply).where(Reply.sentiment == sentiment)
            ).one()

        return {
            "drafted": count_emails(Email.status == "draft"),
            "incomplete": count_emails(Email.status == "incomplete"),
            "approved": count_emails(Email.status == "approved"),
            "rejected": count_emails(Email.status == "rejected"),
            "failed": count_emails(Email.status == "failed"),
            "sent": count_emails(col(Email.status).in_(["sent", "replied"])),
            "replied": count_emails(Email.status == "replied"),
            "positive": count_replies("positive"),
            "negative": count_replies("negative"),
            "closed": count_replies("closed"),
            "neutral": count_replies("neutral"),
        }


# ------------------------------------------------------------------ the lifecycle view (2026-10-14)

def lifecycle(e: Email, replies: list[Reply], children: list[Email], contact=None, *, now=None) -> dict:
    """Where one cold email stands, in the user's words:
    draft / incomplete / approved / sent → waiting (days, follow-up due) → replied
    (positive / neutral / negative / closed) / bounced / opted out / rejected / failed."""
    from datetime import timedelta

    from jobhunter.outreach import followup

    now = now or utcnow()
    last = max(replies, key=lambda r: r.received_at or now) if replies else None
    opted = (contact is not None and getattr(contact, "email_status", None) == "opted_out") or any(
        "asked not to be contacted" in (r.sentiment_reason or "") for r in replies)
    out = {"stage": e.status, "sentiment": last.sentiment if last else None, "days_waiting": None,
           "followup_due": None, "followups": len(children), "reply_snippet": None, "replied_at": None}
    if e.status == "failed" and (e.error or "").startswith("bounced"):
        out["stage"] = "bounced"
    elif opted:
        out["stage"] = "opted_out"
    elif replies:
        out["stage"] = "replied"
        out["reply_snippet"] = re.sub(r"\s+", " ", last.body or "")[:200]
        out["replied_at"] = last.received_at.isoformat() if last.received_at else None
    elif e.status in ("sent", "replied"):
        out["stage"] = "waiting"
        sent = e.sent_at or e.created_at
        out["days_waiting"] = (now - sent).days if sent else None
        if not e.followup_sent and not children and followup.MAX_FOLLOWUPS > 0 and sent:
            out["followup_due"] = (sent + timedelta(days=followup.AFTER_DAYS)).date().isoformat()
    return out


def track(*, status: str | None = None, sentiment: str | None = None, limit: int = 500) -> dict:
    """Every cold email with its lifecycle, newest first, and the counts on top."""
    import json as _json

    from jobhunter.db import Company, Contact, Job

    init_db()
    with get_session() as session:
        emails = session.exec(select(Email).where(Email.kind == "cold").order_by(col(Email.id).desc()).limit(limit)).all()
        ids = [e.id for e in emails]
        replies: dict[int, list[Reply]] = {}
        children: dict[int, list[Email]] = {}
        if ids:
            kids = session.exec(select(Email).where(col(Email.parent_email_id).in_(ids))).all()
            for k in kids:
                children.setdefault(k.parent_email_id, []).append(k)
            all_ids = ids + [k.id for k in kids]
            parent_of = {k.id: k.parent_email_id for k in kids}
            for r in session.exec(select(Reply).where(col(Reply.email_id).in_(all_ids))).all():
                replies.setdefault(parent_of.get(r.email_id, r.email_id), []).append(r)
        rows = []
        for e in emails:
            contact = session.get(Contact, e.contact_id)
            company = session.get(Company, e.company_id)
            job = session.get(Job, e.job_id) if e.job_id else None
            lc = lifecycle(e, replies.get(e.id, []), children.get(e.id, []), contact)
            q = None
            if e.quality_detail:
                try:
                    q = _json.loads(e.quality_detail).get("verdict")
                except (ValueError, AttributeError):
                    q = None
            rows.append({
                "email_id": e.id, "company_id": e.company_id, "company_name": company.name if company else None,
                "contact_name": contact.name if contact else None, "contact_email": e.to_email,
                "contact_role": contact.role if contact else None,
                "job_title": job.title if job else None, "job_id": e.job_id, "subject": e.subject,
                "status": e.status, "created_at": e.created_at.isoformat() if e.created_at else None,
                "approved_at": e.approved_at.isoformat() if e.approved_at else None,
                "sent_at": e.sent_at.isoformat() if e.sent_at else None,
                "structure": e.structure, "quality": e.quality, "quality_verdict": q, "writer": e.writer,
                "address_confidence": e.address_confidence, **lc,
            })
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
        if r["sentiment"]:
            counts[f"reply_{r['sentiment']}"] = counts.get(f"reply_{r['sentiment']}", 0) + 1
    sent_total = sum(1 for r in rows if r["sent_at"])
    counts["sent_total"] = sent_total
    if status:
        rows = [r for r in rows if r["stage"] == status or r["status"] == status]
    if sentiment:
        rows = [r for r in rows if r["sentiment"] == sentiment]
    return {"counts": counts, "rows": rows}


def thread(email_id: int) -> dict | None:
    """One conversation in order: our cold email exactly as sent, every follow-up, and every
    reply with its sentiment and the classifier's reason."""
    from jobhunter.db import Company, Contact, Job

    init_db()
    with get_session() as session:
        e = session.get(Email, email_id)
        if e is None:
            return None
        root = session.get(Email, e.parent_email_id) if e.kind != "cold" and e.parent_email_id else e
        kids = session.exec(select(Email).where(Email.parent_email_id == root.id).order_by(col(Email.id))).all()
        msgs = [root, *kids]
        reps = session.exec(select(Reply).where(col(Reply.email_id).in_([m.id for m in msgs]))).all()
        contact = session.get(Contact, root.contact_id)
        company = session.get(Company, root.company_id)
        job = session.get(Job, root.job_id) if root.job_id else None
        items = []
        for m in msgs:
            items.append({"type": "ours", "email_id": m.id, "kind": m.kind, "status": m.status, "to": m.to_email,
                          "subject": m.subject, "body": m.body, "at": (m.sent_at or m.approved_at or m.created_at).isoformat()
                          if (m.sent_at or m.approved_at or m.created_at) else None, "sent": bool(m.sent_at),
                          "error": m.error})
        for r in reps:
            reason, _, action = (r.sentiment_reason or "").partition(" | next: ")
            items.append({"type": "theirs", "reply_id": r.id, "email_id": r.email_id, "from": r.from_addr,
                          "body": r.body, "sentiment": r.sentiment, "reason": reason or None, "next": action or None,
                          "at": r.received_at.isoformat() if r.received_at else None})
        items.sort(key=lambda x: x["at"] or "")
        lc = lifecycle(root, list(reps), list(kids), contact)
        import json as _json

        def _j(raw):
            try:
                return _json.loads(raw) if raw else None
            except ValueError:
                return None

        return {"email_id": root.id, "company_name": company.name if company else None,
                "contact_name": contact.name if contact else None, "contact_role": contact.role if contact else None,
                "to": root.to_email, "job_title": job.title if job else None, "job_url": job.url if job else None,
                "structure": root.structure, "subject_pattern": root.subject_pattern, "hook_type": root.hook_type,
                "quality": _j(root.quality_detail), "contract": _j(root.contract), "review_flags": _j(root.review_flags),
                "writer": root.writer, "thread_id": root.gmail_thread_id, "lifecycle": lc, "messages": items}
