# Temu 云端定时抓取器

在 **GitHub Actions 云端服务器**上，每天固定时间自动用真实 Chromium 浏览器打开商品列表里的每一个链接，抓取**完整 SKU 价格**（含颜色/尺寸各组合价格），并写回 jsonblob 数据中心。

**你的电脑可以完全关机** —— 抓取在 GitHub 云端完成。

## 原理

| 组件 | 作用 |
|------|------|
| `scraper.py` | Playwright 驱动 Chromium，复用书签抓取同样的 DOM 逻辑，逐个点击规格组合读取价格 |
| `.github/workflows/daily-scrape.yml` | 定时触发（默认每天北京时间 09:00）+ 支持手动触发 |
| jsonblob | 共享数据中心，读商品列表、写回价格快照 |

与书签抓取（scraper.js）的数据完全一致：每次生成一个价格快照（`ss`），包含所有变体组合的价格，保留最近 3 次快照用于趋势对比。

## 一键部署（需要 GitHub 账号，免费）

### 第 1 步：创建 GitHub 账号（如已有请跳过）

打开 https://github.com/signup ，按提示注册。免费账号即可。

### 第 2 步：创建仓库并上传文件

1. 打开 https://github.com/new ，仓库名填 `temu-scraper`，选择 **Private**（私有），点 Create repository
2. 把本文件夹里的 `scraper.py`、`requirements.txt`、`.github/` 全部上传到仓库：
   - 页面会出现 "uploading an existing file"，点 **uploading an existing file**
   - 把以下文件拖进去：
     - `scraper.py`
     - `requirements.txt`
     - `.github/workflows/daily-scrape.yml`（需先在本地建好 `.github/workflows/` 目录结构再拖）
   - 点 **Commit changes**

> 提示：也可以直接用 `git push`，如果你熟悉 Git。

### 第 3 步：配置登录 Cookie（必须，否则 Temu 要求登录）

GitHub 云端的浏览器是全新环境，未登录时 Temu 商品页会跳转登录页。需要把你本地的登录态"借"给云端：

1. 在**本地已登录 Temu** 的浏览器上安装扩展 **EditThisCookie**
2. 打开任一 Temu 商品页，点扩展图标 → 点**向下箭头（Export）** → **Export as JSON**，复制全部内容
3. 去仓库 **Settings → Secrets and variables → Actions → New repository secret**：
   - Name：`TEMU_COOKIES`
   - Value：粘贴刚才复制的 JSON（可直接粘贴 EditThisCookie 导出格式，脚本会自动转换）
4. 点 **Add secret**

> ⚠️ **Cookie 会过期**（Temu 登录态约 15 天，`AccessToken` 过期后需要重新导出更新）。看到运行日志提示"被重定向到登录页"时，重新导出一次 cookie 并更新该 Secret 即可。
>
> ⚠️ 如果你的运营区不是美国站，登录 cookie 里已包含 `region`/`currency`/`language`，云端会跟随你的站点设置。

### 第 3 步补充：自动通过图片验证码（强烈推荐）

Temu 对数据中心 IP（GitHub 云端服务器）偶尔会弹出「按顺序点击图片」验证码。旧版脚本不会做这道题，会卡在验证界面直到超时（表现为全部失败，artifact 里有 `xxx_timeout.png` 截图）。

本版本已内置**自动过验证码**：检测到验证码 → 截图 → 调用视觉 AI 识别物品位置和点击顺序 → 模拟鼠标按顺序点击。

只需配置一个视觉 API Key（推荐智谱，`glm-4v-flash` 模型有免费额度）：

1. 打开 https://open.bigmodel.cn/ 注册登录
2. 进入「API Keys」页面 → 点「创建 API Key」→ 复制（形如 `xxxxxxxx.xxxxxx`）
3. 去仓库 **Settings → Secrets and variables → Actions → New repository secret**：
   - Name：`VISION_API_KEY`，Value：粘贴刚复制的 Key
   - 可选：`VISION_API_URL`（默认 `https://open.bigmodel.cn/api/paas/v4/chat/completions`，无需添加）
   - 可选：`VISION_MODEL`（默认 `glm-4v-flash`，免费，无需添加）

> 未配置 `VISION_API_KEY` 时行为与旧版一致：遇到验证码会跳过该商品并保留旧数据（日志会提示「未配置 VISION_API_KEY」）。
> 也支持任何 OpenAI 兼容的视觉接口，把 `VISION_API_URL` / `VISION_MODEL` 换成你自己的即可。

**支持的验证码题型**：

✅ **自动处理（点击类）**
- 顺序点击、频率题、排除题、匹配题（详见下方）

⚠️ **当前不支持自动处理（会触发 Refresh 换题，连续 5 次仍抽中则跳过该商品保留旧数据）**
- 拖拽拼图：`Drag the correct pipe(s)... in the direction shown by the arrows`（管道连通题）
- 滑块验证：`Slide the piece to complete the puzzle`
- 拖放配对、`puzzle` / `connect` / `rotate` 等需要 Drag 操作的题型
- 文字点选（如 Click on characters in order）

**4 种点击题型细节**：
- **顺序点击**：`Click on the corresponding images in the following order: 'bicycle', 'dog', 'television'` 或 `按顺序点击 X Y Z`
- **点击最频繁类型**：`Click on the type of fruit that appears most frequently`，先统计再点击所有该类型格子
- **反向/排除题**：`Click on all images that do NOT match the following description: food`，点击所有不符合描述的格子
- **正向匹配题**：`Click on all images that match the following description: vehicle`，点击所有符合描述的格子

> 设计思路：模型直接返回 `click_cells`（最终要按顺序点击的格子编号数组），不论题型如何变化都由同一段点击逻辑执行。如反复失败，把 artifacts 里的 `*_captcha_fail.png` 发我调优 prompt。
> Temu 验证码库持续更新，遇到新题型无法处理时脚本会自动 Refresh 换题重抽，若多次仍抽中不支持的题型则保留旧价格数据并跳过该商品，不影响其他商品。

### 第 4 步：立即验证一次（推荐）

1. 进入仓库 → 点顶部 **Actions** 标签
2. 在左侧列表点 **Temu 每日自动抓取**
3. 点右侧 **Run workflow** → 绿色按钮确认
4. 等几分钟，看运行日志：应看到 `🔑 已注入 19 个 Temu 登录 cookie` 和 `✅ 已更新 N 个变体价格`
5. 打开你的仪表板，刷新即可看到新价格

> 日志里如果出现截图（artifacts）可下载查看，用于排查是否被 Temu 拦截或要求登录。

### 第 5 步：完成

从第 2 步开始，GitHub 会每天自动执行一次抓取（默认北京时间 09:00）。你什么都不用管。

## 修改配置

### 修改抓取时间

编辑 `.github/workflows/daily-scrape.yml` 第 5 行的 cron 表达式（UTC 时间）：

| 北京时间 | UTC | cron |
|---------|-----|------|
| 06:00 | 22:00（前一天） | `0 22 * * *` |
| 09:00 | 01:00 | `0 1 * * *`（默认） |
| 15:00 | 07:00 | `0 7 * * *` |
| 22:00 | 14:00 | `0 14 * * *` |

改完 commit，第二天生效。

### 修改快照运营名

默认运营名是「云端自动」。想改成别的名字，去仓库 **Settings → Secrets and variables → Actions → New repository secret**：

- Name：`SCRAPE_OPERATOR`
- Value：你的运营名

### 修改数据中心地址

一般不需要改（已内置你的 jsonblob 地址）。如需改成别的 blob，添加 secret `JSONBLOB_URL` 即可。

## 本地调试（可选）

也可以在你自己的电脑上跑，用于先验证抓取逻辑：

```bash
pip install -r requirements.txt
python -m playwright install chromium
python scraper.py        # Windows 有显示环境可直接跑
# 或无头模式：
HEADLESS=1 python scraper.py
```

注意：本地跑需要电脑开机；云端部署后无需本地运行。

## 常见问题

**Q: 运行日志显示"页面内容加载超时"或截图显示验证码/Cloudflare 验证？**
Temu 对数据中心 IP 偶尔会弹「按顺序点击图片」验证码。本版本已内置自动过验证码（见上文"第 3 步补充"），配置好 `VISION_API_KEY` 后会自动识别并点击。
如果**没配置** `VISION_API_KEY`，遇到验证码会跳过该商品并保留旧数据。若配置后仍频繁超时：
- 先看 artifact 里的截图确认原因
- 可尝试换执行时间（有时段差异）
- 或联系我调整抓取参数（如增加重试、更换浏览器指纹）

**Q: 运行日志提示"被重定向到登录页"？**
说明 `TEMU_COOKIES` 里的登录态失效了（或没配置）。重新用 EditThisCookie 导出一次 cookie，更新仓库 Settings 里的 `TEMU_COOKIES` Secret 即可。

**Q: 抓到的变体数量比书签少？**
说明部分规格组合在自动抓取时未显示价格（例如缺货、组合无效），与书签行为一致（无价格则跳过）。

**Q: 免费额度够吗？**
GitHub Actions 私有仓库每月免费 2000 分钟。每天抓一次约 5~20 分钟，一个月约 150~600 分钟，远低于免费额度。

**Q: 电脑关机了还能跑吗？**
能。抓取完全在 GitHub 云端运行，与你的电脑无关。
