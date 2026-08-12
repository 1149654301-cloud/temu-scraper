#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Temu 价格监测 - Playwright 版（带住宅代理 + stealth）

原理：
    用 Playwright 启动 Chromium，配置住宅代理和 stealth 参数，
    访问商品页后提取 __NEXT_DATA__ 中的价格数据。
    Playwright 能自动执行 Cloudflare 的 JS challenge，
    住宅代理避免触发验证码。

环境变量：
    PROXY_URL    代理地址，如 http://user:pass@host:port
    PRODUCTS_FILE 商品列表文件（默认 products.json）
    DATA_FILE     价格快照输出文件（默认 data.json）
    DELAY_MIN / DELAY_MAX  请求间隔随机范围（秒）
    MAX_RETRY     每个商品最大重试次数
"""

import json
import os
import re
import sys
import time
import random
import datetime

from playwright.sync_api import sync_playwright


def env_or(key, default=""):
    return os.environ.get(key, default)


PROXY_URL = env_or("PROXY_URL", "")
PRODUCTS_FILE = env_or("PRODUCTS_FILE", "products.json")
DATA_FILE = env_or("DATA_FILE", "data.json")
DELAY_MIN = float(env_or("DELAY_MIN", "2"))
DELAY_MAX = float(env_or("DELAY_MAX", "5"))
MAX_RETRY = int(env_or("MAX_RETRY", "3"))


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ==================== 代理解析 ====================
def parse_proxy():
    """解析 PROXY_URL 为 Playwright proxy 字典"""
    if not PROXY_URL:
        return None
    raw = PROXY_URL.strip()
    if "://" not in raw:
        raw = "http://" + raw
    # 格式: http://user:pass@host:port
    m = re.match(r"^(https?)://([^:]+):([^@]+)@(.+):(\d+)$", raw)
    if m:
        return {
            "server": f"{m.group(1)}://{m.group(4)}:{m.group(5)}",
            "username": m.group(2),
            "password": m.group(3),
        }
    # 无认证格式: http://host:port
    m2 = re.match(r"^(https?)://(.+):(\d+)$", raw)
    if m2:
        return {"server": raw}
    log(f"⚠️ 代理格式无法解析，将直连: {raw}")
    return None


# ==================== 商品读取 ====================
def load_products():
    if not os.path.exists(PRODUCTS_FILE):
        log(f"❌ 找不到商品文件 {PRODUCTS_FILE}")
        sys.exit(1)
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    products = data.get("p") or []
    log(f"从 {PRODUCTS_FILE} 读取 {len(products)} 个商品")
    return products


def extract_goods_id(product):
    pid = str(product.get("id", ""))
    lu = str(product.get("lu", ""))
    m = re.search(r"(\d{10,})", pid)
    if m:
        return m.group(1)
    m = re.search(r"g-(\d{10,})", lu)
    if m:
        return m.group(1)
    m = re.search(r"goods_id=(\d{10,})", lu)
    if m:
        return m.group(1)
    m = re.search(r"(\d{10,})", lu)
    if m:
        return m.group(1)
    return None


# ==================== __NEXT_DATA__ 提取 ====================
def extract_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def deep_find_price(obj, depth=0, max_depth=7):
    if depth > max_depth:
        return None
    if isinstance(obj, dict):
        gid = obj.get("goods_id")
        if gid:
            price = None
            currency = None
            skus = obj.get("sku_list") or obj.get("sku") or []
            if isinstance(skus, list) and skus and not isinstance(skus[0], (str, int, float)):
                skus = skus[0].get("spec_list") if isinstance(skus[0], dict) and skus[0].get("spec_list") else skus

            for price_field in ("min_normal_price", "sale_price", "normal_price", "price", "min_price", "sku_price"):
                pf = obj.get(price_field)
                if isinstance(pf, dict) and pf.get("amount") is not None:
                    price = pf["amount"]
                    currency = pf.get("currency") or pf.get("symbol") or currency
                    break
                if isinstance(pf, (int, float)) and price is None:
                    price = pf

            sku_prices = []
            if isinstance(skus, list):
                for s in skus:
                    if isinstance(s, dict):
                        p = s.get("price")
                        if isinstance(p, dict):
                            if p.get("amount") is not None:
                                sku_prices.append(float(p["amount"]))
                        elif isinstance(p, (int, float)):
                            sku_prices.append(float(p))

            if price is not None or sku_prices:
                final_price = price
                if final_price is None and sku_prices:
                    final_price = min(sku_prices)
                return {
                    "goods_id": str(gid),
                    "goods_name": obj.get("goods_name") or obj.get("name") or "",
                    "price": float(final_price) if final_price is not None else None,
                    "currency": currency or "USD",
                    "sku_count": len(sku_prices),
                    "sku_prices": sku_prices,
                }

        for v in obj.values():
            r = deep_find_price(v, depth + 1, max_depth)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = deep_find_price(v, depth + 1, max_depth)
            if r:
                return r
    return None


# ==================== 浏览器抓取 ====================
def fetch_product(goods_id, context):
    urls = [
        f"https://www.temu.com/g-{goods_id}.html",
        f"https://www.temu.com/goods.html?goods_id={goods_id}",
    ]
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        for url in urls:
            page = None
            try:
                page = context.new_page()
                log(f"  尝试{attempt}: {url}")

                # 先访问首页建立 cookies 和信任
                if attempt == 1:
                    try:
                        home = context.new_page()
                        home.goto("https://www.temu.com/", wait_until="domcontentloaded", timeout=30000)
                        home.wait_for_timeout(3000)
                        home.close()
                        log("    首页预加载完成")
                    except Exception as e:
                        log(f"    首页预加载失败（非致命）: {e}")

                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # 模拟人类行为：移动鼠标、滚动
                page.mouse.move(random.randint(200, 800), random.randint(200, 600))
                page.wait_for_timeout(random.randint(500, 1500))
                page.mouse.wheel(0, random.randint(100, 400))
                page.wait_for_timeout(random.randint(500, 1500))

                # 循环检测：最多等待 20 秒，看 Cloudflare challenge 是否完成
                html = ""
                for check in range(8):  # 8 * 2.5s = 20s
                    html = page.content()
                    if "__NEXT_DATA__" in html:
                        log(f"    ✓ 第 {check+1} 次检测发现 __NEXT_DATA__")
                        break
                    lower = html.lower()
                    challenge_kw = ["challenge", "captcha", "verification", "cloudflare", "checking your browser"]
                    if any(kw in lower for kw in challenge_kw):
                        log(f"    ⏳ 第 {check+1} 次检测到验证页面，等待 2.5s...")
                        page.wait_for_timeout(2500)
                        # 偶尔滚动一下，模拟人类
                        if random.random() > 0.5:
                            page.mouse.wheel(0, random.randint(50, 150))
                    else:
                        # 没有 challenge 也没有 __NEXT_DATA__，可能是其他页面
                        log(f"    ⏳ 第 {check+1} 次检测，等待页面加载...")
                        page.wait_for_timeout(2500)
                else:
                    # 循环结束仍未找到
                    log(f"    页面加载完成 | {len(html)} bytes")
                    last_err = "页面无 __NEXT_DATA__（Cloudflare 验证超时或失败）"
                    body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
                    preview = (body_m.group(1) if body_m else html)[:300].replace("\n", " ").replace("  ", " ")
                    log(f"    ⚠️ {last_err}")
                    log(f"    📄 HTML 预览: {preview}")
                    for kw in ("challenge", "captcha", "cloudflare", "blocked", "denied", "verification"):
                        if kw in html.lower():
                            log(f"    🔒 检测到拦截关键词: {kw}")
                            break
                    continue

                log(f"    页面加载完成 | {len(html)} bytes")
                data = extract_next_data(html)
                if not data:
                    last_err = "__NEXT_DATA__ 解析失败"
                    log(f"    ⚠️ {last_err}")
                    continue
                result = deep_find_price(data)
                if result and result.get("price") is not None:
                    return result
                last_err = "HTML 中未找到价格字段"
                log(f"    ⚠️ {last_err}")
            except Exception as e:
                last_err = str(e)
                log(f"    ✗ 异常: {e}")
            finally:
                if page:
                    page.close()
        if attempt < MAX_RETRY:
            wait = random.uniform(6, 12)
            log(f"  重试前等待 {wait:.1f}s ...")
            time.sleep(wait)
    log(f"  ❌ 抓取失败: {last_err}")
    return None


# ==================== 数据更新 ====================
def update_data(results):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"p": []}
    else:
        data = {"p": []}

    by_id = {p.get("id"): p for p in data.get("p", [])}

    for r in results:
        pid = f"g-{r['goods_id']}"
        cur = float(r["price"])
        item = by_id.get(pid) or {
            "id": pid,
            "nm": r.get("goods_name", ""),
            "cat": "",
            "lu": f"https://www.temu.com/g-{r['goods_id']}.html",
            "ss": [],
        }
        item["nm"] = r.get("goods_name", item.get("nm", ""))
        item["lu"] = item.get("lu") or f"https://www.temu.com/g-{r['goods_id']}.html"

        prev = item.get("vn")
        if isinstance(prev, (int, float)):
            changed = abs(float(prev) - cur) > 0.001
        else:
            changed = True
        if changed:
            item["ss"] = (item.get("ss") or [])[-4:]
            item["ss"].append({"t": now, "op": prev, "v": cur})
        item["vn"] = cur
        by_id[pid] = item

    data["p"] = list(by_id.values())

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"💾 已更新 {DATA_FILE}（{len(results)} 个商品）")


# ==================== 主流程 ====================
def main():
    log("🚀 Temu 价格监测启动（Playwright + 住宅代理版）")
    proxy = parse_proxy()
    if proxy:
        log(f"🌐 代理已配置: {proxy['server']}")
    else:
        log("⚠️ 未配置代理，将直连（可能被风控）")

    products = load_products()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        context_opts = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if proxy:
            context_opts["proxy"] = proxy

        context = browser.new_context(**context_opts)

        # stealth: 覆盖 navigator 指纹
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [
                {name: "Chrome PDF Plugin", filename: "internal-pdf-viewer"},
                {name: "Chrome PDF Viewer", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai"},
                {name: "Native Client", filename: "internal-nacl-plugin"}
            ]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
            window.chrome = { runtime: {} };
            // 覆盖 Permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({state: Notification.permission})
                    : originalQuery(parameters)
            );
        """)

        results = []
        ok = fail = 0
        for i, product in enumerate(products, 1):
            gid = extract_goods_id(product)
            if not gid:
                log(f"[{i}/{len(products)}] ⚠️ 无法解析商品 ID: {product.get('id')}")
                fail += 1
                continue
            log(f"[{i}/{len(products)}] 抓取 g-{gid}")
            r = fetch_product(gid, context)
            if r:
                results.append(r)
                ok += 1
                sku_note = f"，{r['sku_count']} SKU" if r.get("sku_count") else ""
                log(f"  ✅ {r['goods_name'][:40]} → ${r['price']}{sku_note}")
            else:
                fail += 1
            if i < len(products):
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        context.close()
        browser.close()

    log(f"🏁 完成：成功 {ok} / 失败 {fail}")

    if results:
        update_data(results)
        print("RESULT_JSON=" + json.dumps(results, ensure_ascii=False))
        sys.exit(0 if fail == 0 else 2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
