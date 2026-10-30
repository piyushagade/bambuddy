"""Group model for permission-based access control."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.user import User


# Many-to-many association table between users and groups
user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("expires_at", DateTime, nullable=True, default=None),
)


class Group(Base):
    """Group model for organizing users and assigning permissions.

    Groups contain a list of permissions that are granted to all members.
    Users can belong to multiple groups, and their permissions are additive.
    System groups (Administrators, Operators, Viewers) cannot be deleted.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    expiry_warned_7d_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    expiry_warned_1d_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    max_print_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    max_filament_grams_per_job: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    max_active_queue_items: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_hours_start: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    allowed_hours_end: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship to users through association table
    users: Mapped[list[User]] = relationship(
        "User",
        secondary=user_groups,
        back_populates="groups",
        lazy="selectin",
    )

    @property
    def is_expired(self) -> bool:
        """Return True if the group has a termination datetime in the past (UTC)."""
        if self.expires_at is None:
            return False
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return f"<Group {self.name}>"
