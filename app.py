#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zampto 自动续期 - GitHub Actions 版 (Camoufox 引擎, 对齐本机 cron 逻辑)
逻辑: 注入 cookies -> 打开服务器页 -> 解析下次到期时间 ->
     剩余 < 阈值则点击 Renew Server (真实鼠标) -> 等待 Turnstile 自动通过 ->
     刷新验证续期结果 -> Telegram 通知
浏览器: Camoufox (隐身 Firefox, humanize/disable_coop/geoip), 通过 NODE_LINK socks5 代理
凭证: 全部从环境变量/Secrets 读取, 不进仓库
"""
import json, os, re, sys, time
from datetime import datetime, timezone

# ---- 环境变量 (GitHub Secrets) ----
COOKIE_JSON  = os.environ.get('COOKIE_JSON', '')      # 完整 cookies JSON (session.json 内容)
NODE_LINK    = os.environ.get('NODE_LINK', '')        # socks5://user:pass@host:port (干净节点)
SERVER_ID    = os.environ.get('SERVER_ID', '9810')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID', '')
RENEW_THRESHOLD_H = float(os.environ.get('RENEW_THRESHOLD_HOURS', '24'))
FORCE        = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'
LOG_FILE     = os.environ.get('LOG_FILE', 'zampto-camoufox.log')

BASE = "https://dash.zampto.net"

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def send_tg(msg):
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        log(f"TG fail: {e}")

def goto_retry(page, url, retries=3, wait="domcontentloaded", timeout=45000, gap=15):
    """page.goto 自动重试: 卡死/超时后等 gap 秒重跑, 避免单次卡死直接失败"""
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until=wait, timeout=timeout)
            return
        except Exception as e:
            err = str(e).split(chr(10))[0]
            if attempt < retries:
                log(f"  ⚠️ goto 第{attempt}次超时: {err} | {gap}s 后重试 ({attempt+1}/{retries})")
                time.sleep(gap)
            else:
                log(f"  ❌ goto 已重试{retries}次仍失败: {err}")
                raise

def parse_expiry(text):
    """从页面文本解析 'Expiry (Next Renewal): 1d 19h 36m' -> 小时数"""
    m = re.search(r'Expiry \(Next Renewal\):\s*([\d.]+)\s*d(?:\s+(\d+)\s*h)?(?:\s+(\d+)\s*m)?', text)
    if m:
        days = float(m.group(1)); hours = int(m.group(2) or 0); mins = int(m.group(3) or 0)
        return days * 24 + hours + mins / 60.0
    return None

def get_last_renewed(text):
    m = re.search(r'Server last renewed:\s*([A-Za-z]{3}\s*\d{1,2},\s*\d{4},\s*\d{1,2}:\d{2}\s*[AP]M\s*UTC)', text)
    return m.group(1) if m else None

def close_overlay_naturally(page):
    """自然关闭广告弹窗: 优先点击关闭按钮, 否则等待自动消失 (不修改 DOM 结构)"""
    for attempt in range(3):
        has = page.evaluate("() => !!document.getElementById('adblocker-overlay')")
        if not has:
            log("  ✓ 无广告弹窗遮挡")
            return True
        # 1) 找 overlay 内的关闭类按钮自然点击
        clicked = False
        try:
            els = page.locator('#adblocker-overlay button, #adblocker-overlay a, #adblocker-overlay [role="button"], #adblocker-overlay svg')
            for i in range(els.count()):
                el = els.nth(i)
                try:
                    txt = (el.inner_text() or '').strip().lower()
                except Exception:
                    txt = ''
                aria = (el.get_attribute('aria-label') or '').lower()
                cls = (el.get_attribute('class') or '').lower()
                if any(k in txt or k in aria or k in cls for k in ['close','✕','×','dismiss','skip','continue','got it','ok']):
                    el.click()  # 自然点击
                    log("  🖱️ 自然点击广告弹窗关闭按钮: " + (txt or aria or cls)[:40])
                    clicked = True
                    break
        except Exception:
            pass
        if clicked:
            page.wait_for_timeout(2500)
            continue
        # 2) 无关闭按钮 -> 等待自然消失 (最多 12s, 广告加载完成)
        for i in range(12):
            page.wait_for_timeout(1000)
            if not page.evaluate("() => !!document.getElementById('adblocker-overlay')"):
                log("  ✓ 广告弹窗自然消失 (等待 " + str(i+1) + "s)")
                return True
        if attempt < 2:
            log("  ⏳ 广告弹窗仍在, 等待广告加载 (第" + str(attempt+1) + "轮)...")
            page.wait_for_timeout(3000)
    return False

def load_cookies():
    if not COOKIE_JSON:
        raise RuntimeError("未配置 COOKIE_JSON")
    data = json.loads(COOKIE_JSON)
    if isinstance(data, dict) and 'cookies' in data:
        return data['cookies']
    if isinstance(data, list):
        return data
    raise RuntimeError("COOKIE_JSON 格式无法识别 (需为 cookies 数组或 {'cookies': [...]} )")

# ---------------- main ----------------
log("=" * 60)
log(f"🚀 Zampto 全自动续期 (Camoufox) | Server={SERVER_ID} | 阈值={RENEW_THRESHOLD_H}h" + (" | FORCE" if FORCE else ""))
log("=" * 60)

cookies = load_cookies()
log(f"🍪 cookies: {len(cookies)} 个")

from camoufox.sync_api import Camoufox
from camoufox import DefaultAddons

success = False
result_msg = ""

# 代理: 直接使用 NODE_LINK (socks5 干净节点)
if not NODE_LINK:
    log("❌ 未配置 NODE_LINK (socks5 代理)")
    send_tg(f"<b>⚡ Zampto 自动续期 (Server {SERVER_ID})</b>\n❌ 未配置 NODE_LINK (socks5 代理)")
    sys.exit(1)

with Camoufox(headless=False, humanize=True, os="windows", disable_coop=True,
              proxy={"server": NODE_LINK}, geoip=True, enable_cache=False,
              exclude_addons=[DefaultAddons.UBO],  # 不加载 uBlock Origin, 让广告正常显示
              i_know_what_im_doing=True) as browser:
    try:
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})

        # 1. 访问站点 + 注入 cookies
        log("🌐 访问 Zampto (通过 socks5 代理)...")
        goto_retry(page, BASE + "/")
        page.wait_for_timeout(3000)
        for c in cookies:
            try:
                domain = c.get("domain") or ".zampto.net"
                context.add_cookies([{"name": c["name"], "value": c["value"],
                                      "domain": domain, "path": c.get("path", "/"),
                                      "secure": c.get("secure", True),
                                      "httpOnly": c.get("httpOnly", False),
                                      "sameSite": "Lax"}])
            except Exception as e:
                log(f"   cookie '{c.get('name')}' fail: {e}")
        goto_retry(page, BASE + "/")
        page.wait_for_timeout(4000)

        # 验证出口 IP + 登录状态
        try:
            ip = page.evaluate("() => fetch('https://api.ipify.org').then(r => r.text()).catch(e => 'err')")
        except Exception:
            ip = 'n/a'
        log(f"🌍 浏览器出口 IP: {ip}")
        if "login" in page.url.lower() or "Sign in" in (page.inner_text("body")[:2000] or ""):
            log("⚠️ 疑似未登录! URL=" + page.url)
            page.screenshot(path="zampto-login-fail.png")

        # 2. 打开服务器详情页
        server_url = f"{BASE}/server?id={SERVER_ID}"
        log(f"📂 打开服务器页: {server_url}")
        goto_retry(page, server_url)
        page.wait_for_timeout(6000)
        text = page.inner_text("body")

        # 3. 解析到期时间
        hours_left = parse_expiry(text)
        last_renewed_before = get_last_renewed(text)
        log(f"📊 页面状态: last renewed={last_renewed_before}, 剩余={hours_left:.1f}h" if hours_left is not None else "📊 未解析到到期时间")

        # 4. 判断是否续期
        if hours_left is not None and hours_left > RENEW_THRESHOLD_H and not FORCE:
            result_msg = f"⏭️ 剩余 {hours_left:.1f}h (>{RENEW_THRESHOLD_H:.0f}h 阈值)，无需续期"
            log("ℹ️ " + result_msg)
            page.screenshot(path="zampto-skip.png")
        else:
            # 5. 自然关闭广告弹窗 + 点击 Renew Server (不修改 DOM)
            log("🖱️ 点击 'Renew Server' (先自然处理广告弹窗)...")
            clicked_ok = False
            for attempt in range(3):
                # 检查并自然关闭 overlay
                if not close_overlay_naturally(page):
                    log("  ⚠️ 广告弹窗无法自然关闭 (尝试 " + str(attempt+1) + "/3)")
                    page.wait_for_timeout(5000)
                    continue
                try:
                    # 滚动按钮到视口中央 (避开右下角浮动广告)
                    page.evaluate("""() => {
                        const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').includes('Renew Server'));
                        if (btn) { btn.scrollIntoView({block:'center'}); return true; }
                        return false;
                    }""")
                    page.wait_for_timeout(1500)
                    # 真实鼠标点击按钮中心 (React 需要真实 pointer 事件)
                    r = page.evaluate("""() => {
                        const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').includes('Renew Server'));
                        const rect = btn.getBoundingClientRect();
                        return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                    }""")
                    page.mouse.click(r['x'], r['y'])
                    clicked_ok = True
                    log(f"  🖱️ 真实鼠标点击 Renew Server 按钮 (坐标 {int(r['x'])},{int(r['y'])})")
                    break
                except Exception as e:
                    msg = str(e)
                    log("  ⚠️ 鼠标点击异常: " + msg[:120])
                    try:
                        btn = page.get_by_role("button", name="Renew Server").first
                        btn.click(force=True)
                        clicked_ok = True
                        break
                    except Exception as e2:
                        log("  ❌ 点击失败: " + str(e2)[:120])
                        break
            if not clicked_ok:
                log("  ❌ 无法点击 Renew 按钮 - 标记为手动处理")
                page.screenshot(path="zampto-click-fail.png")
                result_msg = "⚠️ 广告弹窗遮挡无法自动续期，请手动打开 Dashboard 续期"
            else:
                log("✅ 已点击，等待 Turnstile 自动通过...")

            # 6. 等待 Turnstile (真实鼠标点击后 api.js 已加载)
            turnstile_ok = False
            if not clicked_ok:
                context.close()
                sys.exit(1)
            # 6.1 等待 Turnstile iframe 出现
            ts_frame = None
            for i in range(25):
                for f in page.frames:
                    if 'challenges.cloudflare.com' in f.url and f != page.main_frame:
                        ts_frame = f
                        break
                if ts_frame:
                    log(f"  ✅ [{i}s] Turnstile iframe 出现: {ts_frame.url[:90]}")
                    break
                page.wait_for_timeout(1000)
            # 6.2 点击 checkbox (managed 模式)
            if ts_frame:
                try:
                    cb = ts_frame.locator('input[type="checkbox"]')
                    if cb.count() > 0:
                        cb.first.click(force=True)
                        log("  🖱️ 点击 Turnstile 复选框")
                    else:
                        log("  Turnstile iframe 内无 checkbox (可能是 non-interactive 模式)")
                except Exception as e:
                    log("  checkbox err: " + str(e)[:80])
            # 6.3 轮询 token (最长 120s, 导航容错)
            for i in range(120):
                time.sleep(1)
                try:
                    token = page.evaluate("""() => {
                        const inp = document.querySelector('input[name="cf-turnstile-response"]');
                        return inp ? inp.value : null;
                    }""")
                except Exception as nav_err:
                    # 页面导航 = 续期可能已成功刷新
                    log(f"  ↻ [{i}s] 页面导航 (续期成功?): {str(nav_err)[:60]}")
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    try:
                        token = page.evaluate("""() => {
                            const inp = document.querySelector('input[name="cf-turnstile-response"]');
                            return inp ? inp.value : null;
                        }""")
                    except Exception:
                        token = None
                if token and len(token) > 10:
                    log(f"  ✅ [{i}s] Turnstile 通过!")
                    turnstile_ok = True
                    break
                # 导航后等页面稳定再继续
                page.wait_for_timeout(3000)
                if i % 15 == 0:
                    log(f"  ⏳ [{i}s] 等待 token... iframe={'有' if ts_frame else '无'}")
            if not turnstile_ok:
                log("  ⚠️ Turnstile 120s 未通过")
                page.screenshot(path="zampto-turnstile-fail.png")

            page.wait_for_timeout(4000)

            # 7. 刷新页面验证续期结果
            log("🔄 刷新验证...")
            try:
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
            except Exception as e:
                log("  reload err: " + str(e)[:80])
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
            text = page.inner_text("body")
            last_renewed_after = get_last_renewed(text)
            hours_after = parse_expiry(text)
            log(f"📊 续期后: last renewed={last_renewed_after}, 剩余={hours_after:.1f}h" if hours_after is not None else "📊 续期后未解析到时间")

            if last_renewed_before and last_renewed_after and last_renewed_after != last_renewed_before:
                success = True
                result_msg = f"✅ 续期成功！<b>Server last renewed: {last_renewed_after}</b> | 下次到期: {hours_after:.1f}h"
            elif hours_after is not None and hours_left is not None and hours_after >= hours_left + 12:
                success = True
                result_msg = f"✅ 续期成功！剩余时间 {hours_left:.1f}h -> {hours_after:.1f}h"
            else:
                result_msg = f"⚠️ 未确认续期成功 (续期前 {hours_left if hours_left else '?'}h -> 续期后 {hours_after if hours_after else '?'}h)"
                page.screenshot(path="zampto-renew-fail.png")

            try:
                page.screenshot(path="zampto-renew-result.png")
            except Exception:
                pass

        context.close()
    except Exception as e:
        import traceback; traceback.print_exc()
        result_msg = f"❌ 异常: {e}"

log("\n📨 发送 Telegram 通知...")
send_tg(f"<b>⚡ Zampto 自动续期 (Server {SERVER_ID})</b>\n{result_msg}")
log(f"📌 结果: {result_msg}")
sys.exit(0 if success else 1)
