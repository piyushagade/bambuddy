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
        "12345678,Li,Bo,bo@ufl.edu,ABE,4042C,Engineering,Engineering - Biological Engineering,Fifth Year Senior,Enrolled,Letter Grade,2268,UGRD,UFLOR,17491,2,,,,2026-04-02\n"
    )
    resp = await async_client.post(
        "/api/v1/users/roster-import",
        headers=headers,
        json={"csv_content": classroll_csv},
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["created_count"] == 3
    usernames = {u["username"] for u in body["users"]}
    assert usernames == {"davidarceozamora", "sareasmayorga", "bo"}

    # Verify group 'F26 - ABE4042C' was auto-created and assigned, along with first_name and last_name
    first_user = next(u for u in body["users"] if u["username"] == "davidarceozamora")
    assert first_user["first_name"] == "David"
    assert first_user["last_name"] == "Arceo Zamora"
    second_user = next(u for u in body["users"] if u["username"] == "sareasmayorga")
    assert second_user["first_name"] == "Sofia Eugenia"
    assert second_user["last_name"] == "Areas Mayorga"
    group_names = [g["name"] for g in first_user["groups"]]
    assert "F26 - ABE4042C" in group_names
    assert first_user["expires_at"].startswith("2026-12-15T23:59:00")

    # Verify student can log in with email prefix as username and (first 4 of username + last 4 of UFID) as password
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "davidarceozamora", "password": "davi0601"},
    )
    assert login_resp.status_code == 200, login_resp.text
    assert login_resp.json()["user"]["username"] == "davidarceozamora"
    assert login_resp.json()["user"]["first_name"] == "David"
    assert login_resp.json()["user"]["last_name"] == "Arceo Zamora"

    # Verify short username (< 4 chars) is right-padded with '.' to 4 chars + last 4 of UFID ("bo..5678")
    short_login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "bo", "password": "bo..5678"},
    )
    assert short_login_resp.status_code == 200, short_login_resp.text
    assert short_login_resp.json()["user"]["username"] == "bo"


@pytest.mark.asyncio
async def test_library_folder_permissions_and_ownership(async_client, db_session):
    """Test folder-level read/write ACLs, ancestor tree traversal, and student folder ownership."""
    _, admin_token = await _create_admin_with_token(db_session)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Create folder hierarchy: Course -> F26 - ABE4042C -> [ExoGlove, GatorByte, Stella]
    r_course = await async_client.post(
        "/api/v1/library/folders", headers=admin_headers, json={"name": "Course"}
    )
    assert r_course.status_code == 200
    course_id = r_course.json()["id"]

    r_f26 = await async_client.post(
        "/api/v1/library/folders",
        headers=admin_headers,
        json={"name": "F26 - ABE4042C", "parent_id": course_id},
    )
    assert r_f26.status_code == 200
    f26_id = r_f26.json()["id"]

    r_exo = await async_client.post(
        "/api/v1/library/folders",
        headers=admin_headers,
        json={"name": "ExoGlove", "parent_id": f26_id},
    )
    exo_id = r_exo.json()["id"]

    r_gator = await async_client.post(
        "/api/v1/library/folders",
        headers=admin_headers,
        json={"name": "GatorByte", "parent_id": f26_id},
    )
    gator_id = r_gator.json()["id"]

    r_stella = await async_client.post(
        "/api/v1/library/folders",
        headers=admin_headers,
        json={"name": "Stella", "parent_id": f26_id},
    )
    assert r_stella.status_code == 200

    # 2. Create two student groups with library permissions and two students
    student_perms = [
        "library:read_all",
        "library:read_own",
        "library:upload",
        "library:update_own",
        "library:delete_own",
    ]
    g_exo_resp = await async_client.post(
        "/api/v1/groups/",
        headers=admin_headers,
        json={"name": "Team ExoGlove", "permissions": student_perms},
    )
    g_exo_id = g_exo_resp.json()["id"]

    g_gator_resp = await async_client.post(
        "/api/v1/groups/",
        headers=admin_headers,
        json={"name": "Team GatorByte", "permissions": student_perms},
    )
    g_gator_id = g_gator_resp.json()["id"]

    u_exo_resp = await async_client.post(
        "/api/v1/users/",
        headers=admin_headers,
        json={
            "username": "student_exo",
            "password": "StudentPass1!",
            "role": "user",
            "group_ids": [g_exo_id],
        },
    )
    assert u_exo_resp.status_code in (200, 201), u_exo_resp.text
    u_exo_id = u_exo_resp.json()["id"]
    exo_token = create_access_token(data={"sub": "student_exo", "role": "user"})
    exo_headers = {"Authorization": f"Bearer {exo_token}"}

    u_gator_resp = await async_client.post(
        "/api/v1/users/",
        headers=admin_headers,
        json={
            "username": "student_gator",
            "password": "StudentPass1!",
            "role": "user",
            "group_ids": [g_gator_id],
        },
    )
    assert u_gator_resp.status_code in (200, 201), u_gator_resp.text
    u_gator_id = u_gator_resp.json()["id"]
    gator_token = create_access_token(data={"sub": "student_gator", "role": "user"})
    gator_headers = {"Authorization": f"Bearer {gator_token}"}

    # 3. Default deny: before granting folder permissions, neither student sees any folders
    exo_folders = (await async_client.get("/api/v1/library/folders", headers=exo_headers)).json()
    assert exo_folders == []

    # 4. Admin grants Team ExoGlove Read & Write on ExoGlove, and Team GatorByte Read-Only on GatorByte
    perm_exo = await async_client.put(
        f"/api/v1/library/folders/{exo_id}/permissions",
        headers=admin_headers,
        json={"permissions": [{"group_id": g_exo_id, "can_read": True, "can_write": True}]},
    )
    assert perm_exo.status_code == 200

    perm_gator = await async_client.put(
        f"/api/v1/library/folders/{gator_id}/permissions",
        headers=admin_headers,
        json={"permissions": [{"group_id": g_gator_id, "can_read": True, "can_write": False}]},
    )
    assert perm_gator.status_code == 200

    # 5. Verify student_exo sees Course (traverse-only) -> F26 - ABE4042C (traverse-only) -> ExoGlove (read+write)
    #    and does NOT see GatorByte or Stella
    exo_tree = (await async_client.get("/api/v1/library/folders", headers=exo_headers)).json()
    assert len(exo_tree) == 1
    assert exo_tree[0]["name"] == "Course"
    assert exo_tree[0]["can_read"] is False
    assert exo_tree[0]["can_write"] is False
    f26_node = exo_tree[0]["children"][0]
    assert f26_node["name"] == "F26 - ABE4042C"
    assert f26_node["can_read"] is False
    child_names = [c["name"] for c in f26_node["children"]]
    assert child_names == ["ExoGlove"]
    assert f26_node["children"][0]["can_read"] is True
    assert f26_node["children"][0]["can_write"] is True

    # student_exo can create a subfolder inside ExoGlove (has write permission)
    sub_resp = await async_client.post(
        "/api/v1/library/folders",
        headers=exo_headers,
        json={"name": "FingerJoints", "parent_id": exo_id},
    )
    assert sub_resp.status_code == 200

    # 6. Verify student_gator sees GatorByte as Read-Only and cannot create a subfolder inside GatorByte
    gator_tree = (await async_client.get("/api/v1/library/folders", headers=gator_headers)).json()
    gator_children = gator_tree[0]["children"][0]["children"]
    assert [c["name"] for c in gator_children] == ["GatorByte"]
    assert gator_children[0]["can_read"] is True
    assert gator_children[0]["can_write"] is False

    forbidden_sub = await async_client.post(
        "/api/v1/library/folders",
        headers=gator_headers,
        json={"name": "ForbiddenSub", "parent_id": gator_id},
    )
    assert forbidden_sub.status_code == 403

    # 7. Student creates their own top-level folder -> owns it (can_manage=True) -> shares with another student
    own_folder_resp = await async_client.post(
        "/api/v1/library/folders",
        headers=exo_headers,
        json={"name": "DavidPersonalProject"},
    )
    assert own_folder_resp.status_code == 200
    own_folder_id = own_folder_resp.json()["id"]
    assert own_folder_resp.json()["created_by_id"] == u_exo_id
    assert own_folder_resp.json()["can_manage"] is True
    assert own_folder_resp.json()["is_shared"] is False

    # Private My Files folders are not visible to other users (even admins)
    admin_tree_before = (await async_client.get("/api/v1/library/folders", headers=admin_headers)).json()
    assert "DavidPersonalProject" not in [f["name"] for f in admin_tree_before]
    assert next(f for f in admin_tree_before if f["name"] == "Course")["is_shared"] is True

    # student_gator cannot see DavidPersonalProject yet
    gator_tree_before = (await async_client.get("/api/v1/library/folders", headers=gator_headers)).json()
    assert "DavidPersonalProject" not in [f["name"] for f in gator_tree_before]

    # student_exo shares DavidPersonalProject with student_gator
    share_resp = await async_client.put(
        f"/api/v1/library/folders/{own_folder_id}/permissions",
        headers=exo_headers,
        json={"permissions": [{"user_id": u_gator_id, "can_read": True, "can_write": True}]},
    )
    assert share_resp.status_code == 200

    # Now student_gator sees DavidPersonalProject in Shared Files with read & write access!
    gator_tree_after = (await async_client.get("/api/v1/library/folders", headers=gator_headers)).json()
    shared_node = next((f for f in gator_tree_after if f["name"] == "DavidPersonalProject"), None)
    assert shared_node is not None
    assert shared_node["is_shared"] is True
    assert shared_node["can_read"] is True
    assert shared_node["can_write"] is True
    assert shared_node["can_manage"] is False


@pytest.mark.asyncio
async def test_delete_group_with_delete_users_guardrails(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Deleting a group with delete_users=true deletes single-group students
    while preserving multi-group students and admin users."""
    _, token = await _create_admin_with_token(db_session)
    admin_headers = {"Authorization": f"Bearer {token}"}

    # Create two course cohort groups
    g_fall = (
        await async_client.post(
            "/api/v1/groups/",
            headers=admin_headers,
            json={"name": "Fall 2026 Cohort", "permissions": ["library:read"]},
        )
    ).json()
    g_spring = (
        await async_client.post(
            "/api/v1/groups/",
            headers=admin_headers,
            json={"name": "Spring 2027 Cohort", "permissions": ["library:read"]},
        )
    ).json()

    # 1. Single-group student (only in Fall 2026 Cohort) -> should be deleted
    u_single = (
        await async_client.post(
            "/api/v1/users/",
            headers=admin_headers,
            json={
                "username": "single_cohort_student",
                "password": "Password123!",
                "group_ids": [g_fall["id"]],
            },
        )
    ).json()

    # 2. Multi-group student (in Fall 2026 Cohort AND Spring 2027 Cohort) -> should be preserved
    u_multi = (
        await async_client.post(
            "/api/v1/users/",
            headers=admin_headers,
            json={
                "username": "multi_cohort_student",
                "password": "Password123!",
                "group_ids": [g_fall["id"], g_spring["id"]],
            },
        )
    ).json()

    # 3. Admin user who is also in Fall 2026 Cohort -> should be preserved
    u_admin2 = (
        await async_client.post(
            "/api/v1/users/",
            headers=admin_headers,
            json={
                "username": "course_instructor_admin",
                "password": "Password123!",
                "role": "admin",
                "group_ids": [g_fall["id"]],
            },
        )
    ).json()

    # Delete Fall 2026 Cohort with delete_users=true
    del_resp = await async_client.delete(
        f"/api/v1/groups/{g_fall['id']}?delete_users=true",
        headers=admin_headers,
    )
    assert del_resp.status_code == 204

    # Verify Fall 2026 Cohort is gone
    get_group = await async_client.get(f"/api/v1/groups/{g_fall['id']}", headers=admin_headers)
    assert get_group.status_code == 404

    # Check remaining users
    users_list = (await async_client.get("/api/v1/users/", headers=admin_headers)).json()
    remaining_ids = {u["id"] for u in users_list}

    assert u_single["id"] not in remaining_ids, "Single-cohort student should be deleted"
    assert u_multi["id"] in remaining_ids, "Multi-cohort student should be preserved"
    assert u_admin2["id"] in remaining_ids, "Admin user should never be deleted via group delete"

    # Verify multi-cohort student still belongs to Spring 2027 Cohort
    multi_after = next(u for u in users_list if u["id"] == u_multi["id"])
    assert [g["id"] for g in multi_after["groups"]] == [g_spring["id"]]




