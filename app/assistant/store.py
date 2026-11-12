"""
app/assistant/store.py
───────────────────────
Chat history in app.ChatConversation / app.ChatMessage.

Limits (app/config.py): each user keeps their CHAT_MAX_CONVERSATIONS most recent
conversations (older ones are deleted when a new one starts) and a conversation
holds at most CHAT_MAX_MESSAGES messages.
"""

import datetime as dt
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, insert, select, update

from app.config import get_settings
from app.db.app_tables import chat_conversation, chat_message
from app.db.sqlserver import get_db_session

settings = get_settings()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)  # naive UTC, matches DATETIME2


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


def _conversation(row) -> dict:
    return {"id": row["Id"], "title": row["Title"], "scope": row["Scope"],
            "created_at": _iso(row["CreatedAt"]), "updated_at": _iso(row["UpdatedAt"])}


def _message(row) -> dict:
    return {"id": row["Id"], "role": row["Role"], "content": row["Content"],
            "payload": json.loads(row["Payload"]) if row["Payload"] else {},
            "created_at": _iso(row["CreatedAt"])}


def list_conversations(login_id: int) -> List[dict]:
    with get_db_session() as s:
        rows = s.execute(
            select(chat_conversation)
            .where(chat_conversation.c.LoginId == login_id)
            .order_by(chat_conversation.c.UpdatedAt.desc(), chat_conversation.c.Id.desc())
            .limit(settings.chat_max_conversations)
        ).mappings().all()
        return [_conversation(r) for r in rows]


def get_conversation(login_id: int, conversation_id: int) -> Optional[dict]:
    with get_db_session() as s:
        row = s.execute(select(chat_conversation).where(
            chat_conversation.c.Id == conversation_id,
            chat_conversation.c.LoginId == login_id,
        )).mappings().first()
        if not row:
            return None
        messages = s.execute(
            select(chat_message)
            .where(chat_message.c.ConversationId == conversation_id)
            .order_by(chat_message.c.Id)
        ).mappings().all()
        return {**_conversation(row), "messages": [_message(m) for m in messages]}


def create_conversation(login_id: int, title: str, scope: str) -> dict:
    now = _now()
    with get_db_session() as s:
        new_id = s.execute(
            insert(chat_conversation)
            .values(LoginId=login_id, Title=title[:200], Scope=scope, CreatedAt=now, UpdatedAt=now)
            .returning(chat_conversation.c.Id)
        ).scalar_one()

        # Keep only the most recent N conversations for this user.
        keep = select(chat_conversation.c.Id).where(chat_conversation.c.LoginId == login_id) \
            .order_by(chat_conversation.c.UpdatedAt.desc(), chat_conversation.c.Id.desc()) \
            .limit(settings.chat_max_conversations)
        keep_ids = [r[0] for r in s.execute(keep)]
        old_ids = [r[0] for r in s.execute(
            select(chat_conversation.c.Id).where(
                chat_conversation.c.LoginId == login_id,
                chat_conversation.c.Id.not_in(keep_ids),
            )
        )]
        if old_ids:
            s.execute(delete(chat_message).where(chat_message.c.ConversationId.in_(old_ids)))
            s.execute(delete(chat_conversation).where(chat_conversation.c.Id.in_(old_ids)))
    return {"id": new_id, "title": title[:200], "scope": scope,
            "created_at": _iso(now), "updated_at": _iso(now)}


def delete_conversation(login_id: int, conversation_id: int) -> bool:
    with get_db_session() as s:
        owned = s.execute(select(chat_conversation.c.Id).where(
            chat_conversation.c.Id == conversation_id, chat_conversation.c.LoginId == login_id,
        )).first()
        if not owned:
            return False
        s.execute(delete(chat_message).where(chat_message.c.ConversationId == conversation_id))
        s.execute(delete(chat_conversation).where(chat_conversation.c.Id == conversation_id))
        return True


def message_count(conversation_id: int) -> int:
    with get_db_session() as s:
        return s.execute(select(func.count()).select_from(chat_message)
                         .where(chat_message.c.ConversationId == conversation_id)).scalar_one()


def add_message(conversation_id: int, role: str, content: str,
                payload: Optional[Dict[str, Any]] = None, scope: Optional[str] = None) -> dict:
    now = _now()
    with get_db_session() as s:
        new_id = s.execute(
            insert(chat_message).values(
                ConversationId=conversation_id, Role=role, Content=content,
                Payload=json.dumps(payload, default=str) if payload else None, CreatedAt=now,
            ).returning(chat_message.c.Id)
        ).scalar_one()
        values = {"UpdatedAt": now}
        if scope:
            values["Scope"] = scope
        s.execute(update(chat_conversation)
                  .where(chat_conversation.c.Id == conversation_id).values(**values))
    return {"id": new_id, "role": role, "content": content, "payload": payload or {},
            "created_at": _iso(now)}


def get_message(login_id: int, message_id: int) -> Optional[dict]:
    """A message, only if it belongs to one of this user's conversations."""
    with get_db_session() as s:
        row = s.execute(
            select(chat_message)
            .join(chat_conversation, chat_conversation.c.Id == chat_message.c.ConversationId)
            .where(chat_message.c.Id == message_id, chat_conversation.c.LoginId == login_id)
        ).mappings().first()
        return _message(row) if row else None


def update_message(message_id: int, content: str, payload: Dict[str, Any]) -> None:
    with get_db_session() as s:
        s.execute(update(chat_message).where(chat_message.c.Id == message_id).values(
            Content=content, Payload=json.dumps(payload, default=str)))
