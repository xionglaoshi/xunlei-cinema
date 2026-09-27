# 迅雷家庭影院：故障修复指南

本页记录已遇到的故障和实测可行的恢复路径。先判断故障发生在**检索、独立登录、提交离线任务、目录读回**哪一步；不要把网页已登录、任务已创建、文件已入盘混为一谈。所有命令从技能根目录运行。账号密码、短信码、`creditkey`、token 及带凭据的 URL 不写入聊天、命令参数或日志。

## xunlei-cli 需要重新登录／重新验证：首选完整流程

1. **先查现状**：确认 `~/.config/xunlei-cli/token.json` 是否存在且权限为 `0600`，运行 `var/xunlei-cli-venv/bin/xunlei user`。若能读出正确账号及会员状态，无需重新登录；失败才继续。网页端 `pan.xunlei.com` 的登录状态不等于 CLI 已登录。
2. **启动技能内临时登录页**：运行 `var/xunlei-cli-venv/bin/python scripts/browser_login.py`，保留该终端进程。脚本输出一条随机的 `http://127.0.0.1:<端口>/<随机串>` 地址。用当前浏览器自动化工具把它**显示在内置浏览器**，交由用户自行输入迅雷账号、密码并点“登录”；不要让用户在聊天中发送凭据。此服务只绑定本机回环地址，密码仅在进程内存暂存；成功或失败后清空。
3. **遇到 `review` 阶段**：在内置浏览器另开迅雷官方 `https://i.xunlei.com/xlcaptcha/android.html`，保持用户可见。把本机地址记作 `base`。用浏览器工具在 Agent 运行环境读取 `base + '/review'`，请求头 `Origin: https://i.xunlei.com`；**不要输出响应内容**。调用官方页的 `window.reviewCb(JSON.stringify(reviewData))` 启动官方验证。`reviewData` 包含一次性凭据，不要打印、复制到聊天或放进浏览器 URL。按当前浏览器操作规则获得必要授权；图形和短信验证由用户亲自完成。
4. **页面看似不动时查回调**：只在当前验证页读取开发日志，在 Agent 运行环境内找到最后一条 `nativeRecvOperationResult` 并解析 JSON；`roErrorCode === '0'` 且 `roData.creditkey` 非空，才表示验证通过。日志原文和新 key 不输出。将 `{"creditkey": <新 key>}` 通过本机 POST 发给 `base + '/verified'`；检查返回 `stage: success`。旧 `reviewData.creditkey` 不能代替这里的新 key。
5. **读回确认**：刷新本机登录页，应显示“登录成功”；再检查 token 文件权限 `0600` 并运行 `var/xunlei-cli-venv/bin/xunlei user`。三项都成立才报告独立登录成功。停止 `browser_login.py` 临时进程，避免服务和密码内存长期留存。若浏览器会话结束使验证页消失，重新走本流程，不复用上一轮一次性验证数据。

在支持 CUA 的环境中，第 3、4 步可按下面的结构实施。`base` 必须来自本次脚本输出；浏览器 API 名称以当前工具文档为准。代码仅展示数据流，**不得把 reviewData、日志对象或 key 作为工具输出**。

```javascript
// base = 本次 browser_login.py 输出的本机 URL
const reviewData = await (await fetch(base + '/review', {
  headers: { Origin: 'https://i.xunlei.com' }
})).json();
const tab = await cua.createBrowserTab('iab',
  'https://i.xunlei.com/xlcaptcha/android.html', { visible: true });
const cdp = await tab.capabilities.get('cdp');
await cdp.send('Runtime.evaluate', {
  expression: 'window.reviewCb(' + JSON.stringify(JSON.stringify(reviewData)) + ')',
  returnByValue: true
});
```

用户亲自完成图形/短信验证后，再执行下段；不要提前运行，也不要打印 `logs`：

```javascript
const logs = await tab.dev.logs({ limit: 100 });
const hits = logs.map(x => x.message.match(/^nativeRecvOperationResult (\{.*\})\s*$/))
  .filter(Boolean);
if (!hits.length) throw Error('尚无验证完成回调');
const result = JSON.parse(hits[hits.length - 1][1]);
const key = result?.roData?.creditkey;
if (result?.roErrorCode !== '0' || typeof key !== 'string' || !key)
  throw Error('验证回调尚未成功');
const response = await fetch(base + '/verified', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ creditkey: key })
});
if (!response.ok) throw Error('本机登录服务未接受验证结果');
nodeRepl.write({ stage: (await response.json()).stage }); // 只输出状态
```

## 已遇到的问题与处理

| 现象 | 原因或证据 | 处理 |
|---|---|---|
| 迅雷给出的 `vertifyPhone.html` 长地址打开后白屏 | 本机遇到；[OpenList 同类报告](https://github.com/OpenListTeam/OpenList/issues/136)也记录直接打开失败 | 使用官方 `android.html` 外壳页和本次 `reviewData` 启动验证，不手工拼接或转发长地址。 |
| 滑块或短信“确定”后页面停留原处 | 本机实测页面未跳转，但开发日志已出现成功回调 | 看 `roErrorCode` 和新 `creditkey`，以 token 及 `xunlei user` 读回为最终依据；不要反复点击或重发短信。 |
| 终端 `xunlei login` 按 Enter 后再次出现“新设备验证” | 旧流程用服务端最初给的 `creditkey` 重试；短信成功回调会生成**新的** key | 退出旧登录进程，改用 `browser_login.py` 一次完成；不要让用户反复输入短信码。上游终端路径会打印一次性验证数据，不作为推荐入口。 |
| 网页云盘已登录，但 CLI 提示未登录 | 两套登录状态独立 | 检查 `token.json` 和 `xunlei user`，失败则走上面的浏览器登录流程；不直接搬运网页 Cookie。 |
| 登录本机页显示 `review`，官方页仍是默认图形验证或没有用户手机号 | 尚未用本轮 `reviewData` 调用 `reviewCb`，或使用了旧数据 | 确认临时服务仍运行、`/review` 属于本轮、官方页已重新加载，再启动一次本轮验证。避免在日志里输出验证数据。 |
| 本机登录页出现 `error` 或 token 未生成 | 登录接口返回失败，或验证回调未被提交 | 先看 `stage` 与是否有成功回调；若账号密码错误，重启临时服务，让用户在新的本机页自行重输。不得据网页状态报成功。 |
| 迁移设备后技能脚本找不到依赖或可执行文件不能运行 | `var/xunlei-cli-venv/` 和 PanSou 二进制与原机平台绑定 | 按 `dependencies.md` 在技能目录内重建 venv / 对应架构的 PanSou；新设备重新登录，不复制旧 token。 |
| PanSou 或搜索引擎无结果、限流 | 单一渠道覆盖不足；`search_errors` 记录失败渠道 | 按 `SKILL.md` 继续网页搜索和浏览器兜底，打开原页核对；不能仅凭脚本无结果说“找不到”。 |
| 资源标称 4K，但容量过小或只有搜索摘要 | 标题、摘要不证明真实分辨率 | 按 `search-and-quality.md` 对照片长与容量，降低置信度；核对来源页、文件信息，必要时退到可信 1080p。 |
| `cinema_cli.py` 显示 `Skipped` | 目标目录已有同名文件，或最近已有同一来源任务 | 先核对已有文件版本和大小；不要为了重复提交而改名绕过检查。 |
| 离线任务已创建，但目录看不到片 | 任务受理不代表完成，或目标目录选错／云端解析失败 | 查任务状态，读回目标目录的文件名、大小和格式；失败记录服务端提示，避免盲目重复提交。 |
| 迅雷分享链接被 `cinema_cli.py` 拒绝 | 分享转存与直接链接离线下载是不同流程 | 按 `save-to-xunlei.md` 用分享页逐文件转存并读回；此路径尚未完成端到端实测。 |
| 磁力、`thunder://`、FTP、BT 种子是否都可用不明 | 当前真实云端实测仅覆盖 HTTPS 直接链接 | 逐类型用合法小样本验证；BT 种子文件不能当普通 URL 交给 `cinema_cli.py`。未实测时明确标注。 |

## 维护边界

- `scripts/browser_login.py` 是当前已实测的独立登录入口。原版 `xunlei login` 会打印一次性验证数据，因此公开版默认不用它处理新设备验证。
- 不把 token 内容、账号密码、短信码、浏览器开发日志原文或一次性验证 URL 写入本文件。token 仍在用户指定的 `~/.config/xunlei-cli/token.json`，权限应为 `0600`。
- 开发机实测证据：CLI 读到正确迅雷 VIP 账号；先前改造版 `cinema-save` 将 MDN 开放样片 `flower.webm` 保存至 `家庭影院/技能验证-开放样片`，任务为 `completed`，目录读回约 541 KB 文件。公开版 `cinema_cli.py` 已完成只读预览，实际提交尚待复测。该结果不证明其他协议或迅雷分享转存已可用。
