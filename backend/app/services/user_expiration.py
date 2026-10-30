"""User account & group membership expiration sweeper service.

Automatically:
1. Sends pre-expiration warning emails (7 days and 24 hours prior) for user
   accounts and cohort groups.
2. Drops expired group memberships (`user_groups.expires_at` or `Group.expires_at`)
   and transitions affected active users with no remaining active groups to the
   system `Viewers` group so students retain read-only access to their past
   `PrintArchive` logs and 3MF downloads.
3. Deactivates user accounts whose `expires_at` termination datetime has passed
   (while safeguarding the last active administrator) and cancels their pending
   queue items.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.database import async_session
from backend.app.models.group import Group, user_groups
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.services.email_service import send_user_expiration_warning
from backend.app.services.finance_budget import release_budget_reservation
from backend.app.utils.local_time import utcnow_naive

logger = logging.getLogger(__name__)


class UserExpirationService:
    """Background service that sweeps expired user accounts and group memberships."""

    def __init__(self) -> None:
        self._scheduler_task: asyncio.Task | None = None
        self._check_interval = 60  # Check every 60 seconds

    async def start_scheduler(self) -> None:
        """Start the background expiration sweeper (idempotent)."""
        if self._scheduler_task is not None:
            return
        logger.info("Starting user expiration sweeper")
        try:
            async with async_session() as db:
                await self.sweep(db)
        except Exception as e:
            logger.warning("Initial user expiration sweep failed: %s", e)
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Stopped user expiration sweeper")

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                async with async_session() as db:
                    await self.sweep(db)
            except asyncio.CancelledError:
                break
            except Exception as e:  # pragma: no cover - defensive
                logger.error("Error in user expiration sweeper: %s", e)
                await asyncio.sleep(60)

    async def _send_expiration_warnings(self, db: AsyncSession, now) -> int:
        """Send 7-day and 24-hour pre-expiration warning emails for users and groups."""
        warnings_sent = 0
        in_7_days = now + timedelta(days=7)
        in_24_hours = now + timedelta(hours=24)

        # 1. User account expiration warnings
        user_result = await db.execute(
            select(User).where(
                User.is_active.is_(True),
                User.email.isnot(None),
                User.expires_at.isnot(None),
                User.expires_at > now,
                User.expires_at <= in_7_days,
            )
        )
        for user in user_result.scalars().all():
            if not user.email or not user.expires_at:
                continue
            if user.expires_at <= in_24_hours and user.expiry_warned_1d_at is None:
                await send_user_expiration_warning(
                    db,
                    user_email=user.email,
                    username=user.username,
                    expires_at=user.expires_at,
                    time_remaining="24 hours",
                    target_label="account",
                )
                user.expiry_warned_1d_at = now
                warnings_sent += 1
            elif user.expires_at > in_24_hours and user.expiry_warned_7d_at is None:
                await send_user_expiration_warning(
                    db,
                    user_email=user.email,
                    username=user.username,
                    expires_at=user.expires_at,
                    time_remaining="7 days",
                    target_label="account",
                )
                user.expiry_warned_7d_at = now
                warnings_sent += 1

        # 2. Cohort group expiration warnings
        group_result = await db.execute(
            select(Group)
            .where(
                Group.expires_at.isnot(None),
                Group.expires_at > now,
                Group.expires_at <= in_7_days,
            )
            .options(selectinload(Group.users))
        )
        for group in group_result.scalars().all():
            if not group.expires_at:
                continue
            if group.expires_at <= in_24_hours and group.expiry_warned_1d_at is None:
                for member in group.users:
                    if member.is_active and member.email:
                        await send_user_expiration_warning(
                            db,
                            user_email=member.email,
                            username=member.username,
                            expires_at=group.expires_at,
                            time_remaining="24 hours",
                            target_label=f"group '{group.name}'",
                        )
                        warnings_sent += 1
                group.expiry_warned_1d_at = now
            elif group.expires_at > in_24_hours and group.expiry_warned_7d_at is None:
                for member in group.users:
                    if member.is_active and member.email:
                        await send_user_expiration_warning(
                            db,
                            user_email=member.email,
                            username=member.username,
                            expires_at=group.expires_at,
                            time_remaining="7 days",
                            target_label=f"group '{group.name}'",
                        )
                        warnings_sent += 1
                group.expiry_warned_7d_at = now

        return warnings_sent

    async def _sweep_expired_group_memberships(self, db: AsyncSession, now) -> tuple[int, int]:
        """Remove expired group memberships and transition group-less active users to Viewers."""
        affected_user_ids: set[int] = set()
        removed_count = 0

        # 1. Per-membership expiration in user_groups
        ug_rows = await db.execute(
            select(user_groups.c.user_id, user_groups.c.group_id).where(
                user_groups.c.expires_at.isnot(None),
                user_groups.c.expires_at <= now,
            )
        )
        expired_pairs = list(ug_rows.all())
        for uid, gid in expired_pairs:
            await db.execute(
                delete(user_groups).where(
                    user_groups.c.user_id == uid,
                    user_groups.c.group_id == gid,
                )
            )
            affected_user_ids.add(uid)
            removed_count += 1

        # 2. Cohort group expiration on Group.expires_at
        expired_groups_result = await db.execute(
            select(Group)
            .where(
                Group.is_system.is_(False),
                Group.expires_at.isnot(None),
                Group.expires_at <= now,
            )
            .options(selectinload(Group.users))
        )
        for group in expired_groups_result.scalars().all():
            if not group.users:
                continue
            for member in list(group.users):
                affected_user_ids.add(member.id)
                removed_count += 1
            group.users.clear()

        if not affected_user_ids:
            return 0, 0

        await db.flush()

        # Transition active users who lost all active groups into the Viewers group
        viewers_result = await db.execute(select(Group).where(Group.name == "Viewers"))
        viewers_group = viewers_result.scalar_one_or_none()
        fallback_assigned = 0

        if viewers_group is not None:
            users_result = await db.execute(
                select(User)
                .where(User.id.in_(affected_user_ids), User.is_active.is_(True))
                .options(selectinload(User.groups))
            )
            for user in users_result.scalars().all():
                active_groups = [g for g in user.groups if not getattr(g, "is_expired", False)]
                if not active_groups and user.role != "admin":
                    user.groups.append(viewers_group)
                    fallback_assigned += 1
                    logger.info(
                        "Transitioned user '%s' (id=%d) to Viewers group after course/group membership expired",
                        user.username,
                        user.id,
                    )

        return removed_count, fallback_assigned

    async def _sweep_pickup_sla(self, db: AsyncSession, now, sla_hours: int = 4) -> int:
        """Track build-plate pickup SLA and flag overdue printers awaiting plate clear."""
        overdue_alerts = 0
        printers_res = await db.execute(
            select(Printer).where(Printer.awaiting_plate_clear.is_(True))
        )
        for printer in printers_res.scalars().all():
            if printer.plate_finished_at is None:
                printer.plate_finished_at = now
                continue
            if (
                printer.pickup_sla_notified_at is None
                and printer.plate_finished_at <= now - timedelta(hours=sla_hours)
            ):
                printer.pickup_sla_notified_at = now
                overdue_alerts += 1
                logger.info(
                    "Build-plate pickup SLA (%dh) exceeded on printer '%s' (id=%d)",
                    sla_hours,
                    printer.name,
                    printer.id,
                )
        return overdue_alerts

    async def _sweep_expired_user_library_cleanup(
        self, db: AsyncSession, now, retention_days: int = 30
    ) -> int:
        """Move LibraryFile uploads of users expired for >= retention_days to trash."""
        cutoff = now - timedelta(days=retention_days)
        expired_users_res = await db.execute(
            select(User.id).where(
                User.expires_at.isnot(None),
                User.expires_at <= cutoff,
            )
        )
        expired_ids = list(expired_users_res.scalars().all())
        if not expired_ids:
            return 0

        files_res = await db.execute(
            select(LibraryFile).where(
                LibraryFile.created_by_id.in_(expired_ids),
                LibraryFile.deleted_at.is_(None),
            )
        )
        trashed_count = 0
        for lib_file in files_res.scalars().all():
            lib_file.deleted_at = now
            trashed_count += 1
        if trashed_count:
            logger.info(
                "Moved %d library file(s) from long-expired student accounts to trash",
                trashed_count,
            )
        return trashed_count

    async def sweep(self, db: AsyncSession) -> dict[str, int]:
        """Run full expiration sweep: warnings, group memberships, user accounts, queue items, pickup SLA, and library cleanup."""
        now = utcnow_naive()
        warnings_sent = await self._send_expiration_warnings(db, now)
        expired_memberships, fallback_viewers = await self._sweep_expired_group_memberships(db, now)
        pickup_sla_alerts = await self._sweep_pickup_sla(db, now)
        trashed_library_files = await self._sweep_expired_user_library_cleanup(db, now)

        result = await db.execute(
            select(User)
            .where(
                User.is_active.is_(True),
                User.expires_at.isnot(None),
                User.expires_at <= now,
            )
            .options(selectinload(User.groups))
        )
        expired_candidates = list(result.scalars().all())

        deactivated_user_ids: list[int] = []
        cancelled_queue_count = 0

        if expired_candidates:
            # Resolve all currently active admins so we never deactivate the last admin
            admin_role_result = await db.execute(select(User).where(User.role == "admin", User.is_active.is_(True)))
            role_admins = list(admin_role_result.scalars().all())

            admin_group_result = await db.execute(
                select(Group).where(Group.name == "Administrators").options(selectinload(Group.users))
            )
            admin_group = admin_group_result.scalar_one_or_none()
            group_admins = [u for u in (admin_group.users if admin_group else []) if u.is_active]

            active_admin_ids = {u.id for u in role_admins} | {u.id for u in group_admins}

            for user in expired_candidates:
                if user.is_admin and user.id in active_admin_ids:
                    if len(active_admin_ids) <= 1:
                        logger.warning(
                            "Skipping automatic expiration for user '%s' (id=%d): cannot deactivate the last admin user",
                            user.username,
                            user.id,
                        )
                        continue
                    active_admin_ids.discard(user.id)

                user.is_active = False
                deactivated_user_ids.append(user.id)
                logger.info(
                    "Deactivated expired user account '%s' (id=%d, expires_at=%s)",
                    user.username,
                    user.id,
                    user.expires_at.isoformat() if user.expires_at else None,
                )

            if deactivated_user_ids:
                # Cancel pending/skipped queue items owned by expired users; leave in-flight
                # ("printing") jobs untouched so active prints finish cleanly.
                queue_result = await db.execute(
                    select(PrintQueueItem).where(
                        PrintQueueItem.created_by_id.in_(deactivated_user_ids),
                        PrintQueueItem.status.in_(("pending", "skipped")),
                    )
                )
                pending_items = list(queue_result.scalars().all())
                for item in pending_items:
                    item.status = "cancelled"
                    item.completed_at = now
                    item.error_message = "Cancelled automatically: user account expired"
                    await release_budget_reservation(
                        db,
                        source_type="queue_item",
                        source_id=item.id,
                    )
                cancelled_queue_count = len(pending_items)

        if (
            warnings_sent
            or expired_memberships
            or fallback_viewers
            or deactivated_user_ids
            or pickup_sla_alerts
            or trashed_library_files
        ):
            await db.commit()

        return {
            "expired_users": len(deactivated_user_ids),
            "cancelled_queue_items": cancelled_queue_count,
            "expired_group_memberships": expired_memberships,
            "fallback_viewers_assigned": fallback_viewers,
            "warnings_sent": warnings_sent,
            "pickup_sla_alerts": pickup_sla_alerts,
            "trashed_library_files": trashed_library_files,
        }


user_expiration_service = UserExpirationService()
