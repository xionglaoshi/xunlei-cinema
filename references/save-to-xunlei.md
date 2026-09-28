# 保存到迅雷云盘：已验证路径与操作要点

## 目录与完成标准

默认目录结构：

- 系列影片：`我的云盘/家庭影院/<系列名>系列/`，例如 `星球大战系列`、`星际迷航系列`、`漫威系列`。同一系列的各部影片都放入这一目录，不按每部影片在 `家庭影院` 根目录另建文件夹。
- 非系列影片：`我的云盘/家庭影院/<片名>/`，例如 `家庭影院/阿甘正传/`。片名有歧义时可在目录名后加年份；不要套 `单部电影` 等中间目录。
- 用户指定的归类优先。例如用户把《指环王》和《霍比特人》合并为 `指环王与霍比特人系列` 时，两组影片都沿用该目录；若已存在 `指环王系列` 等相近目录，先核对内容及用户既定归类，不自动再建近义目录，也不擅自移动旧文件。

每次保存先在云盘匹配已有目录，只有不存在时才创建。再检查目标目录内是否已有同一影片和版本，避免重复任务。提交后查看传输状态和目标目录：文件名、大小、所属影片及视频格式应相符。任务进入列表只表示已受理，不能等同于保存完成。

正片命名为 `中文片名.英文原名.上映年份.技术信息.扩展名`，例如 `阿丽塔 战斗天使.Alita Battle Angel.2019.BluRay.2160p.x265.10bit.HDR.mkv`。中英文片名和年份必须根据片目核对，不能从含混的分享标题猜测。保留来源文件已有且有用的 BluRay、2160p、编码、HDR、音轨等字段；删除 `来源标称4K` 等宣传或人工备注。文件系统不通用的冒号等标点以空格替代。名称中的技术字段仍属来源声明，不代表已解码实测。

## 直接链接：技能内适配脚本优先

运行 `sh scripts/setup.sh` 后，`xunlei-cli` 被单独安装到技能内的虚拟环境；本项目原创的云盘路径适配脚本是 `scripts/cinema_cli.py`。首次登录用 `scripts/browser_login.py`：以该虚拟环境的 Python 启动，把输出的一次性本机 URL 打开在内置浏览器，让用户自行输入账号和密码。脚本只在进程内存中暂存密码并调用迅雷登录接口；验证成功后清空密码，将 token 写到 `~/.config/xunlei-cli/token.json`，权限为 `0600`。完成后停止临时服务。不要将密码、短信码、带 `creditkey` 的链接或 token 发在聊天里。

若新设备登录触发 `review_panel`：迅雷给出的 `vertifyPhone.html` 原始长链接直接打开可能白屏，[OpenList 的同类报告](https://github.com/OpenListTeam/OpenList/issues/136)也有此现象。Agent 在内置浏览器打开官方 `https://i.xunlei.com/xlcaptcha/android.html`，按当前浏览器工具的授权规则调用页面的 `reviewCb`，传入本机登录服务提供的一次性验证数据；用户自行完成图形/短信验证。网页可能不显示成功提示，但 `nativeRecvOperationResult` 回调中的 `roErrorCode: 0` 和新的 `creditkey` 表示通过。Agent 将新 key 只传回本机登录服务的 `/verified` 接口，不在聊天或日志里打印。以 token 文件存在、权限正确和 `xunlei user` 成功作为登录完成证据。完整步骤见 `troubleshooting.md`；原版终端 `xunlei login` 会打印敏感验证数据，不作为推荐入口。

保存示例：`var/xunlei-cli-venv/bin/python scripts/cinema_cli.py 'https://example.org/film.mp4' --folder '家庭影院/星球大战系列' --expect-name 'film.mp4'`。默认只预览目标路径和缺失目录；确认路径后加 `--execute` 才会创建目录及离线任务。命令会检查同名文件和最近的同链接离线任务；创建任务后还须查任务状态和目标目录文件，不能只凭提交成功汇报完成。`pan.xunlei.com/s/...` 分享链接会被拒绝并改走转存流程。

开发机上的先前改造版已通过真实账号独立登录及 HTTPS 小样片的云端提交、完成状态和目标目录读回。本公开版 `cinema_cli.py` 调用同一 API，已做只读预览；新安装后的 `--execute` 仍应先用开放小样片复测。若接口或登录失败，在已登录的网页端进入目标目录，点击“添加 → 添加链接”。“新建离线链接任务”应显示完整的“保存到”路径；若不是目标目录，先点“更改”。填入经核对的单个链接，提交后检查反馈、传输列表和目录中的文件。多文件磁力任务要核对解析后的文件清单，避免整包混入非目标影片。失败时记录原链接类型、服务端提示和目录，不盲目重复提交。

2026-09-28 实测：在 `家庭影院/技能验证-开放样片` 提交 MDN 的 CC0 样片 `https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4`。源站 HTTP 200、`video/mp4`、`content-length: 1128375`；云盘目录读回 `flower.mp4`、1.1 MB。该实测只证明 HTTPS 小文件经网页端指定目录离线成功。磁力、`thunder://`、FTP、BT 种子尚未逐类完成真实离线验证。

2026-09-28 再测：独立登录成功后，CLI 以 `cinema-save --execute` 把同源 CC0 的 `flower.webm` 提交到相同目录；离线任务读回 `completed`，目录读回约 541 KB 的新文件。再次预览已存在的 `flower.mp4` 时显示 `Skipped`，证明同名文件检查生效。此结果只覆盖 HTTPS 直接链接，不外推到其他链接类型。

## 迅雷分享：按文件转存

分享 URL 属于 `pan.xunlei.com/s/...` 时，优先运行 `var/xunlei-cli-venv/bin/python scripts/cinema_share.py '<分享 URL>'`。此命令只读取分享状态及视频文件清单，输出文件 ID、完整文件名、实际字节数、分享内路径；按片名、年份、画质声明和容量选择**一个准确文件 ID**。使用 `--file-id '<ID>' --folder '家庭影院/<目录名>' --min-gb 10` 预览，核对后加 `--execute` 才会提交转存并读回文件名和字节数。`--min-gb 10` 适用于当前用户对常规 4K 长片的偏好，不能把容量当成分辨率实测。多部影片须逐个 ID 操作，避免海报、NFO 和错误影片一起入盘。

已确认片目时给转存命令同时加 `--chinese '中文片名' --english 'English Title' --year 2023`，脚本将来源文件名中的可用技术信息接在规范中英片名和年份后；仅在特殊情况下使用 `--save-as`。已有云盘文件的批量改名用 `scripts/cinema_rename.py <JSON清单>` 预览，再加 `--execute`；中断后先核对读回，再用 `--resume --execute` 继续。脚本逐项核对文件 ID、原名、大小，执行前保存回滚清单，改名后按 ID 读回。回滚使用该清单的 `--rollback --resume --execute`，会跳过尚未改名的项目。

2026-09-28 实测：用迅雷分享接口读取《流浪地球》两部合集，分别将 2019 年 `The Wandering Earth`（20,510,105,588 字节）与 2023 年 `The Wandering Earth II`（22,679,383,091 字节）转存到 `家庭影院/流浪地球系列`。两次接口返回 `RESTORE_COMPLETE`，目标目录均按文件名、大小读回。此结果证明这两个分享文件已进入云盘；文件名中的 2160p/HDR 等是资源声明，尚未解码实测，也不证明 TV 客户端播放正常。

分享接口依据迅雷网页客户端公开调用流程：`GET /drive/v1/share` 取得分享状态及 `pass_code_token`；目录下用 `GET /drive/v1/share/detail` 分页读取；提交单个 `file_ids` 到 `POST /drive/v1/share/restore`，并指定目标 `parent_id`。脚本使用已独立登录的 CLI 会话调用，未接触浏览器凭据。分享失效、提取码错误、敏感资源或无准确影片文件时停止，不按聚合搜索标题猜测。若 CLI 登录态或接口异常，可在已登录的网页端打开分享、逐文件选择并转存；读回目标目录后才报告成功。

## 第三方 CLI 的边界

本项目单独下载 `nesk-woe/xunlei-cli` commit `f6af5bfd6b2b182ed2129421285fc57e8db13706`，不发布其源码；原创的 `cinema_cli.py` / `cinema_ops.py` 提供路径解析、目录创建、重复检查和默认预览，`browser_login.py` 将配置目录设为 `0700`、token/配置文件设为 `0600`。上游没有分享转存命令，也不能复用已登录网页会话。其 `dl` 会下载到本机并可能清理云端文件，不适合本技能的“仅保存云盘”目标。授权边界见仓库根目录 `THIRD_PARTY.md`。

来源：[xunlei-cli README](https://github.com/nesk-woe/xunlei-cli/blob/main/xunlei-cli/README.md)、[项目源码](https://github.com/nesk-woe/xunlei-cli)。
