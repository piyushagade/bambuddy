"""Enrollment invite model for self-service student cohort onboarding."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


def _generate_invite_code() -> str:
    return "UF-" + secrets.token_urlsafe(6).upper().replace("-", "").replace("_", "")[:8]


class EnrollmentInvite(Base):
    """Self-service enrollment code / invite link bound to a Group, CostCenter, and expiration date."""

    __tablename__ = "enrollment_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=_generate_invite_code)
    name: Mapped[str] = mapped_column(String(150))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    cost_center_id: Mapped[int | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="SET NULL"), nullable=True
    )
    user_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    uses_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    group = relationship("Group", lazy="selectin")
    cost_center = relationship("CostCenter", lazy="selectin")

    @property
    def is_expired(self) -> bool:
        if self.invite_expires_at is None:
            return False
        exp = self.invite_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= datetime.now(timezone.utc)

    @property
    def is_exhausted(self) -> bool:
        if self.max_uses is None:
            return False
        return self.uses_count >= self.max_uses

    @property
    def is_redeemable(self) -> bool:
        return bool(self.is_active) and not self.is_expired and not self.is_exhausted
