# Zampto 自动续期 (GitHub Actions)

使用 GitHub Actions 自动为 [Zampto](https://dash.zampto.net) 免费服务器续期。
Chromium 通过 `NODE_LINK` 指定的 socks5 代理访问，规避 Cloudflare 风控。

## 配置 Secrets

在仓库 `Settings → Secrets and variables → Actions` 添加以下 Secrets：

| Secret 名称 | 是否必填 | 说明 |
|---|---|---|
| `COOKIE_JSON` | ✅ 必填 | 登录 cookies 的完整 JSON（见下方获取方法） |
| `NODE_LINK` | ✅ 必填 | socks5 代理节点，格式 `socks5://user:[REDACTED]@host:port` 或 `socks://user:[REDACTED]@host:port` |
| `EMAIL` | ❌ 可选 | Zampto 登录邮箱 |
| `PASSWORD` | ❌ 可选 | Zampto 登录密码 |
| `TG_BOT_TOKEN` | ❌ 可选 | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ 可选 | Telegram Chat ID |
| `SERVER_ID` | ❌ 可选 | 服务器 ID（默认 9810） |
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
3. 查看运行日志确认结果

> ⚠️ 注意：fork 仓库首次运行 Actions 时，需在 Actions 页点 **"Enable workflow"** 授权（GitHub 对 fork 默认禁用 secrets 工作流）。

## 免责声明

本脚本仅供学习交流使用，使用者需遵守 Zampto 服务条款。
