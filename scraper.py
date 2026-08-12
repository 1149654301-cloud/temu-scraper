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
  JSONBLOB_URL    jsonblob 数据中心地址（默认使用 temu-tracker-v3 的共享 blob）
  SCRAPE_OPERATOR 本次快照的运营名（默认 "云端自动"）
  HEADLESS        1=无头模式（CI 建议配合 xvfb 用有头模式），默认 0 有头
  DELAY_SEC       商品之间的间隔秒数（默认 5，防限流）
  MAX_RETRY       单个商品抓取失败重试次数（默认 3）
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

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def log(msg):
    print(msg, flush=True)


def now_str():
    now = datetime.now(timezone(timedelta(hours=8)))
    # 与浏览器 toLocaleString('zh-CN') 一致的格式：2026/8/12 09:30:45
    return f"{now.year}/{now.month}/{now.day} {now.hour:02d}:{now.minute:02d}:{now.second:02d}"


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


def wait_for_content(page, timeout=90):
    """等待 Cloudflare 挑战通过、页面出现可抓取的数据。返回 (mode, data)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = page.evaluate(RAW_JS)
            if raw and raw.get("variants"):
                return "raw", raw["variants"]

            has_radios = page.evaluate(
                "() => document.querySelectorAll('div[role=\"radio\"]').length > 0"
            )
            if has_radios:
                return "click", None

            # 页面出现明显反爬拦截时截图留证
            body_txt = page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")
            if 'Access Denied' in body_txt or 'attention' in body_txt.lower():
                page.screenshot(path="artifacts/blocked.png")
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

        for i, prod in enumerate(products):
            pid = prod.get("id")
            if not pid:
                continue
            log(f"\n[{i + 1}/{len(products)}] 商品 {pid}")
            result = scrape_product(page, pid)
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
