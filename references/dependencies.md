# 依赖与迁移

## 仓库内容

- 本项目原创脚本、技能规则和文档按 MIT 发布。
- `vendor/pansou-darwin-arm64` 为基于 [PanSou](https://github.com/fish2018/pansou) commit `1667c66f578a91144153f9dfba388d086c23449e` 构建的 macOS Apple Silicon 二进制。PanSou 的 MIT 许可证保留在 `vendor/PANSOU-LICENSE`。启动脚本只监听 `127.0.0.1`。
- `scripts/setup.sh` 将 [xunlei-cli](https://github.com/nesk-woe/xunlei-cli) 指定 commit 单独下载至技能内 `vendor/xunlei-cli-src/`，再创建 `var/xunlei-cli-venv/`。两者均被 Git 忽略，不随本仓库分发。上游没有声明许可证；本项目 MIT 不覆盖它。
- 缓存、日志和搜索 JSON 置于技能内 `var/`。独立登录 token 位于 `~/.config/xunlei-cli/token.json`；不要复制到仓库或 WIKI 教程，换机重新登录。

## 重新安装

在 macOS Apple Silicon 上，克隆仓库后运行 `sh scripts/setup.sh`。需要 Python 3.9+、Git 和网络。浏览器自动化由当前 Agent 环境提供，不是技能目录里的二进制依赖。

其他平台：先从 PanSou 上游指定 commit 获取源码，使用 Go 1.25+ 编译本平台二进制；将监听地址限制为 `127.0.0.1`，把二进制放入本技能的 `vendor/`，并通过 `PANSOU_BIN` 指定给 `scripts/start_pansou.sh`。Python venv 在新平台重新运行安装脚本生成，不搬运旧 venv。若 PanSou 尚未装好，脚本仍能用 Bing RSS 和百度搜索，并按 `SKILL.md` 走网页/浏览器补查。
