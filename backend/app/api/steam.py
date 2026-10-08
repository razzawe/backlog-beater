import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.main import get_current_user
from app.core.config import APP_PUBLIC_URL, STEAM_CALLBACK_URL, STEAM_OPENID_URL
from app.database import SteamLinkRequest, SteamOpenIDNonce, User, get_session


steam_router = APIRouter(prefix="/steam", tags=["steam"])
STEAM_LINK_COOKIE = "steam_link_browser"
STEAM_LINK_TTL_SECONDS = 600
CALLBACK = urlsplit(STEAM_CALLBACK_URL)
COOKIE_OPTIONS = {
    "path": CALLBACK.path or "/",
    "secure": CALLBACK.scheme == "https",
    "httponly": True,
    "samesite": "lax",
}


class SteamLinkResponse(BaseModel):
    login_url: str


class SteamLinkedResponse(BaseModel):
    steam_id: str


def token_hash(token: str) -> str:
    """Compute the SHA-256 fingerprint of a token.
    Return a hexadecimal digest for storage and token comparisons."""
    # Store token fingerprints instead of raw credentials.
    return sha256(token.encode()).hexdigest()


def steam_return_to(state: str) -> str:
    """Build the Steam callback URL with the supplied linking state.
    Preserve configured query parameters while replacing any existing state."""
    # Preserve configured query parameters and replace the linking state.
    query = [(key, value) for key, value in parse_qsl(CALLBACK.query) if key != "state"]
    query.append(("state", state))
    return urlunsplit(CALLBACK._replace(query=urlencode(query)))


@steam_router.post("/link", response_model=SteamLinkResponse)
def start_steam_link(
    response: Response,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SteamLinkResponse:
    """Start Steam account linking for an authenticated, unlinked user.
    Store an expiring request, set browser proof, and return the Steam login URL."""
    # Only an authenticated, unlinked account can start linking.
    if current_user.steam_id is not None:
        raise HTTPException(status_code=409, detail="Steam account already linked")
    if current_user.id is None:
        raise HTTPException(status_code=500, detail="User has no database ID")

    # Bind a ten-minute request to both the local user and this browser.
    state = token_urlsafe(32)
    browser_token = token_urlsafe(32)
    pending_link = SteamLinkRequest(
        user_id=current_user.id,
        state_hash=token_hash(state),
        browser_token_hash=token_hash(browser_token),
        expires_at=datetime.now(UTC) + timedelta(seconds=STEAM_LINK_TTL_SECONDS),
    )
    session.add(pending_link)
    session.commit()

    # Ask Steam to authenticate and return to our state-bearing callback.
    openid_params = {
        'openid.ns': 'http://specs.openid.net/auth/2.0',
        'openid.mode': 'checkid_setup',
        'openid.return_to': steam_return_to(state),
        'openid.realm': APP_PUBLIC_URL,
        'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
        'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
    }
    login_url = f"{STEAM_OPENID_URL}?{urlencode(openid_params)}"

    # Keep browser proof in an HttpOnly cookie until Steam redirects back.
    response.set_cookie(
        key=STEAM_LINK_COOKIE,
        value=browser_token,
        max_age=STEAM_LINK_TTL_SECONDS,
        **COOKIE_OPTIONS,
    )
    response.headers["Cache-Control"] = "no-store"
    return SteamLinkResponse(login_url=login_url)


@steam_router.get("/callback", response_model=SteamLinkedResponse)
def finish_steam_link(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> SteamLinkedResponse:
    """Verify a Steam OpenID callback and link its identity to the initiating user.
    Enforce request and replay checks, save the link, and clear browser proof."""
    invalid = HTTPException(status_code=400, detail="Invalid or expired Steam linking request")
    # Reject ambiguous parameters and require both linking tokens.
    pairs = list(request.query_params.multi_items())
    params = dict(pairs)
    if len(pairs) != len(params):
        raise invalid
    state = params.get("state", "")
    browser_token = request.cookies.get(STEAM_LINK_COOKIE, "")
    if not state or not browser_token:
        raise invalid

    # Lock the request so concurrent callbacks cannot consume it twice.
    pending = session.exec(
        select(SteamLinkRequest).where(
            SteamLinkRequest.state_hash == token_hash(state)
        ).with_for_update()
    ).first()
    now = datetime.now(UTC)
    if (
        pending is None
        or pending.consumed_at is not None
        or pending.expires_at <= now
        or not compare_digest(pending.browser_token_hash, token_hash(browser_token))
    ):
        raise invalid

    # Require Steam's provider, expected return URL, and signed identity fields.
    if params.get("openid.mode") == "cancel":
        raise HTTPException(status_code=400, detail="Steam linking cancelled")
    required_signed = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce", "assoc_handle"}
    if (
        params.get("openid.ns") != "http://specs.openid.net/auth/2.0"
        or params.get("openid.mode") != "id_res"
        or params.get("openid.op_endpoint") != STEAM_OPENID_URL
        or params.get("openid.return_to") != steam_return_to(state)
        or not required_signed.issubset(set(params.get("openid.signed", "").split(",")))
        or not params.get("openid.sig")
        or any(not params.get(f"openid.{field}") for field in required_signed)
    ):
        raise invalid
    claimed_id = params["openid.claimed_id"]
    identity_match = re.fullmatch(r"https://steamcommunity\.com/openid/id/([0-9]{17})", claimed_id)
    if identity_match is None or params["openid.identity"] != claimed_id:
        raise invalid

    # Reject stale assertions and timestamps too far in the future.
    nonce = params["openid.response_nonce"]
    try:
        nonce_time = datetime.strptime(nonce[:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise invalid
    if len(nonce) <= 20 or not now - timedelta(seconds=STEAM_LINK_TTL_SECONDS) <= nonce_time <= now + timedelta(seconds=60):
        raise invalid

    # Verify the signature directly with the fixed Steam endpoint.
    verification = {key: value for key, value in params.items() if key.startswith("openid.")}
    verification["openid.mode"] = "check_authentication"
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            result = client.post(STEAM_OPENID_URL, data=verification)
            result.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Steam verification unavailable; please retry")
    fields = dict(line.split(":", 1) for line in result.text.splitlines() if ":" in line)
    if fields.get("ns") != "http://specs.openid.net/auth/2.0" or fields.get("is_valid") != "true":
        raise invalid
    if pending.expires_at <= datetime.now(UTC):
        raise invalid

    # Lock the initiating account and reject existing Steam associations.
    user = session.exec(select(User).where(User.id == pending.user_id).with_for_update()).first()
    if user is None:
        raise invalid
    if user.steam_id is not None:
        raise HTTPException(status_code=409, detail="Steam account already linked")
    steam_id = identity_match.group(1)
    if session.exec(select(User).where(User.steam_id == steam_id)).first() is not None:
        raise HTTPException(status_code=409, detail="Steam account belongs to another user")
    # Save the link, consume the request, and record the nonce atomically.
    nonce_hash = token_hash(nonce)
    if session.get(SteamOpenIDNonce, nonce_hash) is not None:
        raise invalid
    session.add(SteamOpenIDNonce(nonce_hash=nonce_hash))
    user.steam_id = steam_id
    pending.consumed_at = datetime.now(UTC)
    session.add(user)
    session.add(pending)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Steam account or response already used")

    # Remove temporary browser proof after a successful link.
    response.delete_cookie(STEAM_LINK_COOKIE, **COOKIE_OPTIONS)
    response.headers["Cache-Control"] = "no-store"
    return SteamLinkedResponse(steam_id=steam_id)
