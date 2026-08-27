#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zampto 自动续期 - GitHub Actions 版
逻辑: 注入 cookies -> 打开服务器页 -> 解析到期时间 -> 剩余<threshold 则点击续期
      -> 等待 Turnstile 自动通过 -> 验证结果 -> Telegram 通知
代理: 直接使用 NODE_LINK (socks5://user:pass@host:port), Chromium 直连
凭证: 全部从环境变量/Secrets 读取, 不进仓库
"""
import json, os, re, sys
from datetime import datetime, timezone
from urllib.parse import urlparse

# ---- 环境变量 (GitHub Secrets) ----
COOKIE_JSON  = os.environ.get('COOKIE_JSON', '')     # 完整 cookies JSON (session.json 内容)
EMAIL        = os.environ.get('EMAIL', '')
PASSWORD     = os.environ.get('PASSWORD', '')
NODE_LINK    = os.environ.get('NODE_LINK', '')       # socks5://user:pass@host:port
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID', '')
SERVER_ID    = os.environ.get('SERVER_ID', '9810')
THRESHOLD_H  = float(os.environ.get('RENEW_THRESHOLD_HOURS', '24'))
FORCE        = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'

BASE = "https://dash.zampto.net"

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)

def send_tg(text):
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        log("ℹ️ 未配置 TG, 跳过通知")
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)
        if r.status_code == 200:
            log("✅ TG 通知已发送")
        else:
            log(f"⚠️ TG 发送失败: HTTP {r.status_code}")
    except Exception as e:
        log(f"⚠️ TG 异常: {e}")

def parse_hours(text):
    """从页面文本解析剩余时间 -> 小时数"""
    m = re.search(r'Expiry \(Next Renewal\):\s*([\d.]+)\s*d(?:\s+(\d+)\s*h)?(?:\s+(\d+)\s*m)?', text)
    if m:
        days = float(m.group(1)); hours = int(m.group(2) or 0); mins = int(m.group(3) or 0)
        return days * 24 + hours + mins / 60.0
    return None

def get_last_renewed(text):
    m = re.search(r'Server last renewed:\s*([A-Za-z]{3}\s*\d{1,2},\s*\d{4},\s*\d{1,2}:\d{2}\s*[AP]M\s*UTC)', text)
    return m.group(1) if m else None

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = {runtime: {}};
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters.name === 'notifications'
    ? Promise.resolve({state: Notification.permission})
    : origQuery(parameters)
);
"""

def main():
    log("🚀 Zampto 自动续期 (GitHub Actions) | Server=%s | 阈值=%.1fh" % (SERVER_ID, THRESHOLD_H))

    # 解析代理
    proxy_server = None
    if NODE_LINK:
        p = urlparse(NODE_LINK)
        if p.scheme.startswith('socks'):
            userinfo = f"{p.username}:{p.password}@" if p.username else ""
            proxy_server = f"socks5://{userinfo}{p.hostname}:{p.port}"
        else:
            log(f"❌ 不支持的 NODE_LINK 协议: {p.scheme}")
            sys.exit(1)
    else:
        log("❌ 未配置 NODE_LINK (socks5 代理)")
        sys.exit(1)
    log(f"🌐 代理: {proxy_server.split('@')[-1] if '@' in proxy_server else proxy_server}")

    # 解析 cookies
    try:
        data = json.loads(COOKIE_JSON)
        cookies = data.get('cookies', data) if isinstance(data, dict) else data
        if not isinstance(cookies, list):
            raise ValueError("cookies 不是列表")
        log(f"🍪 cookies: {len(cookies)} 个")
    except Exception as e:
        log(f"❌ COOKIE_JSON 解析失败: {e}")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    success = False
    result_msg = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                ],
                proxy={"server": proxy_server} if proxy_server else None,
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/Los_Angeles",
            )
            context.add_init_script(STEALTH_JS)
            for c in cookies:
                try:
                    context.add_cookies([{
                        "name": c.get("name"), "value": c.get("value"),
                        "domain": c.get("domain", "dash.zampto.net"),
                        "path": c.get("path", "/"),
                    }])
                except Exception:
                    pass
            page = context.new_page()

            # 打开服务器页
            server_url = f"{BASE}/server?id={SERVER_ID}"
            log(f"📂 打开: {server_url}")
            page.goto(server_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)

            text = page.inner_text("body")
            hours_left = parse_hours(text)
            last_renewed = get_last_renewed(text)
            log(f"📊 页面状态: last renewed={last_renewed or '?'}, 剩余={hours_left if hours_left is not None else '?'}h")

            if hours_left is not None and hours_left > THRESHOLD_H and not FORCE:
                result_msg = f"⏭️ 剩余 {hours_left:.1f}h (>阈值 {THRESHOLD_H}h)，无需续期"
                log(result_msg)
            else:
                # 点击续期
                log("🖱️ 查找并点击续期按钮...")
                clicked = False
                for btn_text in ["Renew Server", "Renew", "Extend"]:
                    try:
                        btn = page.get_by_role("button", name=btn_text).first
                        btn.click(timeout=8000)
                        clicked = True
                        break
                    except Exception:
                        pass
                if not clicked:
                    try:
                        page.locator("text=Renew Server").first.click(timeout=8000)
                        clicked = True
                    except Exception:
                        pass
                if clicked:
                    log("✅ 已点击续期按钮, 等待 Turnstile 自动通过...")
                    # 等待 Turnstile iframe 出现
                    for i in range(6):
                        has_ts = any('challenges.cloudflare.com' in (f.url or '') for f in page.frames)
                        if has_ts:
                            log(f"  ✅ [{i*5}s] Turnstile iframe 出现")
                            break
                        page.wait_for_timeout(5000)
                    # 等待续期生效 (最多 120s)
                    for i in range(12):
                        page.wait_for_timeout(10000)
                        text = page.inner_text("body")
                        renewed = get_last_renewed(text)
                        if renewed and renewed != last_renewed:
                            log(f"  ✅ [{(i+1)*10}s] 检测到续期成功: {renewed}")
                            break
                    # 最终验证
                    text = page.inner_text("body")
                    hours_after = parse_hours(text)
                    renewed_after = get_last_renewed(text)
                    if renewed_after and renewed_after != last_renewed:
                        success = True
                        result_msg = f"✅ 续期成功！Server last renewed: {renewed_after} | 剩余 {hours_after:.1f}h"
                    else:
                        result_msg = f"⚠️ 未确认续期成功 (续期前 {hours_left if hours_left is not None else '?'}h -> 续期后 {hours_after if hours_after is not None else '?'}h)"
                else:
                    result_msg = "❌ 未找到续期按钮"
            try:
                page.screenshot(path="result.png")
            except Exception:
                pass
            browser.close()
    except Exception as e:
        import traceback; traceback.print_exc()
        result_msg = f"❌ 异常: {e}"

    log("")
    log("📨 发送 Telegram 通知...")
    send_tg(f"<b>⚡ Zampto 自动续期 (Server {SERVER_ID})</b>\n{result_msg}")
    log(f"📌 结果: {result_msg}")
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
