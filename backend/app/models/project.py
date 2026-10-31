from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.group import Group
    from backend.app.models.user import User


class Project(Base):
    """Project to group related prints (e.g., 'Voron Build' with multiple parts)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Hex color for UI
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, completed, archived

    # External link rendered as a clickable icon next to the project name (#1155).
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Filename of the cover photo inside the project's attachments dir; serves as
    # the card's hero image when set (#1155). The file lives alongside other
    # attachments but is tracked here separately so users can manage one without
    # the other.
    cover_image_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Optional target number of prints (plates)
    target_parts_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Optional target number of parts/objects
    # Optional copies-per-file target (#1897): every printable file in the
    # project's linked folders should be printed this many times ("sets").
    target_sets: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Phase 2: Rich text notes (HTML from WYSIWYG editor)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase 3: File attachments stored as JSON array
    # Format: [{"filename": "x.stl", "original_name": "part.stl", "size": 1234, "uploaded_at": "..."}]
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Phase 4: Tags (comma-separated)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase 5: Due dates and priority
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")  # low, normal, high, urgent

    # Phase 6: Budget tracking
    budget: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 8: Templates
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    template_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Phase 10: Sub-projects (hierarchical)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)

    # User tracking / ownership
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])
    archives: Mapped[list["PrintArchive"]] = relationship(back_populates="project")
    queue_items: Mapped[list["PrintQueueItem"]] = relationship(back_populates="project")
    children: Mapped[list["Project"]] = relationship(
        "Project",
        back_populates="parent",
        foreign_keys="Project.parent_id",
    )
    parent: Mapped["Project | None"] = relationship(
        "Project",
        back_populates="children",
        remote_side="Project.id",
        foreign_keys="Project.parent_id",
    )
    bom_items: Mapped[list["ProjectBOMItem"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    permissions: Mapped[list["ProjectPermission"]] = relationship(
        "ProjectPermission",
        back_populates="project",
        cascade="all, delete-orphan",
    )


class ProjectPermission(Base):
    """Per-project user or group read/write ACL entry (inherits top-down to sub-projects)."""

    __tablename__ = "project_permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)
    can_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped["Project"] = relationship("Project", back_populates="permissions")
    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id])
    group: Mapped["Group | None"] = relationship("Group", foreign_keys=[group_id])


from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.print_queue import PrintQueueItem  # noqa: E402
from backend.app.models.project_bom import ProjectBOMItem  # noqa: E402
