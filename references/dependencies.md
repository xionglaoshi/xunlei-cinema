# 运行环境、工具重建与数据迁移

## 目录职责

- 技能目录 `~/.codex/skills/xunlei-cinema/`：规则、参考文档、`scripts/`、`private/`、`var/` 和 `影片库.md`。脚本随技能发布；私人数据、缓存、日志和备份由 `.gitignore` 排除，禁止公开发布。
- Codex 共用环境 `~/.codex/venv/`：所有 Python 脚本共用该环境，不创建技能专属解释器或 venv。
- 可重下载工具 `~/.codex/tools/xunlei-cinema/vendor/`：PanSou 二进制及源码、xunlei-cli 上游源码和第三方许可证。可用 `XUNLEI_TOOLS_DIR` 覆盖工具根目录。
- CLI token 仍在 `~/.config/xunlei-cli/token.json`；网页登录与 CLI 独立登录互不替代。不要复制账号凭据到技能或仓库。

## 重建共用 Python 环境和 xunlei-cli

前置：本机已有 `uv`、Git 和网络。先检查 `~/.codex/venv/bin/python3 -V`。已有可用环境直接复用；环境缺失时才运行下面的创建命令，不能覆盖已有环境：

```sh
uv venv --python 3.11 ~/.codex/venv
```

上面只创建共用 Python 环境；其他 Codex 任务的依赖按各自清单恢复。xunlei-cinema 的依赖用以下入口安装，不需要 pip，也不另外创建 venv：

```sh
sh ~/.codex/skills/xunlei-cinema/scripts/setup.sh
~/.codex/venv/bin/python3 -c 'from xunlei.api import XunleiAPI; from xunlei.auth import AuthManager; print("OK")'
~/.codex/venv/bin/python3 ~/.codex/skills/xunlei-cinema/scripts/cinema_cli.py --help
```

安装脚本将 xunlei-cli 源码下载到工具目录，固定 commit `f6af5bfd6b2b182ed2129421285fc57e8db13706`，再使用 `uv pip install --python` 安装进共用环境。`PYTHON` 可指定已经存在的 Codex 共用解释器。安装不会恢复独立登录；需要时按 `troubleshooting.md` 由用户完成验证。缺少 CLI 不阻断网页登录操作。

## 重建 PanSou

本机已有二进制可直接复用。新设备需要 Go 1.25+、Git 和网络；以下示例从固定上游源码重建，不把二进制放入技能目录：

```sh
mkdir -p ~/.codex/tools/xunlei-cinema/vendor
git clone https://github.com/fish2018/pansou.git ~/.codex/tools/xunlei-cinema/vendor/pansou-src
git -C ~/.codex/tools/xunlei-cinema/vendor/pansou-src checkout --detach 1667c66f578a91144153f9dfba388d086c23449e
cd ~/.codex/tools/xunlei-cinema/vendor/pansou-src
```

若源码目录已存在，先检查现有修改，不能用以上命令覆盖；无未提交修改时再 fetch 并切到指定 commit。编译前检查 `main.go` 的 HTTP server `Addr`：应为 `"127.0.0.1:" + port`。若上游是 `":" + port`，仅将该项改成回环地址；无法定位时停止启动，不能直接暴露服务。

```sh
go build -o ../pansou .
cp LICENSE ../PANSOU-LICENSE
PANSOU_BIN="$HOME/.codex/tools/xunlei-cinema/vendor/pansou" sh "$HOME/.codex/skills/xunlei-cinema/scripts/start_pansou.sh"
```

现有 macOS Apple Silicon 二进制名为 `pansou-darwin-arm64`，启动脚本默认使用它；其他二进制用 `PANSOU_BIN` 指定。服务前台运行，完成后 Ctrl-C 停止。缓存写入技能 `var/`。重建说明的命令和路径已检查；本轮未重新下载或编译 PanSou。缺少 Go、架构不兼容或构建失败时，继续可用的网页检索渠道。

## 私人数据迁移

`private/`、`var/` 与 `影片库.md` 不能通过公开仓库重建，应按用户指定的私有备份成组恢复到技能目录，保留权限及原始证据；不可用同名搜索候选重造历史。token 不属于这些数据，换机重新登录。

第三方来源：[PanSou](https://github.com/fish2018/pansou)，MIT 许可证在工具目录保留；[xunlei-cli](https://github.com/nesk-woe/xunlei-cli)，技能 MIT 不覆盖该上游源码，使用前核对其条款。
