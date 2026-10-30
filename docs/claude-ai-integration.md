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

## 第 2/3 步：保存与再看图

`rm_asset_upload_link` 保留 expected_bytes/filename/mime_type，增加可选 mime 别名、
title、description、tags、occurred_at、source（默认 claude.ai）。mime 省略时由
Core 解码识别 PNG/JPEG。occurred_at 须为带时区的 ISO timestamp，统一转 UTC。
metadata 在签发 Ticket 前校验，写入新资产；内容去重时保留已有 metadata。
新增 source/occurred_at 存在独立 `host-metadata.sqlite3`；恢复旧 Core 数据时没有
sidecar 就以空字段显示，不伪造历史。扩展写入失败会清理本次新建资产并让上传失败。

返回 upload_url/url、method=POST、headers={}、multipart_field=file、expires_at、
max_bytes、curl_template。curl 负责生成 Content-Type boundary，无需 Bearer。
有效期 15 分钟；原单次、失败不可重放和重启失效规则不变。返回模板中的 `{FILE}`
换成附件路径。路径一般可含空格；包含单引号的特殊路径先重命名到一个普通临时名，
避免破坏 bash 的引用。不要输出 signed URL 到日志，也不要用 curl -v。
上传成功顶层有 asset_id。实测以 curl 执行模板上传 JPEG 和 PNG。

工具说明明确附件目录 `/mnt/user-data/uploads/`、使用 bash curl、不读文件内容/
不转 base64。HEIC/WebP 等先由容器 Pillow 转 JPEG；Pillow 默认不一定能解码
HEIC，缺解码器时需安装 pillow-heif 或让手机分享 JPEG，不能冒称 Pillow 原生支持。
不要自行计算、猜测或提供哈希。

`rm_asset_inspect(asset_id, size="medium")` 返回真正 MCP ImageContent：small
长边不超过 512px，medium 1024px；保持比例、不放大，JPEG quality 80，透明部分
以白色合成。original 返回完整清洗后 PNG/JPEG 存储字节，不重新编码。
预览从已校验的清洗后字节生成，不写回 CAS，不改资产身份。
`rm_asset_view` 保留原 Viewer `_meta` 行为，没有新增 ImageContent。
两者的 TextContent 都含标题、完整描述、标签、时间与短期图片下载链接。
链接仅供人点击，不能让文字客户端的模型自行获得像素。

`rm_asset_search` 仅 TextContent/紧凑 structuredContent，不返回图片；默认 5 条，
显式 limit 最大 50，description 前 100 字符。items 字段为 asset_id、title、
description、tags、created_at；同时返回 total/offset/limit/search_mode/note。
created_from/created_to 接受 YYYY-MM-DD，按服务端上传时间 created_at 的 UTC
日历日含边界过滤，不能填「上周」这类自然语言；Claude 应先算日期再传入。
occurred_at 是独立事件时间，当前检索时间范围针对上传日期。

## 第 4 步：检索与索引

Host 的搜索启用 Core 的关键词/向量 reciprocal rank fusion（k=60）；相同资产
去重，精确 asset_id 优先，其后按两路倒数排名合计排序，同分按时间、ID 稳定排序。
描述和标签继续进入原 canonical index text，没有改变 embedding 内容 hash 算法。
纯语义最低分继续为 0.42，同模型/同维度/当前 metadata hash 才可参与。

HTTP、MCP 修改 metadata，普通/签名/快捷上传都会自动调用 Core 的逐资产索引，
不要求用户手动 reindex。提供方失败不会回滚已保存的图片或文字；关键词仍可用，
旧向量按原 Core 安全替换策略处理，过期向量不会参与查询。缺服务或查询失败会在
结果中明确 search_mode=keyword；成功执行语义合并时为 hybrid。

外部 embedding 网络提供方：等待冰冰明确选择接收方后接入。自动审批曾拒绝
OpenAI-compatible 外发补丁，理由是需要明确授权发出的索引文本和目的地；拒绝的
补丁未写入。当前部署不发送任何 metadata 给第三方，使用 NullVectorProvider。
Core 的注入 provider、自动更新、混合排序和失败降级已使用测试 provider 验证。

## 第 5 步：iOS

`POST /api/v1/quick-upload` 接收 multipart file、可选 description、tags，
服务端自行计算长度，一次完成 ingest+metadata+索引，返回 asset_id。
独立上传 Token 仅 Header，不能读/搜/改/删或调用 MCP；默认每分钟 10 次，
单文件 10 MiB、请求 11 MiB。去重仅返回 ID/状态，不泄露旧图和旧 metadata。
HEIC/WebP 明确返回 415 并要求先转 JPEG。

`undescribed=true` 在关键词和语义路径共同过滤、排序和分页前执行。
Claude 应先查未描述列表，再逐图 inspect，最后 update_metadata 补描述/标签。
分享菜单详细搭建和配置源文件见 [ios-shortcut.md](ios-shortcut.md)。
当前仅交付未签名配置；Apple 签名和原生导入/HEIC 分享仍待设备验证。

## claude.ai 端设置（冰冰操作）

1. 设置中开启代码执行，开启网络出站。Additional allowed domains **只填**
   `rm.wu-bing.xyz`，不要填 https、路径、Token、signed URL，不要开启 All domains。
2. 添加自定义连接器，填私下交付的 `https://rm.wu-bing.xyz/<随机路径>/mcp`，
   不配置自定义 Authorization Header。路径本身是密钥，不贴进公开文档或提示词。
3. 在个人偏好中由冰冰自行写保存/再看图规则。规则应要求保存时用 upload_link +
   curl 原附件字节并核实 asset_id；再看时先 search，再 inspect，不能只读描述就声称看过。
4. 新开对话发一张 JPEG/PNG，验证同一轮拿链接+curl 保存，描述/标签正确；再开
   另一个窗口，凭内容/时间查找，inspect 后回答一个只有看图才知道的细节。
   当前没有操作已登录 claude.ai 或冰冰 iPhone，这些客户端验收尚未执行。

## 第 6 步：Kelivo 兼容性

冰冰确认只有 iPhone，电脑没有 Kelivo；手机具体版本尚未取得，因此**未完成手机
客户端的端到端视觉实测**。不能以 MCP 协议通过、界面显示图片或源码审计替代。

只读审计的 Kelivo 主分支 commit 为
`ca2721e07b31cc8acc331c7a9f400109f8c1e298`，pubspec 版本 `1.3.2+81`。
源码链路（不是冰冰安装版本的保证）：

- [mcp_tool_service.dart](https://github.com/Chevey339/kelivo/blob/ca2721e07b31cc8acc331c7a9f400109f8c1e298/lib/core/services/mcp/mcp_tool_service.dart#L291)
  识别 typed MCP ImageContent，保存本地快照，单独记录 imageUris，不依靠文字猜图片。
- [tool_result_content.dart](https://github.com/Chevey339/kelivo/blob/ca2721e07b31cc8acc331c7a9f400109f8c1e298/lib/core/services/api/tool_result_content.dart#L18)
  读取快照；`claudeContent` 将图片转为 Claude 原生 image/source/base64 block，
  如果模型未标记为支持图片或快照不可用则明确省略。
- [claude_official.dart](https://github.com/Chevey339/kelivo/blob/ca2721e07b31cc8acc331c7a9f400109f8c1e298/lib/core/services/api/providers/claude_official.dart#L686)
  在 tool_result 中使用上述内容。由此**推断**当前代码支持真正的模型看图，仍需真机验证。
- 历史 [issue #486](https://github.com/Chevey339/kelivo/issues/486) 曾报告 iOS 图片快照
  路径错误，已关闭；不能据此认定所有安装版本都正常。

手机验收步骤：

1. 记录 Kelivo 设置/关于中的版本号。添加 MCP，类型 **Streamable HTTP**，地址和
   claude.ai 相同，使用私下交付的密钥路径；不要再开一个服务端。
2. 确认全部九个 rm_asset_* 工具可见，并选支持视觉的 Claude API 模型。
3. 新建一个对话，**不要把测试图发进该对话**，只给盲测图的 asset_id。要求先调
   `rm_asset_inspect`，再回答图中随机数字和形状位置，禁止从标题/描述猜测。
   盲测图的标题和描述不含答案；提示词也不要泄露答案。
4. 继续发下一条消息，确认图片快照仍可读。若视觉细节答不出，记录工具响应中
   是否有 image block、Kelivo 是否标记模型支持视觉、API 请求是否带 image block。
5. 如果图片块被丢弃，完整文字描述可降级检索；inspect/view 同时附的五分钟签名
   链接供冰冰点击看图。模型并不会因为看到 URL 而看到像素。

客户端验收记录表：

| 客户端 | 实测版本 | 工具列表/调用 | 陌生图视觉细节 | 状态 |
| --- | --- | --- | --- | --- |
| claude.ai 手机/网页 | 未取得 | 待客户端连接 | 待新窗口测试 | 待验收 |
| Kelivo iPhone | 待冰冰提供 | 待客户端连接 | 待盲测 | 待验收 |
| 官方 Python MCP SDK | 1.28.1 | HTTPS 反代验收脚本覆盖 | 校验 ImageContent 字节/尺寸，非模型视觉理解 | 服务端验证 |

任何数量的客户端都连同一个 systemd Host；不会因 claude.ai 与 Kelivo 同时连接
而增加服务器写进程。部署验收会核实 8787 的唯一监听和该 service 的进程树。
