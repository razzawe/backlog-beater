import os
from datetime import datetime
from enum import Enum

from sqlalchemy import Column, DateTime, Enum as SQLAlchemyEnum
from sqlmodel import Field, Session, SQLModel, create_engine


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://backlog:backlog_dev_password@localhost:5433/backlog_beater",
)

engine = create_engine(DATABASE_URL)


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    steam_id: str | None = Field(default=None, unique=True, max_length=20)


class Game(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    steam_app_id: int = Field(unique=True)  # PostgreSQL prevents concurrent duplicates.
    cover_url: str | None = None


class CompletionStatus(str, Enum):
    NOT_STARTED = "not started"
    IN_PROGRESS = "in progress"
    FINISHED = "finished"

class OwnedGame(SQLModel, table=True):
    # Each user can own each catalog game only once.
    __tablename__ = "owned_games"

    user_id: int = Field(foreign_key="user.id", primary_key=True)
    game_id: int = Field(foreign_key="game.id", primary_key=True)
    completion_status: CompletionStatus = Field(
        default=CompletionStatus.NOT_STARTED,
        sa_column=Column(
            SQLAlchemyEnum(
                CompletionStatus,
                values_callable=lambda statuses: [status.value for status in statuses],
                native_enum=False,
                create_constraint=True,
                name="completion_status",
                validate_strings=True,
            ),
            nullable=False,
            server_default=CompletionStatus.NOT_STARTED.value,
        ),
    )

class SteamLinkRequest(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    state_hash: str = Field(unique=True, max_length=64)
    browser_token_hash: str = Field(max_length=64)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    consumed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

class SteamOpenIDNonce(SQLModel, table=True):
    nonce_hash: str = Field(primary_key=True, max_length=64)


def create_db_and_tables() -> None:
    """Create missing tables registered with SQLModel.
    Preserve existing tables in the configured database."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """Yield a database session for a request or dependency.
    Close the session when the caller finishes using it."""
    with Session(engine) as session:
        yield session
