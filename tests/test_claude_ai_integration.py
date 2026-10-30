# SPDX-License-Identifier: CPAL-1.0
import asyncio
import base64
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import plistlib
import shlex
import shutil
import socket
import subprocess
import threading
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest
import uvicorn

from remember_me.standalone.app import create_app
from remember_me.standalone.config import StandaloneConfig
from remember_me.core import IngestImageRequest
from remember_me.factory import create_local_runtime

MAIN_TOKEN = "main-" + "m" * 48
UPLOAD_TOKEN = "upload-" + "u" * 48


def image_bytes(fmt="JPEG", size=(30, 20), color="red"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, fmt)
    return output.getvalue()


def call(app, name, **kwargs):
    tool = app.state.mcp_runtime.server._tool_manager.get_tool(name)
    result = tool.fn(**kwargs)
    return asyncio.run(result) if asyncio.iscoroutine(result) else result


def test_prefilled_upload_contract_and_restart(tmp_path):
    app = create_app(StandaloneConfig(data_root=tmp_path))
    content = image_bytes()
    before = datetime.now(timezone.utc)
    link = call(app, "rm_asset_upload_link", expected_bytes=len(content), mime="image/jpeg",
                title="旅行", description="海边的红伞", tags=["旅行", "海边"],
                occurred_at="2026-10-20T12:30:00+08:00").structuredContent
    assert link["method"] == "POST"
    assert link["headers"] == {}
    assert link["multipart_field"] == "file"
    assert link["url"] == link["upload_url"]
    assert "{FILE}" in link["curl_template"] and "--form" in link["curl_template"]
    assert "Bearer" not in link["curl_template"]
    assert (datetime.fromisoformat(link["expires_at"]) - before).total_seconds() >= 600
    with TestClient(app) as client:
        response = client.post(link["upload_url"], files={"file": ("trip.jpg", content, "image/jpeg")})
        assert response.status_code == 200
        result = response.json()
        asset_id = result["asset_id"]
        assert result["asset"]["title"] == "旅行"
        assert result["asset"]["description"] == "海边的红伞"
        assert set(result["asset"]["tags"]) == {"旅行", "海边"}
        assert client.post(link["upload_url"], files={"file": ("trip.jpg", content)}).status_code == 409
    restarted = create_app(StandaloneConfig(data_root=tmp_path))
    asset = call(restarted, "rm_asset_get", asset_id=asset_id).structuredContent["asset"]
    assert asset["source"] == "claude.ai"
    assert asset["occurred_at"] == "2026-10-20T04:30:00+00:00"


@pytest.mark.parametrize("extra", [{"mime":"image/heic"}, {"occurred_at":"yesterday"},
                                  {"occurred_at":"2026-10-20T12:30:00"}, {"title":"x"*201},
                                  {"tags":["x"*65]}])
def test_upload_metadata_validation_before_ticket(tmp_path, extra):
    app = create_app(StandaloneConfig(data_root=tmp_path))
    result = call(app, "rm_asset_upload_link", expected_bytes=10, **extra)
    assert result.isError
    assert not app.state.mcp_runtime.upload_tickets._tickets


def test_dedup_does_not_overwrite_metadata(tmp_path):
    app = create_app(StandaloneConfig(data_root=tmp_path))
    content = image_bytes()
    with TestClient(app) as client:
        for title in ("first", "second"):
            link = call(app, "rm_asset_upload_link", expected_bytes=len(content), title=title).structuredContent
            result = client.post(link["upload_url"], files={"file": ("x.jpg", content)}).json()
            assert result["asset"]["title"] == "first"
        assert result["deduplicated"] is True


@pytest.mark.parametrize("size,edge", [("small",512), ("medium",1024), ("original",2000)])
def test_inspect_preview_and_viewer_preservation(tmp_path, size, edge):
    app = create_app(StandaloneConfig(data_root=tmp_path))
    content = image_bytes("PNG", (2000,1000))
    asset = app.state.service.ingest_image(IngestImageRequest(
        content, len(content), "large.png", title="大图", description="完整描述", tags=("红色",)
    )).asset
    result = call(app, "rm_asset_inspect", asset_id=asset.asset_id, size=size)
    image = next(block for block in result.content if block.type == "image")
    data = base64.b64decode(image.data)
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.size == (edge, edge//2)
        assert decoded.format == ("PNG" if size == "original" else "JPEG")
    if size == "original":
        assert data == app.state.blob_store.read(asset.stored_relpath)
    assert "完整描述" in result.content[0].text
    assert "红色" in result.content[0].text
    assert "download_url" in result.structuredContent
    assert image.data not in result.content[0].text
    default = call(app, "rm_asset_inspect", asset_id=asset.asset_id)
    assert default.structuredContent["size"] == "medium"
    viewer = call(app, "rm_asset_view", asset_id=asset.asset_id)
    assert all(b.type == "text" for b in viewer.content)
    assert base64.b64decode(viewer.meta["remember_me"]["image"]["data"]) == app.state.blob_store.read(asset.stored_relpath)


def test_search_text_limit_truncation_and_dates(tmp_path):
    class Clock:
        def now(self):
            return datetime(2026,10,20,tzinfo=timezone.utc)
    app = create_app(StandaloneConfig(data_root=tmp_path), clock=Clock())
    for index in range(8):
        content = image_bytes(color=(index*30,0,0))
        app.state.service.ingest_image(IngestImageRequest(content,len(content),f"{index}.jpg",
                                                         title="旅行",description="海"*150,tags=("海边",)))
    result = call(app,"rm_asset_search",query="旅行",created_from="2026-10-20",created_to="2026-10-20")
    assert not result.isError
    assert all(b.type == "text" for b in result.content)
    assert result.structuredContent["total"] == 8
    assert len(result.structuredContent["items"]) == 5
    assert len(result.structuredContent["items"][0]["description"]) == 100
    assert set(result.structuredContent["items"][0]) == {"asset_id","title","description","tags","created_at"}
    assert call(app,"rm_asset_search",created_from="2026-10-21").structuredContent["total"] == 0
    assert call(app,"rm_asset_search",created_to="2026-10-19").structuredContent["total"] == 0
    assert call(app,"rm_asset_search",created_from="last week").isError
    assert call(app,"rm_asset_search",created_from="2026-10-21",created_to="2026-10-20").isError


def test_host_auto_index_and_hybrid_search(tmp_path):
    class Provider:
        enabled = True
        model_id = "test/image-description-v1"
        fail = False
        async def embed(self, text):
            if self.fail:
                raise RuntimeError("private provider error")
            return [1.0, 0.0] if "蓝色" in text or text == "ocean" else [0.0, 1.0]
    provider = Provider()
    runtime = create_local_runtime(tmp_path, vector_provider=provider)
    app = create_app(StandaloneConfig(data_root=tmp_path), runtime=runtime)
    content = image_bytes()
    with TestClient(app) as client:
        link = call(app,"rm_asset_upload_link",expected_bytes=len(content),description="蓝色海洋",tags=["旅行"]).structuredContent
        asset_id = client.post(link["upload_url"],files={"file":("x.jpg",content)}).json()["asset_id"]
        assert runtime.repository.get_embedding(asset_id) is not None
        result = call(app,"rm_asset_search",query="ocean").structuredContent
        assert result["search_mode"] == "hybrid"
        assert result["items"][0]["asset_id"] == asset_id
        call(app,"rm_asset_update_metadata",asset_id=asset_id,description="红色花朵")
        assert call(app,"rm_asset_search",query="ocean").structuredContent["total"] == 0
        provider.fail = True
        call(app,"rm_asset_update_metadata",asset_id=asset_id,description="provider outage")
        result = call(app,"rm_asset_search",query="outage").structuredContent
        assert result["search_mode"] == "keyword" and result["total"] == 1
        assert "private provider" not in str(result)


def test_quick_upload_scope_dedup_and_undescribed_pagination(tmp_path):
    app = create_app(StandaloneConfig(data_root=tmp_path,auth_token=MAIN_TOKEN,upload_token=UPLOAD_TOKEN))
    upload_headers = {"Authorization":"Bearer " + UPLOAD_TOKEN}
    with TestClient(app) as client:
        content = image_bytes(color="blue")
        first = client.post("/api/v1/quick-upload",headers=upload_headers,
                            files={"file":("photo.jpg",content,"image/jpeg")}, data={"tags":"相册,旅行"})
        assert first.status_code == 201
        aid = first.json()["asset_id"]
        assert set(first.json()) == {"ok","asset_id","deduplicated","index_updated"}
        assert call(app,"rm_asset_get",asset_id=aid).structuredContent["asset"]["source"] == "ios-shortcut"
        for method, path in (("GET","/api/v1/assets"),("GET",f"/api/v1/assets/{aid}"),
                             ("GET",f"/api/v1/assets/{aid}/content"),("DELETE",f"/api/v1/assets/{aid}"),
                             ("POST","/mcp")):
            assert client.request(method,path,headers=upload_headers).status_code == 401
        assert client.post("/api/v1/quick-upload",params={"token":UPLOAD_TOKEN},files={"file":("x.jpg",content)}).status_code == 401
        assert client.post("/api/v1/quick-upload",cookies={"token":UPLOAD_TOKEN},files={"file":("x.jpg",content)}).status_code == 401
        duplicate = client.post("/api/v1/quick-upload",headers=upload_headers,files={"file":("x.jpg",content)},data={"description":"must not overwrite"})
        assert duplicate.status_code == 200 and duplicate.json()["deduplicated"]
        assert "asset" not in duplicate.json() and "description" not in duplicate.json()
        for index in range(6):
            content = image_bytes(color=(index*30+10,20,20))
            response = client.post("/api/v1/quick-upload",headers=upload_headers,files={"file":("x.jpg",content)},data={"description":"has description" if index % 2 else ""})
            assert response.status_code == 201
        result = call(app,"rm_asset_search",undescribed=True,limit=2,offset=2).structuredContent
        assert result["total"] == 4 and len(result["items"]) == 2
        assert all(item["description"] == "" for item in result["items"])
        call(app,"rm_asset_update_metadata",asset_id=aid,description="补充好了",tags=["蓝色"])
        assert call(app,"rm_asset_search",undescribed=True).structuredContent["total"] == 3


def test_quick_upload_heic_limits_rate_and_disabled(tmp_path):
    app = create_app(StandaloneConfig(data_root=tmp_path,auth_token=MAIN_TOKEN,upload_token=UPLOAD_TOKEN,quick_upload_rate_limit=2))
    headers = {"Authorization":"Bearer " + UPLOAD_TOKEN}
    with TestClient(app) as client:
        bad = client.post("/api/v1/quick-upload",headers=headers,files={"file":("photo.heic",b"not-a-jpeg","image/heic")})
        assert bad.status_code == 415 and "HEIC" in bad.json()["error"]["message"]
        oversized = client.post("/api/v1/quick-upload",headers=headers,files={"file":("big.jpg",b"x"*(10*1024*1024+1),"image/jpeg")})
        assert oversized.status_code == 413
        limited = client.post("/api/v1/quick-upload",headers=headers,files={"file":("x.jpg",image_bytes())})
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
    disabled = create_app(StandaloneConfig(data_root=tmp_path / "disabled"))
    with TestClient(disabled) as client:
        assert client.post("/api/v1/quick-upload",headers=headers).status_code == 404
    for config in ({"upload_token":UPLOAD_TOKEN}, {"auth_token":MAIN_TOKEN,"upload_token":MAIN_TOKEN}):
        with pytest.raises(Exception):
            StandaloneConfig(data_root=tmp_path,**config)


def test_curl_template_real_jpeg_and_png(tmp_path):
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        pytest.fail("curl is required for the integration upload test")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1",0))
        port = probe.getsockname()[1]
    app = create_app(StandaloneConfig(data_root=tmp_path/"data",port=port))
    server = uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,access_log=False,log_level="warning"))
    thread = threading.Thread(target=server.run,daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(0.01)
        assert server.started
        for fmt, mime in (("JPEG","image/jpeg"),("PNG","image/png")):
            image = tmp_path/("photo with space." + fmt.lower())
            image.write_bytes(image_bytes(fmt))
            link = call(app,"rm_asset_upload_link",expected_bytes=image.stat().st_size,
                        mime=mime,title="curl "+fmt,description="uploaded via curl",tags=[fmt]).structuredContent
            command = shlex.split(link["curl_template"].replace("{FILE}",image.as_posix()))
            command[0] = curl
            uploaded = subprocess.run(command,capture_output=True,text=True,timeout=20)
            assert uploaded.returncode == 0, "curl upload failed (URL redacted)"
            asset_id = json.loads(uploaded.stdout)["asset_id"]
            found = call(app,"rm_asset_search",query="curl "+fmt).structuredContent
            assert found["items"][0]["asset_id"] == asset_id
            viewed = call(app,"rm_asset_inspect",asset_id=asset_id)
            assert any(block.type == "image" for block in viewed.content)
            assert call(app,"rm_asset_get",asset_id=asset_id).structuredContent["asset"]["description"] == "uploaded via curl"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def test_shortcut_source_rebuilds_and_contains_file_form(tmp_path):
    root = Path(__file__).resolve().parents[1]
    import runpy
    build = runpy.run_path(str(root/"tools/build_ios_shortcut.py"))["build"]
    source = plistlib.loads((root/"shortcuts/Remember-Me.unsigned.shortcut").read_bytes())
    assert source == build()
    actions = source["WFWorkflowActions"]
    upload = next(item["WFWorkflowActionParameters"] for item in actions
                  if item["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl")
    assert upload["WFHTTPMethod"] == "POST" and upload["WFHTTPBodyType"] == "Form"
    file_item = upload["WFFormValues"]["Value"]["WFDictionaryFieldValueItems"][0]
    assert file_item["WFItemType"] == 5
    assert file_item["WFValue"]["WFSerializationType"] == "WFTokenAttachmentParameterState"
    assert "REPLACE_WITH_UPLOAD_ONLY_TOKEN" in str(source)
