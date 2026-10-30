<!-- SPDX-License-Identifier: CPAL-1.0 -->

# 存进 Remember-Me（iPhone 分享菜单）

Remember-Me — originally created by Ting (peanutsuee).

服务地址：`https://rm.wu-bing.xyz/api/v1/quick-upload`。
用单独的 **上传专用 Token**，不要填 Dashboard 主 Token，也不要填 MCP 密钥路径。
Token 从私下交付的连接资料获取，不放进 Git、截图或聊天提示词。

## 在 iPhone 上分步搭建

1. 新建快捷指令，命名「存进 Remember-Me」。在详情启用「在共享表单中显示」，
   输入类型只选「图像」。每次分享一张；批量分享需要另外加逐张重复，本配置只取第一张。
2. 「从列表中获取项目」取快捷指令输入的第一项，存为「照片」。
3. 获取照片「宽度」和「高度」。如果宽度大于高度，用「调整图像大小」将宽设为
   2048，高留自动；否则宽留自动，高设为 2048。两分支都引用「照片」。
4. 对「如果结果」执行「转换图像」，选 **JPEG**，质量约 85%，关闭「保留元数据」。
   把结果保留为「JPEG」。这一步不能省：HEIC/WebP 不被服务端接受。
5. 「询问输入」，提示「这张图是什么？（可以留空）」，类型文本，默认空，存为「描述」。
6. 「获取 URL 内容」：上面的 quick-upload 地址，方法 **POST**。Header：
   `Authorization` = `Bearer <上传专用Token>`（Bearer 后有一个空格）。
   请求体选 **表单**，添加 `file`，类型必须是**文件**、值选步骤 4 的「JPEG」；
   添加文本 `description`，值选「描述」；可选文本 `tags`，逗号分隔，例如「相册,旅行」。
   不要手填 Content-Type，快捷指令会生成 multipart boundary；不要发 base64 或 JSON 文件内容。
7. 从返回词典取 `asset_id`。有值才「显示通知」：**已存入**；否则显示错误响应。
   网络/HTTP 错误会终止获取 URL 内容，不应无条件弹成功通知。
8. 第一次运行时，只允许连接 `rm.wu-bing.xyz`。从相册分享一张 HEIC 照片测试，
   再在 Claude 中搜索未描述图片，确认其能 inspect 并补写描述。

描述可以为空。Claude 之后调用 `rm_asset_search(undescribed=true)`，逐张
`rm_asset_inspect(asset_id)`，再 `rm_asset_update_metadata` 补描述和标签。
补完后该图自动退出未描述列表，索引也会自动更新。去重上传保留原有 metadata。

## 配置源文件与 Apple 导入限制

仓库提供 `tools/build_ios_shortcut.py` 和生成的
`shortcuts/Remember-Me.unsigned.shortcut`，包含以上完整动作、导入 Token 问题、
长边缩放、JPEG 转换、multipart 文件字段、成功/失败分支。文件只含 Token 占位符。

这个文件是**未签名配置源文件**，不能声称已成为可在现代 iOS 上直接导入的成品。
当前工作环境是 Windows + Ubuntu，没有可用 Mac/iCloud 签名环境，也不能操作冰冰的
iPhone。可直接按上面的步骤搭建；如果之后有 Mac，可以运行：

```sh
python3 tools/build_ios_shortcut.py
shortcuts sign --mode anyone \
  --input shortcuts/Remember-Me.unsigned.shortcut \
  --output '存进 Remember-Me.shortcut'
```

先签署不含真实 Token 的模板，导入 iPhone 后再填上传 Token。
Apple 官方说明签名时会接收配置副本用于验证，见
[从命令行运行和签名快捷指令](https://support.apple.com/en-gb/guide/shortcuts-mac/apd455c82f02/mac)。
配置字段依据 [Shortcuts 参数格式研究](https://github.com/viticci/shortcuts-playground-plugin/blob/main/claude/skills/shortcuts-playground/PARAMETER_TYPES.md)
和 [ScPL 动作规范](https://docs.scpl.dev/actions/getcontentsofurl) 核对。
原生导入、HEIC 分享和通知流程仍须在设备上验证，结构校验不能代替设备实测。

## 服务端限制与 Token 撤销

一张图最多 10 MiB，整个 multipart 最多 11 MiB，每个上传 Token 每分钟默认最多
10 个请求，失败请求也计数；超限 429 并带 Retry-After。上限可用
`REMEMBER_ME_QUICK_UPLOAD_RATE_LIMIT` 设置 1–1000。限制是全局单用户的，
不相信客户端 IP 或 X-Forwarded-For。重启重置内存速率窗口。

服务端用 `REMEMBER_ME_UPLOAD_TOKEN_FILE` 配置上传 Token，不配置则接口返回 404。
它必须和主 Token 不同；仅接受 Header。它不能访问资产读取、搜索、修改、删除、
MCP，不能签发下载/上传 Ticket。成功响应仅包含 ok、asset_id、deduplicated、
index_updated，不返回旧图片或旧 metadata。

泄露后在服务器更换上传 Token 文件并重启 remember-me，再更新快捷指令；
不需要更换 Dashboard 主 Token 或 MCP 路径。服务端仍只有一个数据写进程。
