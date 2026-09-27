# 迅雷家庭影院 / xunlei-cinema

我在电视上用迅雷 TV 版观看云盘影片，但每次找资源、分辨真假 4K、再整理到云盘目录都要重复操作。这个技能把过程交给 AI Agent：按片名搜索公开线索，比较版本与画质可信度，在用户要求保存时把选定资源转存或提交离线任务到迅雷云盘的影片目录。

## 能做什么

- 先用 PanSou、Bing RSS、百度检索，再用 Agent 的网页搜索及浏览器自动化补查；覆盖迅雷分享、磁力、`thunder://`、种子线索、常见 HTTP/HTTPS 视频直链和公开 FTP 线索。
- 结合片名、年份、来源页、声明分辨率、片长和文件大小给候选排序。默认优先可信的 4K/2160p，以 1080p 兜底。名称或网页声称的分辨率不等于真实视频参数。
- 使用独立的迅雷登录：系列片放在 `家庭影院/<系列名>系列`，非系列片放在 `家庭影院/<片名>`，不套中间目录。提交前预览目标并检查同名文件；任务完成后读回目录，才确认已保存。
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

把技能所在目录交给支持技能的 AI Agent，然后直接说：

> 用 xunlei-cinema 找《黑鹰坠落》的可信 4K 片源；如果没有可信 4K，再找 1080p。先报告候选，不要保存。

需要入盘时说：

> 把我选定的链接保存到迅雷云盘“家庭影院/星际迷航系列”，完成后核对目录。

Agent 按 [`SKILL.md`](SKILL.md) 执行。手动检查链接的目标目录：

```sh
var/xunlei-cli-venv/bin/python scripts/cinema_cli.py 'https://example.org/film.mp4' \
  --folder '家庭影院/星际迷航系列' --expect-name 'film.mp4'
```

默认只预览；确认后加 `--execute` 才会创建缺失目录并提交离线任务。迅雷云盘分享使用独立的逐文件转存命令：

```sh
var/xunlei-cli-venv/bin/python scripts/cinema_share.py 'https://pan.xunlei.com/s/分享ID?pwd=提取码'
# 从只读清单选准确文件 ID 后预览；核对无误再加 --execute
var/xunlei-cli-venv/bin/python scripts/cinema_share.py 'https://pan.xunlei.com/s/分享ID?pwd=提取码' \
  --file-id '清单中的ID' --folder '家庭影院/流浪地球系列' --min-gb 10
```

分享中的正片原名若只有 `1.mkv`，可附加 `--save-as '影片英文名 (年份) - 4K.mkv'`，转存后在云盘改成可辨认的名称；文件名中的 4K 仍是来源声明。登录时运行 `var/xunlei-cli-venv/bin/python scripts/browser_login.py`，把它输出的本机 URL 在内置浏览器打开，由用户自行输入账号和完成迅雷官方验证。

## 已验证与边界

在真实账号上验证了独立登录、HTTPS 开放样片的离线任务完成和目标目录读回，也验证了迅雷分享中单个视频的转存、重命名与目标目录读回。同名文件检查、容量明显过小的 4K 声明降权也已验证。磁力、`thunder://`、FTP 和 BT 种子尚未逐类完成真实入盘验证；搜到链接不代表其可播放或画质真实。只处理你有权访问和保存的内容。

本仓库原创代码与文档按 [MIT License](LICENSE) 发布；第三方 PanSou 及独立下载的 `xunlei-cli` 各自遵循其上游条款。本项目与迅雷官方无关联。
