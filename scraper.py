#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Temu 云端定时抓取脚本（GitHub Actions / 任意 Linux 环境运行）
==============================================================
与书签抓取（scraper.js）同一套 DOM 抓取逻辑，用 Playwright 真实浏览器
自动打开每个商品页、点击所有规格组合、读取完整 SKU 价格，然后合并写回
jsonblob 数据中心。

依赖：pip install -r requirements.txt
      python -m playwright install chromium

环境变量（均可选）：
  JSONBLOB_URL     jsonblob 数据中心地址（默认使用 temu-tracker-v3 的共享 blob）
  SCRAPE_OPERATOR  本次快照的运营名（默认 "云端自动"）
  HEADLESS         1=无头模式（CI 建议配合 xvfb 用有头模式），默认 0 有头
  DELAY_SEC        商品之间的间隔秒数（默认 5，防限流）
  MAX_RETRY        单个商品抓取失败重试次数（默认 3）
  TEMU_COOKIES     已登录的 Temu cookie（EditThisCookie 导出的 JSON 数组字符串）。
                   用 GitHub Actions 时放到名为 TEMU_COOKIES 的 Secret 中。
                   未提供时以游客身份访问（可能被 Temu 要求登录）。
"""

import json
import os
import re
import sys
import time
import base64
import urllib.parse
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    print("缺少 requests，请先 pip install -r requirements.txt")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("缺少 playwright，请先 pip install -r requirements.txt && python -m playwright install chromium")
    sys.exit(1)

# ============ 配置 ============
def env_or(key, default):
    """读取环境变量，空字符串视为未设置，回退默认值。"""
    v = os.environ.get(key, "")
    return v if v and v.strip() else default


BLOB_URL = env_or(
    "JSONBLOB_URL",
    "https://jsonblob.com/api/jsonBlob/019feff3-ac2e-7da2-b540-d521af103618",
)
OPERATOR = env_or("SCRAPE_OPERATOR", "云端自动")
HEADLESS = env_or("HEADLESS", "0") == "1"
DELAY_SEC = int(env_or("DELAY_SEC", "5"))
MAX_RETRY = int(env_or("MAX_RETRY", "3"))
TEMU_COOKIES_RAW = env_or("TEMU_COOKIES", "")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ---- 验证码自动识别（可选）----
# 遇到"按顺序点击图片"验证码时，自动截图并调用视觉 AI 完成点击。
# 推荐免费方案：智谱 bigmodel.cn 的 glm-4v-flash（OpenAI 兼容接口）
#   VISION_API_KEY  = 智谱的 API Key
#   VISION_API_URL  = https://open.bigmodel.cn/api/paas/v4/chat/completions
#   VISION_MODEL    = glm-4v-flash
# 未配置 VISION_API_KEY 时，遇到验证码会跳过该商品并留截图，行为与原来一致。
VISION_API_KEY = env_or("VISION_API_KEY", "")
VISION_API_URL = env_or(
    "VISION_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions"
)
VISION_MODEL = env_or("VISION_MODEL", "glm-4v-flash")
CAPTCHA_RETRY = int(env_or("CAPTCHA_MAX_RETRY", "3"))


def log(msg):
    print(msg, flush=True)


def now_str():
    now = datetime.now(timezone(timedelta(hours=8)))
    # 与浏览器 toLocaleString('zh-CN') 一致的格式：2026/8/12 09:30:45
    return f"{now.year}/{now.month}/{now.day} {now.hour:02d}:{now.minute:02d}:{now.second:02d}"


# ============ 登录 cookie 注入 ============
def parse_temu_cookies(raw):
    """把 EditThisCookie 导出的 JSON 数组转换为 Playwright 的 add_cookies 格式。"""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log("  ⚠️ TEMU_COOKIES 不是合法的 JSON，忽略 cookie 注入")
        return []
    if not isinstance(items, list):
        log("  ⚠️ TEMU_COOKIES 不是数组，忽略 cookie 注入")
        return []

    out = []
    for c in items:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        ck = {
            "name": name,
            "value": str(value),
            "domain": c.get("domain") or ".temu.com",
            "path": c.get("path") or "/",
        }
        if c.get("expirationDate"):
            ck["expires"] = int(c["expirationDate"])
        if c.get("httpOnly"):
            ck["httpOnly"] = True
        if c.get("secure"):
            ck["secure"] = True
        ss = (c.get("sameSite") or "").lower()
        if ss == "no_restriction":
            ck["sameSite"] = "None"
        elif ss == "lax":
            ck["sameSite"] = "Lax"
        elif ss == "strict":
            ck["sameSite"] = "Strict"
        out.append(ck)
    return out


def inject_cookies(context, page):
    """先访问 Temu 首页建立站点上下文，再注入登录 cookie，最后刷新回首页。"""
    cookies = parse_temu_cookies(TEMU_COOKIES_RAW)
    if not cookies:
        log("  ℹ️ 未提供 TEMU_COOKIES，以游客身份访问")
        return False
    try:
        page.goto("https://www.temu.com/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    time.sleep(3)
    context.add_cookies(cookies)
    log(f"  🔑 已注入 {len(cookies)} 个 Temu 登录 cookie")
    try:
        page.goto("https://www.temu.com/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    time.sleep(3)
    return True


# ============ jsonblob 读写 ============
def get_blob():
    for i in range(5):
        try:
            r = requests.get(BLOB_URL, timeout=30)
            if r.status_code == 200:
                return r.json(), r.headers.get("ETag")
            if r.status_code == 404:
                return {"p": []}, None
            log(f"  ⚠️ GET 数据中心 HTTP {r.status_code}，重试 {i + 1}/5")
        except Exception as e:
            log(f"  ⚠️ GET 数据中心异常: {e}，重试 {i + 1}/5")
        time.sleep(3 * (i + 1))
    return None, None


def put_blob(data, etag):
    headers = {"Content-Type": "application/json"}
    if etag:
        headers["If-Match"] = etag
    for i in range(5):
        try:
            r = requests.put(BLOB_URL, headers=headers, data=json.dumps(data), timeout=30)
            if r.status_code in (200, 201):
                return True
            log(f"  ⚠️ PUT 数据中心 HTTP {r.status_code}，重试 {i + 1}/5")
        except Exception as e:
            log(f"  ⚠️ PUT 数据中心异常: {e}，重试 {i + 1}/5")
        time.sleep(3 * (i + 1))
    return False


# ============ 浏览器抓取 ============
# 与书签 scraper.js 一致的 DOM 抓取逻辑（在页面内执行）
CLICK_EXTRACT_JS = r"""
async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // 1. 读取规格组
  const radios = [...document.querySelectorAll('div[role="radio"]')];
  const readPrices = () => {
    const prices = [...document.querySelectorAll('._14At0Pe5')]
      .map(e => e.textContent.trim())
      .filter(t => t.startsWith('$'));
    let cur = null, orig = null;
    if (prices.length >= 2) {
      orig = parseFloat(prices[0].replace(/[$,]/g, ''));
      cur = parseFloat(prices[1].replace(/[$,]/g, ''));
    } else if (prices.length === 1) {
      cur = parseFloat(prices[0].replace(/[$,]/g, ''));
    }
    return { cur, orig };
  };

  // 无规格选项：只抓主价格（单 SKU 商品）
  if (!radios.length) {
    const { cur, orig } = readPrices();
    if (cur === null) return { error: 'no_price', variants: [] };
    return { error: null, variants: [{ n: '', p: cur, o: orig }] };
  }

  const gm = new Map();
  radios.forEach(r => {
    const ancestor = r.parentElement?.parentElement;
    if (!ancestor) return;
    if (!gm.has(ancestor)) gm.set(ancestor, []);
    gm.get(ancestor).push(r);
  });

  const labels = ['Color', 'Size', 'Quantity', 'Style', 'Pattern', 'Material', 'Type', 'Version', 'Model'];
  const groups = [];
  let gi = 0;
  gm.forEach((rs, a) => {
    const t = a.textContent || '';
    let n = 'Spec-' + (++gi);
    for (const l of labels) { if (t.includes(l)) { n = l; break; } }
    groups.push({ name: n, options: rs.map(r => r.getAttribute('aria-label')) });
  });

  // 2. 全组合遍历
  const combos = [];
  (function gen(i, cur) {
    if (i === groups.length) { combos.push([...cur]); return; }
    for (const o of groups[i].options) { cur.push(o); gen(i + 1, cur); cur.pop(); }
  })(0, []);

  const variants = [];
  for (let i = 0; i < combos.length; i++) {
    const name = combos[i].join(' / ');
    for (const opt of combos[i]) {
      const el = document.querySelector('div[aria-label="' + opt.replace(/"/g, '\\"') + '"]');
      if (el) el.click();
      await sleep(500);
    }
    await sleep(1800);

    const { cur, orig } = readPrices();
    if (cur === null) continue;  // 该组合不存在（无价格），跳过
    variants.push({ n: name, p: cur, o: orig });
  }

  return {
    error: null,
    variants,
    groups: groups.map(g => g.name + '(' + g.options.length + ')'),
    combos: combos.length,
  };
}
"""

# 快速路径：直接从 window.rawData 解析全部 SKU（若 SSR 数据已带上）
RAW_JS = r"""
() => {
  try {
    const raw = window.rawData;
    if (!raw) return null;
    const store = raw.store || {};
    const goods = store.goods || {};
    const f = store.formatSkuData || {};
    const skuInfos = f.skuInfos || {};
    if (!Object.keys(goods).length && !Object.keys(skuInfos).length) return null;

    const variants = [];
    const seen = new Set();
    for (const skuId of Object.keys(skuInfos)) {
      const info = skuInfos[skuId];
      if (!info) continue;
      const specs = (info.specInfo || info.specs || []).map(s => s.specValue || s.value || '');
      const name = specs.join(' / ');
      if (seen.has(name)) continue;
      seen.add(name);

      const pi = info.priceInfo || info.price || info;
      const sale = pi.salePrice || pi.price || pi.sale || {};
      const amount = (sale && (sale.amount !== undefined ? sale.amount : sale.value))
                     || (pi.amount !== undefined ? pi.amount : pi.value);
      let cur = null;
      if (typeof amount === 'number') cur = amount;
      else if (typeof amount === 'string' && amount) cur = parseFloat(amount);
      if (cur === null) continue;

      const origInfo = pi.originalPrice || pi.original_price || (sale && sale.originalPrice);
      let orig = null;
      if (origInfo) {
        const oa = origInfo.amount !== undefined ? origInfo.amount : origInfo.value;
        orig = typeof oa === 'number' ? oa : (oa ? parseFloat(oa) : null);
      }
      variants.push({ n: name, p: cur, o: orig });
    }
    if (!variants.length) return null;
    return { variants };
  } catch (e) { return null; }
}
"""


# ============ 验证码自动识别（可选） ============
# 目标验证码："按顺序点击图片"（如 Click in order: hamburger, keyboard, ...）。
# 思路：找到验证码区域(iframe/弹窗) → 截图 → 视觉 AI 识别网格与顺序 → 模拟鼠标按序点击。
CAPTCHA_INDICATORS = (
    "captcha", "verify", "security-check", "geetest", "sec-cpt",
    "hcaptcha", "turnstile", "validation", "challenge",
)


def _norm(name):
    """物品名归一化：小写、去空格、去复数 s。"""
    n = (name or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if n.endswith("s") and not n.endswith("ss"):
        n = n[:-1]
    return n


def detect_captcha(page):
    """检测页面上是否出现验证码。"""
    # 1) 验证码 iframe
    for fr in page.frames:
        u = (fr.url or "").lower()
        if any(k in u for k in CAPTCHA_INDICATORS):
            return True
    # 2) 页面文字提示（兼容英文/中文）
    try:
        body = page.evaluate("() => document.body ? document.body.innerText.slice(0, 800) : ''")
        low = body.lower()
        for kw in ("click in order", "select the following", "按顺序", "依次点击",
                   "click every", "please select", "验证码", "verify you are human"):
            if kw in low:
                return True
    except Exception:
        pass
    return False


def find_captcha_box(page):
    """返回验证码区域在页面上的 {x, y, width, height}（视口坐标）；找不到返回 None。"""
    # 1) 验证码 iframe：frame_element() 拿到底层 <iframe> 元素，取 bounding box
    for fr in page.frames:
        u = (fr.url or "").lower()
        if any(k in u for k in CAPTCHA_INDICATORS):
            try:
                el = fr.frame_element()
                bb = el.bounding_box()
                if bb and bb["width"] > 150 and bb["height"] > 150:
                    return {k: int(v) for k, v in bb.items()}
            except Exception:
                pass
    # 2) 页面内验证码容器（弹窗）
    for sel in (
        'iframe[src*="captcha" i]', 'iframe[src*="verify" i]',
        'iframe[src*="sec" i]', 'iframe[src*="challenge" i]',
        'div[class*="captcha" i]', 'div[id*="captcha" i]',
        'div[class*="verify" i]', 'div[role="dialog"]',
    ):
        try:
            els = page.locator(sel)
            n = els.count()
            for i in range(n):
                bb = els.nth(i).bounding_box()
                if bb and bb["width"] > 150 and bb["height"] > 150:
                    # 弹窗需确认含验证码特征，避免误点普通弹窗
                    txt = (els.nth(i).inner_text() or "")[:200]
                    low = txt.lower()
                    if any(k in low for k in ("click", "select", "按顺序", "依次", "captcha",
                                              "验证", "human")):
                        return {k: int(v) for k, v in bb.items()}
        except Exception:
            continue
    return None


def vision_request(image_b64):
    """调用 OpenAI 兼容视觉接口，返回模型文本。"""
    headers = {
        "Authorization": f"Bearer {VISION_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": (
                    "这是一张网页安全验证码截图。图片里通常上方有提示文字，下方是多张物品图片组成的网格。\n"
                    "请完成：\n"
                    "1) grid：物品网格在整张图中的相对包围框（左上角 left/top、右下角 right/bottom，0~1 小数，"
                    "只包住物品图片网格，不含上方提示文字）。\n"
                    "2) rows / cols：物品网格的行数和列数。\n"
                    "3) cells：从左到右、从上到下给每个格子编号（从 1 开始），并识别每格物品的英文名"
                    "（单数名词，如 phone、hamburger、keyboard、sports car、bookcase、peach）。\n"
                    "4) order：读取图片中的提示文字（例如 Click in order: hamburger, keyboard, bookcase, peach, chair），"
                    "提取要求按顺序点击的物品英文名列表；如果看不清或没有提示文字，返回空数组。\n"
                    "只输出 JSON，不要代码块和任何解释："
                    '{"grid":{"left":0.1,"top":0.2,"right":0.9,"bottom":0.95},"rows":3,"cols":3,'
                    '"cells":[{"cell":1,"name":"phone"}],"order":["hamburger","keyboard"]}'
                )},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        "max_tokens": 1000,
        "temperature": 0,
    }
    r = requests.post(VISION_API_URL, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _parse_vision_json(text):
    """从模型输出中提取 JSON 对象。"""
    text = (text or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"视觉模型未返回 JSON: {text[:200]}")
    return json.loads(m.group(0))


def solve_captcha(page):
    """自动完成一次验证码。成功返回 True，失败返回 False。"""
    if not VISION_API_KEY:
        log("  ⚠️ 检测到验证码，但未配置 VISION_API_KEY，无法自动处理")
        return False

    for attempt in range(1, CAPTCHA_RETRY + 1):
        box = find_captcha_box(page)
        if not box:
            return True  # 已验证通过或已消失

        # 截取验证码区域
        try:
            shot = page.screenshot(clip=box)
        except Exception as e:
            log(f"  ⚠️ 验证码截图失败: {e}")
            time.sleep(3)
            continue
        b64 = base64.b64encode(shot).decode("ascii")

        # AI 识别
        try:
            result = _parse_vision_json(vision_request(b64))
        except Exception as e:
            log(f"  ⚠️ 验证码识别失败(尝试 {attempt}/{CAPTCHA_RETRY}): {e}")
            time.sleep(3)
            continue

        grid = result.get("grid") or {}
        rows = int(result.get("rows") or 0)
        cols = int(result.get("cols") or 0)
        cells = result.get("cells") or []
        order = [x.strip() for x in (result.get("order") or []) if str(x).strip()]
        if rows <= 0 or cols <= 0 or not cells or not order:
            log(f"  ⚠️ 验证码识别结果不完整(尝试 {attempt}/{CAPTCHA_RETRY})，重试")
            time.sleep(3)
            continue

        # 建立 物品名 → 格子编号 映射
        name2cell = {}
        for c in cells:
            nm = _norm(c.get("name"))
            if nm and c.get("cell"):
                name2cell[nm] = int(c["cell"])

        # 按顺序点击
        left = box["x"] + box["width"] * float(grid.get("left", 0.0))
        top = box["y"] + box["height"] * float(grid.get("top", 0.0))
        right = box["x"] + box["width"] * float(grid.get("right", 1.0))
        bottom = box["y"] + box["height"] * float(grid.get("bottom", 1.0))
        cw = (right - left) / cols
        ch = (bottom - top) / rows

        clicked = []
        ok_all = True
        for item in order:
            ni = _norm(item)
            cell = name2cell.get(ni)
            if cell is None:
                # 模糊匹配：识别名包含指令词，或指令词包含识别名
                for k, v in name2cell.items():
                    if ni in k or k in ni:
                        cell = v
                        break
            if cell is None:
                log(f"  ⚠️ 找不到物品 '{item}' 的位置，重试")
                ok_all = False
                break
            row, col = divmod(cell - 1, cols)
            x = left + (col + 0.5) * cw
            y = top + (row + 0.5) * ch
            try:
                page.mouse.click(x, y)
                clicked.append(f"{item}@({x:.0f},{y:.0f})")
                time.sleep(1.2)  # 模拟人类点击间隔
            except Exception as e:
                log(f"  ⚠️ 点击 '{item}' 失败: {e}")
                ok_all = False
                break

        if not ok_all:
            time.sleep(3)
            continue
        log(f"  🖱️ 已按顺序点击: {' → '.join(clicked)}")

        # 等待验证结果
        time.sleep(4)
        if not detect_captcha(page):
            log("  ✅ 验证码已通过")
            return True
        log(f"  ⚠️ 点击后验证码仍在(尝试 {attempt}/{CAPTCHA_RETRY})，重试")
        time.sleep(3)
    return False


def wait_for_content(page, timeout=90):
    """等待 Cloudflare 挑战通过、页面出现可抓取的数据。返回 (mode, data)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # 优先处理验证码：验证码弹窗期间页面拿不到任何数据
            if detect_captcha(page):
                log("  🧩 检测到验证码，尝试自动点击处理")
                if solve_captcha(page):
                    log("  ✅ 验证码已通过，继续加载数据")
                else:
                    try:
                        pid_m = re.search(r"g-(\d+)\.html", page.url)
                        name = pid_m.group(1) if pid_m else "captcha"
                        page.screenshot(path=f"artifacts/{name}_captcha_fail.png")
                    except Exception:
                        pass
                    log("  ❌ 验证码自动处理失败，本轮放弃，外层将重试")
                    return None, None
                time.sleep(2)
                continue

            raw = page.evaluate(RAW_JS)
            if raw and raw.get("variants"):
                return "raw", raw["variants"]

            has_radios = page.evaluate(
                "() => document.querySelectorAll('div[role=\"radio\"]').length > 0"
            )
            if has_radios:
                return "click", None

            # 检测登录页（cookie 失效或游客被要求登录）
            url = page.url
            if "login" in url.lower() or "register" in url.lower():
                page.screenshot(path="artifacts/login_required.png")
                return "login", None

            # 页面出现明显反爬拦截时截图留证
            body_txt = page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")
            if 'Access Denied' in body_txt or 'attention' in body_txt.lower():
                page.screenshot(path="artifacts/blocked.png")
            elif 'sign in' in body_txt.lower() and ('register' in body_txt.lower() or 'create account' in body_txt.lower()):
                page.screenshot(path="artifacts/login_required.png")
                return "login", None
        except Exception:
            pass
        time.sleep(2)
    return None, None


def scrape_product(page, pid):
    url = f"https://www.temu.com/g-{pid}.html"
    for attempt in range(1, MAX_RETRY + 1):
        try:
            log(f"  打开 {url} (尝试 {attempt}/{MAX_RETRY})")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            mode, variants = wait_for_content(page, timeout=75)
            if mode == "login":
                page.screenshot(path=f"artifacts/{pid}_login.png")
                log("  ⚠️ 被重定向到登录页 — cookie 可能已失效或未注入")
                return "LOGIN"
            if mode is None:
                page.screenshot(path=f"artifacts/{pid}_timeout.png")
                log(f"  ⚠️ 页面内容加载超时（可能被反爬拦截）")
                time.sleep(10)
                continue

            if mode == "raw":
                log(f"  ✅ 快速路径：从 rawData 解析到 {len(variants)} 个变体")
                return {"variants": variants}

            # click 模式
            result = page.evaluate(CLICK_EXTRACT_JS)
            if result.get("error") == "no_price":
                log("  ⚠️ 未找到价格元素（商品可能已下架）")
                page.screenshot(path=f"artifacts/{pid}_noprice.png")
                return None
            if result.get("error") is not None:
                log(f"  ⚠️ 抓取错误: {result.get('error')}")
                return None
            vs = result["variants"]
            log(f"  ✅ 点击抓取完成：{len(vs)} 个变体"
                + (f"，规格组 {result.get('groups')}，组合 {result.get('combos')}" if vs else ""))
            return {"variants": vs}
        except Exception as e:
            log(f"  ⚠️ 异常: {e}")
            try:
                page.screenshot(path=f"artifacts/{pid}_err.png")
            except Exception:
                pass
            time.sleep(8)
    return None


# ============ 数据合并（与 upload.html 逻辑一致） ============
def reindex(snap, old_vn, new_vn):
    if not snap or "v" not in snap:
        return snap
    lookup = {name: i for i, name in enumerate(old_vn)}
    new_v = []
    for name in new_vn:
        old_idx = lookup.get(name)
        new_v.append(snap["v"][old_idx] if old_idx is not None and old_idx < len(snap["v"]) else None)
    out = {"t": snap.get("t", ""), "op": snap.get("op", "")}
    if "v" in snap:
        out["v"] = new_v
    return out


def update_product(prod, result):
    variants = result["variants"]
    ts = now_str()

    new_vn = [v["n"] for v in variants]
    new_vo = [v.get("o") for v in variants]
    prices = [v["p"] for v in variants]

    existing = prod.get("vn") or []
    if existing:
        # 新变体在前，旧变体在后去重
        merged = list(dict.fromkeys(new_vn + existing))
        merged_vo = []
        for name in merged:
            if name in existing:
                old_idx = existing.index(name)
                merged_vo.append(prod.get("vo", [])[old_idx] if old_idx < len(prod.get("vo", [])) else None)
            else:
                new_idx = new_vn.index(name)
                merged_vo.append(new_vo[new_idx] if new_idx < len(new_vo) else None)
        if len(merged) != len(existing):
            prod["ss"] = [reindex(s, existing, merged) for s in prod.get("ss", [])]
        prod["vn"] = merged
        prod["vo"] = merged_vo
    else:
        prod["vn"] = new_vn
        prod["vo"] = new_vo

    prod["ss"] = [{"t": ts, "op": OPERATOR, "v": prices}] + (prod.get("ss") or [])
    prod["ss"] = prod["ss"][:3]
    prod["lu"] = ts
    if not prod.get("nm"):
        prod["nm"] = result.get("name", "")
    return len(prices)


# ============ 主流程 ============
def main():
    os.makedirs("artifacts", exist_ok=True)

    log("=" * 50)
    log(f"Temu 云端抓取开始  {now_str()}")
    log(f"数据中心: {BLOB_URL}")

    data, etag = get_blob()
    if data is None:
        log("❌ 无法读取数据中心")
        sys.exit(1)

    products = data.get("p") or []
    if not products:
        log("❌ 数据中心暂无商品")
        sys.exit(1)

    log(f"共 {len(products)} 个商品")

    ok = fail = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="en-US",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 2000},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        inject_cookies(context, page)

        for i, prod in enumerate(products):
            pid = prod.get("id")
            if not pid:
                continue
            log(f"\n[{i + 1}/{len(products)}] 商品 {pid}")
            result = scrape_product(page, pid)
            if result == "LOGIN":
                log("❌ 云端浏览器被要求登录 — 请更新 GitHub Secrets 中的 TEMU_COOKIES")
                browser.close()
                sys.exit(1)
            if result is None:
                fail += 1
                log("  ⚠️ 保留旧数据")
            else:
                n = update_product(prod, result)
                ok += 1
                log(f"  ✅ 已更新 {n} 个变体价格")
            if i < len(products) - 1:
                time.sleep(DELAY_SEC)

        browser.close()

    log("\n" + "=" * 50)
    log(f"抓取完成：成功 {ok} 个，失败 {fail} 个")

    if ok == 0:
        log("❌ 全部失败，不写回数据，避免破坏现有数据")
        sys.exit(1)

    log("写回数据中心...")
    if put_blob(data, etag):
        log("✅ 数据已更新")
    else:
        log("❌ 写回失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
