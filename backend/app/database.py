import os
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Boolean, false, CheckConstraint, Column, DateTime, Enum as SQLAlchemyEnum, ForeignKeyConstraint, Index, JSON
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
    name: str | None = None
    cover_url: str | None = None
    hltb_id: int | None = None
    main_story_hours: float | None = None
    main_extra_hours: float | None = None
    completionist_hours: float | None = None
    __table_args__ = (
        CheckConstraint("hltb_id IS NULL OR hltb_id > 0", name="game_positive_hltb_id"),
        CheckConstraint("hltb_id IS NOT NULL OR (main_story_hours IS NULL AND "
                        "main_extra_hours IS NULL AND completionist_hours IS NULL)",
                        name="game_hours_require_hltb_id"),
        CheckConstraint("(main_story_hours IS NULL OR main_story_hours > 0) AND "
                        "(main_extra_hours IS NULL OR main_extra_hours > 0) AND "
                        "(completionist_hours IS NULL OR completionist_hours > 0)",
                        name="game_positive_hours"),
    )


class CompletionStatus(str, Enum):
    NOT_STARTED = "not started"
    IN_PROGRESS = "in progress"
    FINISHED = "finished"

class OwnedGame(SQLModel, table=True):
    # Each user can own each catalog game only once.

    user_id: int = Field(foreign_key="user.id", primary_key=True)
    game_id: int = Field(foreign_key="game.id", primary_key=True)
    recommendation_hidden: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=false()))
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

class PlayQueueEntry(SQLModel, table=True):
    user_id: int = Field(primary_key=True)
    game_id: int = Field(primary_key=True)
    position: int
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC),
                               sa_column=Column(DateTime(timezone=True), nullable=False))
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "game_id"], ["ownedgame.user_id", "ownedgame.game_id"], ondelete="CASCADE"),
        CheckConstraint("position >= 0", name="queue_nonnegative_position"),
    )


class EnrichmentStatus(str, Enum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"
    LOOKUP_FAILED = "lookup_failed"


class GameEnrichment(SQLModel, table=True):
    """Durable lookup outcomes; review candidates live temporarily in Redis."""
    game_id: int = Field(foreign_key="game.id", primary_key=True)
    status: EnrichmentStatus = Field(sa_column=Column(SQLAlchemyEnum(
        EnrichmentStatus, values_callable=lambda values: [v.value for v in values],
        native_enum=False, create_constraint=True, name="enrichment_status",
        validate_strings=True,
    ), nullable=False))
    reason: str
    last_lookup_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_success_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    last_error: str | None = None


class PersonalHLTBOverride(SQLModel, table=True):
    """An owner's selected candidate, independent of automatic refreshes."""
    user_id: int = Field(primary_key=True)
    game_id: int = Field(primary_key=True)
    hltb_id: int
    main_story_hours: float | None = None
    main_extra_hours: float | None = None
    completionist_hours: float | None = None
    candidate: dict = Field(sa_column=Column(JSON, nullable=False))
    selected_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "game_id"], ["ownedgame.user_id", "ownedgame.game_id"]),
        CheckConstraint("hltb_id > 0", name="override_positive_hltb_id"),
        CheckConstraint("(main_story_hours IS NULL OR main_story_hours > 0) AND "
                        "(main_extra_hours IS NULL OR main_extra_hours > 0) AND "
                        "(completionist_hours IS NULL OR completionist_hours > 0)",
                        name="override_positive_hours"),
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


class JobStatus(str, Enum):
    READY = "ready"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EnrichmentJob(SQLModel, table=True):
    __tablename__ = "enrichment_jobs"
    game_id: int = Field(foreign_key="game.id", primary_key=True)
    status: JobStatus = Field(default=JobStatus.READY, sa_column=Column(SQLAlchemyEnum(
        JobStatus, values_callable=lambda values: [v.value for v in values],
        native_enum=False, create_constraint=True, name="enrichment_job_status",
        validate_strings=True), nullable=False))
    attempt_token: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False))
    last_published_at: datetime | None = Field(default=None,
        sa_column=Column(DateTime(timezone=True)))
    last_error: str | None = None
    attempt_count: int = Field(default=0, nullable=False)
    next_attempt_at: datetime | None = Field(default=None,
        sa_column=Column(DateTime(timezone=True)))
    __table_args__ = (Index("ix_enrichment_jobs_status_updated", "status", "updated_at"),)


class GameMetadata(SQLModel, table=True):
    """Shared Steam Store metadata; unknown game mode remains NULL.

    Genres contain objects with stable string IDs and display names. An empty
    list does not establish successful collection; consult last_success_at.
    """
    __tablename__ = "game_metadata"
    game_id: int = Field(foreign_key="game.id", primary_key=True)
    single_player: bool | None = None
    multiplayer: bool | None = None
    is_free: bool | None = None
    trailer_url: str | None = None
    trailer_thumbnail: str | None = None
    trailer_title: str | None = None
    trailer_format: str | None = None
    genres: list[dict[str, str]] = Field(default_factory=list,
        sa_column=Column(JSON, nullable=False))
    last_lookup_at: datetime | None = Field(default=None,
        sa_column=Column(DateTime(timezone=True)))
    last_success_at: datetime | None = Field(default=None,
        sa_column=Column(DateTime(timezone=True)))
    last_error: str | None = None
    __table_args__ = (
        CheckConstraint("json_typeof(genres) = 'array'",
                        name="game_metadata_genres_array").ddl_if(dialect="postgresql"),
    )


class GameMetadataJob(SQLModel, table=True):
    """Steam metadata work progresses independently of HLTB attempts."""
    __tablename__ = "game_metadata_jobs"
    game_id: int = Field(foreign_key="game.id", primary_key=True)
    status: JobStatus = Field(default=JobStatus.READY, sa_column=Column(SQLAlchemyEnum(
        JobStatus, values_callable=lambda values: [v.value for v in values],
        native_enum=False, create_constraint=True, name="game_metadata_job_status",
        validate_strings=True), nullable=False))
    attempt_token: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False))
    last_published_at: datetime | None = Field(default=None,
        sa_column=Column(DateTime(timezone=True)))
    last_error: str | None = None
    __table_args__ = (Index("ix_game_metadata_jobs_status_updated", "status", "updated_at"),)
