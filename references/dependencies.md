# Codex 运行环境与工具入口

技能目录仅保留指导文档，不安装或分发环境和工具。所有 Python 辅助脚本使用 Codex 共用解释器 `~/.codex/venv/bin/python3`，不创建技能专属 venv。

本机辅助工具根目录：`~/.codex/tools/xunlei-cinema/`。
- `scripts/`：检索、评估、逐文件转存、改名、独立登录和影片库维护脚本。
- `vendor/`：已有 PanSou 二进制、许可证和 xunlei-cli 上游源码；不随技能分发。
- `var/`：缓存、日志、回滚清单和历史检索证据。
- `private/` 与 `影片库.md`：私人渠道台账和影片库，禁止公开发布。

xunlei-cli 安装在 Codex 共用 Python 环境。网页登录与 CLI 独立登录互不替代；token 仍在 `~/.config/xunlei-cli/token.json`，不搬动或复制。新设备按已有 Codex 运行环境核实依赖，缺失时报告；网页保存不要求先安装 CLI。

PanSou 启动入口：`sh ~/.codex/tools/xunlei-cinema/scripts/start_pansou.sh`；可通过 `PANSOU_BIN` 指定已有二进制，平台不兼容时走其他检索渠道。

第三方来源：PanSou（https://github.com/fish2018/pansou），commit `1667c66f578a91144153f9dfba388d086c23449e`，MIT 许可证保留于工具目录 `vendor/PANSOU-LICENSE`；xunlei-cli（https://github.com/nesk-woe/xunlei-cli），commit `f6af5bfd6b2b182ed2129421285fc57e8db13706`，本技能 MIT 不覆盖上游源码；使用前以本机保留的上游条款为据。
