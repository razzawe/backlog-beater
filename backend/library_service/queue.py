"""Personal play queue. Serialize edits on the owning account row."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from app.core.identity import get_gateway_user_id
from app.database import CompletionStatus, OwnedGame, PlayQueueEntry, User, get_session

queue_router = APIRouter(prefix="/games/queue", tags=["play queue"])


def lock_owner(session, user_id):
    if session.exec(select(User).where(User.id == user_id).with_for_update()).first() is None:
        raise HTTPException(401, "Unknown gateway user")


def entries(session, user_id):
    return session.exec(select(PlayQueueEntry).where(PlayQueueEntry.user_id == user_id)
                        .order_by(PlayQueueEntry.position, PlayQueueEntry.game_id)).all()


class QueueOrder(BaseModel):
    game_ids: list[int] = Field(max_length=10000)


@queue_router.get("")
def get_queue(user_id: int = Depends(get_gateway_user_id), session: Session = Depends(get_session)) -> list[int]:
    return [entry.game_id for entry in entries(session, user_id)]


@queue_router.put("")
def reorder_queue(body: QueueOrder, user_id: int = Depends(get_gateway_user_id), session: Session = Depends(get_session)) -> list[int]:
    lock_owner(session, user_id)
    current = {entry.game_id: entry for entry in entries(session, user_id)}
    if len(set(body.game_ids)) != len(body.game_ids) or set(body.game_ids) != set(current):
        raise HTTPException(409, "Queue changed. Refresh and reorder the current queue.")
    for position, game_id in enumerate(body.game_ids):
        current[game_id].position = position
        session.add(current[game_id])
    session.commit()
    return body.game_ids


@queue_router.post("/{game_id}")
def add_to_queue(game_id: int, user_id: int = Depends(get_gateway_user_id), session: Session = Depends(get_session)) -> list[int]:
    lock_owner(session, user_id)
    ownership = session.get(OwnedGame, (user_id, game_id))
    if ownership is None:
        raise HTTPException(404, "Owned game not found")
    if ownership.completion_status == CompletionStatus.IN_PROGRESS:
        raise HTTPException(409, "This game is already currently playing")
    current = entries(session, user_id)
    if not any(entry.game_id == game_id for entry in current):
        session.add(PlayQueueEntry(user_id=user_id, game_id=game_id,
                                  position=max((entry.position for entry in current), default=-1) + 1))
    session.commit()
    return [entry.game_id for entry in entries(session, user_id)]


@queue_router.delete("/{game_id}")
def remove_from_queue(game_id: int, user_id: int = Depends(get_gateway_user_id), session: Session = Depends(get_session)) -> list[int]:
    lock_owner(session, user_id)
    entry = session.get(PlayQueueEntry, (user_id, game_id))
    if entry is not None:
        session.delete(entry)
    session.commit()
    return [entry.game_id for entry in entries(session, user_id)]
