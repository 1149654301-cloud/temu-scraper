# -*- coding: utf-8 -*-
"""把新商品链接追加到 products.json（手动 Run workflow 时使用）

用法:
    python add_links.py "链接1, 链接2, 链接3"
    python add_links.py "链接1
链接2"

支持 Temu 商品页链接，如:
    https://www.temu.com/g-606767861837588.html
    https://www.temu.com/g-606767861837588.html?srsltid=xxx
    g-606767861837588
"""
import json
import re
import sys
from pathlib import Path

PRODUCTS_FILE = Path(__file__).resolve().parent / "products.json"


def extract_ids(raw: str):
    """从输入文本中提取所有 Temu 商品 ID（g-xxxx 或链接）。"""
    raw = (raw or "").replace("\r", "\n")
    chunks = [c.strip() for c in re.split(r"[\n,，;；\s]+", raw) if c.strip()]
    ids = []
    for chunk in chunks:
        # 直接是 ID
        if re.fullmatch(r"g-\d{6,}", chunk):
            ids.append(chunk)
            continue
        # 是 URL，提取 g-xxxx
        m = re.search(r"(g-\d{6,})", chunk)
        if m:
            ids.append(m.group(1))
            continue
        print(f"  ⚠️ 无法识别，已跳过: {chunk}")
    return ids


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("❌ 没有输入链接")
        sys.exit(1)

    new_ids = extract_ids(sys.argv[1])
    if not new_ids:
        print("❌ 没有提取到有效商品 ID")
        sys.exit(1)

    if not PRODUCTS_FILE.exists():
        data = {"p": []}
    else:
        with open(PRODUCTS_FILE, encoding="utf-8") as f:
            data = json.load(f)

    existing = {p.get("id") for p in data.get("p", [])}
    added = []
    for pid in new_ids:
        if pid in existing:
            print(f"  ℹ️ 已存在，跳过: {pid}")
            continue
        data.setdefault("p", []).append({
            "id": pid,
            "nm": "",
            "vn": "Current Price",
            "vo": True,
            "cat": "",
            "lu": f"https://www.temu.com/{pid}.html",
            "ss": [],
        })
        existing.add(pid)
        added.append(pid)

    if not added:
        print("✅ 没有新链接需要添加（全部已存在）")
        sys.exit(0)

    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ 已添加 {len(added)} 个商品: {', '.join(added)}")
    print(f"当前共 {len(data['p'])} 个商品")


if __name__ == "__main__":
    main()
