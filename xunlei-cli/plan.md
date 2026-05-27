# Web UI 集成计划

## 目标
将 Web UI 监控面板集成到 xunlei-cli 中，通过 `xunlei webui` 命令启动。

## 阶段

### Stage 1 - 并行文件更新
- **Task A**: 更新依赖文件（requirements.txt + pyproject.toml）添加 fastapi, uvicorn, jinja2
- **Task B**: 完善 webui.py（添加 uvicorn 启动函数 + `__main__` 入口）
- **Task C**: 更新 cli.py（添加 `xunlei webui` 命令）
- **Task D**: 更新 downloader.py（添加 DB 进度跟踪钩子）

### Stage 2 - 验证
- 检查所有文件的一致性
- 确保导入正确
