import csv
import io
from datetime import datetime, timezone
from typing import Annotated

import jwt as _jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.settings import get_external_login_url
from backend.app.core.auth import (
    ALGORITHM,
    SECRET_KEY,
    RequireAdminIfAuthEnabled,
    RequireAnyPermissionIfAuthEnabled,
    RequirePermissionIfAuthEnabled,
    create_access_token,
    get_current_user_optional,
    get_password_hash,
    revoke_jti,
    security,
    verify_password,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType
from backend.app.models.connected_app import ConnectedApp, ConnectedAppGrant
from backend.app.models.enrollment_invite import EnrollmentInvite, _generate_invite_code
from backend.app.models.finance import CostCenter, CostCenterMember
from backend.app.models.group import Group
from backend.app.models.library import LibraryFile, LibraryFolder, LibraryFolderPermission
from backend.app.models.long_lived_token import LongLivedToken
from backend.app.models.oidc_provider import UserOIDCLink
from backend.app.models.print_batch import PrintBatch
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.models.user_otp_code import UserOTPCode
from backend.app.models.user_totp import UserTOTP
from backend.app.schemas.auth import (
    ChangePasswordRequest,
    EnrollmentInviteCreate,
    EnrollmentInviteResponse,
    EnrollmentRedeemRequest,
    EnrollmentRedeemResponse,
    GroupBrief,
    RosterImportRequest,
    RosterImportResponse,
    UserCreate,
    UserResponse,
    UserSlim,
    UserUpdate,
)
from backend.app.services.email_service import (
    create_welcome_email_from_template,
    generate_secure_password,
    get_smtp_settings,
    send_email,
)
from backend.app.services.finance_defaults import ensure_user_finance_defaults
from backend.app.utils.library_paths import remove_library_photos_dir
from backend.app.utils.local_time import to_naive_utc, utcnow_naive

router = APIRouter(prefix="/users", tags=["users"])


def _group_to_brief(g: Group) -> GroupBrief:
    return GroupBrief(
        id=g.id,
        name=g.name,
        expires_at=g.expires_at.isoformat() if getattr(g, "expires_at", None) else None,
        is_expired=bool(getattr(g, "is_expired", False)),
        max_print_duration_minutes=getattr(g, "max_print_duration_minutes", None),
        max_filament_grams_per_job=getattr(g, "max_filament_grams_per_job", None),
        max_active_queue_items=getattr(g, "max_active_queue_items", None),
        requires_approval=bool(getattr(g, "requires_approval", False)),
        require_project=bool(getattr(g, "require_project", False)),
        allowed_hours_start=getattr(g, "allowed_hours_start", None),
        allowed_hours_end=getattr(g, "allowed_hours_end", None),
    )


def _user_to_response(user: User) -> UserResponse:
    """Convert a User model to UserResponse schema."""
    return UserResponse(
        id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        role=user.role,
        is_active=user.is_effectively_active,
        is_admin=user.is_admin,
        auth_source=getattr(user, "auth_source", "local"),
        groups=[_group_to_brief(g) for g in user.groups],
        permissions=sorted(user.get_permissions()),
        created_at=user.created_at.isoformat(),
        expires_at=user.expires_at.isoformat() if user.expires_at else None,
        is_expired=user.is_expired,
        certified_until=user.certified_until.isoformat() if user.certified_until else None,
        require_safety_certification=bool(user.require_safety_certification),
        is_certification_expired=user.is_certification_expired,
        project_waiver=bool(getattr(user, "project_waiver", False)),
        requires_project=bool(getattr(user, "requires_project_for_print", False)),
    )


@router.get("", response_model=list[UserResponse])
@router.get("/", response_model=list[UserResponse])
async def list_users(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """List all users.

    Read-only — gated on ``USERS_READ`` only. Operator-visible UIs
    (Stats filter-by-user, Archives Print Log username column, File
    Manager username autocomplete) consume this endpoint via custom-
    group ``users:read`` grants without admin role. The admin-only
    boundary lives on the write endpoints below."""
    result = await db.execute(select(User).options(selectinload(User.groups)).order_by(User.created_at))
    users = result.scalars().all()
    return [_user_to_response(user) for user in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user.

    When advanced authentication is enabled:
    - Email is required
    - Password is auto-generated and emailed to user
    - Admin cannot set or see the password
    """
    import logging

    logger = logging.getLogger(__name__)

    # Check if advanced auth is enabled
    result = await db.execute(select(Settings).where(Settings.key == "advanced_auth_enabled"))
    advanced_auth_setting = result.scalar_one_or_none()
    advanced_auth_enabled = advanced_auth_setting and advanced_auth_setting.value.lower() == "true"

    # Check if username already exists (case-insensitive)
    existing_user = await db.execute(select(User).where(func.lower(User.username) == func.lower(user_data.username)))
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # Validate role
    if user_data.role not in ["admin", "user"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'",
        )

    # Advanced auth validation
    if advanced_auth_enabled:
        if not user_data.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is required when advanced authentication is enabled",
            )
        # Check if email already exists (case-insensitive)
        existing_email = await db.execute(select(User).where(func.lower(User.email) == func.lower(user_data.email)))
        if existing_email.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists",
            )

    # Generate password if advanced auth enabled, otherwise require password
    if advanced_auth_enabled:
        password = generate_secure_password()
    else:
        if not user_data.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is required when advanced authentication is disabled",
            )
        password = user_data.password

    expires_at = to_naive_utc(user_data.expires_at)
    is_active = expires_at is None or expires_at > utcnow_naive()

    new_user = User(
        username=user_data.username,
        first_name=user_data.first_name.strip() if user_data.first_name and user_data.first_name.strip() else None,
        last_name=user_data.last_name.strip() if user_data.last_name and user_data.last_name.strip() else None,
        email=user_data.email,
        password_hash=get_password_hash(password),
        role=user_data.role,
        is_active=is_active,
        expires_at=expires_at,
        certified_until=to_naive_utc(user_data.certified_until),
        require_safety_certification=bool(user_data.require_safety_certification),
        project_waiver=bool(user_data.project_waiver),
    )

    # Handle group assignments
    if user_data.group_ids:
        groups_result = await db.execute(select(Group).where(Group.id.in_(user_data.group_ids)))
        groups = groups_result.scalars().all()
        if len(groups) != len(user_data.group_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more group IDs are invalid",
            )
        new_user.groups = list(groups)

    db.add(new_user)
    await db.flush()
    await ensure_user_finance_defaults(db, new_user)
    await db.commit()
    await db.refresh(new_user)

    # Send welcome email if advanced auth enabled
    if advanced_auth_enabled and new_user.email:
        try:
            smtp_settings = await get_smtp_settings(db)
            if smtp_settings:
                login_url = await get_external_login_url(db)
                subject, text_body, html_body = await create_welcome_email_from_template(
                    db, new_user.username, password, login_url
                )
                send_email(smtp_settings, new_user.email, subject, text_body, html_body)
                logger.info(f"Welcome email sent to {new_user.email}")
            else:
                logger.warning(f"SMTP not configured, could not send welcome email to {new_user.email}")
        except Exception as e:
            logger.error(f"Failed to send welcome email: {e}")
            # Don't fail user creation if email fails

    return _user_to_response(new_user)


@router.get("/slim", response_model=list[UserSlim])
async def list_users_slim(
    _: User | None = RequireAnyPermissionIfAuthEnabled(Permission.USERS_READ_SLIM, Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """List users as ``{id, username}`` only (#1894).

    Exists so an API key -- or a group that should not see emails, roles and
    permission sets -- can turn the ``created_by_id`` values it already gets
    back from archives, stats and the queue into names.

    ``USERS_READ`` is accepted alongside ``USERS_READ_SLIM`` because it is
    strictly broader; groups that already hold it keep working without a
    permission backfill. For API keys only the slim permission resolves (the
    full one is unmapped = administrative), so a key reaches this and not the
    listing above.

    Declared before ``/{user_id}`` on purpose: FastAPI matches in declaration
    order, and the reverse order would parse "slim" as the int path parameter
    and answer 422.
    """
    result = await db.execute(select(User.id, User.username).order_by(User.username))
    return [UserSlim(id=row.id, username=row.username) for row in result.all()]


def _invite_to_response(invite: EnrollmentInvite) -> EnrollmentInviteResponse:
    return EnrollmentInviteResponse(
        id=invite.id,
        code=invite.code,
        name=invite.name,
        group_id=invite.group_id,
        group_name=invite.group.name if invite.group else None,
        cost_center_id=invite.cost_center_id,
        cost_center_name=invite.cost_center.name if invite.cost_center else None,
        user_expires_at=invite.user_expires_at.isoformat() if invite.user_expires_at else None,
        invite_expires_at=invite.invite_expires_at.isoformat() if invite.invite_expires_at else None,
        max_uses=invite.max_uses,
        uses_count=invite.uses_count,
        is_active=invite.is_active,
        is_redeemable=invite.is_redeemable,
        created_at=invite.created_at.isoformat() if invite.created_at else "",
    )


async def _ensure_cost_center_member(db: AsyncSession, user_id: int, cost_center_id: int) -> None:
    existing = (
        await db.execute(
            select(CostCenterMember).where(
                CostCenterMember.user_id == user_id,
                CostCenterMember.cost_center_id == cost_center_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(CostCenterMember(user_id=user_id, cost_center_id=cost_center_id, can_print=True))


@router.post("/roster-import", response_model=RosterImportResponse)
async def import_roster_csv(
    payload: RosterImportRequest,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Bulk import/upsert a Canvas or UF Registrar student roster CSV.

    Supported columns (header optional): ``username,email,group,cost_center,expires_at``.
    Creates new student accounts or extends ``expires_at`` and updates group /
    cost-center memberships for returning students.
    """
    now_naive = utcnow_naive()
    default_exp = to_naive_utc(payload.default_expires_at)

    # Preload groups and cost centers
    groups_res = await db.execute(select(Group))
    groups_by_id: dict[int, Group] = {g.id: g for g in groups_res.scalars().all()}
    groups_by_name: dict[str, Group] = {g.name.lower(): g for g in groups_by_id.values()}
    operators_group = groups_by_name.get("operators")

    cc_res = await db.execute(select(CostCenter).where(CostCenter.is_private.is_(False)))
    cc_by_id: dict[int, CostCenter] = {c.id: c for c in cc_res.scalars().all()}
    cc_by_key: dict[str, CostCenter] = {}
    for cc in cc_by_id.values():
        cc_by_key[cc.name.lower()] = cc
        if cc.code:
            cc_by_key[cc.code.lower()] = cc

    reader = csv.reader(io.StringIO(payload.csv_content.strip()))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return RosterImportResponse()

    # Detect header row (supports clean CSVs, UF ONE.UF Class Rolls, and UF Canvas Gradebook exports)
    first_lower = [c.strip().lower() for c in rows[0]]
    header_map: dict[str, int] | None = None
    if any(
        h in ("username", "gatorlink", "user", "email", "sis_login_id", "sis login id", "login id", "ufid")
        for h in first_lower
    ):
        header_map = {}
        for idx, col in enumerate(first_lower):
            if col in ("username", "gatorlink", "user", "sis_login_id", "sis login id", "login id"):
                header_map["username"] = idx
            elif col in ("ufid", "sis user id", "sis_user_id", "password"):
                header_map["ufid"] = idx
            elif col in ("first name", "first_name", "firstname", "first", "given name", "given_name"):
                header_map["first_name"] = idx
            elif col in ("last name", "last_name", "lastname", "last", "surname", "family name", "family_name"):
                header_map["last_name"] = idx
            elif col in ("student", "name", "full name", "full_name"):
                header_map["full_name"] = idx
            elif col in ("email", "mail"):
                header_map["email"] = idx
            elif col in ("group", "course", "section", "cohort"):
                header_map["group"] = idx
            elif col in ("cost_center", "costcenter", "budget"):
                header_map["cost_center"] = idx
            elif col in ("expires_at", "expires", "expiration", "termination"):
                header_map["expires_at"] = idx
            elif col == "subject":
                header_map["subject"] = idx
            elif col in ("catalog number", "catalog_number"):
                header_map["catalog_number"] = idx
            elif col == "term":
                header_map["term"] = idx
            elif col in ("session", "subterm", "summer term"):
                header_map["session"] = idx
            elif col in ("class number", "class_number"):
                header_map["class_number"] = idx
            elif col == "status":
                header_map["status"] = idx
        data_rows = rows[1:]
    else:
        data_rows = rows

    def _decode_uf_term(term_code: str, session_code: str = "") -> tuple[str | None, datetime | None]:
        """Decode UF term code into ('SYY', expiry_dt) where S is F, S, SA, SB, or SC.

        Examples:
        - '2268' -> ('F26', 2026-12-15 23:59:00)
        - '2271' -> ('S27', 2027-05-05 23:59:00)
        - '2265' / '2265C' -> ('SC26', 2026-08-10 23:59:00)
        - '2265A' or ('2265', 'A') -> ('SA26', 2026-06-25 23:59:00)
        - '2265B' or ('2265', 'B') -> ('SB26', 2026-08-10 23:59:00)
        """
        t = term_code.strip().upper()
        sess = session_code.strip().upper()
        if len(t) >= 4 and t[:4].isdigit() and t.startswith("2"):
            yy = t[1:3]
            year = 2000 + int(yy)
            sem = t[3]
            suffix = t[4:] or sess
            if sem == "8":
                return f"F{yy}", datetime(year, 12, 15, 23, 59, 0)
            if sem == "1":
                return f"S{yy}", datetime(year, 5, 5, 23, 59, 0)
            if sem in ("5", "6", "7"):
                if "A" in suffix or sem == "6":
                    return f"SA{yy}", datetime(year, 6, 25, 23, 59, 0)
                if "B" in suffix or sem == "7":
                    return f"SB{yy}", datetime(year, 8, 10, 23, 59, 0)
                return f"SC{yy}", datetime(year, 8, 10, 23, 59, 0)
        # Also support direct shorthand ('F26', 'S27', 'SA26', 'SB26', 'SC26')
        for prefix in ("SA", "SB", "SC", "F", "S"):
            if t.startswith(prefix) and len(t) == len(prefix) + 2 and t[len(prefix):].isdigit():
                yy = t[len(prefix):]
                year = 2000 + int(yy)
                if prefix == "F":
                    return f"F{yy}", datetime(year, 12, 15, 23, 59, 0)
                if prefix == "S":
                    return f"S{yy}", datetime(year, 5, 5, 23, 59, 0)
                if prefix == "SA":
                    return f"SA{yy}", datetime(year, 6, 25, 23, 59, 0)
                return f"{prefix}{yy}", datetime(year, 8, 10, 23, 59, 0)
        return None, None

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors: list[str] = []
    touched_user_ids: list[int] = []

    for row_idx, raw_row in enumerate(data_rows, start=1):
        cells = [c.strip() for c in raw_row]
        # Skip Canvas Gradebook metadata/dummy rows ("Points Possible", "Student, Test")
        if cells and cells[0].lower() in ("points possible", "student, test", "test student"):
            continue
        if header_map is not None:
            username = cells[header_map["username"]] if "username" in header_map and header_map["username"] < len(cells) else ""
            ufid_str = cells[header_map["ufid"]] if "ufid" in header_map and header_map["ufid"] < len(cells) else ""
            first_name_str = cells[header_map["first_name"]] if "first_name" in header_map and header_map["first_name"] < len(cells) else ""
            last_name_str = cells[header_map["last_name"]] if "last_name" in header_map and header_map["last_name"] < len(cells) else ""
            full_name_str = cells[header_map["full_name"]] if "full_name" in header_map and header_map["full_name"] < len(cells) else ""
            if not first_name_str and not last_name_str and full_name_str:
                if "," in full_name_str:
                    last_name_str, first_name_str = [p.strip() for p in full_name_str.split(",", 1)]
                elif " " in full_name_str:
                    first_name_str, last_name_str = [p.strip() for p in full_name_str.split(" ", 1)]
                else:
                    first_name_str = full_name_str.strip()
            email = cells[header_map["email"]] if "email" in header_map and header_map["email"] < len(cells) else ""
            group_str = cells[header_map["group"]] if "group" in header_map and header_map["group"] < len(cells) else ""
            cc_str = cells[header_map["cost_center"]] if "cost_center" in header_map and header_map["cost_center"] < len(cells) else ""
            exp_str = cells[header_map["expires_at"]] if "expires_at" in header_map and header_map["expires_at"] < len(cells) else ""
            subject_str = cells[header_map["subject"]] if "subject" in header_map and header_map["subject"] < len(cells) else ""
            catalog_str = cells[header_map["catalog_number"]] if "catalog_number" in header_map and header_map["catalog_number"] < len(cells) else ""
            term_str = cells[header_map["term"]] if "term" in header_map and header_map["term"] < len(cells) else ""
            session_str = cells[header_map["session"]] if "session" in header_map and header_map["session"] < len(cells) else ""
            status_str = cells[header_map["status"]] if "status" in header_map and header_map["status"] < len(cells) else ""

            # Skip dropped/withdrawn students in UF Class Roll exports
            if status_str and status_str.lower() not in ("enrolled", "active"):
                skipped_count += 1
                continue

            # Synthesize course group ('SYY - Course'), cost center, and semester expiration from UF Class Roll columns
            term_label, term_exp_dt = _decode_uf_term(term_str, session_str) if term_str else (None, None)
            course_code = f"{subject_str}{catalog_str}".replace(" ", "") if (subject_str or catalog_str) else ""
            if not group_str and payload.default_group_id is None and course_code:
                group_str = f"{term_label} - {course_code}" if term_label else course_code
            if not cc_str and payload.default_cost_center_id is None and course_code:
                cc_str = course_code
        else:
            username = cells[0] if len(cells) > 0 else ""
            email = cells[1] if len(cells) > 1 else ""
            group_str = cells[2] if len(cells) > 2 else ""
            cc_str = cells[3] if len(cells) > 3 else ""
            exp_str = cells[4] if len(cells) > 4 else ""
            ufid_str = ""
            first_name_str = ""
            last_name_str = ""
            term_exp_dt = None

        # If SIS Login ID is an email address (e.g. gator@ufl.edu), derive email and username
        if username and "@" in username and not email:
            email = username
            username = username.split("@", 1)[0]

        if not username and email and "@" in email:
            username = email.split("@", 1)[0]

        if not username:
            skipped_count += 1
            errors.append(f"Row {row_idx}: missing username")
            continue

        row_exp = default_exp if default_exp is not None else term_exp_dt
        if exp_str:
            try:
                parsed_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                row_exp = to_naive_utc(parsed_dt)
            except ValueError:
                errors.append(f"Row {row_idx} ({username}): invalid expires_at '{exp_str}'")
                skipped_count += 1
                continue

        # Resolve target group
        target_group: Group | None = None
        if group_str:
            target_group = groups_by_name.get(group_str.lower())
            # Upgrade legacy '<Course>-<Semester><YYYY>' group name in-place if present
            if target_group is None and " - " in group_str:
                syy_part, course_part = [p.strip() for p in group_str.split(" - ", 1)]
                legacy_candidates = []
                if syy_part.startswith("F") and len(syy_part) == 3:
                    legacy_candidates.append(f"{course_part}-Fall20{syy_part[1:]}")
                elif syy_part.startswith("S") and len(syy_part) == 3:
                    legacy_candidates.append(f"{course_part}-Spring20{syy_part[1:]}")
                elif syy_part.startswith(("SA", "SB", "SC")) and len(syy_part) == 4:
                    legacy_candidates.append(f"{course_part}-Summer20{syy_part[2:]}")
                for leg in legacy_candidates:
                    found_leg = groups_by_name.pop(leg.lower(), None)
                    if found_leg is not None:
                        found_leg.name = group_str
                        groups_by_name[group_str.lower()] = found_leg
                        target_group = found_leg
                        break
            if target_group is None:
                # Auto-create cohort group with standard Operators permissions
                default_perms = list(operators_group.permissions) if operators_group and operators_group.permissions else []
                target_group = Group(
                    name=group_str,
                    description="Auto-created from roster import",
                    permissions=default_perms,
                    is_system=False,
                    expires_at=row_exp,
                    require_project=True,
                )
                db.add(target_group)
                await db.flush()
                groups_by_id[target_group.id] = target_group
                groups_by_name[target_group.name.lower()] = target_group
        elif payload.default_group_id is not None:
            target_group = groups_by_id.get(payload.default_group_id)
        elif operators_group is not None:
            target_group = operators_group

        # Resolve target cost center
        target_cc: CostCenter | None = None
        if cc_str:
            target_cc = cc_by_key.get(cc_str.lower())
            if target_cc is None:
                target_cc = CostCenter(name=cc_str, is_active=True, is_private=False)
                db.add(target_cc)
                await db.flush()
                cc_by_id[target_cc.id] = target_cc
                cc_by_key[target_cc.name.lower()] = target_cc
        elif payload.default_cost_center_id is not None:
            target_cc = cc_by_id.get(payload.default_cost_center_id)

        # Look up existing user by username or email
        existing_q = select(User).where(User.username == username).options(selectinload(User.groups))
        user = (await db.execute(existing_q)).scalar_one_or_none()
        if user is None and email:
            email_q = select(User).where(User.email == email).options(selectinload(User.groups))
            user = (await db.execute(email_q)).scalar_one_or_none()

        clean_ufid = ufid_str.replace("-", "").strip()
        derived_ufid_password = (
            f"{username[:4].ljust(4, '.')}{clean_ufid[-4:]}" if clean_ufid else ""
        )

        if user is None:
            initial_password = derived_ufid_password if derived_ufid_password else generate_secure_password()
            user = User(
                username=username,
                first_name=first_name_str or None,
                last_name=last_name_str or None,
                email=email or None,
                password_hash=get_password_hash(initial_password),
                role="user",
                is_active=bool(row_exp is None or row_exp > now_naive),
                expires_at=row_exp,
            )
            if target_group is not None:
                user.groups = [target_group]
            db.add(user)
            await db.flush()
            await ensure_user_finance_defaults(db, user)
            if target_cc is not None:
                await _ensure_cost_center_member(db, user.id, target_cc.id)
            created_count += 1
            touched_user_ids.append(user.id)
        else:
            if first_name_str:
                user.first_name = first_name_str
            if last_name_str:
                user.last_name = last_name_str
            if email and not user.email:
                user.email = email
            if derived_ufid_password and user.password_changed_at is None:
                user.password_hash = get_password_hash(derived_ufid_password)
            if row_exp is not None:
                if user.expires_at is None or row_exp > user.expires_at:
                    user.expires_at = row_exp
                    user.expiry_warned_7d_at = None
                    user.expiry_warned_1d_at = None
                if row_exp > now_naive:
                    user.is_active = True
            if target_group is not None and all(g.id != target_group.id for g in user.groups):
                user.groups.append(target_group)
            await ensure_user_finance_defaults(db, user)
            if target_cc is not None:
                await _ensure_cost_center_member(db, user.id, target_cc.id)
            updated_count += 1
            touched_user_ids.append(user.id)

    await db.commit()

    users_list: list[UserResponse] = []
    if touched_user_ids:
        final_res = await db.execute(
            select(User).where(User.id.in_(touched_user_ids)).options(selectinload(User.groups))
        )
        users_list = [_user_to_response(u) for u in final_res.scalars().all()]

    return RosterImportResponse(
        created_count=created_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        errors=errors,
        users=users_list,
    )


@router.get("/invites", response_model=list[EnrollmentInviteResponse])
async def list_enrollment_invites(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """List all self-service enrollment invite codes."""
    result = await db.execute(
        select(EnrollmentInvite)
        .options(selectinload(EnrollmentInvite.group), selectinload(EnrollmentInvite.cost_center))
        .order_by(EnrollmentInvite.created_at.desc())
    )
    return [_invite_to_response(inv) for inv in result.scalars().all()]


@router.post("/invites", response_model=EnrollmentInviteResponse, status_code=status.HTTP_201_CREATED)
async def create_enrollment_invite(
    data: EnrollmentInviteCreate,
    admin_user: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Create a self-service enrollment invite code bound to a Group, CostCenter, and expiration date."""
    code = (data.code.strip().upper() if data.code and data.code.strip() else _generate_invite_code())
    existing = (await db.execute(select(EnrollmentInvite).where(EnrollmentInvite.code == code))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite code already exists")

    invite = EnrollmentInvite(
        code=code,
        name=data.name,
        group_id=data.group_id,
        cost_center_id=data.cost_center_id,
        user_expires_at=to_naive_utc(data.user_expires_at),
        invite_expires_at=to_naive_utc(data.invite_expires_at),
        max_uses=data.max_uses,
        uses_count=0,
        is_active=True,
        created_by_id=admin_user.id if admin_user else None,
    )
    db.add(invite)
    await db.commit()
    res = await db.execute(
        select(EnrollmentInvite)
        .where(EnrollmentInvite.id == invite.id)
        .options(selectinload(EnrollmentInvite.group), selectinload(EnrollmentInvite.cost_center))
    )
    return _invite_to_response(res.scalar_one())


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_enrollment_invite(
    invite_id: int,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    """Delete / revoke an enrollment invite code."""
    invite = (await db.execute(select(EnrollmentInvite).where(EnrollmentInvite.id == invite_id))).scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment invite not found")
    await db.delete(invite)
    await db.commit()


@router.post("/invites/redeem", response_model=EnrollmentRedeemResponse)
async def redeem_enrollment_invite(
    payload: EnrollmentRedeemRequest,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Redeem an enrollment invite code.

    Works for both:
    - An authenticated user (e.g. UF GatorLink SSO user) passing ``{"code": "..."}``
    - A new student self-registering with ``{"code": "...", "username": "...", "password": "...", "email": "..."}``
    """
    code = payload.code.strip().upper()
    res = await db.execute(
        select(EnrollmentInvite)
        .where(EnrollmentInvite.code == code)
        .options(selectinload(EnrollmentInvite.group), selectinload(EnrollmentInvite.cost_center))
    )
    invite = res.scalar_one_or_none()
    if not invite or not invite.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enrollment invite code is invalid or inactive",
        )
    if invite.is_expired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enrollment invite code has expired",
        )
    if invite.is_exhausted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enrollment invite code has reached its maximum number of uses",
        )

    issued_token: str | None = None
    now_naive = utcnow_naive()

    if current_user is not None:
        user_res = await db.execute(
            select(User).where(User.id == current_user.id).options(selectinload(User.groups))
        )
        user = user_res.scalar_one()
    else:
        if not payload.username or not payload.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="username and password are required when redeeming an invite without an active session",
            )
        existing_user = (
            await db.execute(select(User).where(User.username == payload.username).options(selectinload(User.groups)))
        ).scalar_one_or_none()
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists; please sign in first to redeem this invite code",
            )
        user = User(
            username=payload.username,
            first_name=payload.first_name.strip() if payload.first_name and payload.first_name.strip() else None,
            last_name=payload.last_name.strip() if payload.last_name and payload.last_name.strip() else None,
            email=payload.email or None,
            password_hash=get_password_hash(payload.password),
            role="user",
            is_active=True,
            expires_at=invite.user_expires_at,
        )
        db.add(user)
        await db.flush()
        user_res = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
        user = user_res.scalar_one()
        issued_token = create_access_token(data={"sub": user.username, "role": user.role})

    # Apply expiration extension from invite
    if invite.user_expires_at is not None:
        if user.expires_at is None or invite.user_expires_at > user.expires_at:
            user.expires_at = invite.user_expires_at
            user.expiry_warned_7d_at = None
            user.expiry_warned_1d_at = None
        if invite.user_expires_at > now_naive:
            user.is_active = True

    # Assign group
    if invite.group is not None and all(g.id != invite.group.id for g in user.groups):
        user.groups.append(invite.group)

    await ensure_user_finance_defaults(db, user)
    if invite.cost_center_id is not None:
        await _ensure_cost_center_member(db, user.id, invite.cost_center_id)

    invite.uses_count += 1
    await db.commit()

    refreshed = (
        await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
    ).scalar_one()

    return EnrollmentRedeemResponse(
        user=_user_to_response(refreshed),
        invite_name=invite.name,
        access_token=issued_token,
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get a user by ID. Read-only — gated on ``USERS_READ`` only."""
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return _user_to_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Update a user."""
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    now_naive = utcnow_naive()
    expires_at_provided = "expires_at" in user_data.model_fields_set
    new_expires_at = to_naive_utc(user_data.expires_at) if expires_at_provided else user.expires_at
    would_expire_immediately = (
        expires_at_provided and new_expires_at is not None and new_expires_at <= now_naive
    )

    # Prevent deactivating or immediately expiring the last admin
    if (user_data.is_active is False or would_expire_immediately) and user.is_admin:
        # Count admins by role or Administrators group membership
        admin_count_result = await db.execute(select(User).where(User.role == "admin", User.is_active.is_(True)))
        role_admins = [u for u in admin_count_result.scalars().all() if u.is_effectively_active]

        # Also check for users in Administrators group
        admin_group_result = await db.execute(
            select(Group).where(Group.name == "Administrators").options(selectinload(Group.users))
        )
        admin_group = admin_group_result.scalar_one_or_none()
        group_admins = [
            u for u in (admin_group.users if admin_group else []) if u.is_effectively_active
        ]

        # Combine unique admins
        all_admins = {u.id for u in role_admins} | {u.id for u in group_admins}
        if len(all_admins) <= 1 and user.id in all_admins:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate the last admin user",
            )

    # Prevent changing role of last admin
    if user_data.role and user_data.role != "admin" and user.role == "admin":
        admin_count_result = await db.execute(select(User).where(User.role == "admin", User.is_active.is_(True)))
        admin_count = len(admin_count_result.scalars().all())
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change role of the last admin user",
            )

    if user_data.username is not None:
        # Check if new username already exists (case-insensitive)
        existing_user = await db.execute(
            select(User).where(func.lower(User.username) == func.lower(user_data.username), User.id != user_id)
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )
        user.username = user_data.username

    if "first_name" in user_data.model_fields_set:
        user.first_name = user_data.first_name.strip() if user_data.first_name and user_data.first_name.strip() else None
    if "last_name" in user_data.model_fields_set:
        user.last_name = user_data.last_name.strip() if user_data.last_name and user_data.last_name.strip() else None

    if user_data.email is not None:
        # Check if new email already exists (case-insensitive)
        existing_email = await db.execute(
            select(User).where(func.lower(User.email) == func.lower(user_data.email), User.id != user_id)
        )
        if existing_email.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists",
            )
        user.email = user_data.email

    if user_data.password is not None:
        if getattr(user, "auth_source", "local") == "ldap":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot set password for LDAP users",
            )
        user.password_hash = get_password_hash(user_data.password)

    if user_data.role is not None:
        if user_data.role not in ["admin", "user"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role must be 'admin' or 'user'",
            )
        user.role = user_data.role

    was_expired = user.is_expired
    if expires_at_provided:
        user.expires_at = new_expires_at
        user.expiry_warned_7d_at = None
        user.expiry_warned_1d_at = None

    if "certified_until" in user_data.model_fields_set:
        user.certified_until = to_naive_utc(user_data.certified_until)
    if user_data.require_safety_certification is not None:
        user.require_safety_certification = user_data.require_safety_certification
    if user_data.project_waiver is not None:
        user.project_waiver = user_data.project_waiver

    if user_data.is_active is not None:
        user.is_active = user_data.is_active
    elif expires_at_provided:
        if new_expires_at is not None and new_expires_at <= now_naive:
            user.is_active = False
        elif (was_expired or not user.is_active) and (new_expires_at is None or new_expires_at > now_naive):
            user.is_active = True

    # Handle group assignments
    if user_data.group_ids is not None:
        groups_result = await db.execute(select(Group).where(Group.id.in_(user_data.group_ids)))
        groups = groups_result.scalars().all()
        if len(groups) != len(user_data.group_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more group IDs are invalid",
            )
        user.groups = list(groups)

    await ensure_user_finance_defaults(db, user)

    await db.commit()
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one()

    return _user_to_response(user)


@router.get("/{user_id}/items-count")
async def get_user_items_count(
    user_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get count of items created by this user. Read-only — gated on
    ``USERS_READ`` only."""
    # Verify user exists
    result = await db.execute(select(User).where(User.id == user_id))
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Count archives
    archives_result = await db.execute(select(func.count(PrintArchive.id)).where(PrintArchive.created_by_id == user_id))
    archives_count = archives_result.scalar() or 0

    # Count queue items
    queue_result = await db.execute(
        select(func.count(PrintQueueItem.id)).where(PrintQueueItem.created_by_id == user_id)
    )
    queue_items_count = queue_result.scalar() or 0

    # Count library files
    library_result = await db.execute(
        select(func.count(LibraryFile.id)).where(
            LibraryFile.created_by_id == user_id,
            LibraryFile.deleted_at.is_(None),
        )
    )
    library_files_count = library_result.scalar() or 0

    return {
        "archives": archives_count,
        "queue_items": queue_items_count,
        "library_files": library_files_count,
    }


async def purge_user_account(
    db: AsyncSession,
    user: User,
    *,
    delete_items: bool = False,
) -> list[int]:
    """Delete a user account and clean up all dependent records (SQLite-safe).

    Returns the list of ``LibraryFile.id``s whose photo directories should be
    removed from disk after the transaction commits.
    """
    from sqlalchemy import update as _update

    user_id = user.id
    # Photo directories of the library rows about to go, resolved while the
    # rows still exist to say which ids they belong to. Removed after the
    # commit, so a failed delete leaves the pictures alone (#3077).
    doomed_library_file_ids: list[int] = []
    if delete_items:
        doomed_library_file_ids = list(
            (await db.execute(select(LibraryFile.id).where(LibraryFile.created_by_id == user_id))).scalars().all()
        )
        # Delete all items created by this user
        await db.execute(delete(PrintArchive).where(PrintArchive.created_by_id == user_id))
        await db.execute(delete(PrintQueueItem).where(PrintQueueItem.created_by_id == user_id))
        await db.execute(delete(LibraryFile).where(LibraryFile.created_by_id == user_id))
        await db.execute(delete(PrintBatch).where(PrintBatch.created_by_id == user_id))
    else:
        # Explicitly set created_by_id to NULL for all items (ensures consistent behavior
        # across different database backends, including SQLite without foreign key support).
        # PrintBatch carries the same created_by_id FK with ondelete=SET NULL — admin-deleted
        # users would otherwise leave dangling created_by_id on SQLite (#1295 review nit).
        await db.execute(_update(PrintArchive).where(PrintArchive.created_by_id == user_id).values(created_by_id=None))
        await db.execute(
            _update(PrintQueueItem).where(PrintQueueItem.created_by_id == user_id).values(created_by_id=None)
        )
        await db.execute(_update(LibraryFile).where(LibraryFile.created_by_id == user_id).values(created_by_id=None))
        await db.execute(_update(PrintBatch).where(PrintBatch.created_by_id == user_id).values(created_by_id=None))

    await db.execute(_update(LibraryFolder).where(LibraryFolder.created_by_id == user_id).values(created_by_id=None))
    await db.execute(delete(LibraryFolderPermission).where(LibraryFolderPermission.user_id == user_id))
    await db.execute(delete(CostCenterMember).where(CostCenterMember.user_id == user_id))
    await db.execute(_update(EnrollmentInvite).where(EnrollmentInvite.created_by_id == user_id).values(created_by_id=None))

    # Drop API keys owned by this user. The model declares ON DELETE CASCADE
    # so Postgres handles this automatically, but SQLite ships with FK
    # enforcement off (the project's existing pattern — same reason the
    # blocks above set created_by_id = NULL by hand). Without an explicit
    # DELETE here, deleting a user on SQLite would leave their API keys
    # with a dangling user_id and ``_user_from_api_key`` would return None,
    # silently degrading the keys to anonymous (and locking them out of
    # /cloud/* — but the rest of the API would still accept them, which is
    # exactly the orphan-key state the CASCADE was meant to prevent).
    await db.execute(delete(APIKey).where(APIKey.user_id == user_id))

    # Drop OIDC links, MFA state, and long-lived camera-stream tokens
    # owned by this user. Same SQLite/FK pattern as APIKey above. Without
    # these, deleting a user on SQLite leaves:
    #   - UserOIDCLink: the OIDC callback finds the orphan link, fails to
    #     resolve the (now missing) user, and falls through to
    #     "account_inactive" instead of triggering auto_create (#1285).
    #   - UserTOTP: MFA secrets persist in the DB after the owning user.
    #   - UserOTPCode: pending email OTP codes linger.
    #   - LongLivedToken: per-user camera-stream tokens whose secret_hash
    #     is still valid — verify() would happily match them by lookup
    #     prefix even though the user is gone.
    await db.execute(delete(UserOIDCLink).where(UserOIDCLink.user_id == user_id))
    await db.execute(delete(UserTOTP).where(UserTOTP.user_id == user_id))
    await db.execute(delete(UserOTPCode).where(UserOTPCode.user_id == user_id))
    await db.execute(delete(LongLivedToken).where(LongLivedToken.user_id == user_id))

    # Connected apps, same SQLite/FK pattern. A leftover consent row would
    # skip the consent screen for whoever is next given this user id, and a
    # code issued in the last minute is keyed by username, not id.
    await db.execute(delete(ConnectedAppGrant).where(ConnectedAppGrant.user_id == user_id))
    await db.execute(
        delete(AuthEphemeralToken).where(
            AuthEphemeralToken.token_type == TokenType.CONNECT_CODE,
            func.lower(AuthEphemeralToken.username) == user.username.lower(),
        )
    )
    await db.execute(_update(ConnectedApp).where(ConnectedApp.created_by_id == user_id).values(created_by_id=None))

    await db.delete(user)
    return doomed_library_file_ids


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    delete_items: bool = Query(False, description="Delete all items created by this user"),
    _admin: User | None = RequireAdminIfAuthEnabled(),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user.

    If delete_items=True, all archives, queue items, and library files created by
    this user will also be deleted. Otherwise, these items will become "ownerless"
    (created_by_id set to NULL by the foreign key constraint).
    """
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent deleting the last admin
    if user.is_admin:
        # Count admins by role or Administrators group membership
        admin_count_result = await db.execute(select(User).where(User.role == "admin", User.id != user_id))
        other_role_admins = admin_count_result.scalars().all()

        # Also check for users in Administrators group
        admin_group_result = await db.execute(
            select(Group).where(Group.name == "Administrators").options(selectinload(Group.users))
        )
        admin_group = admin_group_result.scalar_one_or_none()
        other_group_admins = [u for u in (admin_group.users if admin_group else []) if u.id != user_id and u.is_active]

        # Combine unique admins
        all_other_admins = {u.id for u in other_role_admins} | {u.id for u in other_group_admins}
        if len(all_other_admins) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the last admin user",
            )

    # Prevent deleting yourself (only if auth is enabled and we have a current user)
    if current_user and user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    doomed_library_file_ids = await purge_user_account(db, user, delete_items=delete_items)
    await db.commit()

    for file_id in doomed_library_file_ids:
        remove_library_photos_dir(file_id)


@router.post("/me/change-password", response_model=dict)
async def change_own_password(
    password_data: ChangePasswordRequest,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password. Requires current password verification."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to change password",
        )

    # Block password change for LDAP users
    if getattr(current_user, "auth_source", "local") == "ldap":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change password for LDAP users — passwords are managed by the LDAP server",
        )

    # Verify current password
    if not current_user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account has no local password set",
        )

    # Rate-limit failed password-change attempts (H-R5-A)
    from backend.app.api.routes.mfa import MAX_2FA_ATTEMPTS, check_rate_limit, record_failed_attempt

    await check_rate_limit(db, current_user.username, event_type="password_change", max_attempts=MAX_2FA_ATTEMPTS)

    if not verify_password(password_data.current_password, current_user.password_hash):
        await record_failed_attempt(db, current_user.username, event_type="password_change")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Fetch user from this session to ensure changes are persisted
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update password
    user.password_hash = get_password_hash(password_data.new_password)
    user.password_changed_at = datetime.now(timezone.utc)  # M-R7-B: invalidate all prior JWTs
    await db.commit()

    # L-R6-A: Password verified successfully — reset the failure counter
    from backend.app.api.routes.mfa import clear_failed_attempts

    await clear_failed_attempts(db, user.username, event_type="password_change")

    # Revoke the current session token so the caller must re-authenticate (M-R5-A)
    if credentials is not None:
        try:
            payload = _jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                try:
                    await revoke_jti(jti, datetime.fromtimestamp(exp, tz=timezone.utc), user.username)
                except Exception as exc:
                    # B4: log so operators know revocation is broken; password was
                    # already changed so the token will fail freshness checks anyway.
                    import logging

                    logging.getLogger(__name__).error(
                        "Failed to revoke JTI after password change for user %s: %s", user.username, exc
                    )
        except Exception:
            pass  # Decode failure is harmless — token is already invalidated by password_changed_at

    return {"message": "Password changed successfully"}
