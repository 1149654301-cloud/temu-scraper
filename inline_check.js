
// ============== 全局状态 ==============
let products = [];
let currentProduct = null;
let currentOperator = null; // null = 运营列表; string = 某运营的商品列表
let viewMode = 'matrix'; // matrix | list

// GitHub 数据容器 (仪表盘只读，写入通过 upload.html 的 GitHub Contents API)
const BLOB_URL = 'https://raw.githubusercontent.com/1149654301-cloud/temu-scraper/main/data.json';
const SCRAPER_INLINE = "(async()=>{\n  const TOKEN_KEY='temu_tracker_gh_token';\n\n  const match=location.href.match(/g-(\\d+)\\.html/);\n  const pid=match?match[1]:'unknown';\n  let product_name=document.querySelector('h1')?.textContent?.trim()||document.title?.split(' - ')[0]?.trim()||pid;\n  const sleep=ms=>new Promise(r=>setTimeout(r,ms));\n  let operator=typeof OPERATOR_NAME!=='undefined'?OPERATOR_NAME:null;\n\n  // ===== UI =====\n  if(document.getElementById('temu_scraper_overlay'))return;\n  const ov=document.createElement('div');\n  ov.id='temu_scraper_overlay';\n  ov.style.cssText='position:fixed;top:10px;right:10px;width:420px;background:#1a1a2e;color:#fff;border-radius:12px;padding:16px;z-index:999999;font-family:system-ui,sans-serif;box-shadow:0 8px 32px rgba(0,0,0,.4);font-size:14px;line-height:1.5;max-height:90vh;overflow-y:auto';\n  ov.innerHTML=`\n    <div style=\"display:flex;justify-content:space-between;align-items:center;margin-bottom:12px\">\n      <span id=\"ts_title\" style=\"font-size:16px;font-weight:700\">🔍 Temu 价格抓取</span>\n      <span id=\"ts_close\" style=\"cursor:pointer;opacity:.5;font-size:18px\" onmouseover=\"this.style.opacity=1\" onmouseout=\"this.style.opacity=.5\">✕</span>\n    </div>\n    <div id=\"ts_body\"></div>\n    <div id=\"ts_progress\" style=\"display:none;\">\n      <div id=\"ts_msg\" style=\"color:#aaa;margin-bottom:8px;margin-top:8px\"></div>\n      <div style=\"height:6px;background:#333;border-radius:3px;overflow:hidden;margin-bottom:8px\">\n        <div id=\"ts_bar\" style=\"height:100%;width:0;background:linear-gradient(90deg,#4CAF50,#8BC34A);transition:width .3s;border-radius:3px\"></div>\n      </div>\n      <div id=\"ts_log\" style=\"max-height:150px;overflow-y:auto;font-size:12px;color:#888;line-height:1.6;word-break:break-all\"></div>\n    </div>\n  `;\n  document.body.appendChild(ov);\n  document.getElementById('ts_close').onclick=()=>ov.remove();\n\n  const body=document.getElementById('ts_body');\n  const progress=document.getElementById('ts_progress');\n  const M=s=>{const e=document.getElementById('ts_msg');if(e)e.textContent=s;};\n  const P=p=>{const e=document.getElementById('ts_bar');if(e)e.style.width=p+'%';};\n  const L=s=>{const e=document.getElementById('ts_log');if(e){e.innerHTML+=s+'<br>';e.scrollTop=e.scrollHeight;}};\n\n  const status=(msg,color='#aaa')=>{\n    body.innerHTML+=`<div style=\"margin:6px 0;font-size:13px;color:${color}\">${msg}</div>`;\n    body.scrollTop=body.scrollHeight;\n  };\n\n  // ===== 名字输入（未预置时） =====\n  if(!operator){\n    body.innerHTML=`\n      <div style=\"margin-bottom:14px;\">\n        <label style=\"display:block;font-size:13px;color:#aaa;margin-bottom:6px;\">商品ID</label>\n        <div style=\"padding:8px 12px;background:#2d2d44;border-radius:6px;font-size:13px;color:#8BC34A;font-family:monospace\">${pid}</div>\n      </div>\n      <div style=\"margin-bottom:14px;\">\n        <label style=\"display:block;font-size:13px;color:#aaa;margin-bottom:6px;\">请输入您的名字（运营名）</label>\n        <input id=\"ts_operator\" type=\"text\" placeholder=\"例如：张三\" style=\"width:100%;padding:10px 12px;background:#2d2d44;border:1px solid #4a4a6a;border-radius:6px;color:#fff;font-size:14px;outline:none;box-sizing:border-box\" autofocus>\n      </div>\n      <button id=\"ts_start\" style=\"width:100%;padding:10px;background:linear-gradient(135deg,#e87400,#ff8c00);color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer\">🚀 开始抓取</button>\n    `;\n    operator=await new Promise(resolve=>{\n      const go=()=>{\n        const name=document.getElementById('ts_operator').value.trim();\n        if(!name){alert('请输入您的名字');return;}\n        resolve(name);\n      };\n      document.getElementById('ts_start').onclick=go;\n      document.getElementById('ts_operator').onkeydown=e=>{if(e.key==='Enter')go();};\n    });\n  }\n\n  document.getElementById('ts_title').textContent='🔍 '+operator+' 正在抓取...';\n  body.style.display='none';\n  progress.style.display='block';\n  M('初始化中...');\n\n  // ===== 抓取逻辑 =====\n  let results=[];\n  try{\n    M('检测规格组...');\n    const radios=document.querySelectorAll('div[role=\"radio\"]');\n    if(!radios.length){M('❌ 未找到规格选项，请确认页面已完全加载');return;}\n\n    const gm=new Map();\n    radios.forEach(r=>{\n      const ancestor=r.parentElement?.parentElement;\n      if(!ancestor)return;\n      if(!gm.has(ancestor))gm.set(ancestor,[]);\n      gm.get(ancestor).push(r);\n    });\n\n    const labels=['Color','Size','Quantity','Style','Pattern','Material','Type','Version','Model'];\n    const groups=[];let gi=0;\n    gm.forEach((rs,a)=>{\n      const t=a.textContent||'';let n='Spec-'+(++gi);\n      for(const l of labels){if(t.includes(l)){n=l;break;}}\n      groups.push({name:n,options:rs.map(r=>r.getAttribute('aria-label')).filter(Boolean)});\n    });\n    const validGroups=groups.filter(g=>g.options.length>0);\n    if(!validGroups.length){M('❌ 规格组为空');return;}\n    L('规格组: '+validGroups.map(g=>g.name+'('+g.options.length+')').join(', '));\n\n    const combos=[];\n    (function gen(i,cur){if(i===validGroups.length){combos.push([...cur]);return;}for(const o of validGroups[i].options){cur.push(o);gen(i+1,cur);cur.pop();}})(0,[]);\n    L('共 '+combos.length+' 个组合');\n    M('开始抓取 '+combos.length+' 个组合...');\n    P(0);\n\n    const readPrices=()=>{\n      let prices=[...document.querySelectorAll('._14At0Pe5')].map(e=>e.textContent.trim()).filter(t=>t.startsWith('$'));\n      if(!prices.length){\n        const seen=new Set();\n        document.querySelectorAll('span,div,p').forEach(el=>{\n          const t=(el.innerText||'').trim();\n          if(t&&/^\\$\\s?\\d/.test(t)&&t.length<20&&!seen.has(t)){seen.add(t);prices.push(t);}\n        });\n        prices=prices.slice(0,4);\n      }\n      return prices;\n    };\n    const priceSig=()=>readPrices().join('|');\n    const waitStable=async(timeout)=>{\n      const t0=Date.now();let last=priceSig();let stable=0;\n      while(Date.now()-t0<timeout){await sleep(200);const cur=priceSig();if(cur===last){stable++;if(stable>=2)return;}else{stable=0;last=cur;}}\n    };\n    const clickOpt=async(opt)=>{\n      const el=document.querySelector('div[aria-label=\"'+opt.replace(/\"/g,'\\\\\"')+'\"]');\n      if(!el)return false;\n      if(el.getAttribute('aria-checked')==='true')return false;\n      el.click();await sleep(250);return true;\n    };\n\n    let prevCombo=null;\n    for(let i=0;i<combos.length;i++){\n      const combo=combos[i];const name=combo.join(' / ');\n      let start=0;\n      if(prevCombo){while(start<combo.length&&prevCombo[start]===combo[start])start++;}\n      for(let k=start;k<combo.length;k++)await clickOpt(combo[k]);\n      await waitStable(2500);\n      const prices=readPrices();let cur=null,orig=null;\n      if(prices.length>=2){orig=parseFloat(prices[0].replace(/[$,]/g,''));cur=parseFloat(prices[1].replace(/[$,]/g,''));}\n      else if(prices.length===1){cur=parseFloat(prices[0].replace(/[$,]/g,''));}\n      if(cur===null){prevCombo=combo;P(Math.round((i+1)/combos.length*85));continue;}\n      results.push({n:name,p:cur,o:orig});\n      P(Math.round((i+1)/combos.length*85));\n      if(i%5===0||i===combos.length-1)L('['+(i+1)+'/'+combos.length+'] '+name+' → $'+cur);\n      prevCombo=combo;\n    }\n\n    if(!results.length){M('❌ 未抓取到任何有效价格');return;}\n    L('抓取完成，准备上传...');\n    M('准备上传...');\n    P(90);\n  }catch(e){\n    M('❌ '+e.message);L(e.message);return;\n  }\n\n  // ===== Token 检查 / 输入 =====\n  let token=localStorage.getItem(TOKEN_KEY);\n  if(!token){\n    body.style.display='block';\n    progress.style.display='none';\n    body.innerHTML=`\n      <div style=\"margin-bottom:14px;color:#ff8c00;font-size:13px\">首次使用或 Token 失效，需要 GitHub Token 才能自动写入数据</div>\n      <div style=\"margin-bottom:14px;\">\n        <label style=\"display:block;font-size:13px;color:#aaa;margin-bottom:6px;\">GitHub Token（需要 repo 权限）</label>\n        <input id=\"ts_token_input\" type=\"text\" placeholder=\"ghp_xxxxxxxx\" style=\"width:100%;padding:10px 12px;background:#2d2d44;border:1px solid #4a4a6a;border-radius:6px;color:#fff;font-size:14px;outline:none;box-sizing:border-box\" autofocus>\n      </div>\n      <button id=\"ts_save_token\" style=\"width:100%;padding:10px;background:linear-gradient(135deg,#e87400,#ff8c00);color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer\">💾 保存 Token</button>\n      <div style=\"text-align:center;margin-top:8px;font-size:11px;color:#666\">Token 只保存在当前浏览器本地</div>\n    `;\n    token=await new Promise(resolve=>{\n      const save=()=>{\n        const val=document.getElementById('ts_token_input').value.trim();\n        if(!val){alert('Token 不能为空');return;}\n        localStorage.setItem(TOKEN_KEY,val);\n        resolve(val);\n      };\n      document.getElementById('ts_save_token').onclick=save;\n      document.getElementById('ts_token_input').onkeydown=e=>{if(e.key==='Enter')save();};\n    });\n    body.style.display='none';\n    progress.style.display='block';\n    M('Token 已保存，开始上传...');\n  }\n\n  // ===== GitHub API 上传（通过 iframe 代理绕过 Temu CSP）=====\n  try{\n    const now=new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});\n    const payload={\n      pid:pid,\n      product_name:product_name,\n      operator:operator,\n      snapshot:{t:now,v:results.map(r=>({n:r.n,p:r.p,o:r.o}))}\n    };\n\n    M('准备上传...');P(95);\n    const proxyUrl='https://1149654301-cloud.github.io/temu-scraper/proxy.html';\n    const iframe=document.createElement('iframe');\n    iframe.style.cssText='position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;border:none;z-index:-1';\n    iframe.src=proxyUrl;\n    document.body.appendChild(iframe);\n\n    const result=await new Promise((resolve,reject)=>{\n      const timer=setTimeout(()=>{\n        window.removeEventListener('message',onMessage);\n        reject(new Error('iframe 代理超时，请重试'));\n      },30000);\n      const onMessage=e=>{\n        if(e.origin!=='https://1149654301-cloud.github.io')return;\n        if(e.data&&e.data.type==='temu_proxy_result'){\n          clearTimeout(timer);\n          window.removeEventListener('message',onMessage);\n          resolve(e.data);\n        }\n      };\n      window.addEventListener('message',onMessage);\n      iframe.onload=()=>{\n        iframe.contentWindow.postMessage({type:'temu_upload',token:token,payload:payload},'https://1149654301-cloud.github.io');\n      };\n      // 部分浏览器 onload 不触发或触发晚，再发一次\n      setTimeout(()=>{\n        if(iframe.contentWindow){\n          iframe.contentWindow.postMessage({type:'temu_upload',token:token,payload:payload},'https://1149654301-cloud.github.io');\n        }\n      },800);\n    });\n\n    try{iframe.remove();}catch(e){}\n    if(result.status==='ok'){\n      M('✅ 上传成功');P(100);L('数据已写入 GitHub，5 分钟后刷新仪表盘可见');\n    }else{\n      const msg=result.message||'未知错误';\n      if(msg.includes('Token')||msg.includes('401')){localStorage.removeItem(TOKEN_KEY);}\n      M('❌ 上传失败: '+msg);L(msg);\n    }\n  }catch(e){\n    M('❌ 上传异常: '+e.message);L(e.message);\n  }\n})();";

// ============== 数据加载 (从 jsonblob 读取紧凑格式并展开) ==============

function expandCompact(compact) {
  // 新格式: {id, nm, vn, vo, cat, lu, ss:[{t, op, v:[prices]}]}
  // 旧格式: {id, n, cn, cat, lu, ss:[{t, operator, v:[{n,p,o}]}]}
  return (compact.p || []).map(cp => ({
    product_id: cp.id,
    product_name: cp.nm || cp.n || (cp.id !== 'unknown' ? cp.id : ''),
    product_name_cn: cp.cn || '',
    category: cp.cat || '',
    last_updated: cp.lu || '',
    snapshots: (cp.ss || []).map(s => ({
      captured_at: s.t,
      operator: s.op !== undefined ? s.op : (s.operator || ''),
      // 新格式: v is price array, o from product-level vo
      // 旧格式: v is array of {n, p, o} objects
      variants: Array.isArray(s.v) && s.v.length > 0 && s.v[0] !== null && typeof s.v[0] === 'object'
        ? s.v.map(v => ({ variant_name: v.n, current_price: v.p, original_price: v.o }))
        : (cp.vn || []).map((name, i) => ({
            variant_name: name,
            current_price: (s.v || [])[i],
            original_price: (s.o || cp.vo || [])[i]
          }))
    }))
  }));
}

const CACHE_KEY = 'temu_tracker_cache';
const CACHE_TS_KEY = 'temu_tracker_ts';
const MAX_RETRIES = 3;

function showError(badge, msg) {
  badge.innerHTML = '<span style="color:var(--orange);font-weight:700">⚠️ ' + msg + ' — 点击刷新重试</span>';
}

function applyData(compact, badge, silent) {
  products = expandCompact(compact);
  currentProduct = products.find(p => p.product_id === (currentProduct && currentProduct.product_id)) || null;
  renderMainList();
  if (currentProduct) renderDetail();
  const ops = buildOperatorGroups();
  const now = new Date();
  badge.innerHTML = (silent ? '🔄 ' : '') + '最后更新: ' + now.toLocaleTimeString('zh-CN') + ' | 共 ' + ops.length + ' 位运营 · ' + products.length + ' 个商品';
  // 保存缓存
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(compact)); localStorage.setItem(CACHE_TS_KEY, now.toISOString()); } catch(e) {}
}

async function refreshData() {
  const badge = document.getElementById('lastUpdate');
  badge.innerHTML = '<span class="badge badge-updating">加载中...</span>';

  // 1. 优先从 localStorage 加载（毫秒级，永不出错）
  const cached = localStorage.getItem(CACHE_KEY);
  const cachedTs = localStorage.getItem(CACHE_TS_KEY);
  let cacheUsed = false;
  if (cached) {
    try {
      const compact = JSON.parse(cached);
      if (compact && compact.p) {
        applyData(compact, badge, true);
        cacheUsed = true;
      }
    } catch(e) {}
  }

  // 2. 从 jsonblob 拉取（最多重试 3 次，递增间隔）
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      if (attempt > 0) {
        badge.innerHTML = '<span class="badge badge-updating">重试中 (' + (attempt + 1) + '/3)...</span>';
        await new Promise(r => setTimeout(r, 1500 * attempt));
      }
      const resp = await fetch(BLOB_URL, { signal: AbortSignal.timeout(10000) });
      if (resp.ok) {
        const compact = await resp.json();
        if (compact && compact.p) {
          applyData(compact, badge, false);
          return;
        }
      }
      lastErr = 'HTTP ' + resp.status;
    } catch(e) {
      lastErr = e.message;
    }
  }

  // 3. 全失败：有缓存继续用，没缓存提示可操作
  if (cacheUsed) {
    const ts = cachedTs ? new Date(cachedTs).toLocaleString('zh-CN') : '未知';
    showError(badge, '云端数据暂不可用，显示本地数据 (' + ts + ')');
  } else {
    badge.innerHTML = '<span style="color:var(--orange);font-weight:700">⚠️ 网络不稳定 (重试3次失败: ' + lastErr + ') — </span><a href="javascript:refreshData()" style="color:var(--accent);font-weight:700;text-decoration:underline">点击重试</a>';
  }
}

// ============== 层级列表：运营 → 商品 ==============

function buildOperatorGroups() {
  const ops = new Map();
  // 初始化所有运营（包括"测试"，即使没有数据也显示）
  OPERATOR_NAMES.forEach(name => {
    ops.set(name, { name, productIds: new Map(), snapCount: 0, lastActive: '' });
  });
  ops.set('测试', { name: '测试', productIds: new Map(), snapCount: 0, lastActive: '' });
  // 合并实际数据
  products.forEach(p => {
    (p.snapshots || []).forEach(s => {
      let name = s.operator || '';
      if (!name) name = '测试';
      // 如果运营名不在列表中，也加入
      if (!ops.has(name)) ops.set(name, { name, productIds: new Map(), snapCount: 0, lastActive: '' });
      const o = ops.get(name);
      if (!o.productIds.has(p.product_id)) {
        o.productIds.set(p.product_id, p);
      }
      o.snapCount++;
      if (!o.lastActive || s.captured_at > o.lastActive) o.lastActive = s.captured_at;
    });
  });
  // 按名字排序：固定列表在前，测试在最后
  const list = [...ops.values()];
  const fixed = list.filter(o => OPERATOR_NAMES.includes(o.name));
  const extra = list.filter(o => !OPERATOR_NAMES.includes(o.name));
  return [...fixed, ...extra];
}

function renderMainList() {
  const container = document.getElementById('productList');
  const title = document.getElementById('listTitle');
  const count = document.getElementById('productCount');

  if (!products.length) {
    title.innerHTML = '📦 商品列表 <span style="font-size:12px;color:var(--text-secondary);"></span>';
    container.innerHTML = '<div class="empty-state"><div class="icon">📭</div><p>还没有商品数据<br>点击上方"抓取新商品"开始</p></div>';
    return;
  }

  if (currentOperator) {
    // ===== 层级2：某个运营的商品列表 =====
    title.innerHTML = '📦 商品列表 <span style="font-size:12px;color:var(--text-secondary);" id="productCount"></span>';
    const ops = buildOperatorGroups();
    const op = ops.find(o => o.name === currentOperator);
    const opProducts = op ? [...op.productIds.values()] : [];
    document.getElementById('productCount').textContent = `共 ${opProducts.length} 个`;

    let html = `<span class="breadcrumb" onclick="goBackToOperators()">← 返回运营列表</span>
      <div style="font-size:14px;font-weight:600;margin-bottom:12px;color:var(--text-secondary);">
        <span class="op-avatar" style="width:28px;height:28px;font-size:13px">${currentOperator[0]}</span>
        ${currentOperator} 的商品
      </div>`;
    if (!opProducts.length) {
      html += '<div class="empty-state" style="padding:24px"><p>该运营还没有商品</p></div>';
    } else {
      opProducts.forEach(p => {
        const latest = p.snapshots && p.snapshots.length ? p.snapshots[0] : null;
        const validPrices = latest ? latest.variants.map(v => v.current_price).filter(x => x != null && isFinite(x)) : [];
        const minPrice = validPrices.length ? Math.min(...validPrices) : null;
        const maxPrice = validPrices.length ? Math.max(...validPrices) : null;
        const isActive = currentProduct && currentProduct.product_id === p.product_id;
        html += `<div class="product-item ${isActive ? 'active' : ''}" onclick="selectProduct('${p.product_id}')">
          <div class="name">${p.product_name_cn || p.product_name || p.product_id}</div>
          <div class="meta">${latest ? latest.variants.length : 0} 个变体 · ${minPrice != null ? '$'+minPrice.toFixed(2)+' ~ $'+maxPrice.toFixed(2) : '暂无价格'} · ${p.snapshots.length} 次抓取</div>
        </div>`;
      });
    }
    container.innerHTML = html;
  } else {
    // ===== 层级1：运营列表 =====
    title.innerHTML = '👥 运营列表 <span style="font-size:12px;color:var(--text-secondary);" id="productCount"></span>';
    const ops = buildOperatorGroups();
    document.getElementById('productCount').textContent = `共 ${ops.length} 位`;

    if (!ops.length) {
      container.innerHTML = '<div class="empty-state" style="padding:24px"><p>暂无运营数据</p></div>';
      return;
    }

    const avatarColors = ['#0984e3,#6c5ce7','#e17055,#d63031','#00b894,#00cec9','#fdcb6e,#e87400','#fd79a8,#e84393','#6c5ce7,#a29bfe','#636e72,#2d3436','#00cec9,#81ecec'];
    container.innerHTML = ops.map((op, i) => {
      const initials = op.name[0].toUpperCase();
      const color = avatarColors[i % avatarColors.length];
      const productCount = op.productIds.size;
      const lastTime = op.lastActive ? op.lastActive.replace(' ', '<br>') : '从未';
      return `<div class="operator-item" onclick="drillIntoOperator('${op.name.replace(/'/g, "\\'")}')">
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <div>
            <span class="op-avatar" style="background:linear-gradient(135deg,${color})">${initials}</span>
            <span class="op-name">${op.name}</span>
          </div>
          <div style="text-align:right;">
            <div style="font-size:18px;font-weight:700;color:var(--accent)">${productCount}</div>
            <div style="font-size:11px;color:var(--text-secondary)">商品</div>
          </div>
        </div>
        <div class="op-stats">
          <span>🔄 ${op.snapCount} 次抓取</span>
          <span>🕐 ${lastTime}</span>
        </div>
      </div>`;
    }).join('');
  }
}

function drillIntoOperator(name) {
  currentOperator = name;
  currentProduct = null;
  document.getElementById('detailPanel').innerHTML = '<div class="card empty-state"><div class="icon">📊</div><p>选择商品查看详情</p></div>';
  renderMainList();
}

function goBackToOperators() {
  currentOperator = null;
  currentProduct = null;
  document.getElementById('detailPanel').innerHTML = '<div class="card empty-state"><div class="icon">📊</div><p>选择左侧运营 → 商品查看详情</p></div>';
  renderMainList();
}

function selectProduct(id) {
  currentProduct = products.find(p => p.product_id === id);
  renderMainList();
  renderDetail();
}

// ============== 价格变动对比 ==============

function buildDiffMap(product) {
  if (!product.snapshots || product.snapshots.length < 2) return {};
  const latest = product.snapshots[0];
  const prev = product.snapshots[1];
  const diffMap = {};
  const prevMap = {};
  prev.variants.forEach(v => { prevMap[v.variant_name] = v.current_price; });
  latest.variants.forEach(v => {
    const prevPrice = prevMap[v.variant_name];
    const currPrice = v.current_price;
    if (prevPrice != null && currPrice != null && prevPrice !== currPrice) {
      const diff = currPrice - prevPrice;
      diffMap[v.variant_name] = {
        prev: prevPrice,
        curr: currPrice,
        diff: diff,
        pct: (diff / prevPrice * 100).toFixed(1)
      };
    }
  });
  return diffMap;
}

// ============== 详情面板 ==============

function renderDetail() {
  if (!currentProduct) return;
  const p = currentProduct;
  const latest = p.snapshots && p.snapshots.length ? p.snapshots[0] : null;
  const variants = latest ? latest.variants : [];
  if (!variants.length) {
    document.getElementById('detailPanel').innerHTML = '<div class="card empty-state"><p>暂无变体数据</p></div>';
    return;
  }

  const prices = variants.map(v => v.current_price).filter(x => x != null && isFinite(x));
  const minPrice = prices.length ? Math.min(...prices) : null;
  const maxPrice = prices.length ? Math.max(...prices) : null;
  const spread = maxPrice && minPrice ? (maxPrice - minPrice).toFixed(2) : null;

  // Parse spec groups from variant names
  const specGroups = parseSpecGroups(variants);
  const isMultiSpec = specGroups.length >= 2;

  // History data for charts and diff
  const hasHistory = p.snapshots && p.snapshots.length > 1;
  const diffMap = buildDiffMap(p);
  const changedCount = Object.keys(diffMap).length;

  let html = `
    <div style="margin-bottom:16px;">
      <h2 style="font-size:20px;">${p.product_name_cn || p.product_name || '商品 ' + p.product_id}</h2>
      <div style="color:var(--text-secondary);font-size:13px;margin-top:4px;">ID: ${p.product_id} · ${p.category || ''} · ${p.snapshots.length} 次抓取${hasHistory ? ' · 最近: '+new Date(latest.captured_at).toLocaleString('zh-CN') : ''}</div>
    </div>
    ${changedCount > 0 ? `<div style="margin-bottom:16px;background:#fff;border-radius:8px;padding:10px 14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px">
      📊 相比上次抓取：<span style="color:var(--red);font-weight:700">↑${Object.values(diffMap).filter(d=>d.diff>0).length}涨</span> <span style="color:var(--green);font-weight:700">↓${Object.values(diffMap).filter(d=>d.diff<0).length}跌</span> <span style="color:var(--text-secondary)">(${changedCount}个变体变动)</span>
    </div>` : (hasHistory ? '<div style="margin-bottom:16px;font-size:13px;color:var(--green);background:#f0fdf7;border-radius:8px;padding:10px 14px">所有变体价格与上次抓取持平</div>' : '')}

    <!-- 统计卡片 -->
    <div class="grid-3" style="margin-bottom:20px;">
      <div class="card"><div class="stat-value">${variants.length}</div><div class="stat-label">变体数量</div></div>
      <div class="card"><div class="stat-value">$${minPrice != null ? minPrice.toFixed(2) : '-'}</div><div class="stat-label">最低价</div></div>
      <div class="card"><div class="stat-value">$${maxPrice != null ? maxPrice.toFixed(2) : '-'}</div><div class="stat-label">最高价</div></div>
    </div>`;

  if (spread && minPrice > 0) {
    html += `<div class="grid-3" style="margin-bottom:20px;">
      <div class="card"><div class="stat-value">$${spread}</div><div class="stat-label">价差</div></div>
      <div class="card"><div class="stat-value">${(parseFloat(spread)/minPrice*100).toFixed(1)}%</div><div class="stat-label">价差比例</div></div>
      ${latest.review_count ? `<div class="card"><div class="stat-value">${latest.review_count}</div><div class="stat-label">评论数</div></div>` : '<div class="card"><div class="stat-value">-</div><div class="stat-label">评论数</div></div>'}
    </div>`;
  }

  // 变体价格表
  if (isMultiSpec) {
    html += `
      <div class="card" style="margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <h3>变体价格矩阵</h3>
          <div class="toggle-btns">
            <button class="toggle-btn ${viewMode==='matrix'?'active':''}" onclick="switchView('matrix')">矩阵热力图</button>
            <button class="toggle-btn ${viewMode==='list'?'active':''}" onclick="switchView('list')">列表</button>
          </div>
        </div>
        ${viewMode === 'matrix' ? renderMatrix(variants, specGroups, minPrice, maxPrice, diffMap) : renderList(variants, minPrice, maxPrice, diffMap)}
      </div>`;
  } else {
    html += `
      <div class="card" style="margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <h3>变体价格</h3>
          <div class="toggle-btns">
            <button class="toggle-btn ${viewMode==='card'?'active':''}" onclick="switchView('card')">卡片</button>
            <button class="toggle-btn ${viewMode==='list'?'active':''}" onclick="switchView('list')">列表</button>
          </div>
        </div>
        ${viewMode === 'card' ? renderCards(variants, minPrice, diffMap) : renderList(variants, minPrice, maxPrice, diffMap)}
      </div>`;
  }

  // 价格走势图
  if (hasHistory) {
    html += `<div class="card"><h3 style="margin-bottom:16px;">价格走势</h3><canvas id="priceChart"></canvas></div>`;
  }

  document.getElementById('detailPanel').innerHTML = html;

  if (hasHistory) {
    setTimeout(() => drawPriceChart(p), 200);
  }
}

// ============== 规格解析 ==============

function parseSpecGroups(variants) {
  if (!variants.length) return [];
  const parts = variants[0].variant_name.split(' / ');
  if (parts.length < 2) return [{ name: 'Variant', values: variants.map(v => v.variant_name) }];
  
  const groups = [];
  parts.forEach((_, idx) => {
    const values = [...new Set(variants.map(v => v.variant_name.split(' / ')[idx]).filter(Boolean))];
    const name = ['Color', 'Size', 'Quantity', 'Style', 'Pattern'].includes(values[0])
      ? values[0] : `Spec-${idx+1}`;
    // Detect common spec names
    if (values.some(v => ['Twin','Full','Queen','King','S','M','L','XL'].includes(v))) groups.push({ name: 'Size', values });
    else if (values.some(v => v.length > 3 && !v.match(/^\d/))) groups.push({ name: 'Color', values });
    else groups.push({ name: `Spec-${idx+1}`, values });
  });
  return groups;
}

// ============== 矩阵热力图 ==============

function renderMatrix(variants, specGroups, minPrice, maxPrice, diffMap) {
  if (specGroups.length < 2) return renderList(variants, minPrice, maxPrice);

  const rows = specGroups[0].values; // e.g., Colors
  const cols = specGroups[1].values; // e.g., Sizes

  // Build lookup: "Color / Size" -> price
  const priceMap = {};
  variants.forEach(v => { priceMap[v.variant_name] = v.current_price; });

  const heatColor = (price) => {
    if (price == null) return '#f0f0f0';
    if (maxPrice === minPrice) return '#ffeaa7';
    const ratio = (price - minPrice) / (maxPrice - minPrice);
    if (ratio < 0.33) return `hsl(${140 - ratio * 60}, 70%, ${85 - ratio * 15}%)`;
    if (ratio < 0.66) return `hsl(${50 - (ratio-0.33) * 150}, 80%, ${80 - ratio * 10}%)`;
    return `hsl(${10 - (ratio-0.66) * 30}, 75%, ${78 - ratio * 8}%)`;
  };

  let html = '<div class="legend"><span>低</span><div class="legend-bar"></div><span>高</span><span style="margin-left:12px;">⬛最低价 ⬛最高价</span></div>';
  html += '<div class="matrix-wrap"><table class="matrix-table"><thead><tr><th></th>';
  cols.forEach(col => { html += `<th>${col}</th>`; });
  html += '</tr></thead><tbody>';

  rows.forEach(row => {
    html += `<tr><td>${row}</td>`;
    cols.forEach(col => {
      const key = `${row} / ${col}`;
      const price = priceMap[key];
      const isMin = price === minPrice && price != null;
      const isMax = price === maxPrice && price != null && maxPrice !== minPrice;
      const cls = isMin ? 'matrix-best' : (isMax ? 'matrix-worst' : '');
      let arrow = '';
      if (diffMap[key]) {
        const d = diffMap[key];
        const up = d.diff > 0;
        arrow = `<span style="font-size:11px;color:${up ? 'var(--red)' : 'var(--green)'};margin-left:4px">${up ? '↑' : '↓'}${Math.abs(d.pct)}%</span>`;
      }
      html += `<td class="${cls}" style="background:${heatColor(price)}">${price != null ? '$'+price.toFixed(2) : '-'}${arrow}</td>`;
    });
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  return html;
}

// ============== 列表视图 ==============

function renderList(variants, minPrice, maxPrice, diffMap) {
  const hasDiff = Object.keys(diffMap).length > 0;
  return `<table>
    <thead><tr><th>变体</th><th>当前售价</th><th>原价</th>${hasDiff ? '<th>变动</th>' : ''}<th>标记</th></tr></thead>
    <tbody>${variants.map(v => {
      const isMin = v.current_price === minPrice;
      const isMax = v.current_price === maxPrice && maxPrice !== minPrice;
      const d = diffMap[v.variant_name];
      let changeHtml = '';
      if (d) {
        const up = d.diff > 0;
        changeHtml = `<span style="color:${up ? 'var(--red)' : 'var(--green)'};font-weight:600">${up ? '↑' : '↓'} $${Math.abs(d.diff).toFixed(2)} (${Math.abs(d.pct)}%)</span>`;
      }
      return `<tr>
        <td>${v.variant_name}</td>
        <td class="price">${v.current_price != null ? '$'+v.current_price.toFixed(2) : '-'}</td>
        <td style="color:var(--text-secondary)">${v.original_price != null ? '$'+v.original_price.toFixed(2) : '-'}</td>
        ${hasDiff ? `<td>${changeHtml || '—'}</td>` : ''}
        <td>${isMin ? '⬇ 最低' : isMax ? '⬆ 最高' : ''}</td>
      </tr>`;
    }).join('')}</tbody></table>`;
}

// ============== 卡片视图 ==============

function renderCards(variants, minPrice, diffMap) {
  return `<div class="variant-cards">${variants.map(v => {
    const d = diffMap[v.variant_name];
    let diffHtml = '';
    if (d) {
      const up = d.diff > 0;
      diffHtml = `<div style="font-size:13px;font-weight:700;color:${up ? 'var(--red)' : 'var(--green)'};margin-top:2px">${up ? '↑' : '↓'} $${Math.abs(d.diff).toFixed(2)}</div>`;
    }
    return `
    <div class="v-card ${v.current_price === minPrice ? 'best' : ''}">
      <div class="v-name">${v.variant_name}</div>
      <div class="v-price">${v.current_price != null ? '$'+v.current_price.toFixed(2) : '-'}</div>
      ${v.original_price ? `<div class="v-original">$${v.original_price.toFixed(2)}</div>` : ''}
      ${diffHtml}
      ${v.current_price === minPrice ? '<div style="font-size:11px;color:var(--green);margin-top:4px;">🏷 最低价</div>' : ''}
    </div>`}).join('')}</div>`;
}

// ============== 视图切换 ==============

function switchView(mode) {
  viewMode = mode;
  renderDetail();
}

// ============== 价格走势图 ==============

function drawPriceChart(product) {
  const ctx = document.getElementById('priceChart');
  if (!ctx) return;

  // Get all snapshots sorted
  const snapshots = [...product.snapshots].reverse();
  // Get unique variant names across all snapshots
  const allVariantNames = [...new Set(snapshots.flatMap(s => s.variants.map(v => v.variant_name)))];
  const topVariants = allVariantNames.slice(0, 8); // show top 8

  const datasets = topVariants.map((name, i) => {
    const colors = ['#e87400','#0984e3','#00b894','#e17055','#6c5ce7','#fdcb6e','#00cec9','#fd79a8'];
    const data = snapshots.map(s => {
      const v = s.variants.find(x => x.variant_name === name);
      return v ? v.current_price : null;
    });
    return {
      label: name.length > 25 ? name.slice(0,25)+'...' : name,
      data,
      borderColor: colors[i],
      backgroundColor: colors[i]+'20',
      tension: 0.3,
      spanGaps: true,
    };
  });

  new Chart(ctx, {
    type: 'line',
    data: {
      labels: snapshots.map(s => new Date(s.captured_at).toLocaleString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})),
      datasets,
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } } },
      scales: {
        y: { ticks: { callback: v => '$'+v } }
      }
    }
  });
}

// ============== 运营角色选择 ==============

const OPERATOR_NAMES = ['王瑞燕','袁佳乐','徐菲','范一非','葛洲','瞿可儿','刘昊鹏','狄威','陈玉婷','丁利芳','邵琪琪','程蕾'];
let selectedOperator = null; // 当前登录的运营

function initOperatorRole() {
  const saved = localStorage.getItem('temu_tracker_operator');
  if (saved && OPERATOR_NAMES.includes(saved)) {
    selectedOperator = saved;
    updateOperatorBadge();
  } else {
    showOperatorSelector();
  }
}

function updateOperatorBadge() {
  const badge = document.getElementById('operatorBadge');
  const avatar = document.getElementById('badgeAvatar');
  const name = document.getElementById('badgeName');
  if (selectedOperator) {
    badge.style.display = 'inline-flex';
    avatar.textContent = selectedOperator[0];
    name.textContent = selectedOperator;
  } else {
    badge.style.display = 'none';
  }
}

function showOperatorSelector() {
  const modal = document.getElementById('operatorModal');
  const grid = document.getElementById('operatorSelectGrid');
  const allNames = [...OPERATOR_NAMES, '测试'];
  grid.innerHTML = allNames.map(n => {
    const cls = n === '测试' ? 'op-select-card test' : 'op-select-card';
    return `<div class="${cls}" onclick="selectOperator('${n}')">
      <div class="avatar">${n[0]}</div>
      <div>${n}</div>
    </div>`;
  }).join('');
  modal.style.display = 'flex';
}

function selectOperator(name) {
  selectedOperator = name;
  localStorage.setItem('temu_tracker_operator', name);
  document.getElementById('operatorModal').style.display = 'none';
  updateOperatorBadge();
  // 更新抓取面板标题
  document.getElementById('scrapeOpName').textContent = name;
}

// ============== 抓取入口 ==============

function toggleScrape() {
  const section = document.getElementById('scrapeSection');
  const btn = document.getElementById('scrapeToggle');
  if (section.style.display === 'none' || !section.style.display) {
    section.style.display = 'block';
    btn.classList.add('active');
    // 更新当前运营名
    document.getElementById('scrapeOpName').textContent = selectedOperator || '未选择运营';
    // 生成每个运营的专属书签
    renderOperatorBookmarks();
  } else {
    section.style.display = 'none';
    btn.classList.remove('active');
  }
}

function renderOperatorBookmarks() {
  const list = document.getElementById('bookmarkList');
  list.innerHTML = OPERATOR_NAMES.map(name => {
    const code = `const OPERATOR_NAME='${name}'; const UPLOAD_BASE='${location.origin + location.pathname.replace(/\/[^/]*$/, '').replace(/\/$/, '')}';` + SCRAPER_INLINE;
    const href = 'javascript:' + encodeURIComponent(code);
    const isMe = (selectedOperator === name);
    return `<a class="bookmark-btn" href="${href}" draggable="true" 
      style="${isMe ? 'background:#00b894;border:2px solid #007a50' : ''}"
      title="拖到书签栏：${name}的专属抓取书签">
      🔍 ${name}
    </a>`;
  }).join('');
}

// ============== 初始化 ==============

initOperatorRole();
refreshData();
setInterval(refreshData, 30000); // auto-refresh every 30s
