# 迅雷家庭影院 / xunlei-cinema

在电视上用迅雷 TV 版观看云盘影片，但每次找资源、分辨真假 4K、再整理到云盘目录都要重复操作。这个技能把过程交给 AI Agent：按片名搜索公开线索，比较版本与画质可信度，在用户要求保存时把选定资源转存或提交离线任务到迅雷云盘的影片目录。

## 能做什么

- 先用 PanSou、Bing RSS、百度检索，再用 Agent 的网页搜索及浏览器自动化补查；覆盖迅雷分享、磁力、`thunder://`、种子线索、常见 HTTP/HTTPS 视频直链和公开 FTP 线索。
- 结合片名、年份、来源页、声明分辨率、片长和文件大小给候选排序。默认优先可信的 4K/2160p，以 1080p 兜底；4K、杜比、蓝光、MKV 分别加 40、20、15、10 分，单片大小偏好约 20 GB（15–25 GB）。名称或网页声称的分辨率不等于真实视频参数，偏好分不能覆盖身份与可信度核验。
- 找片受阻时按 [`references/resource-strategy.md`](references/resource-strategy.md) 换别名、站点和链接类型；其中的“有效资源点”记录最终选优入盘的影片及线索渠道。
- 使用独立的迅雷登录：系列片放在 `家庭影院/<系列名>系列`，非系列片放在 `家庭影院/<片名>`，不套中间目录。提交前预览目标并检查同名文件；任务完成后读回目录，才确认已保存。
- 正片文件统一用中文片名、完整英文原名、上映年份和原有技术信息命名；系列中文名采用 `系列名＋序号.单片副标题`，例如 `黑客帝国3.矩阵革命.The Matrix Revolutions.2003.mkv`。具体规则见 [`references/film-naming.md`](references/film-naming.md)。
- 账号、新设备图形验证和短信验证由用户在内置浏览器完成。登录与故障排除步骤见 [`references/troubleshooting.md`](references/troubleshooting.md)。

## 安装

面向 macOS Apple Silicon；其他平台的 PanSou 重建方法见 [`references/dependencies.md`](references/dependencies.md)。需要 Python 3.9+、Git 与可访问 GitHub/PyPI 的网络。

```sh
git clone https://github.com/xionglaoshi/xunlei-cinema.git ~/.agents/skills/xunlei-cinema
cd ~/.agents/skills/xunlei-cinema
sh scripts/setup.sh
```

安装脚本把第三方 `xunlei-cli` 和 Python 虚拟环境放在**技能目录内部**。PanSou 的 macOS ARM64 二进制已随仓库提供。登录 token 单独保存在 `~/.config/xunlei-cli/token.json`，**不会进入本仓库**；换机时重新登录。第三方组件的授权边界见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

## 使用

说“更新影片库”可手动读取家庭影院现有文件并更新私人 `影片库.md`，保留原始来源、命名、路径和已删除记录。实现入口为 `scripts/cinema_library.py`，默认预览，`--execute` 只更新本地台账；操作与恢复见 [`references/library-management.md`](references/library-management.md)。影片库及 `private/` 数据和备份不随 Git 发布，迁移时另行私有备份。

把技能所在目录交给支持技能的 AI Agent，然后直接说：

> 用 xunlei-cinema 找《黑鹰坠落》的可信 4K 片源；如果没有可信 4K，再找 1080p。先报告候选，不要保存。

需要入盘时说：

> 把我选定的链接保存到迅雷云盘“家庭影院/星际迷航系列”，完成后核对目录。

Agent 按 [`SKILL.md`](SKILL.md) 执行。

PanSou 启动脚本默认查询公开 Telegram 频道 `tgsearchers7,seedhub_pro`，可用环境变量 `CHANNELS` 替换。此方式读取公开频道，不需要用户 Telegram 账号或机器人 token；加入频道不代表已证明它能命中目标影片。SeedHub 的网页入口统一使用 `https://sidhub.cc/`。

手动检查链接的目标目录：

```sh
var/xunlei-cli-venv/bin/python scripts/cinema_cli.py 'https://example.org/film.mp4' \
  --folder '家庭影院/星际迷航系列' --expect-name 'film.mp4'
```

默认只预览；确认后加 `--execute` 才会创建缺失目录并提交离线任务。迅雷云盘分享使用独立的逐文件转存命令：

```sh
var/xunlei-cli-venv/bin/python scripts/cinema_share.py 'https://pan.xunlei.com/s/分享ID?pwd=提取码'
# 从只读清单选准确文件 ID 后预览；核对无误再加 --execute
var/xunlei-cli-venv/bin/python scripts/cinema_share.py 'https://pan.xunlei.com/s/分享ID?pwd=提取码' \
  --file-id '清单中的ID' --folder '家庭影院/流浪地球系列' --min-gb 10 \
  --chinese '流浪地球2' --english 'The Wandering Earth II' --year 2023
```

已核对的中文片名、英文片名和年份会与来源文件中可用的技术信息组成规范文件名；原名为 `1.mkv` 时，应先从分享目录、片页及年份确认影片身份。已有云盘文件可用 `scripts/cinema_rename.py` 的预览、执行和回滚清单整理。登录时运行 `var/xunlei-cli-venv/bin/python scripts/browser_login.py`，把它输出的本机 URL 在内置浏览器打开，由用户自行输入账号和完成迅雷官方验证。

## 已验证与边界

在真实账号上验证了独立登录、HTTPS 开放样片的离线任务完成、部分磁力离线完成和目标目录读回，也验证了迅雷分享中单个视频的转存、批量重命名与目标目录读回。同名文件检查、容量明显过小的 4K 声明降权及私人影片库更新已验证。`thunder://`、FTP 和 BT 种子尚未逐类完成真实入盘验证；磁力也可能被服务端拒绝或仅产生 BDMV 目录，搜到链接不代表其可播放或画质真实。只处理你有权访问和保存的内容。

用户说“验证影片库链接，但不要添加”时，使用 `scripts/check_library_sources.py`：默认只预览，加 `--execute` 仅调用迅雷资源预解析／分享查询并写本机私有验证记录，不创建下载或转存任务。验证结果区分链接可解析、目标文件匹配和仅大小匹配；成功解析不能保证最终离线成功。操作及补提取码流程见 [`references/library-management.md`](references/library-management.md)。

本仓库原创代码与文档按 [MIT License](LICENSE) 发布；第三方 PanSou 及独立下载的 `xunlei-cli` 各自遵循其上游条款。本项目与迅雷官方无关联。
