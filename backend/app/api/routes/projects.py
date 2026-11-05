import io
import json
import logging
import os
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.library import get_library_dir
from backend.app.core.auth import (
    RequireAnyPermissionIfAuthEnabled,
    RequirePermissionIfAuthEnabled,
    require_media_token_permission,
)
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.group import Group, user_groups
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project, ProjectPermission
from backend.app.models.project_bom import ProjectBOMItem
from backend.app.models.user import User
from backend.app.schemas.project import (
    ArchivePreview,
    BatchAddArchives,
    BatchAddQueueItems,
    BOMItemCreate,
    BOMItemResponse,
    BOMItemUpdate,
    ProjectChildPreview,
    ProjectCreate,
    ProjectFileProgress,
    ProjectImport,
    ProjectListResponse,
    ProjectPermissionEntry,
    ProjectPermissionsListResponse,
    ProjectPermissionsUpdateRequest,
    ProjectResponse,
    ProjectShareTargetGroup,
    ProjectShareTargetUser,
    ProjectStats,
    ProjectUpdate,
    TimelineEvent,
)
from backend.app.utils.http import build_content_disposition
from backend.app.utils.safe_path import safe_join_under

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def _require_project_read():
    return RequireAnyPermissionIfAuthEnabled(
        Permission.PROJECTS_READ_ALL,
        Permission.PROJECTS_READ_OWN,
        Permission.PROJECTS_READ,
    )


def _require_project_write():
    return RequireAnyPermissionIfAuthEnabled(
        Permission.PROJECTS_UPDATE_ALL,
        Permission.PROJECTS_UPDATE_OWN,
        Permission.PROJECTS_UPDATE,
        Permission.PROJECTS_READ_ALL,
        Permission.PROJECTS_READ_OWN,
        Permission.PROJECTS_READ,
    )


def _require_project_delete():
    return RequireAnyPermissionIfAuthEnabled(
        Permission.PROJECTS_DELETE_ALL,
        Permission.PROJECTS_DELETE_OWN,
        Permission.PROJECTS_DELETE,
    )


@dataclass
class _ProjectAccess:
    """Resolved access permissions for a user on a single Project."""

    can_read: bool = False
    can_write: bool = False
    can_manage: bool = False
    can_delete: bool = False
    is_shared: bool = False


async def _compute_project_access_map(
    db: AsyncSession,
    user: User | None,
) -> dict[int, _ProjectAccess]:
    """Compute effective project permissions across the project hierarchy.

    Rules:
    - Admins or auth-disabled (`user is None or user.is_admin`) have full access on all projects.
    - Non-admins do NOT inherently have access to projects unless:
      1. They created/own the project (`project.created_by_id == user.id`) ->
         `can_read=True`, and `can_write`/`can_manage`/`can_delete` when holding
         the corresponding update/delete permission.
      2. They or one of their active (non-expired) groups have an explicit
         `ProjectPermission` grant on the project.
    - Top-down inheritance: `can_read`, `can_write`, `can_manage`, `can_delete`,
      and `is_shared` propagate downward from parent projects to all descendant sub-projects.
    """
    all_rows = (
        await db.execute(
            select(Project.id, Project.parent_id, Project.created_by_id)
        )
    ).all()
    if not all_rows:
        return {}

    if user is None:
        return {
            pid: _ProjectAccess(can_read=True, can_write=True, can_manage=True, can_delete=True, is_shared=False)
            for pid, _, _ in all_rows
        }

    can_write_own = user.has_any_permission(
        Permission.PROJECTS_UPDATE_OWN.value,
        Permission.PROJECTS_UPDATE_ALL.value,
        Permission.PROJECTS_UPDATE.value,
    )
    can_delete_own = user.has_any_permission(
        Permission.PROJECTS_DELETE_OWN.value,
        Permission.PROJECTS_DELETE_ALL.value,
        Permission.PROJECTS_DELETE.value,
    )

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    grp_rows = (
        await db.execute(
            select(Group.id, Group.expires_at)
            .join(user_groups, user_groups.c.group_id == Group.id)
            .where(user_groups.c.user_id == user.id)
        )
    ).all()
    active_group_ids = {
        gid
        for gid, gexp in grp_rows
        if gexp is None or (gexp.replace(tzinfo=None) if gexp.tzinfo else gexp) > now_naive
    }

    perm_filters = [ProjectPermission.user_id == user.id]
    if active_group_ids:
        perm_filters.append(ProjectPermission.group_id.in_(active_group_ids))

    perm_rows = (
        await db.execute(
            select(
                ProjectPermission.project_id,
                ProjectPermission.can_read,
                ProjectPermission.can_write,
            ).where(or_(*perm_filters))
        )
    ).all()

    access_map: dict[int, _ProjectAccess] = {}
    children_by_parent: dict[int | None, list[int]] = {}
    creator_by_id: dict[int, int | None] = {}
    all_ids = {pid for pid, _, _ in all_rows}

    for pid, parent_id, creator_id in all_rows:
        effective_parent = parent_id if parent_id in all_ids and parent_id != pid else None
        children_by_parent.setdefault(effective_parent, []).append(pid)
        creator_by_id[pid] = creator_id
        is_owner = creator_id is not None and creator_id == user.id
        if user.is_admin:
            access_map[pid] = _ProjectAccess(
                can_read=True,
                can_write=True,
                can_manage=True,
                can_delete=True,
                is_shared=False,
            )
        else:
            access_map[pid] = _ProjectAccess(
                can_read=is_owner,
                can_write=is_owner and can_write_own,
                can_manage=is_owner and can_write_own,
                can_delete=is_owner and can_delete_own,
                is_shared=False,
            )

    for pid, p_read, p_write in perm_rows:
        acc = access_map.get(pid)
        if acc is not None:
            if p_read or p_write:
                acc.can_read = True
                if creator_by_id.get(pid) != user.id:
                    acc.is_shared = True
            if p_write:
                acc.can_write = True

    # Top-down inheritance (parent -> children)
    queue = list(children_by_parent.get(None, []))
    visited_down: set[int] = set()
    while queue:
        curr_id = queue.pop(0)
        if curr_id in visited_down:
            continue
        visited_down.add(curr_id)
        curr_acc = access_map[curr_id]
        for child_id in children_by_parent.get(curr_id, []):
            child_acc = access_map[child_id]
            if curr_acc.can_read:
                child_acc.can_read = True
            if curr_acc.can_write:
                child_acc.can_write = True
            if curr_acc.can_manage:
                child_acc.can_manage = True
            if curr_acc.can_delete:
                child_acc.can_delete = True
            if (curr_acc.is_shared or (not user.is_admin and curr_acc.can_read)) and creator_by_id.get(child_id) != user.id:
                child_acc.is_shared = True
            queue.append(child_id)

    return access_map


async def user_can_read_project(
    db: AsyncSession,
    user: User | None,
    project_id: int | None,
) -> bool:
    """Return True if ``user`` can read ``project_id``."""
    if project_id is None:
        return False
    if user is None or user.is_admin:
        return True
    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id)
    return bool(acc and acc.can_read)


async def _get_creator_username(db: AsyncSession, created_by_id: int | None) -> str | None:
    if created_by_id is None:
        return None
    return (await db.execute(select(User.username).where(User.id == created_by_id))).scalar_one_or_none()


async def _build_project_permissions_response(
    db: AsyncSession,
    project: Project,
) -> ProjectPermissionsListResponse:
    """Build the ProjectPermissionsListResponse payload for a project."""
    creator_username = await _get_creator_username(db, project.created_by_id)

    perm_rows = (
        await db.execute(
            select(
                ProjectPermission,
                User.username,
                User.first_name,
                User.last_name,
                Group.name,
            )
            .outerjoin(User, ProjectPermission.user_id == User.id)
            .outerjoin(Group, ProjectPermission.group_id == Group.id)
            .where(ProjectPermission.project_id == project.id)
            .order_by(ProjectPermission.id)
        )
    ).all()

    entries = [
        ProjectPermissionEntry(
            id=perm.id,
            project_id=perm.project_id,
            user_id=perm.user_id,
            username=uname,
            first_name=ufirst,
            last_name=ulast,
            group_id=perm.group_id,
            group_name=gname,
            can_read=perm.can_read,
            can_write=perm.can_write,
        )
        for perm, uname, ufirst, ulast, gname in perm_rows
    ]

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    users_res = await db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.username)
    )
    available_users = [
        ProjectShareTargetUser(
            id=u.id,
            username=u.username,
            first_name=u.first_name,
            last_name=u.last_name,
            email=u.email,
            group_names=[g.name for g in (u.groups or []) if g.name != "Administrators"],
        )
        for u in users_res.scalars().all()
        if u.is_effectively_active and not u.is_admin
    ]

    groups_res = await db.execute(select(Group).order_by(Group.name))
    available_groups = [
        ProjectShareTargetGroup(id=g.id, name=g.name, description=g.description)
        for g in groups_res.scalars().all()
        if g.name != "Administrators"
        and (
            g.expires_at is None
            or (g.expires_at.replace(tzinfo=None) if g.expires_at.tzinfo else g.expires_at) > now_naive
        )
    ]

    return ProjectPermissionsListResponse(
        project_id=project.id,
        project_name=project.name,
        created_by_id=project.created_by_id,
        created_by_username=creator_username,
        permissions=entries,
        available_users=available_users,
        available_groups=available_groups,
    )


_FAILURE_STATUSES = ("failed", "aborted", "cancelled", "stopped")

# A completed run whose user verdict is 'reject' (#1898) finished on the
# machine but produced scrap — everywhere a project counts good parts, that
# run must not contribute. NULL verdict (never asked / not answered) counts
# as good, matching behaviour before the feature existed.
_NOT_REJECTED = or_(PrintLogEntry.user_verdict.is_(None), PrintLogEntry.user_verdict != "reject")

# Soft-deleted archives (#1343) keep their row — and therefore their
# ``project_id`` — after their files have been removed from disk, so that global
# Quick Stats can still count their filament / time / cost. Nothing in this
# module filtered on that, which left deleted prints listed on the project with
# thumbnails pointing at files that no longer exist, and no way to unassign them
# (the only unassign UI lives on the Archives page, which correctly hides them)
# — #2731.
#
# Every project-scoped query filters them out, counts included: a project that
# lists 11 prints must not claim 12. That is a deliberate divergence from the
# global Quick Stats behaviour, where the whole point of the soft delete is that
# the contribution survives. A project is a piece of work with a definite
# membership, not a lifetime total, so a print the user deleted has left it.
_LIVE_ARCHIVE = PrintArchive.deleted_at.is_(None)


@dataclass
class _ProjectTotals:
    """Raw per-project aggregates, before targets turn them into percentages.

    Kept addable so a master project's numbers are the plain sum of its own
    and every descendant's (#1264) — no second set of SQL that could drift
    from the single-project path.
    """

    total_runs: int = 0
    total_items: int = 0
    completed_items: int = 0
    failed_runs: int = 0
    total_time_seconds: float = 0.0
    total_filament_grams: float = 0.0
    filament_cost: float = 0.0
    energy_kwh: float = 0.0
    energy_cost: float = 0.0
    queued_prints: int = 0
    in_progress_prints: int = 0
    bom_total_items: int = 0
    bom_completed_items: int = 0
    bom_cost: float = 0.0

    def __add__(self, other: "_ProjectTotals") -> "_ProjectTotals":
        return _ProjectTotals(
            **{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(_ProjectTotals)}
        )


async def _load_totals(db: AsyncSession, project_ids: Sequence[int]) -> dict[int, _ProjectTotals]:
    """Aggregate prints, queue and BOM for several projects at once.

    Grouped rather than one round trip per project because a master project
    has to aggregate its whole subtree, and the sub-project list shows each
    branch's own roll-up alongside it (#1264).

    Aggregates from ``print_log_entries`` joined to ``print_archives`` so
    every actual run contributes — pre-fix this counted ``print_archives``
    (one row per file), which under-reported every reprint by collapsing
    runs back into the source file (#1593). The Archive Print Log view
    already drives off the same source (``archives.py::list_archives_slim``),
    so project stats now stay aligned with the per-archive numbers.

    Orphan log entries (``archive_id IS NULL`` after archive deletion via
    ``ON DELETE SET NULL``) are excluded by the inner join — they can't
    be attributed to a project.

    Projects with nothing recorded are absent from every grouped result, so
    the caller gets a zeroed ``_ProjectTotals`` for them rather than a KeyError.
    """
    totals: dict[int, _ProjectTotals] = {pid: _ProjectTotals() for pid in project_ids}
    if not totals:
        return totals

    # Per-run aggregates. Each run's duration, filament, cost, and energy come
    # from the log row, not the source archive — so multi-plate 3MFs and
    # reprints both count correctly. The total/completed/failed splits are all
    # per-run too: quantity is summed per run, while failures are counted as
    # runs rather than parts.
    log_rows = await db.execute(
        select(
            PrintArchive.project_id.label("project_id"),
            func.count(PrintLogEntry.id).label("total_runs"),
            func.coalesce(func.sum(PrintLogEntry.duration_seconds), 0).label("total_time"),
            func.coalesce(func.sum(PrintLogEntry.filament_used_grams), 0).label("total_filament"),
            func.coalesce(func.sum(PrintLogEntry.cost), 0).label("total_filament_cost"),
            func.coalesce(func.sum(PrintLogEntry.energy_kwh), 0).label("total_energy"),
            func.coalesce(func.sum(PrintLogEntry.energy_cost), 0).label("total_energy_cost"),
            func.coalesce(func.sum(PrintArchive.quantity), 0).label("total_items"),
            # A completed run the user marked as reject (#1898) produced no
            # usable parts — keep it out of the good-parts count.
            func.coalesce(
                func.sum(
                    case((and_(PrintLogEntry.status == "completed", _NOT_REJECTED), PrintArchive.quantity), else_=0)
                ),
                0,
            ).label("completed_items"),
            func.coalesce(
                func.sum(case((PrintLogEntry.status.in_(_FAILURE_STATUSES), 1), else_=0)),
                0,
            ).label("failed_runs"),
        )
        .join(PrintArchive, PrintArchive.id == PrintLogEntry.archive_id)
        .where(PrintArchive.project_id.in_(list(totals)), _LIVE_ARCHIVE)
        .group_by(PrintArchive.project_id)
    )
    for row in log_rows:
        entry = totals[row.project_id]
        entry.total_runs = int(row.total_runs or 0)
        entry.total_time_seconds = float(row.total_time or 0)
        entry.total_filament_grams = float(row.total_filament or 0)
        entry.filament_cost = float(row.total_filament_cost or 0)
        entry.energy_kwh = float(row.total_energy or 0)
        entry.energy_cost = float(row.total_energy_cost or 0)
        entry.total_items = int(row.total_items or 0)
        entry.completed_items = int(row.completed_items or 0)
        entry.failed_runs = int(row.failed_runs or 0)

    queue_rows = await db.execute(
        select(
            PrintQueueItem.project_id.label("project_id"),
            func.coalesce(func.sum(case((PrintQueueItem.status == "pending", 1), else_=0)), 0).label("queued"),
            func.coalesce(func.sum(case((PrintQueueItem.status == "printing", 1), else_=0)), 0).label("in_progress"),
        )
        .where(PrintQueueItem.project_id.in_(list(totals)))
        .group_by(PrintQueueItem.project_id)
    )
    for row in queue_rows:
        entry = totals[row.project_id]
        entry.queued_prints = int(row.queued or 0)
        entry.in_progress_prints = int(row.in_progress or 0)

    bom_rows = await db.execute(
        select(
            ProjectBOMItem.project_id.label("project_id"),
            func.count(ProjectBOMItem.id).label("total"),
            func.sum(case((ProjectBOMItem.quantity_acquired >= ProjectBOMItem.quantity_needed, 1), else_=0)).label(
                "completed"
            ),
            func.coalesce(func.sum(ProjectBOMItem.unit_price * ProjectBOMItem.quantity_needed), 0).label("bom_cost"),
        )
        .where(ProjectBOMItem.project_id.in_(list(totals)))
        .group_by(ProjectBOMItem.project_id)
    )
    for row in bom_rows:
        entry = totals[row.project_id]
        entry.bom_total_items = int(row.total or 0)
        entry.bom_completed_items = int(row.completed or 0)
        entry.bom_cost = float(row.bom_cost or 0)

    return totals


def _stats_from_totals(
    totals: _ProjectTotals, target_count: int | None = None, target_parts_count: int | None = None
) -> ProjectStats:
    """Turn raw aggregates into the response shape, applying the targets."""
    # Calculate progress for plates (target_count vs total_archives)
    progress_percent = None
    remaining_prints = None
    if target_count and target_count > 0:
        progress_percent = round((totals.total_runs / target_count) * 100, 1)
        remaining_prints = max(0, target_count - totals.total_runs)

    # Calculate progress for parts (target_parts_count vs completed_items)
    parts_progress_percent = None
    remaining_parts = None
    if target_parts_count and target_parts_count > 0:
        parts_progress_percent = round((totals.completed_items / target_parts_count) * 100, 1)
        remaining_parts = max(0, target_parts_count - totals.completed_items)

    return ProjectStats(
        total_archives=totals.total_runs,
        total_items=totals.total_items,
        completed_prints=totals.completed_items,  # Sum of quantities for completed prints
        failed_prints=totals.failed_runs,
        queued_prints=totals.queued_prints,
        in_progress_prints=totals.in_progress_prints,
        total_print_time_hours=round(totals.total_time_seconds / 3600, 2),
        total_filament_grams=round(totals.total_filament_grams, 2),
        progress_percent=progress_percent,
        parts_progress_percent=parts_progress_percent,
        estimated_cost=round(totals.filament_cost, 2),
        total_energy_kwh=round(totals.energy_kwh, 3),
        total_energy_cost=round(totals.energy_cost, 3),
        remaining_prints=remaining_prints,
        remaining_parts=remaining_parts,
        bom_total_items=totals.bom_total_items,
        bom_completed_items=totals.bom_completed_items,
        bom_cost=round(totals.bom_cost, 2),
    )


async def compute_project_stats(
    db: AsyncSession, project_id: int, target_count: int | None = None, target_parts_count: int | None = None
) -> ProjectStats:
    """Compute statistics for a single project, excluding any sub-projects.

    Sub-project roll-ups go through ``compute_subtree_stats`` instead. This
    stays own-prints-only on purpose: it is what every existing caller means
    by "this project's numbers", and widening it would silently restate the
    figures of anyone who had already nested projects over the API.
    """
    totals = (await _load_totals(db, [project_id]))[project_id]
    return _stats_from_totals(totals, target_count, target_parts_count)


def _descendants_of(children: dict[int, list[int]], root_id: int) -> list[int]:
    """Every project nested under ``root_id``, at any depth, root excluded.

    Walked in Python off one already-fetched parent map rather than a recursive
    CTE, so SQLite and PostgreSQL stay on identical code paths.

    ``seen`` is not belt-and-braces. ``update_project`` only ever rejected a
    project as its own *direct* parent, so any database written before that
    guard was widened can hold A -> B -> A, and an unguarded walk over one
    would never terminate.
    """
    found: list[int] = []
    seen = {root_id}
    stack = [root_id]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child in seen:
                continue
            seen.add(child)
            found.append(child)
            stack.append(child)
    return found


async def _project_descendants(db: AsyncSession, root_id: int) -> list[int]:
    """``_descendants_of`` for callers that only need the ids, not the totals."""
    rows = (await db.execute(select(Project.id, Project.parent_id).where(Project.parent_id.is_not(None)))).all()
    children: dict[int, list[int]] = {}
    for pid, parent_id in rows:
        children.setdefault(parent_id, []).append(pid)
    return _descendants_of(children, root_id)


@dataclass
class _SubtreeReport:
    """What the detail endpoint needs to describe a project and its tree."""

    descendant_count: int
    # None when the project has no sub-projects: the roll-up would be identical
    # to the project's own stats, and the UI uses its absence to stay quiet
    # rather than showing a second, equal set of numbers.
    rollup: ProjectStats | None
    child_previews: list[ProjectChildPreview]


async def compute_subtree_stats(db: AsyncSession, root_id: int) -> _SubtreeReport:
    """Roll a project's own numbers up with every sub-project beneath it (#1264).

    Four queries regardless of tree size or depth: one for the parent map, then
    the three grouped aggregates in ``_load_totals`` covering the whole subtree
    at once. Each direct child's preview carries *its* branch's roll-up, so the
    listed rows add up to the master's total minus the master's own prints.
    """
    rows = (
        await db.execute(
            select(
                Project.id,
                Project.parent_id,
                Project.name,
                Project.color,
                Project.status,
                Project.target_count,
                Project.target_parts_count,
            )
        )
    ).all()
    by_id = {row.id: row for row in rows}
    children: dict[int, list[int]] = {}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row.id)

    descendants = _descendants_of(children, root_id)
    if not descendants:
        return _SubtreeReport(descendant_count=0, rollup=None, child_previews=[])

    totals = await _load_totals(db, [root_id, *descendants])

    def branch(node_id: int) -> tuple[_ProjectTotals, list[int]]:
        """Totals for ``node_id`` plus everything under it, and that id list."""
        ids = [node_id, *_descendants_of(children, node_id)]
        summed = _ProjectTotals()
        for pid in ids:
            summed = summed + totals[pid]
        return summed, ids

    def summed_target(ids: Sequence[int], attr: str) -> int | None:
        """Targets add up across the tree; all-unset stays unset, not zero."""
        total = sum(getattr(by_id[pid], attr) or 0 for pid in ids)
        return total or None

    subtree_ids = [root_id, *descendants]
    root_totals, _ = branch(root_id)
    rollup = _stats_from_totals(
        root_totals,
        summed_target(subtree_ids, "target_count"),
        summed_target(subtree_ids, "target_parts_count"),
    )

    previews: list[ProjectChildPreview] = []
    for child_id in sorted(children.get(root_id, ()), key=lambda cid: by_id[cid].name):
        child = by_id[child_id]
        child_totals, child_ids = branch(child_id)
        # Progress here is runs-against-plate-target, matching what the child's
        # own page reports. It used to be completed *quantities* against the
        # same target, so a row's percentage disagreed with the page it linked
        # to.
        child_stats = _stats_from_totals(child_totals, summed_target(child_ids, "target_count"))
        previews.append(
            ProjectChildPreview(
                id=child.id,
                name=child.name,
                color=child.color,
                status=child.status,
                progress_percent=child_stats.progress_percent,
                descendant_count=len(child_ids) - 1,
                total_archives=child_stats.total_archives,
                completed_prints=child_stats.completed_prints,
                total_print_time_hours=child_stats.total_print_time_hours,
                total_filament_grams=child_stats.total_filament_grams,
                total_cost=round(child_stats.estimated_cost + child_stats.total_energy_cost + child_stats.bom_cost, 2),
            )
        )

    return _SubtreeReport(descendant_count=len(descendants), rollup=rollup, child_previews=previews)


@router.get("", response_model=list[ProjectListResponse])
@router.get("/", response_model=list[ProjectListResponse])
async def list_projects(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = _require_project_read(),
):
    """List all projects with basic stats."""
    access_map = await _compute_project_access_map(db, current_user)

    query = select(Project, User.username).outerjoin(User, Project.created_by_id == User.id)
    if status:
        query = query.where(Project.status == status)
    query = query.order_by(Project.updated_at.desc())

    result = await db.execute(query)
    project_rows = result.all()

    # Direct sub-project counts for every project in one pass (#1264). Counted
    # across all projects rather than the filtered page: a sub-project hidden
    # by the status filter is still a sub-project, and a parent that claimed
    # none would invite deleting it as if nothing hung off it.
    child_counts = dict(
        (
            await db.execute(
                select(Project.parent_id, func.count(Project.id))
                .where(Project.parent_id.is_not(None))
                .group_by(Project.parent_id)
            )
        ).all()
    )

    # Compute quick stats for each project. Same per-run aggregation as
    # ``compute_project_stats`` — counts and quantities come from
    # ``print_log_entries`` joined to ``print_archives`` so reprints and
    # multi-plate prints contribute every run, not just the source file
    # (#1593). Quick stats and the full stats endpoint must agree.
    response = []
    for project, creator_username in project_rows:
        acc = access_map.get(project.id, _ProjectAccess())
        if not acc.can_read:
            continue

        log_quick_result = await db.execute(
            select(
                func.count(PrintLogEntry.id).label("archive_count"),
                func.coalesce(func.sum(PrintArchive.quantity), 0).label("total_items"),
                func.coalesce(
                    func.sum(
                        case((and_(PrintLogEntry.status == "completed", _NOT_REJECTED), PrintArchive.quantity), else_=0)
                    ),
                    0,
                ).label("completed_count"),
                func.coalesce(
                    func.sum(case((PrintLogEntry.status.in_(_FAILURE_STATUSES), 1), else_=0)),
                    0,
                ).label("failed_count"),
            )
            .join(PrintArchive, PrintArchive.id == PrintLogEntry.archive_id)
            .where(PrintArchive.project_id == project.id, _LIVE_ARCHIVE)
        )
        log_quick = log_quick_result.first()
        archive_count = int(log_quick.archive_count or 0)
        total_items = int(log_quick.total_items or 0)
        completed_count = int(log_quick.completed_count or 0)
        failed_count = int(log_quick.failed_count or 0)

        # Get queue count
        queue_count_result = await db.execute(
            select(func.count(PrintQueueItem.id)).where(
                PrintQueueItem.project_id == project.id,
                PrintQueueItem.status.in_(["pending", "printing"]),
            )
        )
        queue_count = queue_count_result.scalar() or 0

        # Plates progress: archive_count / target_count
        progress_percent = None
        if project.target_count and project.target_count > 0:
            progress_percent = round((archive_count / project.target_count) * 100, 1)

        # Get archive previews (up to 6 most recent)
        archives_result = await db.execute(
            select(PrintArchive)
            .where(PrintArchive.project_id == project.id, _LIVE_ARCHIVE)
            .order_by(PrintArchive.created_at.desc())
            .limit(6)
        )
        archives = archives_result.scalars().all()
        archive_previews = [
            ArchivePreview(
                id=a.id,
                print_name=a.print_name,
                thumbnail_path=a.thumbnail_path,
                status=a.status,
                filament_type=a.filament_type,
                filament_color=a.filament_color,
            )
            for a in archives
        ]

        response.append(
            ProjectListResponse(
                id=project.id,
                name=project.name,
                description=project.description,
                color=project.color,
                status=project.status,
                target_count=project.target_count,
                target_parts_count=project.target_parts_count,
                target_sets=project.target_sets,
                budget=project.budget,
                tags=project.tags,
                due_date=project.due_date,
                priority=project.priority,
                created_at=project.created_at,
                archive_count=archive_count,
                total_items=total_items,
                completed_count=completed_count,
                failed_count=failed_count,
                queue_count=queue_count,
                progress_percent=progress_percent,
                parent_id=project.parent_id,
                child_count=child_counts.get(project.id, 0),
                archives=archive_previews,
                url=project.url,
                cover_image_filename=project.cover_image_filename,
                created_by_id=project.created_by_id,
                created_by_username=creator_username,
                can_read=acc.can_read,
                can_write=acc.can_write,
                can_manage=acc.can_manage,
                can_delete=acc.can_delete,
                is_shared=acc.is_shared,
            )
        )

    return response


@router.post("/", response_model=ProjectResponse)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Create a new project."""
    # Verify parent exists if specified
    parent_name = None
    if data.parent_id:
        parent_result = await db.execute(select(Project).where(Project.id == data.parent_id))
        parent = parent_result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=400, detail="Parent project not found")
        if current_user is not None and not current_user.is_admin:
            access_map = await _compute_project_access_map(db, current_user)
            parent_acc = access_map.get(data.parent_id, _ProjectAccess())
            if not parent_acc.can_write:
                raise HTTPException(
                    status_code=403,
                    detail="You do not have write permission on the parent project",
                )
        parent_name = parent.name

    project = Project(
        name=data.name,
        description=data.description,
        color=data.color,
        target_count=data.target_count,
        target_parts_count=data.target_parts_count,
        target_sets=data.target_sets,
        notes=data.notes,
        tags=data.tags,
        due_date=data.due_date,
        priority=data.priority,
        budget=data.budget,
        parent_id=data.parent_id,
        url=data.url,
        created_by_id=current_user.id if current_user else None,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        target_sets=project.target_sets,
        notes=project.notes,
        attachments=project.attachments,
        url=project.url,
        cover_image_filename=project.cover_image_filename,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=parent_name,
        children=[],
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
        created_by_id=project.created_by_id,
        created_by_username=current_user.username if current_user else None,
        can_read=True,
        can_write=True,
        can_manage=True,
    )


# ============ Phase 8: Template Endpoints (Static routes BEFORE dynamic {project_id}) ============


@router.get("/templates", response_model=list[ProjectListResponse])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = _require_project_read(),
):
    """List all project templates."""
    access_map = await _compute_project_access_map(db, current_user)
    result = await db.execute(
        select(Project, User.username)
        .outerjoin(User, Project.created_by_id == User.id)
        .where(Project.is_template.is_(True))
        .order_by(Project.name)
    )
    template_rows = result.all()

    response = []
    for project, creator_username in template_rows:
        acc = access_map.get(project.id, _ProjectAccess())
        if not acc.can_read:
            continue
        # Get archive count
        archive_count_result = await db.execute(
            select(func.count(PrintArchive.id)).where(PrintArchive.project_id == project.id, _LIVE_ARCHIVE)
        )
        archive_count = archive_count_result.scalar() or 0

        response.append(
            ProjectListResponse(
                id=project.id,
                name=project.name,
                description=project.description,
                color=project.color,
                status=project.status,
                target_count=project.target_count,
                target_parts_count=project.target_parts_count,
                target_sets=project.target_sets,
                budget=project.budget,
                tags=project.tags,
                due_date=project.due_date,
                priority=project.priority,
                created_at=project.created_at,
                archive_count=archive_count,
                queue_count=0,
                progress_percent=None,
                archives=[],
                url=project.url,
                cover_image_filename=project.cover_image_filename,
                created_by_id=project.created_by_id,
                created_by_username=creator_username,
                can_read=acc.can_read,
                can_write=acc.can_write,
                can_manage=acc.can_manage,
                can_delete=acc.can_delete,
                is_shared=acc.is_shared,
            )
        )

    return response


@router.post("/from-template/{template_id}", response_model=ProjectResponse)
async def create_project_from_template(
    template_id: int,
    name: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Create a new project from a template."""
    result = await db.execute(select(Project).where(Project.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if current_user is not None and not current_user.is_admin:
        access_map = await _compute_project_access_map(db, current_user)
        t_acc = access_map.get(template_id, _ProjectAccess())
        if not t_acc.can_read:
            raise HTTPException(status_code=404, detail="Template not found")

    if not template.is_template:
        raise HTTPException(status_code=400, detail="Project is not a template")

    # Create new project
    project = Project(
        name=name or template.name.replace(" (Template)", ""),
        description=template.description,
        color=template.color,
        target_count=template.target_count,
        target_parts_count=template.target_parts_count,
        target_sets=template.target_sets,
        notes=template.notes,
        tags=template.tags,
        priority=template.priority,
        budget=template.budget,
        is_template=False,
        template_source_id=template.id,
        url=template.url,
        created_by_id=current_user.id if current_user else None,
    )
    db.add(project)
    await db.flush()

    # Copy BOM items
    bom_result = await db.execute(select(ProjectBOMItem).where(ProjectBOMItem.project_id == template_id))
    bom_items = bom_result.scalars().all()

    for item in bom_items:
        new_item = ProjectBOMItem(
            project_id=project.id,
            name=item.name,
            quantity_needed=item.quantity_needed,
            quantity_acquired=0,
            unit_price=item.unit_price,
            sourcing_url=item.sourcing_url,
            stl_filename=item.stl_filename,
            remarks=item.remarks,
            sort_order=item.sort_order,
        )
        db.add(new_item)

    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        target_sets=project.target_sets,
        notes=project.notes,
        attachments=project.attachments,
        url=project.url,
        cover_image_filename=project.cover_image_filename,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=None,
        children=[],
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
        created_by_id=project.created_by_id,
        created_by_username=current_user.username if current_user else None,
        can_read=True,
        can_write=True,
        can_manage=True,
    )


# ============ Dynamic {project_id} Routes ============


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """Get a project by ID with detailed stats."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project.id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    # Get parent name
    parent_name = None
    if project.parent_id:
        parent_result = await db.execute(select(Project.name).where(Project.id == project.parent_id))
        parent_name = parent_result.scalar()

    subtree = await compute_subtree_stats(db, project.id)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)
    creator_username = await _get_creator_username(db, project.created_by_id)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        target_sets=project.target_sets,
        notes=project.notes,
        attachments=project.attachments,
        url=project.url,
        cover_image_filename=project.cover_image_filename,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=parent_name,
        children=subtree.child_previews,
        descendant_count=subtree.descendant_count,
        created_by_id=project.created_by_id,
        created_by_username=creator_username,
        can_read=acc.can_read,
        can_write=acc.can_write,
        can_manage=acc.can_manage,
        can_delete=acc.can_delete,
        is_shared=acc.is_shared,
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
        rollup_stats=subtree.rollup,
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Update a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project.id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    # Update fields if provided
    if data.name is not None:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    if data.color is not None:
        project.color = data.color
    if data.status is not None:
        if data.status not in ["active", "completed", "archived"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        project.status = data.status
    if data.target_count is not None:
        project.target_count = data.target_count
    if data.target_parts_count is not None:
        project.target_parts_count = data.target_parts_count
    # Sent-but-null clears the copies-per-file target (#1897); omitted leaves it
    # alone (same #2536 semantics as tags/due_date below).
    if "target_sets" in data.model_fields_set:
        project.target_sets = data.target_sets
    if data.notes is not None:
        project.notes = data.notes
    # Sent-but-null clears the field; omitted leaves it alone. Guarding on
    # ``is not None`` would make an emptied tags field or a removed due date
    # silently revert to the stored value (#2536).
    if "tags" in data.model_fields_set:
        project.tags = data.tags
    if "due_date" in data.model_fields_set:
        project.due_date = data.due_date
    if data.priority is not None:
        if data.priority not in ["low", "normal", "high", "urgent"]:
            raise HTTPException(status_code=400, detail="Invalid priority")
        project.priority = data.priority
    if "budget" in data.model_fields_set:
        project.budget = data.budget
    if "url" in data.model_fields_set:
        # Pydantic validator already guarantees http(s) prefix or None.
        project.url = data.url
    if data.parent_id is not None:
        # Verify parent exists and prevent circular reference
        if data.parent_id == project_id:
            raise HTTPException(status_code=400, detail="Project cannot be its own parent")
        if data.parent_id != 0:  # 0 means remove parent
            parent_result = await db.execute(select(Project).where(Project.id == data.parent_id))
            if not parent_result.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Parent project not found")
            parent_acc = access_map.get(data.parent_id, _ProjectAccess())
            if not parent_acc.can_write:
                raise HTTPException(status_code=403, detail="You do not have write access to the parent project")
            # Refusing only the project itself left A -> B -> A reachable in two
            # calls, and a cycle has no root to roll figures up to — the walk in
            # ``_descendants_of`` would revisit forever without its seen-set
            # (#1264).
            if data.parent_id in await _project_descendants(db, project_id):
                raise HTTPException(status_code=400, detail="Project cannot be moved under one of its own sub-projects")
            project.parent_id = data.parent_id
        else:
            project.parent_id = None

    await db.flush()
    await db.refresh(project)

    # Get parent name
    parent_name = None
    if project.parent_id:
        parent_result = await db.execute(select(Project.name).where(Project.id == project.parent_id))
        parent_name = parent_result.scalar()

    subtree = await compute_subtree_stats(db, project.id)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)
    creator_username = await _get_creator_username(db, project.created_by_id)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        target_sets=project.target_sets,
        notes=project.notes,
        attachments=project.attachments,
        url=project.url,
        cover_image_filename=project.cover_image_filename,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=parent_name,
        children=subtree.child_previews,
        descendant_count=subtree.descendant_count,
        created_by_id=project.created_by_id,
        created_by_username=creator_username,
        can_read=acc.can_read,
        can_write=acc.can_write,
        can_manage=acc.can_manage,
        can_delete=acc.can_delete,
        is_shared=acc.is_shared,
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
        rollup_stats=subtree.rollup,
    )


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_delete(),
):
    """Delete a project. Archives and queue items will have project_id set to NULL."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project.id, _ProjectAccess())
    if not acc.can_delete:
        raise HTTPException(status_code=403, detail="Only the project owner or an administrator can delete this project")

    # Sub-projects move up to the deleted project's own parent rather than
    # being cut loose at the top level, so deleting a middle layer collapses
    # the tree by one instead of scattering a branch (#1264). Left to the ORM
    # this would null their parent_id instead, which loses the grandparent.
    await db.execute(update(Project).where(Project.parent_id == project_id).values(parent_id=project.parent_id))

    await db.delete(project)

    return {"message": "Project deleted"}


@router.get("/{project_id}/permissions", response_model=ProjectPermissionsListResponse)
async def get_project_permissions(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Get explicit user/group sharing permissions for a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project.id, _ProjectAccess())
    if not acc.can_manage:
        raise HTTPException(
            status_code=403,
            detail="Only the project owner or an administrator can manage project permissions",
        )

    return await _build_project_permissions_response(db, project)


@router.put("/{project_id}/permissions", response_model=ProjectPermissionsListResponse)
async def update_project_permissions(
    project_id: int,
    data: ProjectPermissionsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Replace explicit user/group sharing permissions on a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project.id, _ProjectAccess())
    if not acc.can_manage:
        raise HTTPException(
            status_code=403,
            detail="Only the project owner or an administrator can manage project permissions",
        )

    seen_users: set[int] = set()
    seen_groups: set[int] = set()
    validated_grants = []

    for grant in data.permissions:
        if (grant.user_id is None) == (grant.group_id is None):
            raise HTTPException(
                status_code=400,
                detail="Each permission entry must specify either user_id or group_id, not both",
            )
        if not grant.can_read and not grant.can_write:
            continue

        if grant.user_id is not None:
            if grant.user_id in seen_users:
                continue
            seen_users.add(grant.user_id)
            u_exists = (await db.execute(select(User.id).where(User.id == grant.user_id))).scalar_one_or_none()
            if u_exists is None:
                raise HTTPException(status_code=400, detail=f"User {grant.user_id} not found")
        else:
            assert grant.group_id is not None
            if grant.group_id in seen_groups:
                continue
            seen_groups.add(grant.group_id)
            g_exists = (await db.execute(select(Group.id).where(Group.id == grant.group_id))).scalar_one_or_none()
            if g_exists is None:
                raise HTTPException(status_code=400, detail=f"Group {grant.group_id} not found")

        validated_grants.append(grant)

    await db.execute(delete(ProjectPermission).where(ProjectPermission.project_id == project_id))
    for grant in validated_grants:
        db.add(
            ProjectPermission(
                project_id=project_id,
                user_id=grant.user_id,
                group_id=grant.group_id,
                can_read=True if (grant.can_read or grant.can_write) else False,
                can_write=grant.can_write,
            )
        )
    await db.commit()
    await db.refresh(project)

    return await _build_project_permissions_response(db, project)


@router.get("/{project_id}/archives")
async def list_project_archives(
    project_id: int,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """List archives in a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    # Get archives with both ``project`` and ``created_by`` eagerly loaded.
    # ``archive_to_response`` accesses ``archive.created_by.username`` to
    # surface the creator on the archive card; without selectinload that's
    # a lazy attribute access on a closed async session, which throws
    # ``MissingGreenlet`` and produces a 500. ``ArchiveService.list_archives``
    # already loads both — this route just got out of step.
    query = (
        select(PrintArchive)
        .options(selectinload(PrintArchive.project), selectinload(PrintArchive.created_by))
        .where(PrintArchive.project_id == project_id, _LIVE_ARCHIVE)
        .order_by(PrintArchive.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    archives = result.scalars().all()

    # Import the response converter from archives module
    from backend.app.api.routes.archives import _load_run_aggregates, archive_to_response

    # Load run aggregates so multi-run archives' time/accuracy badge is
    # suppressed consistently with the main archives list endpoint (#1608).
    run_aggregates = await _load_run_aggregates(db, [a.id for a in archives])

    return [archive_to_response(a, run_aggregate=run_aggregates.get(a.id)) for a in archives]


@router.get("/{project_id}/queue")
async def list_project_queue(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """List queue items in a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    # Get queue items
    query = select(PrintQueueItem).where(PrintQueueItem.project_id == project_id).order_by(PrintQueueItem.position)
    result = await db.execute(query)
    items = result.scalars().all()

    return items


@router.get("/{project_id}/file-progress", response_model=list[ProjectFileProgress])
async def get_project_file_progress(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """Completed-run counts per library file inside a project (#1897).

    Counts completed ``PrintLogEntry`` rows (same source as the aggregate
    project stats) of archives attributed to this project, and maps each run to
    one of the project's library files — the files living in folders linked to
    the project, the same set the project detail page renders.

    A run is attributed to exactly one file, by the strongest available match:
    1. ``archive.library_file_id`` (stamped at queue dispatch since #1897),
    2. content hash (covers historical rows),
    3. filename (covers hash drift, e.g. re-sliced uploads of the same name).
    Files with no completed runs are omitted — the frontend treats absence as 0.
    """
    result = await db.execute(select(Project.id).where(Project.id == project_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    files_result = await db.execute(
        select(LibraryFile.id, LibraryFile.file_hash, LibraryFile.filename)
        .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
        .where(LibraryFolder.project_id == project_id, LibraryFile.deleted_at.is_(None))
    )
    file_rows = files_result.all()
    if not file_rows:
        return []

    # First match wins within each tier, so iteration order (file id) is stable
    # when duplicates share a hash or filename.
    by_id = {fid for fid, _, _ in file_rows}
    by_hash: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for fid, fhash, fname in file_rows:
        if fhash and fhash not in by_hash:
            by_hash[fhash] = fid
        if fname not in by_name:
            by_name[fname] = fid

    runs_result = await db.execute(
        select(
            PrintArchive.library_file_id,
            PrintArchive.content_hash,
            PrintArchive.filename,
            func.count(PrintLogEntry.id),
        )
        .join(PrintArchive, PrintArchive.id == PrintLogEntry.archive_id)
        .where(
            PrintArchive.project_id == project_id,
            PrintLogEntry.status == "completed",
            _NOT_REJECTED,
            _LIVE_ARCHIVE,
        )
        .group_by(PrintArchive.library_file_id, PrintArchive.content_hash, PrintArchive.filename)
    )

    counts: dict[int, int] = {}
    for lib_file_id, content_hash, filename, run_count in runs_result.all():
        if lib_file_id in by_id:
            fid = lib_file_id
        elif content_hash and content_hash in by_hash:
            fid = by_hash[content_hash]
        elif filename in by_name:
            fid = by_name[filename]
        else:
            continue
        counts[fid] = counts.get(fid, 0) + run_count

    return [ProjectFileProgress(file_id=fid, completed_count=n) for fid, n in sorted(counts.items())]


@router.post("/{project_id}/add-archives")
async def add_archives_to_project(
    project_id: int,
    data: BatchAddArchives,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Batch add archives to a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    # Update archives
    updated = 0
    for archive_id in data.archive_ids:
        result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
        archive = result.scalar_one_or_none()
        if archive:
            archive.project_id = project_id
            updated += 1

    return {"message": f"Added {updated} archives to project"}


@router.post("/{project_id}/add-queue")
async def add_queue_items_to_project(
    project_id: int,
    data: BatchAddQueueItems,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Batch add queue items to a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    # Update queue items
    updated = 0
    for item_id in data.queue_item_ids:
        result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
        item = result.scalar_one_or_none()
        if item:
            item.project_id = project_id
            updated += 1

    return {"message": f"Added {updated} queue items to project"}


@router.post("/{project_id}/remove-archives")
async def remove_archives_from_project(
    project_id: int,
    data: BatchAddArchives,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Remove archives from a project (sets project_id to NULL)."""
    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    updated = 0
    for archive_id in data.archive_ids:
        result = await db.execute(
            select(PrintArchive).where(
                PrintArchive.id == archive_id,
                PrintArchive.project_id == project_id,
            )
        )
        archive = result.scalar_one_or_none()
        if archive:
            archive.project_id = None
            updated += 1

    return {"message": f"Removed {updated} archives from project"}


def get_project_attachments_dir(project_id: int) -> Path:
    """Get the attachments directory for a project."""
    base_dir = Path(settings.archive_dir)
    return base_dir / "projects" / str(project_id) / "attachments"


# Cover-image upload accepts only common web-renderable image types (#1155).
# Subset of ALLOWED_ATTACHMENT_EXTENSIONS minus .svg/.ico because those don't
# render well as a card thumbnail.
COVER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
COVER_IMAGE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


# Allowed file extensions for attachments
ALLOWED_ATTACHMENT_EXTENSIONS = {
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".ico",
    # Documents
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".txt",
    ".rtf",
    ".csv",
    ".md",
    # 3D/CAD files
    ".stl",
    ".obj",
    ".3mf",
    ".step",
    ".stp",
    ".iges",
    ".igs",
    ".f3d",
    ".scad",
    # Archives
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    # Code/scripts (for Klipper macros, scripts, etc.)
    ".py",
    ".sh",
    ".cfg",
    ".conf",
    ".gcode",
    ".ini",
    # Other common formats
    ".json",
    ".xml",
    ".yaml",
    ".yml",
}


@router.post("/{project_id}/attachments")
async def upload_attachment(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Upload an attachment to a project."""
    logger.info("=== UPLOAD START: %s for project %s ===", file.filename, project_id)

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    # Validate file extension
    original_name = file.filename or "unknown"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not supported. Allowed: images, PDFs, documents, STL, 3MF, archives.",
        )

    # Create attachments directory
    attachments_dir = get_project_attachments_dir(project_id)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = attachments_dir / unique_filename  # SEC-PATH-OK: unique_filename = uuid.uuid4().hex + ext

    # Save file
    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        logger.info("=== FILE SAVED: %s, size: %s ===", file_path, len(content))
    except Exception as e:
        logger.error("Failed to save attachment: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save attachment")

    # Update project attachments JSON
    attachments = list(project.attachments or [])
    new_attachment = {
        "filename": unique_filename,
        "original_name": original_name,
        "size": len(content),
        "uploaded_at": datetime.now().isoformat(),
    }
    attachments.append(new_attachment)

    # Simple ORM update
    project.attachments = attachments
    db.add(project)  # Explicitly add to session

    logger.info("=== BEFORE COMMIT: %s attachments ===", len(attachments))

    await db.flush()
    await db.commit()

    logger.info("=== AFTER COMMIT ===")

    # Verify by re-querying
    result = await db.execute(select(Project).where(Project.id == project_id))
    fresh_project = result.scalar_one()

    logger.info("=== VERIFIED: %s attachments ===", len(fresh_project.attachments or []))

    return {
        "status": "success",
        "filename": unique_filename,
        "original_name": original_name,
        "attachments": fresh_project.attachments,
    }


@router.get("/{project_id}/attachments/{filename}")
async def download_attachment(
    project_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """Download an attachment from a project."""
    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename or not filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    # Verify attachment exists in project
    attachments = project.attachments or []
    attachment = next((a for a in attachments if a.get("filename") == filename), None)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Check file exists
    file_path = (
        get_project_attachments_dir(project_id) / filename
    )  # SEC-PATH-OK: filename validated above (no /, \\, .., empty) + attachment membership check
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file not found")

    return FileResponse(
        file_path,
        filename=attachment.get("original_name", filename),
        media_type="application/octet-stream",
    )


@router.delete("/{project_id}/attachments/{filename}")
async def delete_attachment(
    project_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Delete an attachment from a project."""
    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename or not filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    # Find and remove attachment from list
    attachments = project.attachments or []
    attachment = next((a for a in attachments if a.get("filename") == filename), None)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Remove from list
    attachments = [a for a in attachments if a.get("filename") != filename]
    project.attachments = attachments if attachments else None

    # Delete file
    file_path = (
        get_project_attachments_dir(project_id) / filename
    )  # SEC-PATH-OK: filename validated above (no /, \\, .., empty) + attachment membership check
    if file_path.exists():
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning("Failed to delete attachment file: %s", e)

    await db.flush()
    await db.refresh(project)

    return {
        "status": "success",
        "message": "Attachment deleted",
        "attachments": project.attachments,
    }


# ============ #1155: Cover image ============


@router.post("/{project_id}/cover-image")
async def upload_project_cover_image(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Upload (or replace) the project's cover image (#1155).

    Stored alongside other attachments but tracked via Project.cover_image_filename
    so swap/delete operations don't touch the attachments list. Replaces any
    existing cover image — the prior file is deleted on disk before the new one
    lands so a stuck filesystem reference can't accumulate orphaned images.
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    original_name = file.filename or "cover"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in COVER_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Cover image must be one of {sorted(COVER_IMAGE_EXTENSIONS)}",
        )

    attachments_dir = get_project_attachments_dir(project_id)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    # Remove the previous cover-image file from disk first so we don't accumulate
    # orphans when users repeatedly replace it. Best-effort: a missing/locked file
    # shouldn't block a successful replacement.
    if project.cover_image_filename:
        old_path = attachments_dir / project.cover_image_filename
        if old_path.exists():
            try:
                os.remove(old_path)
            except OSError as e:
                logger.warning("Failed to delete old cover image %s: %s", old_path, e)

    unique_filename = f"cover_{uuid.uuid4().hex}{ext}"
    file_path = attachments_dir / unique_filename  # SEC-PATH-OK: unique_filename = f"cover_{uuid.uuid4().hex}{ext}"
    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except OSError as e:
        logger.error("Failed to save cover image: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save cover image")

    project.cover_image_filename = unique_filename
    db.add(project)
    await db.flush()
    await db.commit()

    return {
        "status": "success",
        "filename": unique_filename,
        "size": len(content),
    }


@router.get("/{project_id}/cover-image")
async def get_project_cover_image(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(require_media_token_permission(Permission.PROJECTS_READ)),
):
    """Stream the project's cover image (#1155).

    Browsers can't attach `Authorization: Bearer ...` to `<img src>` requests,
    so this route accepts a `?token=` media credential, the same one
    /archives/{id}/thumbnail takes. The frontend wraps URLs with `withMediaToken`.

    Gated on ``projects:read`` like every other project route. It used to take
    the camera-stream token, which required ``camera:view`` instead -- an
    unrelated permission that a user could hold without any project access, and
    that a project reader could easily lack (#3025)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    if not project.cover_image_filename:
        raise HTTPException(status_code=404, detail="No cover image set")

    file_path = get_project_attachments_dir(project_id) / project.cover_image_filename
    if not file_path.exists():
        # DB references a file that vanished from disk — clear the dangling
        # reference so future GETs get a clean 404 instead of repeatedly
        # touching the filesystem.
        logger.warning("Cover image file missing for project %s: %s", project_id, file_path)
        project.cover_image_filename = None
        await db.commit()
        raise HTTPException(status_code=404, detail="Cover image file not found")

    ext = os.path.splitext(project.cover_image_filename)[1].lower()
    media_type = COVER_IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")
    return FileResponse(file_path, media_type=media_type)


@router.delete("/{project_id}/cover-image")
async def delete_project_cover_image(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Remove the project's cover image (#1155)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    if project.cover_image_filename:
        file_path = get_project_attachments_dir(project_id) / project.cover_image_filename
        if file_path.exists():
            try:
                os.remove(file_path)
            except OSError as e:
                logger.warning("Failed to delete cover image file %s: %s", file_path, e)
        project.cover_image_filename = None
        db.add(project)
        await db.flush()
        await db.commit()

    return {"status": "success"}


# ============ Phase 7: BOM Endpoints ============


@router.get("/{project_id}/bom", response_model=list[BOMItemResponse])
async def list_bom_items(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """List all BOM items for a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    # Get BOM items
    result = await db.execute(
        select(ProjectBOMItem)
        .where(ProjectBOMItem.project_id == project_id)
        .order_by(ProjectBOMItem.sort_order, ProjectBOMItem.id)
    )
    items = result.scalars().all()

    response = []
    for item in items:
        # Get archive name if linked
        archive_name = None
        if item.archive_id:
            archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == item.archive_id))
            archive_name = archive_result.scalar()

        response.append(
            BOMItemResponse(
                id=item.id,
                project_id=item.project_id,
                name=item.name,
                quantity_needed=item.quantity_needed,
                quantity_acquired=item.quantity_acquired,
                unit_price=item.unit_price,
                sourcing_url=item.sourcing_url,
                archive_id=item.archive_id,
                archive_name=archive_name,
                stl_filename=item.stl_filename,
                remarks=item.remarks,
                sort_order=item.sort_order,
                is_complete=item.quantity_acquired >= item.quantity_needed,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )

    return response


@router.post("/{project_id}/bom", response_model=BOMItemResponse)
async def create_bom_item(
    project_id: int,
    data: BOMItemCreate,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Add a BOM item to a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    # Get max sort order
    max_order_result = await db.execute(
        select(func.max(ProjectBOMItem.sort_order)).where(ProjectBOMItem.project_id == project_id)
    )
    max_order = max_order_result.scalar() or 0

    item = ProjectBOMItem(
        project_id=project_id,
        name=data.name,
        quantity_needed=data.quantity_needed,
        unit_price=data.unit_price,
        sourcing_url=data.sourcing_url,
        archive_id=data.archive_id,
        stl_filename=data.stl_filename,
        remarks=data.remarks,
        sort_order=max_order + 1,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)

    # Get archive name if linked
    archive_name = None
    if item.archive_id:
        archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == item.archive_id))
        archive_name = archive_result.scalar()

    return BOMItemResponse(
        id=item.id,
        project_id=item.project_id,
        name=item.name,
        quantity_needed=item.quantity_needed,
        quantity_acquired=item.quantity_acquired,
        unit_price=item.unit_price,
        sourcing_url=item.sourcing_url,
        archive_id=item.archive_id,
        archive_name=archive_name,
        stl_filename=item.stl_filename,
        remarks=item.remarks,
        sort_order=item.sort_order,
        is_complete=item.quantity_acquired >= item.quantity_needed,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.patch("/{project_id}/bom/{item_id}", response_model=BOMItemResponse)
async def update_bom_item(
    project_id: int,
    item_id: int,
    data: BOMItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Update a BOM item."""
    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    result = await db.execute(
        select(ProjectBOMItem).where(
            ProjectBOMItem.id == item_id,
            ProjectBOMItem.project_id == project_id,
        )
    )
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="BOM item not found")

    if data.name is not None:
        item.name = data.name
    if data.quantity_needed is not None:
        item.quantity_needed = data.quantity_needed
    if data.quantity_acquired is not None:
        item.quantity_acquired = data.quantity_acquired
    if data.unit_price is not None:
        item.unit_price = data.unit_price if data.unit_price != 0 else None
    if data.sourcing_url is not None:
        item.sourcing_url = data.sourcing_url if data.sourcing_url else None
    if data.archive_id is not None:
        item.archive_id = data.archive_id if data.archive_id != 0 else None
    if data.stl_filename is not None:
        item.stl_filename = data.stl_filename if data.stl_filename else None
    if data.remarks is not None:
        item.remarks = data.remarks if data.remarks else None

    await db.flush()
    await db.refresh(item)

    # Get archive name if linked
    archive_name = None
    if item.archive_id:
        archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == item.archive_id))
        archive_name = archive_result.scalar()

    return BOMItemResponse(
        id=item.id,
        project_id=item.project_id,
        name=item.name,
        quantity_needed=item.quantity_needed,
        quantity_acquired=item.quantity_acquired,
        unit_price=item.unit_price,
        sourcing_url=item.sourcing_url,
        archive_id=item.archive_id,
        archive_name=archive_name,
        stl_filename=item.stl_filename,
        remarks=item.remarks,
        sort_order=item.sort_order,
        is_complete=item.quantity_acquired >= item.quantity_needed,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.delete("/{project_id}/bom/{item_id}")
async def delete_bom_item(
    project_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_write(),
):
    """Delete a BOM item."""
    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_write:
        raise HTTPException(status_code=403, detail="You do not have write access to this project")

    result = await db.execute(
        select(ProjectBOMItem).where(
            ProjectBOMItem.id == item_id,
            ProjectBOMItem.project_id == project_id,
        )
    )
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="BOM item not found")

    await db.delete(item)

    return {"status": "success", "message": "BOM item deleted"}


@router.post("/{project_id}/create-template", response_model=ProjectResponse)
async def create_template_from_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Create a template from an existing project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    source = result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    # Create template
    template = Project(
        name=f"{source.name} (Template)",
        description=source.description,
        color=source.color,
        target_count=source.target_count,
        target_parts_count=source.target_parts_count,
        target_sets=source.target_sets,
        notes=source.notes,
        tags=source.tags,
        priority=source.priority,
        budget=source.budget,
        is_template=True,
        template_source_id=source.id,
        url=source.url,
        created_by_id=user.id if user else None,
    )
    db.add(template)
    await db.flush()

    # Copy BOM items
    bom_result = await db.execute(select(ProjectBOMItem).where(ProjectBOMItem.project_id == project_id))
    bom_items = bom_result.scalars().all()

    for item in bom_items:
        new_item = ProjectBOMItem(
            project_id=template.id,
            name=item.name,
            quantity_needed=item.quantity_needed,
            quantity_acquired=0,
            unit_price=item.unit_price,
            sourcing_url=item.sourcing_url,
            stl_filename=item.stl_filename,
            remarks=item.remarks,
            sort_order=item.sort_order,
        )
        db.add(new_item)

    await db.flush()
    await db.refresh(template)

    stats = await compute_project_stats(db, template.id, template.target_count, template.target_parts_count)

    return ProjectResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        color=template.color,
        status=template.status,
        target_count=template.target_count,
        target_parts_count=template.target_parts_count,
        target_sets=template.target_sets,
        notes=template.notes,
        attachments=template.attachments,
        url=template.url,
        cover_image_filename=template.cover_image_filename,
        tags=template.tags,
        due_date=template.due_date,
        priority=template.priority,
        budget=template.budget,
        is_template=template.is_template,
        template_source_id=template.template_source_id,
        parent_id=template.parent_id,
        parent_name=None,
        children=[],
        created_by_id=template.created_by_id,
        created_by_username=user.username if user else None,
        can_read=True,
        can_write=True,
        can_manage=True,
        created_at=template.created_at,
        updated_at=template.updated_at,
        stats=stats,
    )


# ============ Phase 9: Timeline Endpoint ============


@router.get("/{project_id}/timeline", response_model=list[TimelineEvent])
async def get_project_timeline(
    project_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """Get timeline of events for a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    events = []

    # Project creation event
    events.append(
        TimelineEvent(
            event_type="project_created",
            timestamp=project.created_at,
            title="Project created",
            description=f"Project '{project.name}' was created",
        )
    )

    # Get archives and add events
    archives_result = await db.execute(
        select(PrintArchive)
        .where(PrintArchive.project_id == project_id, _LIVE_ARCHIVE)
        .order_by(PrintArchive.created_at.desc())
        .limit(limit)
    )
    archives = archives_result.scalars().all()

    for archive in archives:
        if archive.status == "completed":
            events.append(
                TimelineEvent(
                    event_type="print_completed",
                    timestamp=archive.completed_at or archive.created_at,
                    title="Print completed",
                    description=archive.print_name,
                    metadata={
                        "archive_id": archive.id,
                        "print_time_hours": round((archive.print_time_seconds or 0) / 3600, 2),
                        "filament_grams": round(archive.filament_used_grams or 0, 1),
                    },
                )
            )
        elif archive.status == "failed":
            events.append(
                TimelineEvent(
                    event_type="print_failed",
                    timestamp=archive.completed_at or archive.created_at,
                    title="Print failed",
                    description=archive.print_name,
                    metadata={"archive_id": archive.id},
                )
            )

    # Get queue items
    queue_result = await db.execute(
        select(PrintQueueItem)
        .where(PrintQueueItem.project_id == project_id)
        .order_by(PrintQueueItem.created_at.desc())
        .limit(limit)
    )
    queue_items = queue_result.scalars().all()

    for item in queue_items:
        if item.status == "printing":
            events.append(
                TimelineEvent(
                    event_type="print_started",
                    timestamp=item.started_at or item.created_at,
                    title="Print started",
                    description=item.print_name,
                    metadata={"queue_item_id": item.id},
                )
            )
        elif item.status == "pending":
            events.append(
                TimelineEvent(
                    event_type="queued",
                    timestamp=item.created_at,
                    title="Added to queue",
                    description=item.print_name,
                    metadata={"queue_item_id": item.id},
                )
            )

    # Sort by timestamp descending
    events.sort(key=lambda e: e.timestamp, reverse=True)

    return events[:limit]


# ============ Phase 10: Import/Export Endpoints ============


@router.get("/{project_id}/export")
async def export_project(
    project_id: int,
    format: str = "zip",  # "zip" (with files) or "json" (metadata only)
    db: AsyncSession = Depends(get_db),
    user: User | None = _require_project_read(),
):
    """Export a project. Use format=zip (default) for full export with files, or format=json for metadata only."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    access_map = await _compute_project_access_map(db, user)
    acc = access_map.get(project_id, _ProjectAccess())
    if not acc.can_read:
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    # Get BOM items
    bom_result = await db.execute(
        select(ProjectBOMItem).where(ProjectBOMItem.project_id == project_id).order_by(ProjectBOMItem.sort_order)
    )
    bom_items = bom_result.scalars().all()

    bom_export = [
        {
            "name": item.name,
            "quantity_needed": item.quantity_needed,
            "quantity_acquired": item.quantity_acquired,
            "unit_price": item.unit_price,
            "sourcing_url": item.sourcing_url,
            "stl_filename": item.stl_filename,
            "remarks": item.remarks,
        }
        for item in bom_items
    ]

    # Get linked folders and their files
    folders_result = await db.execute(
        select(LibraryFolder).where(LibraryFolder.project_id == project_id).order_by(LibraryFolder.name)
    )
    linked_folders = folders_result.scalars().all()

    folders_export = []
    files_to_include = []  # (archive_path, zip_path)

    for folder in linked_folders:
        # Get files in this folder
        files_result = await db.execute(
            LibraryFile.active().where(LibraryFile.folder_id == folder.id).order_by(LibraryFile.filename)
        )
        files = files_result.scalars().all()

        folder_files = []
        for f in files:
            folder_files.append(
                {
                    "filename": f.filename,
                    "file_type": f.file_type,
                    "notes": f.notes,
                }
            )
            # Add file to include in ZIP
            library_dir = get_library_dir()
            file_path = library_dir / f.file_path
            if file_path.exists():
                zip_path = f"files/{folder.name}/{f.filename}"
                files_to_include.append((file_path, zip_path))
                # Also include thumbnail if exists
                if f.thumbnail_path:
                    thumb_path = library_dir / f.thumbnail_path
                    if thumb_path.exists():
                        thumb_zip_path = f"files/{folder.name}/.thumbnails/{f.filename}.png"
                        files_to_include.append((thumb_path, thumb_zip_path))

        folders_export.append(
            {
                "name": folder.name,
                "files": folder_files,
            }
        )

    # Build project JSON
    project_data = {
        "name": project.name,
        "description": project.description,
        "color": project.color,
        "status": project.status,
        "target_count": project.target_count,
        "target_parts_count": project.target_parts_count,
        "target_sets": project.target_sets,
        "notes": project.notes,
        "tags": project.tags,
        "due_date": project.due_date.isoformat() if project.due_date else None,
        "priority": project.priority,
        "budget": project.budget,
        "bom_items": bom_export,
        "linked_folders": folders_export,
    }

    # Return JSON if requested (for bulk export)
    if format == "json":
        return project_data

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add project.json
        zf.writestr("project.json", json.dumps(project_data, indent=2))

        # Add files
        for file_path, zip_path in files_to_include:
            zf.write(file_path, zip_path)

    zip_buffer.seek(0)

    # Generate filename
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in project.name)
    filename = f"{safe_name}_{datetime.now().strftime('%Y-%m-%d')}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": build_content_disposition(filename)},
    )


@router.post("/import", response_model=ProjectResponse)
async def import_project(
    data: ProjectImport,
    db: AsyncSession = Depends(get_db),
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Import a project with optional BOM items and linked folders."""
    # Create the project
    project = Project(
        name=data.name,
        description=data.description,
        color=data.color,
        status=data.status,
        target_count=data.target_count,
        target_parts_count=data.target_parts_count,
        target_sets=data.target_sets,
        notes=data.notes,
        tags=data.tags,
        due_date=data.due_date,
        priority=data.priority,
        budget=data.budget,
        created_by_id=user.id if user else None,
    )
    db.add(project)
    await db.flush()

    # Create BOM items
    for idx, bom_data in enumerate(data.bom_items):
        bom_item = ProjectBOMItem(
            project_id=project.id,
            name=bom_data.name,
            quantity_needed=bom_data.quantity_needed,
            quantity_acquired=bom_data.quantity_acquired,
            unit_price=bom_data.unit_price,
            sourcing_url=bom_data.sourcing_url,
            stl_filename=bom_data.stl_filename,
            remarks=bom_data.remarks,
            sort_order=idx,
        )
        db.add(bom_item)

    # Create linked folders in library
    for folder_data in data.linked_folders:
        # Check if folder with this name already exists at root level
        existing_result = await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.name == folder_data.name,
                LibraryFolder.parent_id.is_(None),
            )
        )
        existing_folder = existing_result.scalar_one_or_none()

        if existing_folder:
            # Link existing folder to project
            existing_folder.project_id = project.id
        else:
            # Create new folder linked to project
            new_folder = LibraryFolder(
                name=folder_data.name,
                project_id=project.id,
                is_external=False,
                external_readonly=False,
                external_show_hidden=False,
                created_by_id=user.id if user else None,
            )
            db.add(new_folder)

    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        target_sets=project.target_sets,
        notes=project.notes,
        attachments=project.attachments,
        url=project.url,
        cover_image_filename=project.cover_image_filename,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=None,
        children=[],
        created_by_id=project.created_by_id,
        created_by_username=user.username if user else None,
        can_read=True,
        can_write=True,
        can_manage=True,
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )


@router.post("/import/file", response_model=ProjectResponse)
async def import_project_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Import a project from a ZIP or JSON file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Determine file type
    filename_lower = file.filename.lower()
    content = await file.read()

    if filename_lower.endswith(".zip"):
        # Extract project.json from ZIP
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if "project.json" not in zf.namelist():
                    raise HTTPException(status_code=400, detail="ZIP must contain project.json")
                project_json = zf.read("project.json")
                data = json.loads(project_json)

                # Get list of files in the ZIP
                zip_files = {name: zf.read(name) for name in zf.namelist() if name.startswith("files/")}
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid ZIP file")
    elif filename_lower.endswith(".json"):
        try:
            data = json.loads(content)
            zip_files = {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON file")
    else:
        raise HTTPException(status_code=400, detail="File must be .zip or .json")

    # Create the project
    project = Project(
        name=data.get("name", "Imported Project"),
        description=data.get("description"),
        color=data.get("color"),
        status=data.get("status", "active"),
        target_count=data.get("target_count"),
        target_parts_count=data.get("target_parts_count"),
        target_sets=data.get("target_sets"),
        notes=data.get("notes"),
        tags=data.get("tags"),
        due_date=datetime.fromisoformat(data["due_date"]) if data.get("due_date") else None,
        priority=data.get("priority", 0),
        budget=data.get("budget"),
        created_by_id=user.id if user else None,
    )
    db.add(project)
    await db.flush()

    # Create BOM items
    for idx, bom_data in enumerate(data.get("bom_items", [])):
        bom_item = ProjectBOMItem(
            project_id=project.id,
            name=bom_data.get("name", "Unnamed"),
            quantity_needed=bom_data.get("quantity_needed", 1),
            quantity_acquired=bom_data.get("quantity_acquired", 0),
            unit_price=bom_data.get("unit_price"),
            sourcing_url=bom_data.get("sourcing_url"),
            stl_filename=bom_data.get("stl_filename"),
            remarks=bom_data.get("remarks"),
            sort_order=idx,
        )
        db.add(bom_item)

    # Create linked folders and files
    library_dir = get_library_dir()
    for folder_data in data.get("linked_folders", []):
        folder_name = folder_data.get("name")
        if not folder_name:
            continue

        # Containment check on the folder name — refuses absolute paths and
        # ``..`` traversal in ``project.json[linked_folders[*].name]``. The
        # previous code did ``library_dir / folder_name`` directly, which
        # collapses to ``Path(folder_name)`` when folder_name is absolute
        # and lets ``..`` escape after mkdir.
        folder_path = safe_join_under(library_dir, folder_name)

        # Check if folder exists
        existing_result = await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.name == folder_name,
                LibraryFolder.parent_id.is_(None),
            )
        )
        existing_folder = existing_result.scalar_one_or_none()

        if existing_folder:
            # Link existing folder to project
            existing_folder.project_id = project.id
            folder = existing_folder
        else:
            # Create new folder
            folder = LibraryFolder(
                name=folder_name,
                project_id=project.id,
                is_external=False,
                external_readonly=False,
                external_show_hidden=False,
                created_by_id=user.id if user else None,
            )
            db.add(folder)
            await db.flush()

            # Create folder on disk
            folder_path.mkdir(parents=True, exist_ok=True)

        # Import files for this folder from ZIP
        folder_prefix = f"files/{folder_name}/"
        for zip_path, file_content in zip_files.items():
            if not zip_path.startswith(folder_prefix):
                continue
            if "/.thumbnails/" in zip_path:
                continue  # Skip thumbnails, we'll regenerate them

            relative_path = zip_path[len(folder_prefix) :]
            if not relative_path:
                continue

            # Containment check on the per-entry relative path. ZIP names
            # can carry ``..`` segments by spec; without resolve + parent
            # containment, ``files/<folder>/../../../etc/x`` escapes
            # ``library_dir`` entirely. ``relative_path`` is split into
            # parts because ``safe_join_under`` rejects parts that start
            # with ``/``, and a single combined string would hide an
            # embedded ``..`` segment behind a forward slash.
            file_disk_path = safe_join_under(
                library_dir,
                folder_name,
                *Path(relative_path).parts,
            )
            file_disk_path.parent.mkdir(parents=True, exist_ok=True)
            file_disk_path.write_bytes(file_content)

            # Determine file type
            ext = Path(relative_path).suffix.lower()
            if ext in [".stl", ".3mf", ".obj"]:
                file_type = "model"
            elif ext in [".gcode"]:
                file_type = "gcode"
            elif ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                file_type = "image"
            else:
                file_type = "other"

            # Create library file record
            lib_file = LibraryFile(
                folder_id=folder.id,
                filename=relative_path,
                file_path=f"{folder_name}/{relative_path}",
                file_type=file_type,
                file_size=len(file_content),
                is_external=False,
                created_by_id=user.id if user else None,
            )
            db.add(lib_file)

    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        target_sets=project.target_sets,
        notes=project.notes,
        attachments=project.attachments,
        url=project.url,
        cover_image_filename=project.cover_image_filename,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=None,
        children=[],
        created_by_id=project.created_by_id,
        created_by_username=user.username if user else None,
        can_read=True,
        can_write=True,
        can_manage=True,
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )
