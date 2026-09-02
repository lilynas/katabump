#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import subprocess
import requests
import re
from datetime import datetime
from seleniumbase import SB

# 从环境变量获取账号密码和 TG 配置
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""        # tg通知 chat id(可选)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""      # tg通知bot token(可选)

BASE_URL = "https://dashboard.katabump.com"  # 网站链接

# 多账号来源：USERS_JSON 格式 [{"username":"email","password":"pwd"}, ...]
def load_accounts():
    raw = os.environ.get("USERS_JSON", "")
    if not raw:
        # 兼容单账号 env（KATABUMP_EMAIL/KATABUMP_PASSWORD）
        email = os.environ.get("KATABUMP_EMAIL", "")
        pwd   = os.environ.get("KATABUMP_PASSWORD", "")
        if email:
            return [{"email": email, "password": pwd}]
        print("❌ 未配置 USERS_JSON 或 KATABUMP_EMAIL/KATABUMP_PASSWORD")
        return []
    try:
        users = json.loads(raw)
        accounts = []
        for u in users:
            accounts.append({
                "email": u.get("username") or u.get("email") or "",
                "password": u.get("password") or "",
            })
        return [a for a in accounts if a["email"]]
    except Exception as e:
        print(f"❌ USERS_JSON 解析失败: {e}")
        return []

ACCOUNTS = load_accounts()
CURRENT_EMAIL = ""  # 当前正在处理的账号，供 send_tg_message 脱敏

#  Telegram 推送模块
def send_tg_message(status_icon, status_text, time_left=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
        return

    # 获取北京时间 (UTC+8)
    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    # 邮箱脱敏：保留用户名前2位和后2位，中间用****代替
    email = CURRENT_EMAIL
    if '@' in email:
        name, domain = email.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = (email[:2] + '****') if email else "未知"

    # time_left 实际承载面板 alert / 失败详情（历史参数名保留）
    detail = (time_left or "").strip()
    text = (
        f"🇫🇷 katabump 续期通知\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 续期账户: {masked_email}\n"
        f"⏱️ 续期时间: {current_time_str}"
    )
    if detail:
        text += f"\n📋 详情: {detail}"

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("📩 Telegram 通知发送成功！")
        else:
            print(f"⚠️ Telegram 通知发送失败: {r.text}")
    except Exception as e:
        print(f"⚠️ Telegram 通知发送异常: {e}")

#  页面注入脚本
_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

# 是否有已渲染（可见）的 Turnstile iframe（是否为真正的交互式验证框）
# 实测：CF 非交互/auto 模式下，.cf-turnstile 内的 iframe 常以“空 src + 1x1”占位出现（src=""，w=1,h=1），
# 并非总是 challenges.cloudflare.com 的 URL。若只按 src 判断会把真实的 1x1 iframe 漏掉，导致 uc 点击永远不触发。
_TURNSTILE_IFRAME_JS = """
(function(){
    var frames = document.querySelectorAll('iframe');
    for (var i=0;i<frames.length;i++){
        var f=frames[i]; var src=f.src||'';
        if (src.indexOf('challenges.cloudflare.com')>-1 || src.indexOf('/turnstile/')>-1){
            var r=f.getBoundingClientRect();
            if (r.width>0 && r.height>0) return true;
        }
    }
    // 兜底：.cf-turnstile 容器内部的 iframe（含空 src 的 1x1 可见占位）即可视为“已渲染”
    var q=document.querySelector('[class*="cf-turnstile"] iframe, [id*="turnstile"] iframe, [class*="turnstile"] iframe');
    if (q) {
        var qr=q.getBoundingClientRect();
        if (qr.width>0 && qr.height>0) return true;
    }
    return false;
})()
"""

_WININFO_JS = """
(function(){
    return {
        sx: window.screenX || 0,
        sy: window.screenY || 0,
        oh: window.outerHeight,
        ih: window.innerHeight
    };
})()
"""

# Turnstile 复选框 iframe 的可见包围盒（用于 xdotool 物理点击）
_TURNSTILE_BBOX_JS = """
(function(){
    function expand(f){
        f.style.width='300px'; f.style.height='80px';
        f.style.minWidth='300px'; f.style.minHeight='80px';
        f.style.visibility='visible'; f.style.opacity='1';
        f.style.zIndex='9999';
        var p=f.parentElement, guard=0;
        while(p && guard<14){ p.style.overflow='visible'; p=p.parentElement; guard++; }
        var r=f.getBoundingClientRect();
        return { x: Math.round(r.left), y: Math.round(r.top),
                 w: Math.round(r.width), h: Math.round(r.height) };
    }
    if (!window.frames) return null;
    var frames = document.querySelectorAll('iframe');
    for (var i=0;i<frames.length;i++){
        var f=frames[i]; var src=f.src||'';
        if (src.indexOf('challenges.cloudflare.com')>-1 || src.indexOf('/turnstile/')>-1){
            var r=f.getBoundingClientRect();
            if (r.width>0 && r.height>0) return expand(f);
        }
    }
    // 兜底：Turnstile 组件容器内部的 iframe（含空 src 的 1x1 占位也要点，沿住历史可过写法）
    var q = document.querySelector(
        '[class*="cf-turnstile"] iframe, [id*="turnstile"] iframe, '+
        '[class*="turnstile"] iframe, .cf-turnstile-wrapper iframe'
    );
    if (q) {
        var qr = q.getBoundingClientRect();
        if (qr.width>0 || qr.height>0) return expand(q);
    }
    return null;
})()
"""

# 页面所有 iframe 的 src + 矩形（诊断用）
_IFRAME_MAP_JS = """
(function(){
    var out=[];
    var frames=document.querySelectorAll('iframe');
    for (var i=0;i<frames.length;i++){
        var f=frames[i], r=f.getBoundingClientRect();
        out.push({ src:(f.src||'').slice(0,80),
                   x:Math.round(r.left), y:Math.round(r.top),
                   w:Math.round(r.width), h:Math.round(r.height) });
    }
    return JSON.stringify(out);
})()
"""

# ===== 自动续期相关 =====

# 在模态框内查找 iframe 并展开，返回点击坐标
_ALTCHA_EXPAND_JS = """
(function() {
    var modal = document.querySelector('div.modal.show') || document;
    var iframes = modal.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var r = iframes[i].getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            iframes[i].style.width  = '300px';
            iframes[i].style.height = '150px';
            iframes[i].style.minWidth  = '300px';
            iframes[i].style.minHeight = '150px';
            iframes[i].style.visibility = 'visible';
            iframes[i].style.opacity = '1';
            var el = iframes[i];
            for (var j = 0; j < 10; j++) {
                el = el.parentElement;
                if (!el) break;
                el.style.overflow = 'visible';
            }
            var r2 = iframes[i].getBoundingClientRect();
            return { cx: Math.round(r2.x + 30), cy: Math.round(r2.y + r2.height / 2) };
        }
    }
    return null;
})()
"""

# 检测 ALTCHA 是否已验证通过
_ALTCHA_SOLVED_JS = """
(function(){
    var modal = document.querySelector('div.modal.show') || document;
    // hidden input 有值
    var inputs = modal.querySelectorAll('input[type="hidden"]');
    for (var i = 0; i < inputs.length; i++) {
        var n = (inputs[i].name || '').toLowerCase();
        if ((n.includes('altcha') || n.includes('captcha')) &&
            inputs[i].value && inputs[i].value.length > 20) return true;
    }
    // checkbox 变为 disabled
    var cbs = modal.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < cbs.length; j++) {
        if (cbs[j].disabled) return true;
    }
    // widget data-state 属性
    var w = modal.querySelector('[data-state="verified"],.altcha--verified,.altcha-verified');
    if (w) return true;
    return false;
})()
"""

#  底层输入工具
def js_fill_input(sb, selector: str, text: str):
    safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
    sb.execute_script(f"""
    (function(){{
        var el = document.querySelector('{selector}');
        if (!el) return;
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        if (nativeInputValueSetter) {{
            nativeInputValueSetter.call(el, "{safe_text}");
        }} else {{
            el.value = "{safe_text}";
        }}
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()
    """)

def _activate_window():
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _xdotool_click(x: int, y: int):
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")


def _restart_proxy():
    """重启 sing-box，让 urltest 重新探测，可能选中池子里另一个节点。

    仅在 GitHub Actions 环境生效（本地无 sing-box 可执行文件则跳过）。
    """
    if not os.path.exists("sing-box"):
        print("  （本环境无 sing-box 可执行文件，跳过代理节点切换）")
        return
    print("\n🔄 重启 sing-box 以切换代理节点...")
    subprocess.run(["pkill", "-9", "-f", "sing-box"], capture_output=True)
    time.sleep(2)
    log = open("singbox.log", "ab")
    try:
        subprocess.Popen(
            ["./sing-box", "run", "-c", "config.json"],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    finally:
        log.close()
    # 等待 urltest 组完成第一轮探测
    time.sleep(26)
    try:
        with open("singbox.log", "rb") as f:
            lines = f.read().decode("utf-8", "ignore").splitlines()
        shown = 0
        for ln in lines[-40:]:
            if ("urltest" in ln or "selected" in ln or "node-" in ln) and shown < 5:
                print("   sing-box:", ln.strip())
                shown += 1
    except Exception:
        pass

# Turnstile 复选框常驻于 shadow DOM（attachShadow 创建），顶部页无 iframe 时普通选择器看不到。
# 借鉴 XCQ0607/katabump 的思路：注入 attachShadow 钩子截获 checkbox 的视口内比例，
# 再用 CDP 原生鼠标事件在绝对坐标点击。
_TURNSTILE_HOOK_JS = r"""
(function(){
  // 只在 iframe 内执行由 Playwright addInitScript 注入；此处我们不区分，主页面 shadow 也找
  var SLOTS = '__turnstile_hook_ready';
  try {
    if (window[SLOTS]) return;           // 避免重复 hook
    window[SLOTS] = true;
    var hookShadow = (function(){
      var orig = Element.prototype.attachShadow;
      Element.prototype.attachShadow = function(init){
        var root = orig.call(this, init);
        var report = function(){
          var cb = root.querySelector('input[type="checkbox"]');
          if (cb){
            var r = cb.getBoundingClientRect();
            if (r.width>0 && r.height>0 && window.innerWidth>0 && window.innerHeight>0){
              window.__turnstile_data = {
                xRatio:(r.left + r.width/2)/window.innerWidth,
                yRatio:(r.top + r.height/2)/window.innerHeight,
                w:r.width, h:r.height
              };
              return true;
            }
          }
          return false;
        };
        if(!report()){
          var mo = new MutationObserver(function(){ if(report()) mo.disconnect(); });
          mo.observe(root,{childList:true,subtree:true});
        }
        return root;
      };
    })();
  } catch(e){ return false; }
  return true;
})()
"""


def _cdp(sb, cmd, params):
    """稳健执行 CDP 命令：兼容 sb.driver.execute_cdp_cmd / sb.execute_cdp_cmd。
    底层 Command 路径：ChromeDriver 的 SEND_COMMAND_TO_CDP。"""
    d = sb.driver if hasattr(sb, "driver") else sb
    if hasattr(d, "execute_cdp_cmd"):
        return d.execute_cdp_cmd(cmd, params)
    if hasattr(sb, "execute_cdp_cmd"):
        return sb.execute_cdp_cmd(cmd, params)
    # 兜底：undecored ChromeDriver 的 execute(cmd, {"cmd":.., "params":..})
    try:
        return d.execute("SEND_COMMAND_TO_CDP", {"cmd": cmd, "params": params})
    except Exception:
        return d.command_executor.execute(d._commands["SEND_COMMAND_TO_CDP"],
                                          {"cmd": cmd, "params": params})


def _install_turnstile_hook_cdp(sb):
    """通过 CDP 在每个新 document 上注入 attachShadow 钩子（等价 addInitScript）。
    须在页面导航前调用（即 uc_open_with_reconnect 之前）。"""
    try:
        _cdp(sb, "Page.addScriptToEvaluateOnNewDocument",
              {"source": _TURNSTILE_HOOK_JS})
        # 若已加载的页面也补打一次
        try:
            sb.driver.execute_script(_TURNSTILE_HOOK_JS)
        except Exception:
            pass
        print("  ✅ 已注入 Turnstile attachShadow CDP 钩子")
        return True
    except Exception as e:
        print(f"  ⚠️ 无法注入 CDP 钩子（将退回到原有策略）: {e}")
        return False


def _cdp_turnstile_click(sb):
    """在“主页面”与“各 frame”里查找 __turnstile_data，随后用 CDP 原生鼠标点击复选框。
    返回是否已发起一次原生点击（调用方自行探测 solved）。"""
    data = None
    try:
        data = sb.driver.execute_script("return window.__turnstile_data || null")
    except Exception:
        data = None
    # 若主 frame 未有，挨个切到子 frame 找（防跨可以选择分支）
    if not data:
        try:
            for frame in sb.driver.find_elements("xpath", "//iframe"):
                try:
                    sb.driver.switch_to.frame(frame)
                    data = sb.driver.execute_script("return window.__turnstile_data || null")
                    if data:
                        break
                finally:
                    sb.driver.switch_to.default_content()
                data = None
        except Exception:
            data = None
    if not data:
        return False
    xr = data.get("xRatio"); yr = data.get("yRatio")
    if xr is None or yr is None:
        return False
    try:
        w = sb.driver.execute_script("return window.innerWidth")
        h = sb.driver.execute_script("return window.innerHeight")
    except Exception:
        return False
    if not w or not h:
        return False
    # 若在子 frame 中读得比例，视口尺寸需用该 frame 的（上面切换回归 default 后已丢），此处以主尺寸近似
    try:
        w = sb.driver.execute_script("return window.innerWidth")
        h = sb.driver.execute_script("return window.innerHeight")
    except Exception:
        return False
    if not w or not h:
        return False
    cx = int(xr * w); cy = int(yr * h)
    print(f"🖱️ [CDP] 原生点击 Turnstile 复选框 ({cx},{cy})")
    try:
        _cdp(sb, "Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": cx, "y": cy,
              "button": "left", "clickCount": 1})
        import random
        time.sleep(0.05 + random.random() * 0.08)
        _cdp(sb, "Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": cx, "y": cy,
              "button": "left", "clickCount": 1})
        return True
    except Exception as e:
        print(f"  ⚠️ [CDP] 点击异常: {e}")
        return False


def _switch_to_turnstile_frame(sb):
    """切入页面上的 Turnstile iframe，返回是否成功。"""
    try:
        el = sb.driver.execute_script("""
        (function(){
            var frames = document.querySelectorAll('iframe');
            for (var i = 0; i < frames.length; i++){
                var f = frames[i], s = f.src || '';
                if (s.indexOf('challenges.cloudflare.com') > -1 ||
                    s.indexOf('turnstile') > -1) return f;
            }
            var q = document.querySelector(
                '[class*="cf-turnstile"], [id*="turnstile"]');
            if (q){ var qf = q.querySelector('iframe'); if (qf) return qf; }
            return null;
        })()
        """)
        if el is None:
            return False
        sb.driver.switch_to.frame(el)
        return True
    except Exception:
        return False


#  人机验证处理（多策略：CDP 原生点击 shadow 复选框 → SeleniumBase UC → xdotool → iframe 内 JS）
def handle_turnstile(sb) -> bool:
    print("🔍 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)

    # 整体时限：避免策略全部跑完拖到 WebDriver 会话超时导致连接被重置
    deadline = time.time() + 55

    def _solved():
        try:
            return bool(sb.execute_script(_SOLVED_JS))
        except Exception:
            return False

    # 检查是否已静默通过
    if _solved():
        print("✅ 已静默通过")
        return True

    # ── 策略 D（CDP 原生点击 shadow DOM 内复选框，快速简试，不吞噬预算）──
    # 顶部无 iframe 时普通选择器看不到，靠 _TURNSTILE_HOOK_JS 钩子写 __turnstile_data。
    # 仅尝试前面最多 2 次；非交互模式下钩子通常拿不到 checkbox，不应空等。
    for _ in range(2):
        if _solved():
            print("✅ Turnstile 通过（CDP 前缀）")
            return True
        maybe = _cdp_turnstile_click(sb)
        if maybe:
            for _ in range(3):
                if time.time() > deadline:
                    break
                if _solved():
                    print("✅ Turnstile 通过（CDP 原生点击）")
                    return True
                time.sleep(0.4)
        else:
            time.sleep(0.5)
        if time.time() > deadline:
            break

    # 关键：cf-turnstile-response 占位元素可能先出现，真正的交互 iframe 后异步加载。
    # 用修复后的 predicate（识别 .cf-turnstile 内空 src 的 1x1 占位 iframe），正常应 1-2s 内 break；
    # 即便不 break，也最多等 8s，避免像以前耗尽整个 55s 预算导致策略 A 永远轮不到。
    wait_for_iframe = 0
    while time.time() < deadline and wait_for_iframe < 8:
        try:
            has = bool(sb.execute_script(_TURNSTILE_IFRAME_JS))
        except Exception:
            has = False
        if has:
            break
        wait_for_iframe += 1
        if wait_for_iframe % 3 == 1:
            print(f"  ⏳ 等待 Turnstile iframe 渲染... ({wait_for_iframe}s)")
        time.sleep(1)
    if not has:
        print(f"  ⚠️ {wait_for_iframe}s 后仍无 Turnstile iframe，改用容器兜底策略")

    # 记录页面 iframe 布局（诊断用）
    try:
        fm = sb.execute_script(_IFRAME_MAP_JS)
        print(f"  📄 页面 iframe: {fm}")
    except Exception:
        pass

    # 展开 (防止 overflow:hidden 裁剪)
    for _ in range(3):
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.5)

    # ── 策略 A：SeleniumBase UC 内置 GUI 点击 ──
    for attempt in range(4):
        if time.time() > deadline:
            print("⏰ Turnstile 超过 55s 时限，提前结束")
            return False
        if _solved():
            print(f"✅ Turnstile 通过（A 第 {attempt + 1} 次）")
            return True
        print(f"🖱️ [A] 第 {attempt + 1}/4 次调用 uc_gui_click_captcha...")
        try:
            if attempt < 2:
                sb.uc_gui_click_captcha()
            else:
                sb.uc_gui_click_cf(frame="iframe", retry=True, blind=True)
        except Exception as e:
            print(f"⚠️ [A] 调用异常: {e}")
        solved = False
        for _ in range(8):
            if _solved():
                solved = True
                break
            time.sleep(0.5)
        if solved:
            print(f"✅ Turnstile 通过（A 第 {attempt + 1} 次）")
            return True

    # ── 策略 B：xdotool 物理点击复选框坐标 ──
    for attempt in range(4):
        if time.time() > deadline:
            print("⏰ Turnstile 超过 55s 超时，提前结束")
            return False
        if _solved():
            print("✅ Turnstile 通过（B 前缀检查）")
            return True
        bbox = None
        try:
            bbox = sb.execute_script(_TURNSTILE_BBOX_JS)
        except Exception:
            bbox = None
        if not bbox:
            print("⚠️ [B] 未定位到 Turnstile iframe，稍等重试...")
            time.sleep(2)
            continue
        try:
            wi = sb.execute_script(_WININFO_JS)
        except Exception:
            wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
        bar = wi.get("oh", 800) - wi.get("ih", 768)
        cx = bbox["x"] + wi.get("sx", 0) + 30
        cy = bbox["y"] + wi.get("sy", 0) + bar + max(28, int(bbox["h"]) // 2)
        print(f"🖱️ [B] xdotool 点击复选框 ({cx}, {cy})  bbox={bbox}")
        _xdotool_click(cx, cy)
        solved = False
        for _ in range(8):
            if time.time() > deadline:
                break
            if _solved():
                solved = True
                break
            time.sleep(0.5)
        if solved:
            print(f"✅ Turnstile 通过（B 第 {attempt + 1} 次）")
            return True
        print(f"  ⚠️ [B] 第 {attempt + 1} 次未通过")

    # ── 策略 C：切入 iframe 直接点击复选框元素 ──
    for attempt in range(3):
        if time.time() > deadline:
            print("⏰ Turnstile 超时，提前结束")
            return False
        if _solved():
            print("✅ Turnstile 通过（C 前缀检查）")
            return True
        print(f"🖱️ [C] 第 {attempt + 1}/3 切入 iframe 尝试...")
        if not _switch_to_turnstile_frame(sb):
            print("  ⚠️ [C] 未找到 Turnstile iframe")
            sb.driver.switch_to.default_content()
            time.sleep(2)
            continue
        try:
            cb = sb.driver.execute_script("""
            (function(){
                var cands = document.querySelectorAll(
                    '[role="checkbox"], input[type="checkbox"],'+
                    '[class*="checkbox"], [class*="btn-check"]'
                );
                for (var i = 0; i < cands.length; i++){
                    var e = cands[i]; var r = e.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return e;
                }
                return null;
            })()
            """)
            if cb is not None:
                sb.driver.execute_script("arguments[0].focus(); arguments[0].click();", cb)
                print("    [C] 已 click 复选框元素")
            else:
                sb.driver.switch_to.active_element.send_keys(" ")
                print("    [C] 未找到复选框元素，发送空格键")
        except Exception as e:
            print(f"    ⚠️ [C] 异常: {e}")
        finally:
            sb.driver.switch_to.default_content()
        solved = False
        for _ in range(6):
            if time.time() > deadline:
                break
            if _solved():
                solved = True
                break
            time.sleep(1)
        if solved:
            print(f"✅ Turnstile 通过（C 第 {attempt + 1} 次）")
            return True

    print("  ❌ Turnstile A/B/C/D 策略均失败")
    return False

#  账户登录
def login(sb, email, password) -> bool:
    print(f"🌐 打开登录页面: {BASE_URL}/auth/login")
    sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=8)
    time.sleep(6)

    # 先等待 Cloudflare 验证通过（最多等 30 秒）
    print("⏳ 等待 Cloudflare 验证通过...")
    cf_passed = False
    for i in range(30):
        page_src = sb.get_page_source() or ""
        if 'input[name="email"]' in page_src.lower() or 'name="email"' in page_src.lower():
            cf_passed = True
            print(f"✅ Cloudflare 验证已通过（{i+1}s）")
            break
        time.sleep(1)
    if not cf_passed:
        print("⚠️ Cloudflare 验证可能未通过，继续尝试...")

    try:
        sb.wait_for_element('input[name="email"]', timeout=15)
    except Exception:
        # 尝试大写选择器作为后备
        try:
            sb.wait_for_element('input[name="Email"]', timeout=5)
        except Exception:
            print("❌ 页面未加载出登录表单")
            cur_url = sb.get_current_url()
            page_title = sb.get_title() or ""
            print(f"  当前 URL: {cur_url}")
            print(f"  当前标题: {page_title}")
            sb.save_screenshot("login_load_fail.png")
            return False

    print("🍪 关闭可能的 Cookie 弹窗...")
    try:
        for btn in sb.find_elements("button"):
            if "Accept" in (btn.text or ""):
                btn.click()
                time.sleep(0.5)
                break
    except Exception:
        pass

    print(f"📧 填写邮箱...")
    js_fill_input(sb, 'input[name="email"]', email)
    time.sleep(0.3)

    print("🔑 填写密码...")
    js_fill_input(sb, 'input[name="password"]', password)
    time.sleep(1)

    # 等待 Turnstile 验证框出现（最多 10 秒）
    print("⏳ 等待 Turnstile 验证框出现...")
    ts_found = False
    for i in range(10):
        if sb.execute_script(_EXISTS_JS):
            ts_found = True
            print(f"✅ 检测到 Turnstile（{i+1}s）")
            break
        time.sleep(1)

    if ts_found:
        if not handle_turnstile(sb):
            print("❌ 登录界面的 Turnstile 验证失败")
            sb.save_screenshot("login_turnstile_fail.png")
            return False
    else:
        print("ℹ️ 未检测到 Turnstile")

    print("🖱️ 敲击回车提交表单...")
    sb.press_keys('input[name="password"]', '\n')

    print("⏳ 等待登录跳转...")
    for _ in range(12):
        time.sleep(1)
        cur_url = sb.get_current_url().split('?')[0].lower()
        page_title = sb.get_title() or ""
        if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
            break

    cur_url = sb.get_current_url().split('?')[0].lower()
    page_title = sb.get_title() or ""
    if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
        print(f"✅ 登录成功！(URL: {sb.get_current_url()}, Title: {page_title})")
        return True
        
    print(f"❌ 登录失败，页面未跳转到账户页。(URL: {sb.get_current_url()}, Title: {page_title})")
    sb.save_screenshot("login_failed.png")
    return False

# ===== 自动续期流程 =====

def _read_alert(sb):
    """读取页面第一个 Bootstrap alert 的文本，找不到返回空串"""
    try:
        el = sb.find_element("div.alert", timeout=4)
        return (el.text or "").strip()
    except Exception:
        return ""


def _goto_server_detail(sb) -> bool:
    """在 Dashboard 首页查找并点击 See 进入服务器详情页"""
    print("\n🖥️  正在进入服务器续期页...")
    time.sleep(5)

    # 检查页面顶部是否已有"还无法续期"全局提示
    alert_text = _read_alert(sb)
    if alert_text and "can't renew" in alert_text.lower():
        print(f"ℹ️  页面顶部提示: {alert_text}")
        # 冷却期：返回 cooldown 而非 unknown，让 main 停止重试并只发一条冷却通知
        return "cooldown"

    # 多种选择器尝试查找 See 链接
    selectors = [
        'a[href*="/servers/edit?id="]',
        'td a[href*="/servers/edit"]',
        'table a[href*="/servers/edit"]',
        'table td a',
    ]

    see_link = None
    for sel in selectors:
        try:
            see_link = sb.find_element(sel, timeout=8)
            print(f"✅ 通过选择器找到链接: {sel}")
            break
        except Exception:
            continue

    # 选择器全部失败，尝试通过文本内容查找
    if see_link is None:
        print("⚠️ 选择器未命中，尝试文本匹配...")
        try:
            for a in sb.find_elements("a"):
                if (a.text or "").strip().lower() == "see":
                    see_link = a
                    print("✅ 通过文本 'See' 找到链接")
                    break
        except Exception:
            pass

    if see_link is None:
        # 打印调试信息帮助排查
        cur_url = sb.get_current_url()
        title = sb.get_title() or ""
        print(f"❌ 未找到 'See' 链接")
        print(f"当前 URL: {cur_url}")
        print(f"页面标题: {title}")
        try:
            links = sb.find_elements("a")
            print(f"     页面共 {len(links)} 个链接:")
            for a in links[:20]:
                href = a.get_attribute("href") or ""
                txt  = (a.text or "").strip()[:30]
                if href:
                    print(f"       - [{txt}] -> {href}")
        except Exception:
            pass
        sb.save_screenshot("servers_page_fail.png")
        return False

    print("🖱️  点击 'See' 进入服务器详情页...")
    see_link.click()
    time.sleep(5)
    print(f"📄 当前页面: {sb.get_current_url()}")
    return True


def _open_renew_modal(sb) -> bool:
    """滚动到 Renew 按钮并点击，打开模态框"""
    print("\n🔄 查找 Renew 按钮...")
    try:
        renew_btn = sb.find_element('button[data-bs-target="#renew-modal"]', timeout=10)
    except Exception:
        try:
            renew_btn = sb.find_element('button.btn.btn-outline-primary', timeout=5)
        except Exception:
            print("  ❌ 未找到 Renew 按钮")
            return False

    sb.execute_script("""
        (function(){
            var btn = document.querySelector('button[data-bs-target="#renew-modal"]')
                     || document.querySelector('button.btn.btn-outline-primary');
            if (btn) btn.scrollIntoView({behavior:'smooth',block:'center'});
        })()
    """)
    time.sleep(0.8)
    renew_btn.click()
    print("🖱️ 已点击 Renew 按钮，等待 ALTCHA 验证框...")
    time.sleep(3)

    try:
        sb.find_element('div.modal.show', timeout=5)
        print("✅ Renew 模态框已弹出")
        return True
    except Exception:
        print("⚠️ 模态框未弹出")
        return False


def _solve_altcha(sb) -> bool:
    """处理 ALTCHA 人机验证"""
    print("\n🔐 处理 ALTCHA 人机验证...")
    time.sleep(2)

    # 先检查是否已自动通过
    if sb.execute_script(_ALTCHA_SOLVED_JS):
        print("✅ ALTCHA 已自动通过")
        return True

    # 展开模态框内 iframe 并获取坐标
    coords = None
    try:
        coords = sb.execute_script(_ALTCHA_EXPAND_JS)
    except Exception:
        pass

    if coords:
        print(f"  📍 找到模态框内 iframe 坐标: ({coords['cx']}, {coords['cy']})")

    # 最多尝试 3 轮
    for attempt in range(3):
        if sb.execute_script(_ALTCHA_SOLVED_JS):
            print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
            return True

        # 策略 1: xdotool 物理点击 iframe 坐标
        if coords:
            try:
                wi = sb.execute_script(_WININFO_JS)
            except Exception:
                wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
            bar = wi["oh"] - wi["ih"]
            ax  = coords["cx"] + wi["sx"]
            ay  = coords["cy"] + wi["sy"] + bar
            print(f"🖱️  ALTCHA点击复选框  ({ax}, {ay})")
            _xdotool_click(ax, ay)

        # 策略 2: SeleniumBase 原生点击模态框内 iframe 元素
        try:
            iframes = sb.find_elements('div.modal.show iframe')
            for iframe in iframes:
                try:
                    iframe.click()
                    print("🖱️  SeleniumBase 点击模态框 iframe")
                except Exception:
                    pass
        except Exception:
            pass

        # 策略 3: JS 遍历模态框内所有可点击元素
        sb.execute_script("""
            (function(){
                var modal = document.querySelector('div.modal.show');
                if (!modal) return;
                // 点击 iframe
                var iframes = modal.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    iframes[i].click();
                    iframes[i].dispatchEvent(new MouseEvent('click', {bubbles:true}));
                }
                // 点击含 checkbox 的 label
                var labels = modal.querySelectorAll('label');
                for (var j = 0; j < labels.length; j++) {
                    var txt = (labels[j].textContent || '').toLowerCase();
                    if (txt.includes('robot') || txt.includes('captcha') || txt.includes('verify'))
                        labels[j].click();
                }
                // 点击 checkbox
                var cbs = modal.querySelectorAll('input[type="checkbox"]');
                for (var k = 0; k < cbs.length; k++) {
                    if (!cbs[k].disabled) {
                        cbs[k].click();
                        cbs[k].dispatchEvent(new MouseEvent('click', {bubbles:true}));
                    }
                }
            })()
        """)

        # 等待验证结果
        for _ in range(6):
            time.sleep(1)
            if sb.execute_script(_ALTCHA_SOLVED_JS):
                print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
                return True

        print(f"  ⚠️ 第 {attempt + 1} 轮未通过，重试...")
        # 重新获取坐标（iframe 可能已重新渲染）
        try:
            new_coords = sb.execute_script(_ALTCHA_EXPAND_JS)
            if new_coords:
                coords = new_coords
        except Exception:
            pass

    print("  ❌ ALTCHA 3 轮均失败")
    return False


def _submit_renew(sb):
    """点击模态框内的 Renew 提交按钮"""
    print("🖱️  点击模态框中的 Renew 按钮...")
    try:
        submit = sb.find_element('div.modal.show button.btn-primary', timeout=5)
        submit.click()
    except Exception:
        sb.execute_script("""
            (function(){
                var m = document.querySelector('div.modal.show');
                if (!m) return;
                var bs = m.querySelectorAll('button');
                for (var i = 0; i < bs.length; i++)
                    if (/renew/i.test(bs[i].textContent)) bs[i].click();
            })()
        """)
    time.sleep(3)



RENEW_PASS = "ok"
RENEW_COOLDOWN = "cooldown"
RENEW_SUSPENDED = "suspended"
RENEW_UNCONFIRMED = "unconfirmed"
RENEW_UNKNOWN = "unknown"


def _next_renewable(text):
    """从文案提取『as of <date>(in N day(s))』或『in N day(s)』中的下次可续日期/天数。返回 (日期,天数)"""
    import re as _re
    low = (text or "").lower()
    m = _re.search(r"as of\s+([^\n().]{2,40}?)(?:\s*\(in[\s]*(\d+)[^)]*days?\))?", low)
    if m:
        return m.group(1).strip(), (int(m.group(2)) if m.group(2) else None)
    m2 = _re.search(r"in\s*(\d+)\s*day", low)
    if m2:
        return None, int(m2.group(1))
    return None, None


def _read_page_text(sb, timeout=4):
    """读整页正文，续期结果判定不只看单条 div.alert"""
    try:
        return sb.get_text("body", timeout=timeout)
    except Exception:
        try:
            t = sb.execute_script("return document.body.innerText")
            return t or ""
        except Exception:
            return ""


_STATIC_SERVER_TYPE_WARNING = (
    "changing the server type will reset the startup command and environment variables"
)


def _extract_expiry_dates(text):
    """Extract labelled expiry dates from page text, oldest occurrence first."""
    text = text or ""
    patterns = [
        (r"\b\d{4}-\d{1,2}-\d{1,2}\b", ("%Y-%m-%d",)),
        (r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y")),
        (r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
         ("%B %d, %Y", "%B %d %Y")),
        (r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
         ("%d %B %Y",)),
    ]
    labels = ("expir", "valid until", "due date", "end date")
    found = []
    for match_re, formats in patterns:
        for m in re.finditer(match_re, text, re.IGNORECASE):
            context = text[max(0, m.start() - 100):min(len(text), m.end() + 50)].lower()
            if not any(label in context for label in labels):
                continue
            raw = m.group(0)
            parsed = None
            for fmt in formats:
                try:
                    parsed = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed and (parsed, raw) not in found:
                found.append((parsed, raw))
    return found


def _new_cooldown_evidence(before_text, after_text):
    """Return a newly appeared can't-renew sentence, if submission created one."""
    sentence_re = re.compile(r"[^\n.!?]*(?:can't|cannot|unable to)\s+renew[^\n.!?]*[.!?]?", re.IGNORECASE)
    before = {m.group(0).strip().lower() for m in sentence_re.finditer(before_text or "")}
    for m in sentence_re.finditer(after_text or ""):
        sentence = m.group(0).strip()
        if sentence and sentence.lower() not in before:
            return sentence
    return ""


def _classify_renew(alert_text, page_text, before_text=""):
    """权威归类续期结果，避免把假 success 当成功。
    成功判定需【renew/extension/renewal】+ 明确的 success/extend/complete/date 连语，
    不能只靠页面 body 里任一 `success` 子串（易误报）。返回 (status, detail)。
    detail 在成功时给到页面里的真实证据片段，避免只报 server-type 警告误导。"""
    body = (page_text or "") + "\n" + (alert_text or "")
    low = body.lower()

    # Strongest proof: the persisted expiry date moved forward.
    before_dates = _extract_expiry_dates(before_text)
    after_dates = _extract_expiry_dates(body)
    if before_dates and after_dates:
        old_date, old_raw = max(before_dates)
        new_date, new_raw = max(after_dates)
        if new_date > old_date:
            return RENEW_PASS, f"到期时间已更新: {old_raw} → {new_raw}"

    # Some panel versions confirm renewal by replacing the Renew action with a
    # newly generated cooldown message rather than showing a success alert.
    new_cooldown = _new_cooldown_evidence(before_text, body)
    if new_cooldown:
        return RENEW_PASS, f"续期后进入冷却期: {new_cooldown}"

    # 成功：`renew...` 与 success/extend/complete/date 等紧邻（词级），且排除 suspended/冷却语境
    success_pat = re.compile(
        r"renew[a-z]*\s+(?:success[a-z]*|extend[a-z]*|complete[a-z]*|done\b|now\b)"
        r"|renew[a-z]{0,3}\s+until\s+[^\n]{0,40}"
        r"|renewal\s+(?:success[a-z]*|complete[a-z]*|extended?\b)"
        r"|(?:your\s+)?server\s+has\s+been\s+renew[a-z]*",
        re.IGNORECASE)
    block = re.compile(r"suspend|can't renew|cannot renew|unable to renew")
    sp = success_pat.search(low)
    if sp and not block.search(low):
        # 提取匹配附近作为人眼可见的证据
        s0 = max(0, sp.start() - 40); e0 = min(len(low), sp.end() + 40)
        ev = low[s0:e0].strip().replace("\n", " ")[:180]
        return RENEW_PASS, (ev or alert_text or "续期成功（页面出现续期成功文案）")

    if "suspended" in low:
        return RENEW_SUSPENDED, (alert_text or "服务器仍 suspended，续期未生效")
    if "can't renew" in low or "cannot renew" in low or "unable" in low:
        return RENEW_COOLDOWN, (alert_text or "未到续期时间/冷却中")
    if _STATIC_SERVER_TYPE_WARNING in low:
        # This alert belongs to the server-type form and is always present on
        # the edit page. It is not a renewal response and must never prove
        # success or cooldown by itself.
        return RENEW_UNCONFIRMED, "只检测到服务器类型的静态警告，未找到续期成功证据"
    if alert_text:
        return RENEW_UNKNOWN, alert_text
    return RENEW_UNKNOWN, "未检测到明确提示"

def _check_renew_result(sb, before_text=""):
    """读取提示，判定续期是否真生效。返回 (status, detail)。
    （不再在每个节点尝试内发 TG；由 main 对每个账号统一发一条汇总消息，避免冷却/失败时重复推送）"""
    print("\n📋 检查续期结果...")
    alert_text = _read_alert(sb)
    if not alert_text:
        time.sleep(3)
        alert_text = _read_alert(sb)
    page_text = _read_page_text(sb)
    status, detail = _classify_renew(alert_text, page_text, before_text)
    if status in (RENEW_UNKNOWN, RENEW_UNCONFIRMED):
        # Reload once to verify persisted state. Transient success messages may
        # vanish, but an advanced expiry date or cooldown state should remain.
        try:
            sb.refresh()
            time.sleep(4)
            page_text = _read_page_text(sb)
            alert_text = _read_alert(sb)
            status, detail = _classify_renew(alert_text, page_text, before_text)
        except Exception as e:
            print(f"⚠️ 重新加载验证续期状态失败: {e}")
    print(f"📩 页面提示: {detail}")
    return status, detail


def renew_server(sb):
    """登录成功后调用：自动进入详情页 -> Renew -> ALTCHA -> 提交。
    返回 dict(status, detail, before)：只有 status==RENEW_PASS 才算真续上。"""
    print("\n" + "#" * 25)
    print("  开始自动续期流程")
    print("#" * 25)

    gs = _goto_server_detail(sb)
    if gs == "cooldown":
        return {"status": RENEW_COOLDOWN, "detail": "未到续期时间（页面提示 can't renew）", "before": ""}
    if not gs:
        return {"status": RENEW_UNKNOWN, "detail": "未能进入详情页"}

    before = _read_page_text(sb)

    if not _open_renew_modal(sb):
        return {"status": RENEW_UNKNOWN, "detail": "未弹 Renew 模态框"}

    altcha_ok = _solve_altcha(sb)
    if not altcha_ok:
        print("⚠️ ALTCHA 验证未通过，仍尝试提交 Renew...")

    _submit_renew(sb)
    status, detail = _check_renew_result(sb, before)
    return {"status": status, "detail": detail, "before": before}


def _run_account(sb_kwargs, email, pwd):
    """单个账号：启动浏览器 -> 登录 -> 自动续期。
    返回 (status, detail)。status ∈ RENEW_*：RENEW_PASS 才算真续上；RENEW_COOLDOWN 为合法冷却；其余未确认为失败。
    detail 用于最终发一条汇总 TG，避免每次节点尝试重复推送。"""
    global CURRENT_EMAIL
    CURRENT_EMAIL = email
    print("🚀 启动浏览器...")
    try:
        with SB(**sb_kwargs) as sb:
            # 在首次导航前注入 Turnstile attachShadow CDP 钩子（对所有后续文档生效，含登录页）
            _install_turnstile_hook_cdp(sb)
            try:
                sb.open("https://api.ip.sb/ip")
                print(f"📍  当前出口IP: {sb.get_text('body')}")
            except Exception:
                pass

            if not login(sb, email, pwd):
                print("\n❌ 登录失败，终止该账号续期操作。")
                return (RENEW_UNKNOWN, "登录失败")

            res = renew_server(sb)
            st = res.get("status", RENEW_UNKNOWN) if isinstance(res, dict) else RENEW_UNKNOWN
            detail = res.get("detail", "") if isinstance(res, dict) else ""
            print(f"ℹ️  账号 {email} 续期状态: {st}")
            return (st, detail)
    except Exception as e:
        print(f"\n❌ 账号 {email} 处理异常: {e}")
        return (RENEW_UNKNOWN, f"处理异常: {e}")

#  脚本执行入口 (可选代理)
def main():
    print("#" * 25)
    print("   katabump 自动登录续期")
    print("#" * 25)

    if not ACCOUNTS:
        print("❌ 没有可用的账号，退出。")
        raise SystemExit(1)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy_str = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:8080"
    sb_kwargs = {"uc": True, "headless": False}

    if IS_PROXY:
        print(f"🔗 挂载代理: {proxy_str}")
        sb_kwargs["proxy"] = proxy_str
    else:
        print("🌐 未使用代理，直连访问")

    print(f"👥 共 {len(ACCOUNTS)} 个账号待处理")

    renewed = 0
    cooldown = 0
    failed = 0
    max_attempts = int(os.environ.get("NODE_ATTEMPTS", "3"))
    for idx, acc in enumerate(ACCOUNTS, 1):
        email = acc["email"]
        pwd   = acc["password"]
        print("\n" + "=" * 25)
        print(f"  处理账号 {idx}/{len(ACCOUNTS)}: {email}")
        print("=" * 25)

        acc_res = RENEW_UNKNOWN
        acc_detail = ""
        for attempt in range(1, max_attempts + 1):
            print(f"  ── 节点尝试 {attempt}/{max_attempts} ──")
            if attempt > 1:
                _restart_proxy()
            st, detail = _run_account(sb_kwargs, email, pwd)
            acc_res = st
            acc_detail = detail or acc_detail
            if st == RENEW_PASS:
                break
            if st == RENEW_COOLDOWN:
                # 冷却期是终结态：重试也不会变成可续，直接结束，避免 3 次重复尝试与重复通知
                break
            # 未确认/失败：可再换节点试（后续详尽看）。
        if acc_res == RENEW_PASS:
            renewed += 1
            print(f"✅ 账号 {email} 续期成功")
            send_tg_message("✅", "续期成功", acc_detail or "续期成功")
        elif acc_res == RENEW_COOLDOWN:
            cooldown += 1
            print(f"⏳ 账号 {email} 冷却中（未到续期）")
            # 统一只发一条冷却通知（此前每个节点尝试都发，导致一天/一次 run 重复刷屏）
            send_tg_message("⏳", "未到续期时间（冷却中）", (acc_detail or "未到续期") + f" | 账户 {email}")
        else:
            failed += 1
            print(f"❌ 账号 {email} 未能确认续期（{acc_res}）")
            send_tg_message("❌", "续期失败/未确认", f"{email} status={acc_res} | {acc_detail}")

    print("\n" + "#" * 25)
    print(f"  处理完毕：续期成功 {renewed} / 冷却 {cooldown} / 失败 {failed} / 共 {len(ACCOUNTS)}")
    print("#" * 25)
    # 只要存在「未确认/失败」（不是冷却），才让 Actions 失败报警
    if failed > 0:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
