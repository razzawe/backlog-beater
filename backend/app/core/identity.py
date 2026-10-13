"""Identity supplied by Nginx on the trusted service network."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlmodel import Session

from app.database import User, get_session


def get_gateway_user_id(
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
) -> int:
    """Parse the identity verified by Nginx; never fall back to a token."""
    if user_id is None or not user_id.isascii() or not user_id.isdecimal():
        raise HTTPException(status_code=401, detail="Verified gateway identity required")
    if len(user_id) > 10 or not 0 < int(user_id) <= 2147483647:
        raise HTTPException(status_code=401, detail="Invalid gateway identity")
    return int(user_id)


def get_gateway_user(
    user_id: int = Depends(get_gateway_user_id),
    session: Session = Depends(get_session),
) -> User:
    """Load account details only for endpoints that need them."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown gateway user")
    return user
