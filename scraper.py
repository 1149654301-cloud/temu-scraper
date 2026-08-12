#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Temu 价格监测 - ScrapingBee API 版（绕开 Cloudflare Turnstile）

原理：
    本地/服务器直连 Temu 会被 Cloudflare Turnstile 拦截（curl_cffi 模拟指纹、
    Playwright 浏览器、cloudscraper 均失败）。改为调用 ScrapingBee 云服务，
    由它用真实 Chrome + 反爬指纹池渲染页面，返回完整 HTML，我们只解析
    __NEXT_DATA__ 提取价格。

额度（免费 1000 credits，无需信用卡）：
    render_js=true（JS 渲染）          = 5 credits / 次
    render_js + premium_proxy（住宅） = 25 credits / 次（付费计划才可用）
    只有 HTTP 200/404 才扣费，超时/失败不扣费。

环境变量：
    SCRAPINGBEE_API_KEY   必填，ScrapingBee API key
    SCRAPINGBEE_PREMIUM   可选，"true" 时启用住宅代理（更稳，扣 25 credits）
    PRODUCTS_FILE         商品列表文件（默认 products.json）
    DATA_FILE             价格快照输出文件（默认 data.json）
    DELAY_MIN / DELAY_MAX 请求间隔随机范围（秒）
    MAX_RETRY             每个商品最大重试次数
"""

import json
import os
import re
import sys
import time
import random
import datetime

import requests


def env_or(key, default=""):
    return os.environ.get(key, default)


API_KEY = env_or("SCRAPINGBEE_API_KEY", "").strip()
PREMIUM = env_or("SCRAPINGBEE_PREMIUM", "false").lower() == "true"
API_ENDPOINT = "https://app.scrapingbee.com/api/v1/"
PRODUCTS_FILE = env_or("PRODUCTS_FILE", "products.json")
DATA_FILE = env_or("DATA_FILE", "data.json")
DELAY_MIN = float(env_or("DELAY_MIN", "2"))
DELAY_MAX = float(env_or("DELAY_MAX", "5"))
MAX_RETRY = int(env_or("MAX_RETRY", "2"))


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


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


# ==================== ScrapingBee 抓取 ====================
def scrapingbee_fetch(url):
    """调用 ScrapingBee API，返回渲染后的 HTML；失败返回 None。"""
    params = {
        "api_key": API_KEY,
        "url": url,
        "render_js": "true",     # 真实 Chrome 渲染，5 credits
        "wait": "5000",          # 等待 JS 执行完成
        "country_code": "us",    # 美国站
    }
    if PREMIUM:
        params["premium_proxy"] = "true"  # 住宅代理，25 credits，需付费计划
    try:
        resp = requests.get(API_ENDPOINT, params=params, timeout=90)
    except Exception as e:
        log(f"    ✗ ScrapingBee 请求异常: {e}")
        return None

    log(f"    ScrapingBee: HTTP {resp.status_code} | {len(resp.text)} bytes")
    if resp.status_code == 200:
        return resp.text
    if resp.status_code == 402:
        log("    ❌ 402: ScrapingBee 额度不足，请到 app.scrapingbee.com 查看余额")
    elif resp.status_code == 403:
        log("    ❌ 403: ScrapingBee 拒绝了请求（API key 无效或需要 premium 计划）")
        log(f"      响应内容: {resp.text[:200]}")
    else:
        log(f"    ❌ ScrapingBee 错误 {resp.status_code}: {resp.text[:200]}")
    return None


def fetch_product(goods_id):
    urls = [
        f"https://www.temu.com/g-{goods_id}.html",
        f"https://www.temu.com/goods.html?goods_id={goods_id}",
    ]
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        for url in urls:
            html = scrapingbee_fetch(url)
            if not html:
                last_err = "ScrapingBee 返回空/错误"
                continue
            if "__NEXT_DATA__" not in html:
                last_err = "页面无 __NEXT_DATA__（可能被反爬拦截）"
                body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
                preview = (body_m.group(1) if body_m else html)[:400].replace("\n", " ").replace("  ", " ")
                log(f"    ⚠️ {last_err}")
                log(f"    📄 HTML 预览: {preview}")
                for kw in ("challenge", "captcha", "cloudflare", "blocked", "denied", "verification", "turnstile"):
                    if kw in html.lower():
                        log(f"    🔒 检测到拦截关键词: {kw}")
                        break
                continue
            data = extract_next_data(html)
            if not data:
                last_err = "__NEXT_DATA__ 解析失败"
                log(f"    ⚠️ {last_err}")
                continue
            result = deep_find_price(data)
            if result and result.get("price") is not None:
                return result
            top_keys = list(data.keys())[:10]
            pp = data.get("props", {}).get("pageProps", {})
            pp_keys = list(pp.keys())[:15] if isinstance(pp, dict) else []
            log(f"    ⚠️ 未找到价格字段 | 顶层={top_keys} | pageProps={pp_keys}")
            last_err = "HTML 中未找到价格字段"
        if attempt < MAX_RETRY:
            wait = random.uniform(3, 6)
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
    log("🚀 Temu 价格监测启动（ScrapingBee 云渲染版）")

    if not API_KEY:
        log("❌ 未配置 SCRAPINGBEE_API_KEY。请到 app.scrapingbee.com 注册获取后，")
        log("   在 GitHub 仓库 Settings → Secrets → 新建 SCRAPINGBEE_API_KEY")
        sys.exit(1)
    mode = "JS 渲染（5 credits/次）" if not PREMIUM else "JS 渲染 + 住宅代理（25 credits/次）"
    log(f"🔑 API key 已配置 | 模式: {mode}")

    products = load_products()

    results = []
    ok = fail = 0
    for i, product in enumerate(products, 1):
        gid = extract_goods_id(product)
        if not gid:
            log(f"[{i}/{len(products)}] ⚠️ 无法解析商品 ID: {product.get('id')}")
            fail += 1
            continue
        log(f"[{i}/{len(products)}] 抓取 g-{gid}")
        r = fetch_product(gid)
        if r:
            results.append(r)
            ok += 1
            sku_note = f"，{r['sku_count']} SKU" if r.get("sku_count") else ""
            log(f"  ✅ {r['goods_name'][:40]} → ${r['price']}{sku_note}")
        else:
            fail += 1
        if i < len(products):
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    log(f"🏁 完成：成功 {ok} / 失败 {fail}")

    if results:
        update_data(results)
        print("RESULT_JSON=" + json.dumps(results, ensure_ascii=False))
        sys.exit(0 if fail == 0 else 2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
