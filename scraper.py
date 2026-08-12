#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Temu 价格监测 - Apify 云抓取版（最终稳定方案）

原理：
    Temu 商品页是 SPA 纯客户端渲染，HTML 中不包含价格数据（已实测确认）。
    价格只能通过内部 API 获取，而 API 需要 anti_content 签名（逆向脆弱且易失效）。
    本方案改为调用 Apify 现成的 Temu Products Scraper actor：
      它用 Playwright stealth + 住宅代理 + 逆向签名，输入商品链接即可返回价格，
      反爬问题全部由它处理，不依赖本仓库任何破解代码。

费用（Apify 免费注册送 $5 credit，够测试）：
    goat255/temu-products-scraper: $7 / 1000 条商品
    3 个商品每轮 ≈ $0.02，每 3 小时一轮 ≈ $0.17/天，约 $5/月

环境变量：
    APIFY_API_TOKEN   必填，Apify API token（console.apify.com → Settings → Integrations）
    PRODUCTS_FILE     商品列表文件（默认 products.json）
    DATA_FILE         价格快照输出文件（默认 data.json）
"""

import json
import os
import sys
import datetime

import requests


def env_or(key, default=""):
    return os.environ.get(key, default)


API_TOKEN = env_or("APIFY_API_TOKEN", "").strip()
ACTOR_ID = "goat255~temu-products-scraper"
API_URL = f"https://api.apify.com/v2/actors/{ACTOR_ID}/run-sync-get-dataset-items"
PRODUCTS_FILE = env_or("PRODUCTS_FILE", "products.json")
DATA_FILE = env_or("DATA_FILE", "data.json")


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
    """从商品记录的 id/lu 字段提取纯数字商品 ID"""
    import re
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


# ==================== Apify 抓取 ====================
def fetch_prices(goods_ids):
    """调用 Apify actor，返回 {goods_id: price_record} 映射"""
    payload = {
        "productUrls": goods_ids,
        "concurrency": 3,
        "proxyConfig": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
    }
    log(f"🚀 调用 Apify actor: {ACTOR_ID}")
    log(f"   商品: {goods_ids}")
    try:
        resp = requests.post(
            API_URL,
            params={"token": API_TOKEN, "timeout": 180},
            json=payload,
            timeout=200,
        )
    except Exception as e:
        log(f"❌ Apify 请求异常: {e}")
        return {}

    if resp.status_code == 200:
        try:
            items = resp.json()
            log(f"✅ Apify 返回 {len(items)} 条数据")
            return items
        except Exception as e:
            log(f"❌ 响应解析失败: {e} | {resp.text[:300]}")
            return {}
    elif resp.status_code == 401 or resp.status_code == 403:
        log("❌ Apify 认证失败：API token 无效。请检查 APIFY_API_TOKEN secret")
        log(f"   {resp.text[:300]}")
    elif resp.status_code == 429:
        log("❌ Apify 额度/频率超限：免费额度可能用完，或运行过于频繁")
    else:
        log(f"❌ Apify 错误 {resp.status_code}: {resp.text[:300]}")
    return {}


def parse_apify_result(items, goods_ids):
    """把 Apify 返回的数据转成统一结果列表"""
    results = []
    # 按商品 ID 索引
    by_id = {}
    for it in items:
        gid = str(it.get("id") or "")
        if gid:
            by_id[gid] = it

    for gid in goods_ids:
        it = by_id.get(gid)
        if not it:
            log(f"  ⚠️ g-{gid}: Apify 未返回数据（可能链接失效或抓取失败）")
            continue
        price = it.get("price") or it.get("priceMin")
        if price is None:
            log(f"  ⚠️ g-{gid}: 返回了数据但没有价格: {json.dumps(it, ensure_ascii=False)[:200]}")
            continue
        variants = it.get("variants") or []
        sku_prices = [float(v["price"]) for v in variants if v.get("price") is not None]
        results.append({
            "goods_id": gid,
            "goods_name": it.get("title") or "",
            "price": float(price),
            "currency": it.get("currency") or "USD",
            "sku_count": len(sku_prices),
            "sku_prices": sku_prices,
        })
        log(f"  ✅ {it.get('title', '')[:40]} → ${price}")
    return results


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
    log("🚀 Temu 价格监测启动（Apify 云抓取版）")

    if not API_TOKEN:
        log("❌ 未配置 APIFY_API_TOKEN。请到 console.apify.com 注册获取后，")
        log("   在 GitHub 仓库 Settings → Secrets → 新建 APIFY_API_TOKEN")
        sys.exit(1)

    products = load_products()

    # 提取商品 ID
    goods_ids = []
    for product in products:
        gid = extract_goods_id(product)
        if gid:
            goods_ids.append(gid)
        else:
            log(f"⚠️ 无法解析商品 ID: {product.get('id')}")
    if not goods_ids:
        log("❌ 没有可抓取的商品")
        sys.exit(1)
    log(f"共 {len(goods_ids)} 个商品")

    items = fetch_prices(goods_ids)
    if not items:
        sys.exit(1)

    results = parse_apify_result(items, goods_ids)
    log(f"🏁 完成：成功 {len(results)} / 共 {len(goods_ids)}")

    if results:
        update_data(results)
        print("RESULT_JSON=" + json.dumps(results, ensure_ascii=False))
        sys.exit(0 if len(results) == len(goods_ids) else 2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
