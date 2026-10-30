# SPDX-License-Identifier: CPAL-1.0
"""Nine public MCP tools backed by the existing Remember-Me runtime."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shlex
from datetime import date
from typing import Annotated, Any, Literal, Optional

from mcp.types import (
    CallToolResult,
    ImageContent,
    TextContent,
    ToolAnnotations,
)
from PIL import Image, UnidentifiedImageError
from pydantic import Field

from remember_me.host_metadata import upload_metadata

from remember_me.core import (
    AssetFileUnavailable,
    AssetUnavailable,
    GetAssetRequest,
    InvalidMetadata,
    RememberMeError,
    ReindexEmbeddingsRequest,
    ResolveAssetRequest,
    SearchAssetsRequest,
    StorageFailure,
    UpdateMetadataRequest,
)

from .schemas import (
    McpToolOutput,
    asset_to_public_dict,
    error_payload,
    ok_payload,
)
from .transfers import TransferError
from .viewer import VIEWER_TOOL_META


MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    "application/octet-stream",
    "image/jpeg",
    "image/png",
}
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
NON_DESTRUCTIVE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
LINK_CREATION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


def _safe_error(error: Exception) -> CallToolResult:
    if isinstance(error, (AssetUnavailable, AssetFileUnavailable)):
        payload = error_payload(
            "asset_unavailable",
            "Asset is unavailable.",
        )
    elif isinstance(error, InvalidMetadata):
        payload = error_payload(
            "validation_error",
            "The request is invalid.",
        )
    elif isinstance(error, TransferError):
        payload = error_payload(
            error.code,
            "The transfer is unavailable.",
        )
    elif isinstance(error, RememberMeError):
        payload = error_payload(
            error.code,
            "The asset operation could not be completed.",
        )
    else:
        payload = error_payload(
            "internal_error",
            "The operation could not be completed.",
        )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, separators=(",", ":")),
            )
        ],
        structuredContent=payload,
        isError=True,
    )


def _success(
    message: str,
    payload: dict[str, Any],
    *,
    extra_content=None,
    meta=None,
) -> CallToolResult:
    # Some clients forward only content, ignoring structuredContent entirely.
    if not message.lstrip().startswith("{"):
        message += "\n" + json.dumps(payload, ensure_ascii=False)
    return CallToolResult(
        content=[
            TextContent(type="text", text=message),
            *(extra_content or []),
        ],
        structuredContent=payload,
        isError=False,
        _meta=meta,
    )


def read_cleaned_image(runtime, asset_id: str):
    resolved = runtime.service.resolve_asset(
        ResolveAssetRequest(asset_id)
    )
    content = runtime.blob_store.read(resolved.blob_key)
    asset = resolved.asset
    if (
        len(content) != asset.stored_bytes
        or len(content) > MAX_IMAGE_BYTES
        or hashlib.sha256(content).hexdigest() != asset.stored_sha256
    ):
        raise AssetFileUnavailable()
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            expected_format = (
                "PNG" if asset.mime_type == "image/png" else "JPEG"
            )
            if (
                image.format != expected_format
                or image.size != (asset.width, asset.height)
            ):
                raise AssetFileUnavailable()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AssetFileUnavailable() from exc
    return asset, content


def register_tools(mcp, runtime, uploads, downloads) -> None:
    def public_asset(asset):
        public = asset_to_public_dict(asset)
        store = getattr(runtime, "metadata_store", None)
        if store is not None:
            public.update(store.get(asset.asset_id))
        return public

    def image_text(public, download_url=""):
        return json.dumps({"asset": public, "download_url": download_url}, ensure_ascii=False)

    def image_link(asset_id):
        try:
            ticket, url = downloads.create(asset_id)
            return {"download_url": url, "expires_at": ticket.expires_at}
        except TransferError:
            return {}

    @mcp.tool(
        name="rm_asset_upload_link",
        description=(
            "Create a single-use fifteen-minute multipart POST URL with prefilled metadata. "
            "In claude.ai chat attachments are in /mnt/user-data/uploads/. Use bash curl "
            "with the returned curl_template; do not read image contents or convert them "
            "to base64 tool arguments. Only PNG/JPEG are supported. Convert HEIC, WebP "
            "or other formats to JPEG in the container with Pillow first "
            "(HEIC requires a Pillow HEIF decoder)."
        ),
        annotations=LINK_CREATION,
        structured_output=True,
    )
    def rm_asset_upload_link(
        expected_bytes: Annotated[int, Field(ge=1, le=MAX_IMAGE_BYTES)],
        filename: str = "",
        mime_type: str = "application/octet-stream",
        mime: Optional[str] = None,
        title: str = "",
        description: str = "",
        tags: Optional[list[str]] = None,
        occurred_at: str = "",
        source: str = "claude.ai",
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            normalized_mime = str(mime if mime is not None else mime_type).strip().casefold()
            if normalized_mime not in ALLOWED_MIME_TYPES:
                raise InvalidMetadata()
            if mime is not None and mime_type != "application/octet-stream" and mime_type != mime:
                raise InvalidMetadata()
            metadata = upload_metadata(title, description, tuple(tags or ()), occurred_at, source)
            ticket, upload_url = uploads.create(
                expected_bytes,
                str(filename),
                normalized_mime,
                metadata=metadata,
            )
            return _success(
                "A short-lived upload link was created.",
                ok_payload(
                    upload_id=ticket.upload_id,
                    upload_url=upload_url,
                    url=upload_url,
                    method="POST",
                    headers={},
                    multipart_field="file",
                    curl_template=(
                        "curl --fail-with-body -sS -X POST --form "
                        + shlex.quote('file=@"{FILE}";type=' + normalized_mime + ';filename=upload')
                        + " " + shlex.quote(upload_url)
                    ),
                    expires_at=ticket.expires_at,
                    max_bytes=MAX_IMAGE_BYTES,
                ),
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_upload_status",
        description="Read the status of a previously created upload ticket.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def rm_asset_upload_status(
        upload_id: str,
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            ticket = uploads.inspect(str(upload_id))
            values = {
                "upload_id": ticket.upload_id,
                "status": ticket.status,
            }
            if ticket.status == "completed" and ticket.asset is not None:
                values["asset"] = public_asset(ticket.asset)
                values["asset_id"] = ticket.asset.asset_id
                values["deduplicated"] = bool(ticket.deduplicated)
            if ticket.status == "failed":
                values["error"] = {
                    "code": ticket.error_code or "upload_failed",
                    "message": "The upload could not be completed.",
                }
            return _success(
                "Upload status is {}.".format(ticket.status),
                ok_payload(**values),
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_get",
        description="Get safe metadata for one Remember-Me image asset.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def rm_asset_get(
        asset_id: str,
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            asset = runtime.service.get_asset(
                GetAssetRequest(str(asset_id))
            )
            return _success(
                "Asset metadata was retrieved.",
                ok_payload(asset=public_asset(asset)),
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_update_metadata",
        description=(
            "Replace selected title, description, or tag metadata without "
            "changing image bytes."
        ),
        annotations=NON_DESTRUCTIVE_WRITE,
        structured_output=True,
    )
    async def rm_asset_update_metadata(
        asset_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            if title is None and description is None and tags is None:
                raise InvalidMetadata()
            asset = runtime.service.update_metadata(
                UpdateMetadataRequest(
                    asset_id=str(asset_id),
                    title=title,
                    description=description,
                    tags=tuple(tags) if tags is not None else None,
                )
            )
            hook = getattr(runtime, "after_metadata_write", None)
            if hook is not None:
                await hook(asset.asset_id)
            return _success(
                "Asset metadata was updated.",
                ok_payload(asset=public_asset(asset)),
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_reindex_embeddings",
        description=(
            "Rebuild a bounded batch of optional vector embeddings without "
            "changing image files or user metadata."
        ),
        annotations=NON_DESTRUCTIVE_WRITE,
        structured_output=True,
    )
    async def rm_asset_reindex_embeddings(
        asset_id: str = "",
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            result = await runtime.service.reindex_embeddings(
                ReindexEmbeddingsRequest(
                    asset_id=str(asset_id),
                    limit=limit,
                )
            )
            if not result.enabled:
                return _success(
                    "Vector indexing is disabled; keyword search remains available.",
                    ok_payload(
                        enabled=False,
                        model_id=result.model_id,
                        selected=result.scanned,
                        indexed=result.indexed,
                        failed=result.failed,
                    ),
                )
            return _success(
                "The bounded embedding batch was processed.",
                ok_payload(
                    enabled=True,
                    model_id=result.model_id,
                    selected=result.scanned,
                    indexed=result.indexed,
                    failed=result.failed,
                ),
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_search",
        description=(
            "Search image metadata; returns compact text only, no images, default 5 results. "
            "Use created_from/created_to as inclusive ISO calendar dates for upload time, "
            "undescribed=true finds images with no description for inspect and annotation; "
            "then call rm_asset_inspect to actually see an image."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def rm_asset_search(
        query: str = "",
        tags: Optional[list[str]] = None,
        kind: str = "",
        mime_type: str = "",
        created_from: str = "",
        created_to: str = "",
        limit: Annotated[int, Field(ge=1, le=50)] = 5,
        offset: Annotated[int, Field(ge=0)] = 0,
        undescribed: bool = False,
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            for value in (created_from, created_to):
                if value:
                    try:
                        date.fromisoformat(value)
                    except (ValueError, TypeError):
                        raise InvalidMetadata() from None
            if created_from and created_to and created_from > created_to:
                raise InvalidMetadata()
            result = await runtime.service.search_assets(
                SearchAssetsRequest(
                    query=str(query),
                    tags=tuple(tags or ()),
                    kind=str(kind),
                    mime_type=str(mime_type),
                    created_from=str(created_from),
                    created_to=str(created_to),
                    limit=limit,
                    offset=offset,
                    hybrid_ranking=True,
                    undescribed=undescribed,
                )
            )
            payload = ok_payload(
                    search_mode=result.search_mode,
                    note=("Hybrid keyword and semantic ranking." if result.search_mode == "hybrid"
                          else "Keyword/metadata only; embeddings are unconfigured, unavailable, or unused for this query."),
                    total=result.total,
                    limit=result.limit,
                    offset=result.offset,
                    items=[
                        {
                            "asset_id": item.asset.asset_id,
                            "title": item.asset.title,
                            "description": item.asset.description[:100],
                            "tags": list(item.asset.tags),
                            "created_at": item.asset.created_at,
                        }
                        for item in result.results
                    ],
                )
            return _success(
                json.dumps(payload, ensure_ascii=False), payload,
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_download_link",
        description=(
            "Create a five-minute link for up to three downloads of the "
            "privacy-cleaned stored image."
        ),
        annotations=LINK_CREATION,
        structured_output=True,
    )
    def rm_asset_download_link(
        asset_id: str,
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            asset, _content = read_cleaned_image(runtime, str(asset_id))
            ticket, download_url = downloads.create(asset.asset_id)
            return _success(
                "A short-lived download link was created.",
                ok_payload(
                    asset_id=asset.asset_id,
                    download_url=download_url,
                    expires_at=ticket.expires_at,
                    max_successful_gets=ticket.remaining_gets,
                ),
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_view",
        description=(
            "Open an original Remember-Me MCP App viewer for one cleaned "
            "image, with a text fallback."
        ),
        annotations=READ_ONLY,
        meta=VIEWER_TOOL_META,
        structured_output=True,
    )
    def rm_asset_view(
        asset_id: str,
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            asset, content = read_cleaned_image(runtime, str(asset_id))
            public = public_asset(asset)
            link = image_link(asset.asset_id)
            payload = ok_payload(asset=public, **link)
            return _success(
                image_text(public, link.get("download_url", "")),
                payload,
                meta={
                    "remember_me": {
                        "asset": public,
                        "image": {
                            "mime_type": asset.mime_type,
                            "data": base64.b64encode(content).decode("ascii"),
                        },
                    }
                },
            )
        except Exception as error:
            return _safe_error(error)

    @mcp.tool(
        name="rm_asset_inspect",
        description=(
            "See one image as MCP ImageContent with title, full description, tags and dates. "
            "Default medium: JPEG quality 80, longest edge at most 1024px; small: 512px; "
            "original: full stored privacy-cleaned PNG/JPEG. Download link is for the human; "
            "it does not make a text-only client send image pixels to the model."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def rm_asset_inspect(
        asset_id: str,
        size: Literal["small", "medium", "original"] = "medium",
    ) -> Annotated[CallToolResult, McpToolOutput]:
        try:
            asset, content = read_cleaned_image(runtime, str(asset_id))
            if size not in {"small", "medium", "original"}:
                raise InvalidMetadata()
            mime_type = asset.mime_type
            if size != "original":
                edge = 512 if size == "small" else 1024
                with Image.open(io.BytesIO(content)) as image:
                    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", image.size, "white")
                    if image.mode == "RGBA":
                        canvas.paste(image, mask=image.getchannel("A"))
                    else:
                        canvas.paste(image.convert("RGB"))
                    buffer = io.BytesIO()
                    canvas.save(buffer, "JPEG", quality=80)
                    content = buffer.getvalue()
                mime_type = "image/jpeg"
            public = public_asset(asset)
            link = image_link(asset.asset_id)
            return _success(
                image_text(public, link.get("download_url", "")),
                ok_payload(asset=public, size=size, **link),
                extra_content=[
                    ImageContent(
                        type="image",
                        data=base64.b64encode(content).decode("ascii"),
                        mimeType=mime_type,
                    )
                ],
            )
        except Exception as error:
            return _safe_error(error)
