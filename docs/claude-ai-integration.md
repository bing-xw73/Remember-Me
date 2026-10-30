<!-- SPDX-License-Identifier: CPAL-1.0 -->

# claude.ai / Kelivo 图像记忆集成

Remember-Me — originally created by Ting (peanutsuee).
本改造在 bing-xw73/Remember-Me fork 完成，保留 LICENSE、NOTICE 和原作者署名。

## 第 0 步：改造前审计

基线源码已先阅读 README.md、docs/mcp.md、docs/mcp-tools.md、
docs/http-api.md、docs/security.md、docs/standalone-host.md。

1. `rm_asset_upload_link(expected_bytes, filename, mime_type)` 原来返回
   `ok, upload_id, upload_url, expires_at, max_bytes`。URL 接收 multipart
   **POST**，唯一字段 `file`；由 curl 自动生成含 boundary 的 Content-Type，
   不需要 Bearer。原有效期 300 秒，单次使用，失败后不能重放，重启失效。
   MIME 可为 PNG/JPEG/octet-stream，图片最大 10 MiB，总 multipart 最大 11 MiB。
2. 原 `rm_asset_view` 返回文字、structuredContent 和 Viewer 专用
   `_meta.remember_me.image`，**没有 MCP ImageContent**。`rm_asset_inspect`
   才返回 ImageContent，原来返回完整的清洗后 PNG/JPEG，无缩放。
   按冰冰后续修正，缩放功能改到 **inspect**，view 继续服务 Viewer。
3. 关键词查 asset_id、tags、title、original_filename、description，支持标签、
   类型、MIME、created_from/created_to（日历日、含边界）过滤。Core 已支持将
   关键词结果与有效同模型向量的余弦结果合并，纯语义最低分默认 0.42。
   Standalone 原来仅注入 NullVectorProvider，无 embedding 环境变量或 key，
   没有配置时仅关键词检索；原来上传和 metadata 修改不会自动 reindex。
4. 原 metadata：title（200 字符）、description（4000）、tags（最多 30 个，
   每个 64 字符），原文件名、MIME、尺寸/字节数、created_at/updated_at 为服务端字段。
   原来没有 source/occurred_at；不能把 occurred_at 冒充服务端 created_at。

## 字节与兼容性边界

claude.ai 聊天附件不传入 MCP 参数。唯一字节通道是代码执行容器的
`/mnt/user-data/uploads/` → bash curl → Ticket URL。不要读取图片内容或将
base64 填进工具参数。PNG/JPEG 使用附件原始字节上传；服务端继续按原 Core
清除 EXIF、方向处理和重新编码，长期保存的是隐私清洗后的图像，不能承诺保留
原文件的逐字节副本。不更改 ombre-brain-assets-v1、哈希算法或内容寻址路径。

## 执行记录

- VPS 目标：阿里云 Ubuntu 24.04；域名 `rm.wu-bing.xyz`，沿用 nginx。
- DNS 由冰冰手动添加 Cloudflare DNS-only A 记录；IP 待现有 SSH/部署记录核实。
- 源码、测试、部署和客户端实测结果在完成各阶段后追加；未执行的验证不得视为通过。

## 第 1 步：VPS 配置

`deploy/` 提供 systemd、nginx、备份脚本及精确版本 constraints。
复用 nginx 80/443，只绑定 127.0.0.1:8787，数据 `/var/lib/remember-me`，
独立非 root 用户 `remember-me`。Core 支持范围不变；生产与本次测试固定
Pillow==12.3.0。先检查 `ss -ltnp`，8787 已确认空闲。

回环 bind 的 network gate 不需要开启：`REMEMBER_ME_ALLOW_NETWORK=false`。
`REMEMBER_ME_PUBLIC_BASE_URL=https://rm.wu-bing.xyz` 独立控制 Ticket URL；
不根据 Host 或 forwarded headers 推断，保留 SDK 的 Host/Origin 验证。

nginx 仅精确匹配 `/<随机密钥>/mcp` 并改写为 `/mcp`，覆盖客户端 Authorization
为 Standalone Bearer。裸 `/mcp` 及其子路径返回 404；Dashboard、资产 API、
Ticket transfers 放行，未知路径返回 404。路径密钥与 Bearer 分别独立生成至少
48 字节随机值。不要把 secret 实例文件提交 Git，不要运行会打印配置的 nginx -T。
该站点关闭 access/error 日志，防止密钥路径、Authorization、Ticket query 泄漏；
Host 自带日志只记录改写后的固定 route，Uvicorn access log 关闭。

为了满足“Token 只存在反代配置”，唯一持久化主 Token 位于 root:root 0600 的
`/etc/remember-me/nginx-mcp.conf`。systemd LoadCredential 安全复制至运行时目录，
`run-host.py` 从这份配置提取 Token，仅在进程环境传给 Host，绝不作为命令参数。
因此 Host 仍按原安全策略验证 Header，未增加 URL Token 支持。Dashboard 页面
仍须用同一个主 Token 解锁；不要将主 Token 填入连接器 URL。

systemd 用 flock 锁住 `/var/lib/remember-me/.writer.lock`；仅一个 service worker，
claude.ai 和 Kelivo 只是该进程的两个客户端，不能另起 Host 指向同一目录。
每日 04:15（服务器时区）备份会先停止唯一写进程，归档全部数据库与 assets，验证
归档可读后恢复服务，保留 14 天。停机会使未完成 Ticket 失效。备份在
`/var/backups/remember-me`；异地加密备份目的地尚需另配，不能把同机备份当作
机器丢失后的恢复保证。恢复时先停服务，解压整个 data root 并恢复属主，再启动。

Cloudflare 记录：A / rm / 8.220.241.185 / DNS only（灰云）。DNS 生效后用现有
certbot 的 webroot 签发独立证书，测试续期并复用 nginx reload hook。
