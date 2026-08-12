#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Temu 价格监测 - curl_cffi 版（无需浏览器 / 登录 / cookie / 验证码）

原理：
    Temu 是 Next.js 网站，商品页 HTML 内嵌 <script id="__NEXT_DATA__"> JSON，
    其中包含完整价格 / SKU / 名称等数据，服务端直接渲染，无需执行 JS。
    用 curl_cffi 模拟 Chrome 的 TLS 指纹 + 住宅代理访问商品页，
    即可直接提取价格，全程不需要浏览器。

依赖：curl_cffi（模拟 TLS 指纹）+ 住宅代理（避免数据中心 IP 被风控）

环境变量：
    PROXY_URL    代理地址，如 http://user:pass@host:port（强烈建议配置住宅代理）
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
from curl_cffi import requests as cffi_requests


# ==================== 配置 ====================
def env_or(key, default=""):
    return os.environ.get(key, default)


PROXY_URL = env_or("PROXY_URL", "")
PRODUCTS_FILE = env_or("PRODUCTS_FILE", "products.json")
DATA_FILE = env_or("DATA_FILE", "data.json")
DELAY_MIN = float(env_or("DELAY_MIN", "2"))
DELAY_MAX = float(env_or("DELAY_MAX", "5"))
MAX_RETRY = int(env_or("MAX_RETRY", "3"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.temu.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ==================== 代理 ====================
def build_proxies():
    if not PROXY_URL:
        log("⚠️ 未配置 PROXY_URL，将直连访问（数据中心 IP 极易被风控，强烈建议配置住宅代理）")
        return None
    raw = PROXY_URL
    if "://" not in raw:
        raw = "http://" + raw
    log(f"代理已启用")
    return {"http": raw, "https": raw}


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
    """从商品 id 或 lu 链接中提取纯数字 goods_id"""
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
    """递归搜索 __NEXT_DATA__，找到含 goods_id 与价格字段的商品对象。

    返回 dict：{goods_id, goods_name, price, currency, sku_count, sku_prices}
    找不到返回 None。
    """
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

            # 常见价格字段
            for price_field in ("min_normal_price", "sale_price", "normal_price", "price", "min_price", "sku_price"):
                pf = obj.get(price_field)
                if isinstance(pf, dict) and pf.get("amount") is not None:
                    price = pf["amount"]
                    currency = pf.get("currency") or pf.get("symbol") or currency
                    break
                if isinstance(pf, (int, float)) and price is None:
                    price = pf

            # 从 SKU 列表收集价格（拿最低的）
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

        # 继续向下递归
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


# ==================== 抓取单个商品 ====================
def fetch_product(goods_id, proxies):
    urls = [
        f"https://www.temu.com/g-{goods_id}.html",
        f"https://www.temu.com/goods.html?goods_id={goods_id}",
    ]
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        for url in urls:
            try:
                resp = cffi_requests.get(
                    url,
                    headers=HEADERS,
                    proxies=proxies,
                    impersonate="chrome120",
                    timeout=45,
                    allow_redirects=True,
                )
                log(f"  尝试{attempt}: HTTP {resp.status_code} | {len(resp.text)} bytes")
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}"
                    continue
                html = resp.text
                if "__NEXT_DATA__" not in html:
                    last_err = "页面无 __NEXT_DATA__（可能被反爬拦截或需人工验证）"
                    log(f"    ⚠️ {last_err}")
                    continue
                data = extract_next_data(html)
                if not data:
                    last_err = "__NEXT_DATA__ 解析失败"
                    log(f"    ⚠️ {last_err}")
                    continue
                result = deep_find_price(data)
                if result and result.get("price") is not None:
                    return result
                # 诊断信息：输出顶层结构，帮助定位
                top_keys = list(data.keys())[:10]
                pp = data.get("props", {}).get("pageProps", {})
                pp_keys = list(pp.keys())[:15] if isinstance(pp, dict) else []
                init_state = pp.get("initialState") if isinstance(pp, dict) else None
                is_keys = list(init_state.keys())[:20] if isinstance(init_state, dict) else []
                log(f"    ⚠️ 未找到价格字段 | 顶层={top_keys} | pageProps={pp_keys}")
                if is_keys:
                    log(f"    initialState keys={is_keys}")
                last_err = "HTML 中未找到价格字段"
            except Exception as e:
                last_err = str(e)
                log(f"    ✗ 请求异常: {e}")
        if attempt < MAX_RETRY:
            wait = random.uniform(6, 12)
            log(f"  重试前等待 {wait:.1f}s ...")
            time.sleep(wait)
    log(f"  ❌ 抓取失败: {last_err}")
    return None


# ==================== 数据更新 ====================
def update_data(results):
    """把本次抓取结果写入 data.json（保留历史快照，最多 5 条）"""
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
    log("🚀 Temu 价格监测启动（curl_cffi 版，无需浏览器）")
    proxies = build_proxies()
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
        r = fetch_product(gid, proxies)
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
        # 输出结果 JSON 供下游解析
        print("RESULT_JSON=" + json.dumps(results, ensure_ascii=False))
        sys.exit(0 if fail == 0 else 2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
