"""MakerWorld integration routes.

User searches for models or pastes a model URL (MakerWorld or other supported
host) → Bambuddy resolves it → shows plate list → one-click import/print.

Keyword search routes through ``api.bambulab.com/v1/search-service/select/design2``
(the legacy ``design-service/design/search`` endpoint returns empty results from
server-originated requests).

These are still the *MakerWorld* routes: they consult the shared seams where
one exists — URL routing via :class:`ModelProviderRegistry`, permissions and
folder naming from the provider descriptor, already-imported matching via
:meth:`ModelProvider.source_url_filter` — but request/response shapes remain
MakerWorld-specific. The fully shared import API that makes new hosts work
with zero route changes arrives with #2793.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import resolve_api_key_cloud_owner
from backend.app.api.routes.library import save_3mf_bytes_to_library
from backend.app.core.auth import (
    RequirePermissionIfAuthEnabled,
    require_auth_if_enabled,
    require_permission_if_auth_enabled,
    security,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.user import User
from backend.app.schemas.makerworld import (
    MakerWorldImportRequest,
    MakerWorldImportResponse,
    MakerWorldRecentImport,
    MakerWorldResolvedModel,
    MakerWorldResolveRequest,
    MakerWorldSearchResponse,
    MakerWorldStatus,
)
from backend.app.services.model_providers import makerworld_provider, registry
from backend.app.services.model_providers.base import (
    ModelProvider,
    ProviderAuthError,
    ProviderError,
    ProviderForbiddenError,
    ProviderNotFoundError,
    ProviderResourceRef,
    ProviderService,
    ProviderUnavailableError,
    ProviderUrlError,
)
from backend.app.services.model_providers.makerworld.service import MakerWorldService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/makerworld", tags=["makerworld"])


def _provider_for_url(url: str) -> ModelProvider:
    """Return the registered model provider that claims *url*.

    A pasted link for an unsupported host is a clean 400 — the registry is
    the routing seam, and "nobody supports this URL" is a client-input
    problem, not a server error.
    """
    provider = registry.find_for_url(url)
    if provider is None:
        msg = f"No registered model provider supports {url!r}"
        raise HTTPException(status_code=400, detail=msg)
    return provider


def _provider_for_source(source_type: str) -> ModelProvider:
    """Return the registered model provider with this ``source_type``.

    Import identifies a resource by numeric id, not by URL, so there is
    nothing to route on except the source type the caller names. The detail
    is built here rather than via ``str(KeyError)`` — KeyError's ``__str__``
    is the *repr* of its argument and would ship the quotes to the client.
    """
    try:
        return registry.get(source_type)
    except KeyError as exc:
        msg = f"No model provider registered for source_type {source_type!r}"
        raise HTTPException(status_code=400, detail=msg) from exc


async def _authorize_for_provider(
    provider: ModelProvider,
    permission: Permission | None,
    credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> User | None:
    """Apply *provider*'s own permission to a request that named it.

    This cannot live in the route signature. FastAPI resolves dependencies
    before the body exists, so a dependency can only ever bake in one
    provider's permission — MakerWorld's — while the provider actually being
    used comes from the request (``source_type`` on import, the pasted URL on
    resolve). Importing from a second provider would then be gated on
    ``makerworld:import``, which is nobody's intent.

    The check runs through the same ``require_permission_if_auth_enabled``
    the decorator would have built, so JWT users, API keys (scope gate plus
    the owner-outranks-key rule) and auth-disabled installs behave exactly as
    before. The routes keep a permission-free ``require_auth_if_enabled``
    dependency so an anonymous caller is still refused before the body is
    read.

    A provider that declares no permission is refused rather than waved
    through: the descriptor's permission fields are optional, and "unset"
    must not read as "unrestricted".
    """
    if permission is None:
        raise HTTPException(
            status_code=500,
            detail=f"Model provider {provider.source_type!r} declares no permission for this operation",
        )
    checker = require_permission_if_auth_enabled(permission)
    return await checker(credentials=credentials, x_api_key=x_api_key)


async def _build_service(
    db: AsyncSession,
    provider: ModelProvider,
    current_user: User | None,
    api_key_cloud_owner: User | None = None,
) -> ProviderService:
    """Construct a per-request service via *provider*.

    Identity resolution (JWT user vs API-key owner vs anonymous) and
    credential seeding live inside ``provider.build_service`` — the single
    place every provider resolves them, so the routes never re-implement it.
    """
    return await provider.build_service(db=db, user=current_user, api_key_owner=api_key_cloud_owner)


def _map_service_error(exc: ProviderError) -> HTTPException:
    """Translate provider service exceptions into HTTP responses."""
    if isinstance(exc, ProviderUrlError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ProviderAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, ProviderForbiddenError):
        # 403 forwards the provider's own refusal message (content-gated,
        # region-locked, requires points, etc.) — UI surfaces it verbatim.
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ProviderNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProviderUnavailableError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Model provider error: {exc}")


@router.get("/thumbnail")
async def proxy_thumbnail(
    url: str = Query(..., description="MakerWorld CDN image URL (makerworld.bblmw.com or public-cdn.bblmw.com)"),
):
    """Proxy a MakerWorld CDN thumbnail.

    The SPA's ``img-src`` CSP only allows ``'self' data: blob:`` — hotlinking
    from makerworld.bblmw.com is blocked. This endpoint refetches the image
    server-side and returns it with a long cache window.

    **Unauthenticated on purpose**: ``<img>`` tags can't send Authorization
    headers, so requiring a Bearer token here would break the whole feature
    (browsers would get 401 on every image, rendering as broken-image
    placeholders). The thumbnails being proxied are MakerWorld's *public*
    CDN — any visitor to makerworld.com can fetch them without auth — so no
    data is exposed. The SSRF guard inside ``fetch_thumbnail`` restricts
    the upstream host to the MakerWorld CDN allowlist, so this can't be
    abused as a generic open proxy.

    URLs are content-addressable (filename contains a hash), so the
    aggressive ``immutable`` cache-control is safe.
    """
    service = MakerWorldService(thumbnail_hosts=makerworld_provider.thumbnail_hosts())
    try:
        payload, content_type = await service.fetch_thumbnail(url)
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    return Response(
        content=payload,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )


@router.get("/status", response_model=MakerWorldStatus)
async def get_status(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(makerworld_provider.view_permission),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
):
    """Report whether the caller can import 3MFs (needs a Bambu Cloud token).

    API-keyed callers (which return None from ``current_user``) get the
    owner User via ``resolve_api_key_cloud_owner`` when the key carries the
    cloud-access scope, so ``has_cloud_token`` reflects the owning user's
    stored token rather than always reporting ``False`` (#1777, same shape
    as the cloud-presets fix in #1182).
    """
    service = await _build_service(db, makerworld_provider, current_user, api_key_cloud_owner)
    try:
        status = await service.get_status(db)
    finally:
        await service.close()
    return MakerWorldStatus(
        has_cloud_token=status.authenticated,
        can_download=status.can_download,
        # ``credential_rejected`` is the machine-readable "your sign-in
        # expired" state the provider set exactly when a stored token exists
        # *and* was rejected — no token means there is no sign-in to have
        # expired. It is read instead of ``auth_error is not None`` because
        # the latter is a human-readable reason that providers may also set
        # for non-credential failures (network, rate limit).
        sign_in_expired=status.credential_rejected,
    )


_ALLOWED_SEARCH_ORDER_BY = frozenset(
    {
        "score",
        "downloadCount",
        "likeCount",
        "printCount",
        "hotScore",
        "createTime",
    }
)


@router.get("/search", response_model=MakerWorldSearchResponse)
async def search_models(
    q: str = Query(..., description="Search keyword(s)"),
    limit: int = Query(default=20, ge=1, le=50, description="Maximum results to return (1–50)"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    order_by: str = Query(
        default="score",
        description="Sort field: score, downloadCount, likeCount, printCount, hotScore, createTime",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(makerworld_provider.view_permission),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
):
    """Search MakerWorld models by keyword.

    Hits ``api.bambulab.com/v1/search-service/select/design2`` and annotates
    which returned model IDs already exist in the library so the search grid
    can show an 'Already in library' badge without N extra resolve calls.
    """
    keyword = q.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    if order_by not in _ALLOWED_SEARCH_ORDER_BY:
        raise HTTPException(status_code=400, detail=f"Unsupported order_by value: {order_by!r}")

    service = await _build_service(db, makerworld_provider, current_user, api_key_cloud_owner)
    try:
        result = await service.search_designs(  # type: ignore[attr-defined]
            keyword,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    hits = result.get("hits") or []
    total = int(result.get("total") or 0)

    already_imported_model_ids: list[int] = []
    hit_ids = [hid for h in hits if isinstance(h, dict) and isinstance((hid := h.get("id")), int)]
    if hit_ids:
        owner_user = current_user or api_key_cloud_owner
        existing_conds = [
            or_(*(makerworld_provider.source_url_filter(LibraryFile.source_url, str(hid)) for hid in hit_ids)),
            LibraryFile.deleted_at.is_(None),
        ]
        if owner_user is not None:
            existing_conds.append(LibraryFile.created_by_id == owner_user.id)
        existing_q = await db.execute(select(LibraryFile.source_url).where(*existing_conds))
        matched_ids: set[int] = set()
        for (src_url,) in existing_q.all():
            if not src_url:
                continue
            try:
                ref = makerworld_provider.parse_url(src_url)
                matched_ids.add(int(ref.external_id))
            except (ProviderError, ValueError):
                continue
        already_imported_model_ids = [hid for hid in hit_ids if hid in matched_ids]

    return MakerWorldSearchResponse(
        total=total,
        hits=hits,
        already_imported_model_ids=already_imported_model_ids,
    )


@router.post(
    "/resolve",
    response_model=MakerWorldResolvedModel,
    # Authentication only — the permission belongs to whichever provider the
    # pasted URL routes to, which is not known until the body is parsed (see
    # ``_authorize_for_provider``).
    dependencies=[Depends(require_auth_if_enabled)],
)
async def resolve_url(
    body: MakerWorldResolveRequest,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
):
    """Resolve a MakerWorld URL to full model metadata + plate list.

    The response also tells the caller which (if any) LibraryFile rows already
    exist for the same model URL, so the UI can show an "Already imported"
    badge and skip a redundant download.
    """
    # Strategy pattern: select provider based on URL instead of hardcoding.
    # Routing runs before the permission check because the permission *is* the
    # provider's; all an unpermitted caller learns from the ordering is which
    # hosts Bambuddy supports, which the UI states anyway.
    provider = _provider_for_url(body.url)
    current_user = await _authorize_for_provider(provider, provider.view_permission, credentials, x_api_key)
    try:
        ref = provider.parse_url(body.url)
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    model_id = int(ref.external_id)
    profile_id = int(ref.sub_id) if ref.sub_id else None

    service = await _build_service(db, provider, current_user, api_key_cloud_owner)
    try:
        resolved = await service.resolve(ref)
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    # Find every library row whose source_url belongs to this resource —
    # the provider's :meth:`source_url_filter` owns what "belongs" means
    # (whole-model key, per-plate keys, ...). The frontend surfaces the ids
    # to mark imported plates in the instance picker.
    owner_user = current_user or api_key_cloud_owner
    existing_conds = [
        provider.source_url_filter(LibraryFile.source_url, str(model_id)),
        LibraryFile.deleted_at.is_(None),
    ]
    if owner_user is not None:
        existing_conds.append(LibraryFile.created_by_id == owner_user.id)
    existing_q = await db.execute(select(LibraryFile.id).where(*existing_conds))
    already_imported = [row[0] for row in existing_q.all()]

    return MakerWorldResolvedModel(
        model_id=model_id,
        profile_id=profile_id,
        design=resolved.design,
        instances=resolved.instances,
        already_imported_library_ids=already_imported,
    )


@router.post(
    "/import",
    response_model=MakerWorldImportResponse,
    # Authentication only — the permission belongs to the provider named by
    # ``source_type`` (see ``_authorize_for_provider``).
    dependencies=[Depends(require_auth_if_enabled)],
)
async def import_instance(
    body: MakerWorldImportRequest,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key_cloud_owner: User | None = Depends(resolve_api_key_cloud_owner),
):
    """Download a specific MakerWorld instance (plate configuration) and save
    the 3MF into the library.

    De-duplicates by canonicalised source URL — if the same MakerWorld model
    was imported before (any plate), that existing LibraryFile is returned and
    no new download happens.
    """
    # Resolve the provider first: an unknown ``source_type`` must 400 before
    # the default-destination folder gets auto-created as a side effect — and
    # the permission that applies is the resolved provider's, not MakerWorld's,
    # so it cannot be checked any earlier. All that costs is telling an
    # authenticated-but-unpermitted caller which source types are registered,
    # which the UI lists anyway; anonymous callers never get this far.
    provider = _provider_for_source(body.source_type)
    current_user = await _authorize_for_provider(provider, provider.import_permission, credentials, x_api_key)
    cloud_token_user = current_user or api_key_cloud_owner
    owner_id = cloud_token_user.id if cloud_token_user else None

    if body.folder_id is not None:
        folder_q = await db.execute(select(LibraryFolder).where(LibraryFolder.id == body.folder_id))
        target_folder = folder_q.scalar_one_or_none()
        if target_folder is None:
            raise HTTPException(status_code=404, detail="Folder not found")
        if target_folder.is_external and target_folder.external_readonly:
            raise HTTPException(
                status_code=403,
                detail="Cannot import into a read-only external folder",
            )
        effective_folder_id: int | None = body.folder_id
    else:
        # Default destination: the resolved provider's dedicated per-user top-level
        # folder in My Files (`is_shared=False`). Each user gets their own MakerWorld
        # folder (or claims a legacy unowned folder if no other user has files in it).
        default_folder_name = provider.default_folder_name
        if default_folder_name is None:
            effective_folder_id = None
        else:
            folder_conds = [
                LibraryFolder.name == default_folder_name,
                LibraryFolder.parent_id.is_(None),
                LibraryFolder.is_external.is_(False),
            ]
            if owner_id is not None:
                folder_conds.append(LibraryFolder.created_by_id == owner_id)
            else:
                folder_conds.append(LibraryFolder.created_by_id.is_(None))
            default_folder_q = await db.execute(select(LibraryFolder).where(*folder_conds).limit(1))
            default_folder = default_folder_q.scalar_one_or_none()
            if default_folder is None and owner_id is not None:
                legacy_q = await db.execute(
                    select(LibraryFolder)
                    .where(
                        LibraryFolder.name == default_folder_name,
                        LibraryFolder.parent_id.is_(None),
                        LibraryFolder.is_external.is_(False),
                        LibraryFolder.created_by_id.is_(None),
                    )
                    .limit(1)
                )
                legacy_folder = legacy_q.scalar_one_or_none()
                if legacy_folder is not None:
                    other_user_files = (
                        await db.execute(
                            select(LibraryFile.id)
                            .where(
                                LibraryFile.folder_id == legacy_folder.id,
                                LibraryFile.deleted_at.is_(None),
                                LibraryFile.created_by_id.isnot(None),
                                LibraryFile.created_by_id != owner_id,
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if other_user_files is None:
                        legacy_folder.created_by_id = owner_id
                        legacy_folder.is_shared = False
                        default_folder = legacy_folder
            if default_folder is None:
                default_folder = LibraryFolder(
                    name=default_folder_name,
                    parent_id=None,
                    created_by_id=owner_id,
                    is_shared=False,
                )
                db.add(default_folder)
                await db.flush()
            effective_folder_id = default_folder.id

    service = await _build_service(db, provider, current_user, api_key_cloud_owner)

    # YASTL#51's iot-service endpoint needs the *alphanumeric* modelId
    # (e.g. "US2bb73b106683e5"), not the integer design id from /models/{N} —
    # resolving that, plus picking a default profile when the frontend didn't
    # specify one, lives inside ``get_download``. The route only orchestrates
    # dedupe + persistence so every provider shares those concerns here.
    ref = ProviderResourceRef(
        source_type=provider.source_type,
        external_id=str(body.model_id),
        sub_id=str(body.profile_id) if body.profile_id else None,
    )

    try:
        info = await service.get_download(ref)
        # The provider enriches ``sub_id`` with the actually-resolved profile
        # when the caller omitted one.
        resolved_profile_id = int(info.ref.sub_id) if info.ref.sub_id else None

        # Canonical URL includes profile_id so each plate gets its own library
        # entry (see ``ModelProvider.canonical_url``).
        source_url = provider.canonical_url(info.ref)

        # Dedupe check upfront (scoped to the current user when authenticated).
        dedupe_q = LibraryFile.active().where(LibraryFile.source_url == source_url)
        if owner_id is not None:
            dedupe_q = dedupe_q.where(LibraryFile.created_by_id == owner_id)
        existing_q = await db.execute(dedupe_q.limit(1))
        existing_row = existing_q.scalar_one_or_none()
        if existing_row is not None:
            return MakerWorldImportResponse(
                library_file_id=existing_row.id,
                filename=existing_row.filename,
                folder_id=existing_row.folder_id,
                profile_id=resolved_profile_id,
                was_existing=True,
            )

        download = await service.download(info)
    except ProviderError as exc:
        raise _map_service_error(exc) from exc
    finally:
        await service.close()

    # Basename-strip any path components from the upstream filename so a
    # malicious response (``name: "../../evil.3mf"``) can't persist a suspect
    # string into the library row or the UI. On-disk storage uses a UUID
    # filename regardless (see library.py), so this is defence-in-depth.
    raw_name = info.suggested_filename
    if isinstance(raw_name, str) and raw_name.strip():
        # MakerWorld emits percent-encoded names (`%20` for spaces, etc.)
        # because the same string round-trips through HTTP URLs in the
        # CDN download path. Decode before persisting so the library
        # row, the slice toast, and every later UI surface show the
        # human-readable form.
        suggested_name = os.path.basename(unquote(raw_name.strip())) or f"makerworld-{body.model_id}.3mf"
    else:
        suggested_name = f"makerworld-{body.model_id}.3mf"

    # Prefer the server-provided human-readable filename; the signed URL's
    # path ends in a UUID that's not meaningful to users. Decode the
    # fallback path-tail too — same percent-encoding round-trip applies
    # there as on the manifest-supplied name.
    filename = suggested_name if suggested_name.endswith(".3mf") else unquote(download.filename)

    library_file, was_existing = await save_3mf_bytes_to_library(
        db,
        file_bytes=download.file_bytes,
        filename=filename,
        folder_id=effective_folder_id,
        source_type=provider.source_type,
        source_url=source_url,
        owner_id=owner_id,
    )

    return MakerWorldImportResponse(
        library_file_id=library_file.id,
        filename=library_file.filename,
        folder_id=library_file.folder_id,
        profile_id=resolved_profile_id,
        was_existing=was_existing,
    )


@router.get("/recent-imports", response_model=list[MakerWorldRecentImport])
async def recent_imports(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(makerworld_provider.view_permission),
):
    """Last N MakerWorld imports, newest first.

    Surfaces files whose ``source_type`` is ``"makerworld"`` so the MakerWorld
    page can show a 'Recent imports' sidebar that persists across resolves.
    Widening this to all registered providers is a behaviour change that
    belongs with the provider that needs it.
    ``limit`` is clamped to ``[1, 50]`` to keep payloads sensible.
    """
    capped = max(1, min(50, int(limit)))

    recent_q = LibraryFile.active().where(LibraryFile.source_type == makerworld_provider.source_type)
    if current_user is not None:
        recent_q = recent_q.where(LibraryFile.created_by_id == current_user.id)
    result = await db.execute(recent_q.order_by(LibraryFile.created_at.desc()).limit(capped))
    rows = result.scalars().all()

    return [
        MakerWorldRecentImport(
            library_file_id=row.id,
            filename=row.filename,
            folder_id=row.folder_id,
            thumbnail_path=row.thumbnail_path,
            source_url=row.source_url,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]
