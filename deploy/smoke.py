# SPDX-License-Identifier: CPAL-1.0
"""Live HTTPS/curl acceptance. Run on the VPS; never print capabilities."""

import asyncio
import base64
import io
import json
import logging
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from PIL import Image, ImageDraw


async def main():
    logging.disable(logging.CRITICAL)
    secret = Path("/etc/remember-me/nginx-mcp.conf").read_text()
    path_key = re.search(r"location = /([A-Za-z0-9_-]+)/mcp", secret).group(1)
    main_token = re.search(r'Authorization "Bearer ([A-Za-z0-9_-]+)";', secret).group(1)
    upload_token = Path("/etc/remember-me/upload-token").read_text().strip()
    base = "https://rm.wu-bing.xyz"
    created = []
    # No bearer sent by this MCP client: nginx must inject it.
    async with httpx.AsyncClient(timeout=30) as http:
        assert (await http.get(base + "/readyz")).status_code == 200
        assert (await http.post(base + "/mcp", json={})).status_code == 404
        assert (await http.post(base + "/api/v1/assets", headers={"Authorization": "Bearer " + upload_token})).status_code == 401
        async with streamable_http_client(base + "/" + path_key + "/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert len(listed.tools) == 9
                with tempfile.TemporaryDirectory() as temp:
                    for fmt, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png")):
                        file = Path(temp) / ("curl sample." + fmt.lower())
                        image = Image.new("RGB", (1600, 800), "navy")
                        draw = ImageDraw.Draw(image)
                        draw.ellipse((60, 60, 300, 300), fill="orange")
                        image.save(file, fmt)
                        result = await session.call_tool("rm_asset_upload_link", {
                            "expected_bytes": file.stat().st_size, "mime": mime,
                            "title": "Integration curl " + fmt, "description": "Synthetic curl acceptance fixture",
                            "tags": ["integration-test"], "occurred_at": "2026-10-30T00:00:00+00:00",
                        })
                        assert not result.isError
                        link = result.structuredContent
                        args = shlex.split(link["curl_template"].replace("{FILE}", str(file)))
                        uploaded = subprocess.run(args, capture_output=True, text=True, timeout=30)
                        assert uploaded.returncode == 0, "curl failed (URL redacted)"
                        data = json.loads(uploaded.stdout)
                        aid = data["asset_id"]
                        if not data["deduplicated"]:
                            created.append(aid)
                        search = await session.call_tool("rm_asset_search", {"query": "Integration curl " + fmt})
                        assert search.structuredContent["items"][0]["asset_id"] == aid
                        assert all(block.type == "text" for block in search.content)
                        inspected = await session.call_tool("rm_asset_inspect", {"asset_id": aid})
                        block = next(b for b in inspected.content if b.type == "image")
                        with Image.open(io.BytesIO(base64.b64decode(block.data))) as preview:
                            assert preview.size == (1024, 512)
                        metadata = (await session.call_tool("rm_asset_get", {"asset_id": aid})).structuredContent["asset"]
                        assert metadata["source"] == "claude.ai" and metadata["tags"] == ["integration-test"]
                        viewed = await session.call_tool("rm_asset_view", {"asset_id": aid})
                        assert viewed.meta["remember_me"]["image"]["data"]
                headers = {"Authorization": "Bearer " + upload_token}
                content = io.BytesIO()
                Image.new("RGB", (50, 30), "green").save(content, "JPEG")
                quick = await http.post(base + "/api/v1/quick-upload", headers=headers,
                                        files={"file": ("quick.jpg", content.getvalue(), "image/jpeg")})
                assert quick.status_code in {200, 201}
                aid = quick.json()["asset_id"]
                if not quick.json()["deduplicated"]:
                    created.append(aid)
                for method, route in (("GET", "/api/v1/assets"), ("GET", "/api/v1/assets/" + aid),
                                      ("GET", "/api/v1/assets/" + aid + "/content"), ("DELETE", "/api/v1/assets/" + aid)):
                    assert (await http.request(method, base + route, headers=headers)).status_code == 401
                missing = await session.call_tool("rm_asset_search", {"undescribed": True})
                assert aid in {item["asset_id"] for item in missing.structuredContent["items"]}
                heic = await http.post(base + "/api/v1/quick-upload", headers=headers,
                                       files={"file": ("test.heic", b"not-a-jpeg", "image/heic")})
                assert heic.status_code == 415 and "HEIC" in heic.text
        # Remove only the synthetic assets created in this acceptance run.
        for aid in created:
            assert (await http.delete(base + "/api/v1/assets/" + aid,
                                      headers={"Authorization": "Bearer " + main_token})).status_code == 200
    logs = subprocess.run(["journalctl", "-u", "remember-me", "--no-pager"], capture_output=True, text=True, check=True).stdout
    for value in (path_key, main_token, upload_token):
        assert value not in logs, "credential appeared in service log"
    print(json.dumps({"https": True, "bare_mcp": 404, "tools": 9, "curl_jpeg_png": True,
                      "inspect_1024": True, "metadata": True, "upload_only_scope": True,
                      "heic_415": True, "undescribed": True, "service_log_secrets_absent": True}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        # Client exceptions may contain URLs. Keep credentials out of command logs.
        print("Live smoke failed; exception details redacted to protect capabilities.")
        raise SystemExit(1) from None
