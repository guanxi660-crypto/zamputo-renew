# Zampto 自动续期 (未完成！！ 勿用)

使用 GitHub Actions 自动为 [Zampto](https://dash.zampto.net) 免费服务器续期。
引擎使用 **Camoufox**（隐身 Firefox，humanize / disable_coop / geoip 抗风控），
在 xvfb 虚拟显示下运行，通过 `NODE_LINK` 指定的 socks5 代理（干净节点）访问，规避 Cloudflare 风控。
逻辑与本机 cron 脚本 `zampto-renew-camoufox.py` 保持一致（自然关闭广告弹窗 + 真实鼠标点击 + 先点 Turnstile 复选框再轮询）。

## 配置 Secrets

在仓库 `Settings → Secrets and variables → Actions` 添加以下 Secrets：

| Secret 名称 | 是否必填 | 说明 |
|---|---|---|
| `COOKIE_JSON` | ✅ 必填 | 登录 cookies 的完整 JSON（见下方获取方法） |
| `NODE_LINK` | ✅ 必填 | socks5 代理节点，格式 `socks5://user:pass@host:port` 或 `socks5://host:port` |
| `SERVER_ID` | ❌ 可选 | 服务器 ID（默认 9810） |
| `TG_BOT_TOKEN` | ❌ 可选 | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ 可选 | Telegram Chat ID |
| `RENEW_THRESHOLD_HOURS` | ❌ 可选 | 剩余低于该小时数才续期（默认 24） |
| `FORCE_RENEW` | ❌ 可选 | 设为 `true` 强制续期 |

## COOKIE_JSON 获取

1. 登录 `https://dash.zampto.net`（需使用与 `NODE_LINK` 相同的代理）
2. 浏览器 DevTools (F12) → Application → Cookies
3. 用 Cookie-Editor 类扩展导出全部 cookies 为 JSON
4. 将整个 JSON 填入 `COOKIE_JSON` Secret

（脚本兼容 `{"cookies": [...]}` 或裸 `[...]` 两种格式）

## 使用

1. 在仓库 Secrets 中配置必填项
2. Actions 页手动触发 `workflow_dispatch`，或等待每日 00:40 UTC 自动执行
3. 查看运行日志确认结果；失败时可在 Artifact `zampto-screenshots` 下载调试截图

> ⚠️ 注意：fork 仓库首次运行 Actions 时，需在 Actions 页点 **"Enable workflow"** 授权（GitHub 对 fork 默认禁用 secrets 工作流）。

## 免责声明

本脚本仅供学习交流使用，使用者需遵守 Zampto 服务条款。
