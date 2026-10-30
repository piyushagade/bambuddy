"""Tests for Phase 1: Account Termination & Time-Limited Access (`expires_at`)."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import create_access_token, get_password_hash, invalidate_auth_enabled_cache
from backend.app.models.group import Group
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.user_expiration import user_expiration_service
from backend.app.utils.local_time import utcnow_naive


async def _create_admin_with_token(db_session: AsyncSession) -> tuple[User, str]:
    """Enable auth, create an admin user in the Administrators group, and return (admin, token)."""
    db_session.add(Settings(key="auth_enabled", value="true"))
    invalidate_auth_enabled_cache()

    result = await db_session.execute(select(Group).where(Group.name == "Administrators"))
    admin_group = result.scalar_one_or_none()

    admin = User(
        username="uf_admin",
        password_hash=get_password_hash("AdminPass1!"),
        role="admin",
        is_active=True,
    )
    if admin_group:
        admin.groups = [admin_group]
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    token = create_access_token(data={"sub": admin.username, "role": admin.role})
    return admin, token


@pytest.mark.asyncio
async def test_create_update_and_clear_user_expiration(
    async_client: AsyncClient, db_session: AsyncSession
):
    """POST /users and PATCH /users/{id} should set, preserve, extend, and clear expires_at."""
    _, token = await _create_admin_with_token(db_session)
    headers = {"Authorization": f"Bearer {token}"}

    future_dt = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
    resp = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={
            "username": "student1",
            "password": "StudentPass1!",
            "role": "user",
            "expires_at": future_dt.isoformat(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()
    user_id = data["id"]
    assert data["expires_at"] is not None
    assert data["is_expired"] is False
    assert data["is_active"] is True

    # Omitting expires_at in PATCH should preserve existing expires_at
    resp2 = await async_client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"email": "student1@ufl.edu"},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["expires_at"] == data["expires_at"]

    # Setting expires_at in the past should immediately mark is_active=False and is_expired=True
    past_dt = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
    resp3 = await async_client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"expires_at": past_dt.isoformat()},
    )
    assert resp3.status_code == 200, resp3.text
    assert resp3.json()["is_expired"] is True
    assert resp3.json()["is_active"] is False

    # Extending expires_at into the future should automatically re-activate the user
    new_future_dt = (datetime.now(timezone.utc) + timedelta(days=90)).replace(microsecond=0)
    resp4 = await async_client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"expires_at": new_future_dt.isoformat()},
    )
    assert resp4.status_code == 200, resp4.text
    assert resp4.json()["is_expired"] is False
    assert resp4.json()["is_active"] is True

    # Explicitly passing expires_at: null should clear the termination date
    resp5 = await async_client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"expires_at": None},
    )
    assert resp5.status_code == 200, resp5.text
    assert resp5.json()["expires_at"] is None
    assert resp5.json()["is_expired"] is False


@pytest.mark.asyncio
async def test_expired_user_blocked_at_login_and_jwt_validation(
    async_client: AsyncClient, db_session: AsyncSession
):
    """An account whose expires_at is in the past must be blocked even before the sweeper runs."""
    await _create_admin_with_token(db_session)

    # Create user whose expires_at just passed, while is_active is still True in DB
    student = User(
        username="expired_gator",
        password_hash=get_password_hash("StudentPass1!"),
        role="user",
        is_active=True,
        expires_at=utcnow_naive() - timedelta(minutes=5),
    )
    db_session.add(student)
    await db_session.commit()
    await db_session.refresh(student)

    # 1. Login attempt must be rejected with 401
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "expired_gator", "password": "StudentPass1!"},
    )
    assert login_resp.status_code == 401

    # 2. Existing JWT bearer token must be rejected with 401 ("User account has expired")
    student_token = create_access_token(data={"sub": student.username, "role": student.role})
    me_resp = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert me_resp.status_code == 401
    assert "expired" in me_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_expiration_sweeper_deactivates_and_cancels_pending_queue_only(
    db_session: AsyncSession,
):
    """Sweeper should deactivate expired users, cancel pending/skipped queue items, and leave printing items alone."""
    await _create_admin_with_token(db_session)

    student = User(
        username="graduating_senior",
        password_hash=get_password_hash("StudentPass1!"),
        role="user",
        is_active=True,
        expires_at=utcnow_naive() - timedelta(hours=1),
    )
    db_session.add(student)
    await db_session.commit()
    await db_session.refresh(student)

    pending_item = PrintQueueItem(
        created_by_id=student.id,
        position=1,
        status="pending",
    )
    printing_item = PrintQueueItem(
        created_by_id=student.id,
        position=2,
        status="printing",
    )
    db_session.add_all([pending_item, printing_item])
    await db_session.commit()

    summary = await user_expiration_service.sweep(db_session)
    assert summary["expired_users"] == 1
    assert summary["cancelled_queue_items"] == 1

    await db_session.refresh(student)
    await db_session.refresh(pending_item)
    await db_session.refresh(printing_item)

    assert student.is_active is False
    assert pending_item.status == "cancelled"
    assert pending_item.error_message == "Cancelled automatically: user account expired"
    assert printing_item.status == "printing"


@pytest.mark.asyncio
async def test_last_admin_protected_from_expiration(
    async_client: AsyncClient, db_session: AsyncSession
):
    """The last active admin cannot be given a past expires_at or deactivated by the sweeper."""
    admin, token = await _create_admin_with_token(db_session)
    headers = {"Authorization": f"Bearer {token}"}

    past_dt = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
    resp = await async_client.patch(
        f"/api/v1/users/{admin.id}",
        headers=headers,
        json={"expires_at": past_dt.isoformat()},
    )
    assert resp.status_code == 400
    assert "last admin" in resp.json()["detail"].lower()

    # Even if an admin's future expires_at passes in the DB, the sweeper must skip deactivating the last admin
    admin.expires_at = utcnow_naive() - timedelta(minutes=10)
    await db_session.commit()

    summary = await user_expiration_service.sweep(db_session)
    assert summary["expired_users"] == 0

    await db_session.refresh(admin)
    assert admin.is_active is True


@pytest.mark.asyncio
async def test_phase2_group_expiration_transitions_to_viewers(
    async_client: AsyncClient, db_session: AsyncSession
):
    """When a student's course group expires, they are moved to Viewers for read-only archive access."""
    await _create_admin_with_token(db_session)

    course_group = Group(
        name="EML2322L-Fall2026",
        description="Design & Manufacturing Lab",
        permissions=["queue:create", "printers:read"],
        is_system=False,
        expires_at=utcnow_naive() - timedelta(minutes=5),
    )
    db_session.add(course_group)
    await db_session.flush()

    student = User(
        username="course_student",
        password_hash=get_password_hash("StudentPass1!"),
        role="user",
        is_active=True,
        groups=[course_group],
    )
    db_session.add(student)
    await db_session.commit()

    # Immediate permission check ignores expired group
    assert "queue:create" not in student.get_permissions()

    # Sweeper removes expired group membership and assigns Viewers group
    summary = await user_expiration_service.sweep(db_session)
    assert summary["expired_group_memberships"] == 1
    assert summary["fallback_viewers_assigned"] == 1


@pytest.mark.asyncio
async def test_phase3_csv_roster_import_and_enrollment_invites(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Test bulk CSV roster upsert and self-service enrollment invite redemption."""
    _, token = await _create_admin_with_token(db_session)
    headers = {"Authorization": f"Bearer {token}"}

    csv_text = (
        "username,email,group,cost_center,expires_at\n"
        "gator_one,gator1@ufl.edu,EML2322L-Spring2027,EML2322L,2027-05-05T23:59:00Z\n"
    )
    resp = await async_client.post(
        "/api/v1/users/roster-import",
        headers=headers,
        json={"csv_content": csv_text},
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["created_count"] == 1

    # Mint an enrollment invite code and redeem it
    inv_resp = await async_client.post(
        "/api/v1/users/invites",
        headers=headers,
        json={
            "name": "Senior Design Spring 2027",
            "code": "UF-SD-2027",
            "max_uses": 5,
            "user_expires_at": "2027-05-05T23:59:00Z",
        },
    )
    assert inv_resp.status_code == 201, inv_resp.text

    redeem_resp = await async_client.post(
        "/api/v1/users/invites/redeem",
        json={
            "code": "UF-SD-2027",
            "username": "self_enrolled_gator",
            "password": "StudentPass1!",
            "email": "self@ufl.edu",
        },
    )
    assert redeem_resp.status_code == 200, redeem_resp.text
    assert redeem_resp.json()["user"]["username"] == "self_enrolled_gator"

    # Test max_uses=1 exhaustion
    one_use_resp = await async_client.post(
        "/api/v1/users/invites",
        headers=headers,
        json={
            "name": "Single Use Code",
            "code": "UF-ONE-USE",
            "max_uses": 1,
        },
    )
    assert one_use_resp.status_code == 201
    r1 = await async_client.post(
        "/api/v1/users/invites/redeem",
        json={"code": "UF-ONE-USE", "username": "u_one", "password": "StudentPass1!"},
    )
    assert r1.status_code == 200
    r2 = await async_client.post(
        "/api/v1/users/invites/redeem",
        json={"code": "UF-ONE-USE", "username": "u_two", "password": "StudentPass1!"},
    )
    assert r2.status_code == 400
    assert "maximum number of uses" in r2.json()["detail"].lower()

    # Test invite_expires_at datetime expiration
    expired_code_resp = await async_client.post(
        "/api/v1/users/invites",
        headers=headers,
        json={
            "name": "Expired Drop/Add Code",
            "code": "UF-EXPIRED-CODE",
            "invite_expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        },
    )
    assert expired_code_resp.status_code == 201
    assert expired_code_resp.json()["is_redeemable"] is False
    r3 = await async_client.post(
        "/api/v1/users/invites/redeem",
        json={"code": "UF-EXPIRED-CODE", "username": "u_three", "password": "StudentPass1!"},
    )
    assert r3.status_code == 400
    assert "expired" in r3.json()["detail"].lower()


@pytest.mark.asyncio
async def test_uf_classroll_csv_import_email_prefix_and_ufid_password(
    async_client: AsyncClient, db_session: AsyncSession
):
    """UF classroll CSV should derive username from Email prefix, password from UFID, and course group from Subject+Catalog+Term."""
    _, token = await _create_admin_with_token(db_session)
    headers = {"Authorization": f"Bearer {token}"}

    classroll_csv = (
        "UFID,Last Name,First Name,Email,Subject,Catalog Number,College,Major / Minor,Level,Status,Grading Basis,Term,Career,Institution,Class Number,Credits (Units),Grade,Writing Req,Writing Req Grade,Date Added\n"
        "62640601,Arceo Zamora,David,davidarceozamora@ufl.edu,ABE,4042C,Engineering,Engineering - Biological Engineering,Fifth Year Senior,Enrolled,Letter Grade,2268,UGRD,UFLOR,17491,2,,,,2026-03-30\n"
        "68511827,Areas Mayorga,Sofia Eugenia,sareasmayorga@ufl.edu,ABE,4042C,Engineering,Engineering - Biological Engineering,Fifth Year Senior,Enrolled,Letter Grade,2268,UGRD,UFLOR,17491,2,,,,2026-04-02\n"
    )
    resp = await async_client.post(
        "/api/v1/users/roster-import",
        headers=headers,
        json={"csv_content": classroll_csv},
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["created_count"] == 2
    usernames = {u["username"] for u in body["users"]}
    assert usernames == {"davidarceozamora", "sareasmayorga"}

    # Verify group ABE4042C-Fall2026 was auto-created and assigned
    first_user = next(u for u in body["users"] if u["username"] == "davidarceozamora")
    group_names = [g["name"] for g in first_user["groups"]]
    assert "ABE4042C-Fall2026" in group_names
    assert first_user["expires_at"].startswith("2026-12-15T23:59:00")

    # Verify student can log in with email prefix as username and UFID as password
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "davidarceozamora", "password": "62640601"},
    )
    assert login_resp.status_code == 200, login_resp.text
    assert login_resp.json()["user"]["username"] == "davidarceozamora"


