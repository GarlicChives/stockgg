function showCatalystModal(eventId) {
  const md = catalystModalData[eventId];
  if (!md) return;  // 沒 preview 不彈
  const title = catalystModalTitles[eventId] || '事件';
  document.getElementById('modal-title').textContent = '📝 ' + title;
  document.getElementById('modal-body').innerHTML = md;
  document.getElementById('art-modal').showModal();
}

// Cluster ⓘ info button → art-modal 顯該題材關聯議題(從 market_notes.topics)
function showClusterTopicModal(cardId) {
  const html = (window.IIA_CLUSTER_TOPICS || {})[cardId];
  if (!html) return;
  const card = document.getElementById(cardId);
  const name = card?.querySelector('.cluster-name')?.textContent?.trim() || '題材';
  document.getElementById('modal-title').textContent = '📌 ' + name + ' — 關聯議題';
  document.getElementById('modal-body').innerHTML =
    '<div class="topics-grid">' + html + '</div>';
  document.getElementById('art-modal').showModal();
}

function showTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-pane').forEach(p =>
    p.classList.toggle('active', p.id === 'tab-' + name));
  // 🛡️ 風控 tab:lazy-init 淨值雙線圖(只第一次切過去 init)
  if (name === 'risk' && !_riskRendered) _initRiskChart();
  // 🗺️ 產業地圖 tab:lazy-init 蜘蛛網關聯圖(只第一次切過去 init)
  if (name === 'indmap' && !_indmapRendered) _initIndmapGraph();
}

/* ── 🛡️ 風控 tab — lazy-init「依建議部位 vs 買進持有」淨值雙線圖 ──────
 * payload window.IIA_RISK = { history: [{d, strat, bh, pos}, ...] }
 * 資料由 ingest 風控回測寫入,stockgg 只畫圖。strat 多數時候貼著或略低於
 * bh(誠實:OOS 未打贏買進持有,價值在壓低回撤)。 */
let _riskRendered = false;

function _initRiskChart() {
  const data = window.IIA_RISK;
  if (!data || !data.history || !data.history.length) return;
  _loadLightweightCharts().then(() => {
    const el = document.getElementById('risk-nav-chart');
    if (!el) return;
    const chart = LightweightCharts.createChart(el, {
      layout: { background: { type: 'solid', color: 'transparent' },
                textColor: '#7c8290', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(255,255,255,.04)' },
              horzLines: { color: 'rgba(255,255,255,.04)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,.08)', timeVisible: false },
      crosshair: { mode: 1 }, autoSize: true,
    });
    const stratSeries = chart.addLineSeries({ color: '#60a5fa', lineWidth: 2, priceLineVisible: false });
    const bhSeries = chart.addLineSeries({ color: '#9aa4ad', lineWidth: 2, priceLineVisible: false });
    stratSeries.setData(data.history.filter(r => r.strat != null).map(r => ({ time: r.d, value: r.strat })));
    bhSeries.setData(data.history.filter(r => r.bh != null).map(r => ({ time: r.d, value: r.bh })));
    chart.timeScale().fitContent();
    _riskRendered = true;
  }).catch(e => console.error('risk chart load failed', e));
}

/* ── 🗺️ 產業地圖 — 焦點產業關聯「蜘蛛網」圖 ────────────────────────
 * window.IIA_INDMAP_GRAPH = { nodes:[{i,name,kind,chg,cov,tv,n,mv:[{t,n,c}]}],
 *                             edges:[[a,b,w]], hot: 門檻 }
 * window.IIA_INDMAP_CROSS = { ticker: { n: 名稱, h: [{f,s}] } }
 * 節點 = 焦點產業;邊 = 共享個股;發亮 = 今日成交值加權漲跌幅。手刻力導向布局
 * (Fruchterman-Reingold)+ 原生 SVG,不引入外部圖庫。點節點 → imOpenFocus 展開階層。*/
const SVGNS = 'http://www.w3.org/2000/svg';
let _indmapRendered = false;

function _imEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* 跨產業關聯 modal:點個股 → 列出它出現的所有焦點 */
function imShowCross(ticker) {
  const map = window.IIA_INDMAP_CROSS || {};
  const e = map[ticker];
  const body = document.getElementById('im-modal-body');
  const title = document.getElementById('im-modal-title');
  if (!e || !body || !title) return;
  title.innerHTML = '<span class="im-tk">' + _imEsc(ticker) + '</span> ' +
    _imEsc(e.n || '');
  const hits = e.h || [];
  if (hits.length <= 1) {
    body.innerHTML = '<p class="im-modal-note">' + _imEsc(e.n || ticker) +
      ' 目前只出現在 <b>1</b> 個焦點產業' +
      (hits.length ? '：<b>' + _imEsc(hits[0].f) + '</b>（' +
        _imEsc(hits[0].s) + '）' : '') +
      '。尚無跨產業聯想。</p>';
  } else {
    const rows = hits.map(h =>
      '<li class="im-modal-row"><span class="im-modal-f">' + _imEsc(h.f) +
      '</span><span class="im-modal-s">' + _imEsc(h.s) + '</span></li>'
    ).join('');
    body.innerHTML = '<p class="im-modal-lead">橫跨 <b>' + hits.length +
      '</b> 個焦點產業，可作為投資聯想的交集：</p>' +
      '<ul class="im-modal-list">' + rows + '</ul>';
  }
  document.getElementById('im-modal').showModal();
}

/* 點節點 → 取隱藏 detail store 的該焦點階層 HTML,塞進放大版 im-modal */
function imOpenFocus(i, name) {
  const src = document.getElementById('imf-' + i);
  const body = document.getElementById('im-modal-body');
  const title = document.getElementById('im-modal-title');
  if (!src || !body || !title) return;
  title.innerHTML = '🗺️ ' + _imEsc(name || src.dataset.name || '焦點產業');
  body.innerHTML = '<div class="im-modal-focus">' + src.innerHTML + '</div>';
  document.getElementById('im-modal').showModal();
}

/* hex lerp */
function _imMix(c1, c2, t) {
  const a = parseInt(c1.slice(1), 16), b = parseInt(c2.slice(1), 16);
  const ar = a >> 16, ag = (a >> 8) & 255, ab = a & 255;
  const br = b >> 16, bg = (b >> 8) & 255, bb = b & 255;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return 'rgb(' + r + ',' + g + ',' + bl + ')';
}

/* 色溫:chg null → 空心灰;chg≥0 → 灰→紅(今日強);chg<0 → 灰→綠(今日弱) */
function _imHeatColor(chg) {
  if (chg == null) return { fill: '#252a35', stroke: '#4a5364', txt: '#8893a3', na: true, a: 0 };
  const a = Math.min(Math.abs(chg) / 4, 1);
  if (chg >= 0) return { fill: _imMix('#39414f', '#ff5252', a), stroke: _imMix('#5a6576', '#ff9a9a', a), txt: '#fff', na: false, a: a };
  return { fill: _imMix('#39414f', '#23b277', a), stroke: _imMix('#5a6576', '#5fe0a8', a), txt: '#fff', na: false, a: a };
}

/* Fruchterman-Reingold 力導向布局 → 寫回 node.x / node.y(布局座標系 0..W,0..H) */
function _imLayout(nodes, edges, W, H) {
  const n = nodes.length;
  if (!n) return;
  const area = W * H, k = 0.82 * Math.sqrt(area / n);
  // 環狀初始化(避免全疊在一點 → 退化)
  nodes.forEach((nd, idx) => {
    const ang = (idx / n) * Math.PI * 2;
    nd.x = W / 2 + Math.cos(ang) * W * 0.32 + (Math.random() - 0.5) * 20;
    nd.y = H / 2 + Math.sin(ang) * H * 0.32 + (Math.random() - 0.5) * 20;
  });
  const ITER = 260;
  let t = W * 0.12;
  const cool = t / (ITER + 1);
  for (let it = 0; it < ITER; it++) {
    const dx = new Float64Array(n), dy = new Float64Array(n);
    // 斥力(全對)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let ex = nodes[i].x - nodes[j].x, ey = nodes[i].y - nodes[j].y;
        let d = Math.hypot(ex, ey) || 0.01;
        const f = (k * k) / d;
        ex /= d; ey /= d;
        dx[i] += ex * f; dy[i] += ey * f;
        dx[j] -= ex * f; dy[j] -= ey * f;
      }
    }
    // 引力(邊;共享愈多愈強 → 愈近)
    for (const e of edges) {
      const u = e[0], v = e[1], w = e[2];
      let ex = nodes[u].x - nodes[v].x, ey = nodes[u].y - nodes[v].y;
      let d = Math.hypot(ex, ey) || 0.01;
      const f = (d * d) / k * (1 + 0.35 * Math.log(w + 1));
      ex /= d; ey /= d;
      dx[u] -= ex * f; dy[u] -= ey * f;
      dx[v] += ex * f; dy[v] += ey * f;
    }
    // 向心(弱)+ 位移限速 + 邊界
    for (let i = 0; i < n; i++) {
      dx[i] += (W / 2 - nodes[i].x) * 0.012;
      dy[i] += (H / 2 - nodes[i].y) * 0.012;
      const dd = Math.hypot(dx[i], dy[i]) || 0.01;
      nodes[i].x += (dx[i] / dd) * Math.min(dd, t);
      nodes[i].y += (dy[i] / dd) * Math.min(dd, t);
      nodes[i].x = Math.max(30, Math.min(W - 30, nodes[i].x));
      nodes[i].y = Math.max(30, Math.min(H - 30, nodes[i].y));
    }
    t = Math.max(t - cool, W * 0.004);
  }
}

function _initIndmapGraph() {
  const g = window.IIA_INDMAP_GRAPH;
  const host = document.getElementById('im-graph');
  if (!host || !g || !g.nodes || !g.nodes.length) return;
  _indmapRendered = true;
  host.innerHTML = '';

  // viewBox 寬度跟著容器寬高比走 → 圖滿版填滿容器(不留左右黑邊)
  const H = 680;
  const cw = host.clientWidth || 1200, ch = host.clientHeight || 680;
  const W = Math.round(Math.max(700, Math.min(2200, H * (cw / ch))));
  const nodes = g.nodes, edges = g.edges || [], hot = g.hot || 2.0;
  _imLayout(nodes, edges, W, H);

  // 節點半徑:成交熱度(tv 億)sqrt 縮放
  nodes.forEach(nd => { nd.r = 10 + Math.min(24, Math.sqrt(Math.max(nd.tv, 0)) * 1.05); });

  // 解重疊:力導向會把點推到邊界堆疊 → 幾輪依半徑把太近的點推開
  for (let pass = 0; pass < 70; pass++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.01;
        const min = a.r + b.r + 12;
        if (d < min) {
          const push = (min - d) / 2; dx /= d; dy /= d;
          a.x -= dx * push; a.y -= dy * push; b.x += dx * push; b.y += dy * push;
        }
      }
    }
    nodes.forEach(n => {
      n.x = Math.max(n.r + 4, Math.min(W - n.r - 4, n.x));
      n.y = Math.max(n.r + 4, Math.min(H - n.r - 4, n.y));
    });
  }

  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('class', 'im-svg');
  const vb = { x: 0, y: 0, w: W, h: H };
  const setVB = () => svg.setAttribute('viewBox', vb.x + ' ' + vb.y + ' ' + vb.w + ' ' + vb.h);
  setVB();

  // glow filter
  const defs = document.createElementNS(SVGNS, 'defs');
  defs.innerHTML = '<filter id="im-glow" x="-60%" y="-60%" width="220%" height="220%">' +
    '<feGaussianBlur stdDeviation="5" result="b"/>' +
    '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>';
  svg.appendChild(defs);

  // tooltip(節點 + 供應鏈邊 共用)
  let tip = document.getElementById('im-tooltip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'im-tooltip'; tip.className = 'im-tooltip'; tip.hidden = true;
    host.appendChild(tip);
  }
  const placeTip = (evt) => {
    const hb = host.getBoundingClientRect();
    let x = evt.clientX - hb.left + 14, y = evt.clientY - hb.top + 14;
    x = Math.min(x, hb.width - tip.offsetWidth - 8);
    y = Math.min(y, hb.height - tip.offsetHeight - 8);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  };

  // edges:供應鏈有向邊(e=[from, to, strength, relation];from=上游 → to=下游)。
  // 線在節點邊界收尾、箭頭指下游;滑過顯示關係說明。
  const gEdges = document.createElementNS(SVGNS, 'g');
  for (const e of edges) {
    const a = nodes[e[0]], b = nodes[e[1]], st = e[2], rel = e[3] || '';
    let ux = b.x - a.x, uy = b.y - a.y; const d = Math.hypot(ux, uy) || 0.01;
    ux /= d; uy /= d;
    const sx = a.x + ux * (a.r + 1), sy = a.y + uy * (a.r + 1);
    const ex = b.x - ux * (b.r + 3), ey = b.y - uy * (b.r + 3);
    const op = st >= 3 ? 0.5 : 0.3, sw = st >= 3 ? 1.8 : 1.1;
    const cell = document.createElementNS(SVGNS, 'g');
    cell.setAttribute('class', 'im-edge-g');
    const ln = document.createElementNS(SVGNS, 'line');
    ln.setAttribute('x1', sx); ln.setAttribute('y1', sy);
    ln.setAttribute('x2', ex); ln.setAttribute('y2', ey);
    ln.setAttribute('class', 'im-edge');
    ln.setAttribute('stroke-width', sw); ln.style.opacity = op;
    cell.appendChild(ln);
    // 箭頭(指向下游節點)
    const ah = 7 + st, aw = 3 + st * 0.6;
    const lx = ex - ux * ah, ly = ey - uy * ah, px = -uy, py = ux;
    const tri = document.createElementNS(SVGNS, 'polygon');
    tri.setAttribute('points', ex + ',' + ey + ' ' + (lx + px * aw) + ',' + (ly + py * aw) +
      ' ' + (lx - px * aw) + ',' + (ly - py * aw));
    tri.setAttribute('class', 'im-arrow'); tri.style.opacity = Math.min(op + 0.18, 0.72);
    cell.appendChild(tri);
    // 透明粗線當 hover 命中區
    const hit = document.createElementNS(SVGNS, 'line');
    hit.setAttribute('x1', sx); hit.setAttribute('y1', sy);
    hit.setAttribute('x2', ex); hit.setAttribute('y2', ey);
    hit.setAttribute('class', 'im-edge-hit');
    cell.appendChild(hit);
    const showEdge = (evt) => {
      tip.innerHTML = '<div class="im-tip-edge"><b>' + _imEsc(a.name) +
        '</b> <span class="im-tip-arrow">→</span> <b>' + _imEsc(b.name) + '</b></div>' +
        (rel ? '<div class="im-tip-row im-tip-sub">' + _imEsc(rel) + '</div>' : '') +
        '<div class="im-tip-hint">供應鏈:上游 → 下游</div>';
      tip.hidden = false; placeTip(evt);
    };
    cell.addEventListener('mouseenter', showEdge);
    cell.addEventListener('mousemove', showEdge);
    cell.addEventListener('mouseleave', () => { tip.hidden = true; });
    gEdges.appendChild(cell);
  }
  svg.appendChild(gEdges);
  const showTip = (nd, evt) => {
    const hc = _imHeatColor(nd.chg);
    let s = '<div class="im-tip-name">' + _imEsc(nd.name) + '</div>';
    if (hc.na) {
      s += '<div class="im-tip-row im-tip-na">今日成分股無成交資料</div>';
    } else {
      const sign = nd.chg >= 0 ? '+' : '';
      s += '<div class="im-tip-row">今日加權漲跌 <b class="' +
        (nd.chg >= 0 ? 'im-up' : 'im-down') + '">' + sign + nd.chg.toFixed(2) + '%</b></div>';
    }
    s += '<div class="im-tip-row im-tip-sub">成交熱度 ' + nd.tv + ' 億 · 覆蓋 ' +
      Math.round(nd.cov * 100) + '%（' + nd.n + ' 檔）</div>';
    if (nd.mv && nd.mv.length) {
      s += '<div class="im-tip-mv">' + nd.mv.map(m =>
        '<span>' + _imEsc(m.t) + ' ' + _imEsc(m.n) + ' <b class="' +
        (m.c >= 0 ? 'im-up' : 'im-down') + '">' + (m.c >= 0 ? '+' : '') + m.c + '%</b></span>'
      ).join('') + '</div>';
    }
    s += '<div class="im-tip-hint">點擊展開成分股</div>';
    tip.innerHTML = s; tip.hidden = false;
    const hb = host.getBoundingClientRect();
    let x = evt.clientX - hb.left + 14, y = evt.clientY - hb.top + 14;
    x = Math.min(x, hb.width - tip.offsetWidth - 8);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  };

  // nodes
  const gNodes = document.createElementNS(SVGNS, 'g');
  for (const nd of nodes) {
    const hc = _imHeatColor(nd.chg);
    const grp = document.createElementNS(SVGNS, 'g');
    const isHot = !hc.na && nd.chg >= hot && nd.cov >= 0.2;
    grp.setAttribute('class', 'im-node' + (isHot ? ' im-node-hot' : '') + (hc.na ? ' im-node-na' : ''));
    grp.setAttribute('transform', 'translate(' + nd.x.toFixed(1) + ',' + nd.y.toFixed(1) + ')');

    const c = document.createElementNS(SVGNS, 'circle');
    c.setAttribute('r', nd.r.toFixed(1));
    c.setAttribute('fill', hc.fill);
    c.setAttribute('stroke', hc.stroke);
    c.setAttribute('stroke-width', hc.na ? 1.5 : 2);
    if (hc.na) c.setAttribute('fill-opacity', '0.25');
    grp.appendChild(c);

    const label = (nd.name || '').length > 9 ? nd.name.slice(0, 8) + '…' : nd.name;
    const txt = document.createElementNS(SVGNS, 'text');
    txt.setAttribute('class', 'im-label');
    txt.setAttribute('y', (nd.r + 13).toFixed(1));
    txt.setAttribute('text-anchor', 'middle');
    txt.setAttribute('fill', hc.na ? '#7a8696' : '#cdd6e2');
    txt.textContent = label;
    grp.appendChild(txt);

    grp.addEventListener('mouseenter', e => showTip(nd, e));
    grp.addEventListener('mousemove', e => showTip(nd, e));
    grp.addEventListener('mouseleave', () => { tip.hidden = true; });
    grp.addEventListener('click', () => { tip.hidden = true; imOpenFocus(nd.i, nd.name); });
    gNodes.appendChild(grp);
  }
  svg.appendChild(gNodes);
  host.appendChild(svg);
  // 滿版靜態呈現:不做滾輪縮放、不做拖曳平移(圖已填滿容器、節點全可見);
  // 互動只保留 hover tooltip + 點節點開 modal。滾輪維持頁面正常捲動。
}

function showSubTab(name) {
  document.querySelectorAll('.sub-tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.stab === name));
  document.querySelectorAll('.sub-tab-pane').forEach(p =>
    p.classList.toggle('active', p.id === 'stab-' + name));
}

/* _radarSvg / IIA_RADAR / 個股雷達 2026-05-20 全廢:個股 modal body
   改為「持股主動式 ETF」表(server-side render 進 artModalData),前端不再
   需要客戶端雷達 SVG。 */

/* 個股 modal 來源 scope:從 clicked element 找最近的 stk-pill 容器,
   取其內 visible([onclick*="showArtModal"] 且非 .hidden / row.hidden) 的
   ticker 順序作為左右導覽範圍。順序 = 該容器當下 DOM 順序 = 外層排序結果。
   外層 filter / sort 變動會 hook _refreshArtScope() 重撈。modal 內頂部
   ticker chips bar 允許 user 手動把個別 ticker disable —— 不從 scope 移除
   (避免「都 disable 後不知道從哪 enable 回來」),只在 navigate 時跳過。 */
let _artScope = [];                 // ordered ticker list within source container
let _artScopeIdx = -1;              // current index in _artScope
let _artCurrentTicker = null;
let _artCurrentName = '';
let _artScopeContainer = null;      // DOM ref:來源 container,filter 變動時 re-scan 用
let _artScopeObserver = null;       // MutationObserver:監聽 container 變化自動 refresh

const _ART_SCOPE_SELECTORS = [
  '.cluster-focal-stocks',       // 熱門題材 cluster focal/sentinel pill
  '.cluster-sentinel-stocks',    // 熱門題材 sentinel 展開區
  '.tk-row',                     // 市場話題 / catalyst topic 內 ticker chips
  '.aetf-hold-table tbody',      // 主動式 ETF 持股表
  '.fs-list',                    // 選股雷達 list-style sub-tab
  '.fs-table tbody',             // 選股雷達 table-style sub-tab(交集股等)
  '.aetf-cp-row',                // ETF 異動列(若 stk-pill chip 在內)
];

function _detectArtScope(evt) {
  if (!evt || !evt.currentTarget) return null;
  for (const sel of _ART_SCOPE_SELECTORS) {
    const container = evt.currentTarget.closest(sel);
    if (container) return container;
  }
  return null;
}

function _extractTickerFromOnclick(el) {
  const oc = el.getAttribute && el.getAttribute('onclick');
  if (!oc) return null;
  const m = oc.match(/showArtModal\(\s*"([^"]+)"\s*,\s*"((?:\\.|[^"\\])*)"/);
  if (!m) return null;
  // unescape \uXXXX 之類(name 內中文用 \uXXXX)
  let name = '';
  try { name = JSON.parse('"' + m[2] + '"'); } catch (e) { name = m[2]; }
  return { ticker: m[1], name };
}

/* 重撈 scope:從 _artScopeContainer 內取所有 visible([onclick*="showArtModal"]
   且非 row.hidden / 任意祖先 hidden) 的 ticker。外層 filter / sort 觸發時呼叫。 */
function _refreshArtScope() {
  if (!_artScopeContainer) return;
  const pills = _artScopeContainer.querySelectorAll('[onclick*="showArtModal"]');
  const next = [];
  const seen = new Set();
  pills.forEach(p => {
    // visibility check:任一祖先(到 _artScopeContainer 為止)有 hidden 屬性 → skip
    let el = p;
    let visible = true;
    while (el && el !== _artScopeContainer) {
      if (el.hidden) { visible = false; break; }
      el = el.parentElement;
    }
    if (!visible) return;
    const info = _extractTickerFromOnclick(p);
    if (info && !seen.has(info.ticker)) {
      seen.add(info.ticker);
      next.push(info);
    }
  });
  _artScope = next.length ? next : [{ ticker: _artCurrentTicker, name: _artCurrentName }];
  const idx = _artScope.findIndex(it => it.ticker === _artCurrentTicker);
  _artScopeIdx = idx >= 0 ? idx : 0;
  _updateArtCounter();
}

function showArtModal(ticker, name, evt) {
  _artCurrentTicker = ticker;
  _artCurrentName = name || '';
  _artScopeContainer = _detectArtScope(evt);
  // dispose 上次 observer(若 modal 連續開不同 container)
  if (_artScopeObserver) { _artScopeObserver.disconnect(); _artScopeObserver = null; }
  if (_artScopeContainer) {
    // MutationObserver 監聽 container 內 child / hidden 屬性變化,外層 filter
    // (toggleFsFilter row.hidden) 或 sort (setFocalSort _renderFocalSort 重建
    // pills) 觸發時自動 _refreshArtScope。免逐個 sort/filter handler 加 hook。
    _artScopeObserver = new MutationObserver(() => _refreshArtScope());
    _artScopeObserver.observe(_artScopeContainer, {
      childList: true, subtree: true,
      attributes: true, attributeFilter: ['hidden'],
    });
    _refreshArtScope();
  } else {
    _artScope = [{ ticker, name: _artCurrentName }];
    _artScopeIdx = 0;
  }
  // 確保 current 在 scope 內(_refreshArtScope 若拿到空可能 fallback)
  if (!_artScope.some(it => it.ticker === ticker)) {
    _artScope.unshift({ ticker, name: _artCurrentName });
    _artScopeIdx = 0;
  }
  _renderArtModalBody(ticker, _artCurrentName);
  document.getElementById('art-modal').showModal();
}

/* 重新渲染 modal body(切換 ticker 時 reuse,不關 modal)
   2026-05-25:取消 tab,K 線(上)+ ETF(下)直接排列 */
function _renderArtModalBody(ticker, name) {
  document.getElementById('modal-title').textContent = _dispTk(ticker) + ' ' + (name || '');
  const etfHtml = artModalData[ticker] || '<p style="color:#7a8ba0">本檔目前無主動 ETF 持有</p>';
  document.getElementById('modal-body').innerHTML = (
    '<div class="art-kline-section">' +
      '<div class="art-kline-period">' +
        '<button class="art-kline-chip" data-period="1m" type="button" onclick="setKlinePeriod(\'1m\')">1M</button>' +
        '<button class="art-kline-chip" data-period="3m" type="button" onclick="setKlinePeriod(\'3m\')">3M</button>' +
        '<button class="art-kline-chip active" data-period="6m" type="button" onclick="setKlinePeriod(\'6m\')">6M</button>' +
        '<button class="art-kline-chip" data-period="1y" type="button" onclick="setKlinePeriod(\'1y\')">1Y</button>' +
      '</div>' +
      '<div class="art-kline-chart" id="art-kline-chart"></div>' +
      '<div class="art-kline-empty" id="art-kline-empty" style="display:none">載入 K 線中…</div>' +
    '</div>' +
    '<div class="art-etf-section">' + etfHtml + '</div>'
  );
  _updateArtCounter();
  _loadStockKline(ticker);
}

/* 更新 art-counter「N/total」+ nav 箭頭 disable 條件(總數 ≤ 1 時兩邊都 disable)*/
function _updateArtCounter() {
  const counter = document.getElementById('art-counter');
  if (counter) {
    counter.textContent = _artScope.length
      ? `${_artScopeIdx + 1}/${_artScope.length}`
      : '';
  }
  const prev = document.getElementById('art-nav-prev');
  const next = document.getElementById('art-nav-next');
  const navDisabled = _artScope.length < 2;
  if (prev) prev.disabled = navDisabled;
  if (next) next.disabled = navDisabled;
}

/* 左右導覽:環狀切到 prev/next ticker(同 scope 內)*/
function artNavTicker(dir) {
  if (_artScope.length < 2) return;
  const n = _artScope.length;
  _artScopeIdx = dir === 'next'
    ? (_artScopeIdx + 1) % n
    : (_artScopeIdx - 1 + n) % n;
  const cur = _artScope[_artScopeIdx];
  _artCurrentTicker = cur.ticker;
  _artCurrentName = cur.name;
  _renderArtModalBody(cur.ticker, cur.name);
}

/* ── 個股 modal 日 K 線(lazy fetch per-ticker JSON)─────────────────────── */
const _klineCache = {};            // ticker → [[d,o,h,l,c,v], ...]
let _klineChart = null;            // 當前 chart 實例(modal 關閉時 dispose)
let _klineData = null;             // 當前載入的 data array
let _klinePeriod = '6m';
const _KLINE_PERIOD_DAYS = { '1m': 30, '3m': 90, '6m': 180, '1y': 365, '2y': 730 };

function _loadStockKline(ticker) {
  _klinePeriod = '6m';  // 每次開/切換 ticker 重置預設
  // 切換 ticker 必須先 dispose 上一檔 chart,避免新 ticker render 時舊 chart 還在
  if (_klineChart) {
    try { _klineChart.remove(); } catch (e) {}
    _klineChart = null;
  }
  _klineData = null;
  document.querySelectorAll('.art-kline-chip').forEach(b =>
    b.classList.toggle('active', b.dataset.period === _klinePeriod));
  const empty = document.getElementById('art-kline-empty');
  const chart = document.getElementById('art-kline-chart');
  if (empty) { empty.textContent = '載入 K 線中…'; empty.style.display = ''; }
  if (chart) chart.style.display = 'none';
  Promise.all([_loadLightweightCharts(), _fetchKline(ticker)])
    .then(([_, data]) => {
      _klineData = data || [];
      if (!_klineData.length) {
        if (empty) { empty.textContent = '本檔尚無 K 線資料'; empty.style.display = ''; }
        if (chart) chart.style.display = 'none';
        return;
      }
      if (empty) empty.style.display = 'none';
      if (chart) chart.style.display = '';
      // K 線永遠 visible(取消 tab 後),直接 render
      _renderStockKline();
    })
    .catch(err => {
      console.error('kline load failed', err);
      if (empty) { empty.textContent = 'K 線載入失敗'; empty.style.display = ''; }
      if (chart) chart.style.display = 'none';
    });
}

// 2026-05-25 v2:從 per-ticker /kline/<tk>.json 改為單一 /kline.json。
// 跟 history.json 同模式:固定 URL + cache:'no-cache' revalidate。
// **不要加 ?_=Date.now() cache-bust query** —— 每次 URL 不同會讓 Cloudflare
// 邊緣節點每次 cache miss,放大 manifest sync 延遲。
//
// retry 策略:Cloudflare Workers Static Assets deploy 後可能有 propagation
// 延遲(實測偶爾 10 分鐘),fetch 失敗時自動延遲 retry 直到成功。modal
// 端在 _loadKlineAll 解決前顯「載入 K 線中...」,不顯「本檔尚無 K 線資料」
// 誤訊息(只有 ticker 不在 universe 才顯)。
let _klineAllPromise = null;
function _loadKlineAll() {
  if (_klineAllPromise) return _klineAllPromise;
  const fetchOnce = () =>
    fetch('kline.json', { cache: 'no-cache' })
      .then(r => {
        if (!r.ok) throw new Error('kline.json ' + r.status);
        return r.json();
      })
      .then(payload => payload && payload.k || {});
  // 指數退避 retry:0 / 2s / 5s / 10s / 20s / 30s,共 6 輪嘗試,最久 ~67 秒
  const delays = [0, 2000, 5000, 10000, 20000, 30000];
  _klineAllPromise = (async () => {
    let lastErr;
    for (let i = 0; i < delays.length; i++) {
      if (delays[i]) await new Promise(res => setTimeout(res, delays[i]));
      try {
        return await fetchOnce();
      } catch (err) {
        lastErr = err;
        console.warn(`kline.json attempt ${i + 1} failed: ${err.message}`);
      }
    }
    // 全失敗:清 promise(下次 modal 開可重試)+ 拋錯(讓 _loadStockKline 的
    // catch 顯「載入失敗」而非「本檔尚無」誤訊息)
    _klineAllPromise = null;
    throw lastErr || new Error('kline.json all retries failed');
  })();
  return _klineAllPromise;
}

async function _fetchKline(ticker) {
  if (_klineCache[ticker]) return _klineCache[ticker];
  const all = await _loadKlineAll();
  const arr = all[ticker] || [];
  if (arr.length) _klineCache[ticker] = arr;
  return arr;
}

function _renderStockKline() {
  const container = document.getElementById('art-kline-chart');
  if (!container || !_klineData) return;
  // dispose previous chart(period 切換或重開 modal)
  if (_klineChart) {
    try { _klineChart.remove(); } catch (e) {}
    _klineChart = null;
  }
  const days = _KLINE_PERIOD_DAYS[_klinePeriod];
  let data = _klineData;
  if (days && data.length > days) data = data.slice(-days);
  // data row 格式: [d, o, h, l, c, v]
  const candles = data.map(r => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] }));
  const volumes = data.map(r => ({
    time: r[0], value: r[5] || 0,
    color: (r[4] >= r[1]) ? 'rgba(239,83,80,.5)' : 'rgba(38,166,154,.5)',  // 紅漲綠跌 亞洲
  }));
  _klineChart = LightweightCharts.createChart(container, {
    layout: { background: { type: 'solid', color: 'transparent' },
              textColor: '#7c8290', attributionLogo: false },
    grid: { vertLines: { color: 'rgba(255,255,255,.04)' },
            horzLines: { color: 'rgba(255,255,255,.04)' } },
    rightPriceScale: { borderColor: 'rgba(255,255,255,.08)',
                       scaleMargins: { top: 0.05, bottom: 0.28 } },
    timeScale: { borderColor: 'rgba(255,255,255,.08)', timeVisible: false },
    crosshair: { mode: 1 },
    autoSize: true,
    handleScroll: { mouseWheel: false, pressedMouseMove: true,
                    horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { mouseWheel: false, axisPressedMouseMove: true, pinch: true },
  });
  const candleSeries = _klineChart.addCandlestickSeries({
    upColor: '#ef5350', downColor: '#26a69a',
    borderUpColor: '#ef5350', borderDownColor: '#26a69a',
    wickUpColor: '#ef5350', wickDownColor: '#26a69a',
  });
  candleSeries.setData(candles);
  const volSeries = _klineChart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
  });
  volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
  volSeries.setData(volumes);
  _klineChart.timeScale().fitContent();
}

function setKlinePeriod(p) {
  if (p === _klinePeriod) return;
  _klinePeriod = p;
  document.querySelectorAll('.art-kline-chip').forEach(b =>
    b.classList.toggle('active', b.dataset.period === p));
  _renderStockKline();
}

/* showAetfTab: 主動式 ETF 頁 tab 切換(per-ETF) */
function showAetfTab(code) {
  document.querySelectorAll('.aetf-tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.aetf === code));
  document.querySelectorAll('.aetf-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.aetfPane === code));
}

/* 台股休市日(YYYY-MM-DD)— 用於「資料已更新 n/total」判定交易日。
   每交易日 13:30 收盤後該計數歸零、隨各家 ETF 公布回補;週末 / 休市日不歸零。
   ⚠ 每年需更新一次。以下 2026 為推估,請以 TWSE 官方「有價證券集中交易市場
   開（休）市日期」公告為準(尤其農曆春節休市天數與補假)。 */
const IIA_TW_HOLIDAYS = new Set([
  // 2026(待 TWSE 官方核對)
  '2026-01-01',                                           // 元旦
  '2026-02-13', '2026-02-16', '2026-02-17', '2026-02-18',
  '2026-02-19', '2026-02-20',                             // 農曆春節
  '2026-02-27',                                           // 和平紀念日(2/28 週六)補假
  '2026-04-03', '2026-04-06',                             // 兒童節 / 清明連假
  '2026-05-01',                                           // 勞動節
  '2026-06-19',                                           // 端午節
  '2026-09-25',                                           // 中秋節
  '2026-10-09',                                           // 國慶日(10/10 週六)補假
]);

/* 台北現在時間(UTC+8):回 {date:'YYYY-MM-DD', dow:0-6, mins:當日分鐘數} */
function _twNowParts() {
  const tw = new Date(Date.now() + new Date().getTimezoneOffset() * 60000 + 8 * 3600000);
  return { date: tw.toISOString().slice(0, 10), dow: tw.getUTCDay(),
           mins: tw.getUTCHours() * 60 + tw.getUTCMinutes() };
}

/* 「資料已更新 n/total」即時計算:交易日(平日且非休市日)13:30 收盤後 →
   目標日 = 今日(尚無資料 → 歸零,隨各家公布回補);其餘 → 目標日 = 最新資料日。 */
function aetfUpdateBadge() {
  const P = window.IIA_AETF_UPDATE, el = document.getElementById('aetf-update-badge');
  if (!P || !el) return;
  const t = _twNowParts();
  const tradingDay = t.dow >= 1 && t.dow <= 5 && !IIA_TW_HOLIDAYS.has(t.date);
  const afterClose = t.mins >= 13 * 60 + 30;
  const target = (tradingDay && afterClose) ? t.date : P.latest;
  const n = (P.dates || []).filter(d => d === target).length;
  const total = P.total, full = n >= total;
  el.className = 'aetf-update-badge ' + (full ? 'aetf-done-full' : 'aetf-done-partial');
  el.innerHTML = '資料已更新 <b>' + n + '/' + total + '</b>'
    + (full ? '' : ' · 尚有 ' + (total - n) + ' 檔待今日資料');
}
window.aetfUpdateBadge = aetfUpdateBadge;
aetfUpdateBadge();
setInterval(aetfUpdateBadge, 60000);

/* showFocusStockTab: 焦點股頁 sub-tab 切換(交集股 int / 出量股 vol / 潛力股 pot) */
function showFocusStockTab(name) {
  document.querySelectorAll('.sub-tab-btn[data-fstab]').forEach(b =>
    b.classList.toggle('active', b.dataset.fstab === name));
  document.querySelectorAll('.fs-tab-pane').forEach(p =>
    p.classList.toggle('active', p.id === 'fstab-' + name));
}

/* sortFsTable: 焦點股 table 欄位點擊排序。每 row 帶 data-(skey),th 帶
 * data-skey + data-snum(1=數值)。點擊 toggle desc↔asc;數值缺值排尾。 */
function sortFsTable(th) {
  const table = th.closest('table');
  const tbody = table && table.querySelector('tbody');
  if (!tbody) return;
  const skey = th.dataset.skey;
  const numeric = th.dataset.snum === '1';
  const dir = th.dataset.dir === 'desc' ? 'asc' : 'desc';
  table.querySelectorAll('th[data-skey]').forEach(h => {
    const on = (h === th);
    h.dataset.dir = on ? dir : '';
    h.classList.toggle('fs-sorted-asc', on && dir === 'asc');
    h.classList.toggle('fs-sorted-desc', on && dir === 'desc');
  });
  const mul = dir === 'desc' ? -1 : 1;
  const rows = [...tbody.querySelectorAll('tr.fs-row')];
  rows.sort((a, b) => {
    let va = a.dataset[skey], vb = b.dataset[skey];
    if (numeric) {
      va = (va === '' || va == null) ? NaN : parseFloat(va);
      vb = (vb === '' || vb == null) ? NaN : parseFloat(vb);
      const an = isNaN(va), bn = isNaN(vb);
      if (an && bn) return 0;
      if (an) return 1;   // 缺值永遠排尾(不受方向影響)
      if (bn) return -1;
      return (va - vb) * mul;
    }
    return String(va || '').localeCompare(String(vb || '')) * mul;
  });
  rows.forEach(r => tbody.appendChild(r));
}

/* toggleFsFilter: 交集股「符合條件」篩選列。多選 AND —— row 的 data-matched
 * 必須涵蓋所有 active 條件才顯示;再點 active 鈕即取消(toggle);
 * 無任何 active = 全部顯示。 */
function toggleFsFilter(btn) {
  btn.classList.toggle('active');
  const active = [...document.querySelectorAll('#fstab-int .fs-filter-btn.active')]
    .map(b => b.dataset.cond);
  let visible = 0;
  document.querySelectorAll('#fstab-int .fs-row').forEach(row => {
    const matched = (row.dataset.matched || '').split(',').filter(Boolean);
    const show = active.every(c => matched.includes(c));
    row.hidden = !show;
    if (show) visible++;
  });
  // 計數即時更新(交集股「共 N 檔」)
  const cnt = document.getElementById('fs-int-count');
  if (cnt) cnt.textContent = visible;
  // 動畫:篩選後可見列 fade-in-up
  document.querySelectorAll('#fstab-int .fs-row:not([hidden])').forEach(row => {
    row.animate(
      [{ opacity: 0, transform: 'translateY(-4px)' }, { opacity: 1, transform: 'none' }],
      { duration: 200, easing: 'ease-out' });
  });
  // 個股 modal scope 同步由 MutationObserver 統一處理(showArtModal 內 observe
  // _artScopeContainer 的 hidden / childList 變化),這裡無需顯式呼叫。
}

/* Merged cluster name — 計算螢幕對應 visible 閾值並產出 "+N ▾" / "收合 ▴" */
function _mergedVisibleCount() {
  const w = window.innerWidth;
  if (w <= 480) return 2;
  if (w <= 900) return 3;
  return Infinity;
}

function _refreshClusterToggle(el) {
  const btn = el.querySelector('.cn-toggle');
  if (!btn) return;
  const parts = parseInt(el.dataset.parts, 10) || 0;
  if (el.classList.contains('expanded')) {
    btn.textContent = '收合 ▴';
    return;
  }
  const visible = _mergedVisibleCount();
  if (parts > visible) {
    btn.textContent = '+' + (parts - visible) + ' ▾';
  } else {
    btn.textContent = '';
  }
}

function toggleClusterName(btn) {
  const el = btn.closest('.cn-merged');
  if (!el) return;
  el.classList.toggle('expanded');
  _refreshClusterToggle(el);
}

/* cluster-name 點擊展開/收合:用 CSS .expanded 切 white-space:nowrap → normal
 * 取代之前的 30 字硬閾值。寬度由瀏覽器 layout 自動判斷(cluster-hdr nowrap
 * + cluster-name flex:1 + ellipsis),空間不夠就 ellipsis 自動截尾,
 * 不會把 sparkline 擠到下一行;hover 顯 title attr 全名,點擊解 nowrap
 * 多行展開。 */
function toggleNameExpand(el) {
  el.classList.toggle('expanded');
}

function _initMergedNames() {
  document.querySelectorAll('.cn-merged').forEach(_refreshClusterToggle);
}
window.addEventListener('load', _initMergedNames);
window.addEventListener('resize', _initMergedNames);

/* 頁面 load 時刷一次 sort UI 狀態 + 跑 _recalcClusters 把 cluster meta
 * 文字校正成「平均乖離 X%」(Python 初始 render 只寫「N 檔焦點 · TV」)。
 * 因 Python 端已 pre-sort by bias desc,DOM 順序跟 JS 算出來一致 →
 * FLIP 動畫 dy≈0 不會跳。 */
window.addEventListener('load', () => {
  const C = window.IIA_CLUSTERS || {};
  ['hl_sub', 'pan_sub', 'sub'].forEach(lv => {
    if (typeof _refreshSortUi === 'function') _refreshSortUi(lv);
    if (typeof _recalcClusters === 'function' && C[lv]) _recalcClusters(lv);
  });
});

/* 廣泛概念股濾除 — 點 univ-chip 把該 ticker 在每個 cluster 內反灰、
 * cluster meta 重算、整列依 activeTv 重排(FLIP 動畫)。state 全域共用,
 * 兩 sub-tab(hl_sub / pan_sub)的 cluster 都受影響。 */
const _univDis = new Set();

/* cluster 排序 state per level('hl_sub' / 'pan_sub'),預設 'chg' desc。
 * 重複點同一個 chip → 切 desc ↔ asc;切不同 key → 重置 desc。
 * 兩 tab 各管自己的 state,sort chip 用 data-level 鎖定該 tab。 */
const _clusterSort = {};      // level -> 'chg' / 'bias' / ...
const _clusterSortDir = {};   // level -> 'desc' / 'asc'
function _getSortKey(level)  { return _clusterSort[level] || 'chg'; }
function _getSortDir(level)  { return _clusterSortDir[level] || 'desc'; }
/* 只刷該 level 的 sort-chip(只影響該 sub-tab),不會誤動別 tab */
function _refreshSortUi(level) {
  const key = _getSortKey(level), dir = _getSortDir(level);
  document.querySelectorAll('.sort-chip[data-level="' + level + '"]').forEach(b => {
    const on = b.dataset.sort === key;
    b.classList.toggle('active', on);
    b.dataset.dir = on ? dir : '';
  });
}

/* ── Per-cluster focal sort ─────────────────────────────────────────────────
 * cluster header 的 metric badge(乖離/漲跌/PE/殖利/β)點擊只動該題材
 * 內的 focal pill 順序,不影響外層 cluster 排序。state per cardId,
 * 預設 bias desc(對齊 Python 端 focal_sorted 初始順序)。 */
const _focalSort = new Map();  // cardId -> { key, dir }
function _getFocalSort(cardId) {
  if (!_focalSort.has(cardId)) _focalSort.set(cardId, { key: 'chg', dir: 'desc' });
  return _focalSort.get(cardId);
}
function setFocalSort(cardId, key) {
  const cur = _getFocalSort(cardId);
  if (cur.key === key) cur.dir = cur.dir === 'desc' ? 'asc' : 'desc';
  else { cur.key = key; cur.dir = 'desc'; }
  _renderFocalSort(cardId);
}

/* 依排序 key 算 pill 報價括號內的內容 + 顏色 class。
 * chg(預設):「close(±X.XX%)」沿用既有格式不加 prefix
 * 其他:「close(prefix value)」加維度 prefix,避免使用者混淆是哪一項 */
function _focalQuoteByKey(f, key) {
  if (f.close == null) {
    // 沒收盤價就只顯該維度數字
    if (key === 'chg') { const p = _fmtPctJs(f.chg); return { str: p.str, cls: p.cls }; }
    return { str: '—', cls: 'neutral' };
  }
  const closeStr = f.close.toFixed(2);
  if (key === 'chg') {
    const p = _fmtPctJs(f.chg);
    return { str: closeStr + (f.chg != null ? '(' + p.str + ')' : ''), cls: p.cls };
  }
  if (key === 'bias') {
    const v = f.bias;
    if (v == null) return { str: closeStr + '(乖離 —)', cls: 'neutral' };
    const sign = v > 0 ? '+' : '';
    const cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'flat');
    return { str: closeStr + '(乖離 ' + sign + v.toFixed(2) + '%)', cls };
  }
  if (key === 'pe') {
    const v = f.pe;
    return { str: closeStr + '(PE ' + (v == null || v <= 0 ? '—' : v.toFixed(1)) + ')', cls: 'neutral' };
  }
  if (key === 'tv') {
    const v = f.tv;
    if (v == null || v <= 0) return { str: closeStr + '(成交 —)', cls: 'neutral' };
    return { str: closeStr + '(成交 ' + (v / 1e8).toFixed(0) + '億)', cls: 'neutral' };
  }
  // 2026-05-18 起 yield/beta 全站移除,fallback 顯純 close
  return { str: closeStr, cls: 'neutral' };
}

function _renderFocalSort(cardId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const cluster = _findClusterDef(cardId);
  if (!cluster) return;
  const state = _getFocalSort(cardId);
  // 排序 focal entries(skip _univDis 在外層 _recalcClusters 用 pill-disabled
  // 表達,排序這裡不過濾,保持 pill 都存在,只是順序變)。null 永遠排尾段
  // 不受方向影響(避免缺資料卡在最前面誤導,實例:5347 沒 ma20_bias)。
  const dirMul = state.dir === 'asc' ? -1 : 1;
  const sorted = cluster.focal.slice().sort((a, b) => {
    const va = a[state.key], vb = b[state.key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (vb - va) * dirMul;
  });
  // 拿 DOM pill 重排 + 更新 quote span 顯示當前 sort key 的值
  const container = card.querySelector('.cluster-focal-stocks');
  if (!container) return;
  const pillMap = {};
  container.querySelectorAll('.stk-pill[data-cluster-ticker]').forEach(p => {
    pillMap[p.dataset.clusterTicker] = p;
  });
  // 前哨 toggle button 永遠保持在 container 最末段 — 用 insertBefore
  // 把 pill 塞在 toggle 之前,避免單純 appendChild 把 pill 推到 toggle 之後
  // (那會反過來把 toggle 擠到 pill 之前,結果前哨變成最前)。
  const sntlToggleEl = container.querySelector('.sntl-toggle-inline');
  sorted.forEach(f => {
    const p = pillMap[f.ticker];
    if (!p) return;
    if (sntlToggleEl) container.insertBefore(p, sntlToggleEl);
    else container.appendChild(p);
    const q = p.querySelector('.sp-quote');
    if (q) {
      const r = _focalQuoteByKey(f, state.key);
      q.textContent = r.str;
      q.className = 'sp-quote ' + r.cls;
    }
  });
  // 更新該卡片內 badge 的 active 狀態(只此卡)
  card.querySelectorAll('.cluster-metric.metric-btn').forEach(b => {
    const on = b.dataset.sort === state.key;
    b.classList.toggle('is-active-sort', on);
    if (on) b.dataset.dir = state.dir;
    else b.removeAttribute('data-dir');
  });
}
function setClusterSort(mode, level) {
  level = level || 'sub';  // 舊頁面(沒 data-level)fallback 給 'sub'
  if (mode === _getSortKey(level)) {
    _clusterSortDir[level] = _getSortDir(level) === 'desc' ? 'asc' : 'desc';
  } else {
    _clusterSort[level] = mode;
    _clusterSortDir[level] = 'desc';
  }
  _refreshSortUi(level);
  _recalcClusters(level);
}
/* ── 多題材股篩選(2026-05-20)──────────────────────────────────────────────
 * 點 univ-chip → 該 sub-tab 內只留含此 ticker 的 cluster,其餘 collapse
 * 動畫隱藏;再點同 chip → 全部 expand 恢復。single-select per level。 */
const _multiThemeSel = {};  // level -> ticker | null

function _collapseCard(el) {
  if (el.dataset.mtAnim === 'collapsing' || el.style.display === 'none') return;
  el.dataset.mtAnim = 'collapsing';
  el.style.maxHeight = el.scrollHeight + 'px';
  el.style.overflow = 'hidden';
  void el.offsetWidth;
  el.style.transition = 'max-height .35s ease, opacity .28s ease, margin .35s ease';
  el.style.maxHeight = '0';
  el.style.opacity = '0';
  el.style.marginTop = '0';
  el.style.marginBottom = '0';
  const te = (e) => {
    if (e.propertyName !== 'max-height') return;
    el.style.display = 'none';
    el.dataset.mtAnim = '';
    el.removeEventListener('transitionend', te);
  };
  el.addEventListener('transitionend', te);
}

function _expandCard(el) {
  if (el.style.display !== 'none' && el.dataset.mtAnim !== 'collapsing') return;
  el.dataset.mtAnim = 'expanding';
  el.style.display = '';
  el.style.transition = 'none';
  el.style.maxHeight = '0';
  el.style.opacity = '0';
  el.style.overflow = 'hidden';
  void el.offsetWidth;
  el.style.transition = 'max-height .35s ease, opacity .28s ease, margin .35s ease';
  el.style.maxHeight = el.scrollHeight + 'px';
  el.style.opacity = '1';
  el.style.marginTop = '';
  el.style.marginBottom = '';
  const te = (e) => {
    if (e.propertyName !== 'max-height') return;
    // 還原 inline style,避免 max-height 卡住後續內容變動(tooltip / sentinel 展開)
    el.style.maxHeight = '';
    el.style.overflow = '';
    el.style.transition = '';
    el.style.opacity = '';
    el.dataset.mtAnim = '';
    el.removeEventListener('transitionend', te);
  };
  el.addEventListener('transitionend', te);
}

function toggleMultiTheme(ticker, level) {
  const next = (_multiThemeSel[level] === ticker) ? null : ticker;
  _multiThemeSel[level] = next;
  // chip 高亮:single-select,同 level 只 1 個 active
  document.querySelectorAll('.univ-chip[data-level="' + level + '"]').forEach(b => {
    b.classList.toggle('mt-active', next !== null && b.dataset.ticker === next);
  });
  const clusters = (window.IIA_CLUSTERS || {})[level] || [];
  // 先清掉本 level 內既有的 pill 高亮(切換 ticker / 取消篩選時)
  const _container = document.getElementById('cluster-container-' + level);
  (_container || document).querySelectorAll('.stk-pill.pill-flash')
    .forEach(p => p.classList.remove('pill-flash'));
  const _pillSel = next == null ? null :
    '.stk-pill[data-cluster-ticker="' +
    (window.CSS && CSS.escape ? CSS.escape(next) : next) + '"]';
  clusters.forEach(c => {
    const el = document.getElementById(c.cardId);
    if (!el) return;
    const shouldShow = (next == null) ||
      (c.focal || []).some(f => f.ticker === next);
    if (shouldShow) _expandCard(el);
    else _collapseCard(el);
    // 留下的題材內,把該個股的 pill 閃爍高亮(同全站搜尋效果)
    if (shouldShow && _pillSel) {
      el.querySelectorAll(_pillSel).forEach(pill => {
        pill.classList.remove('pill-flash');
        void pill.offsetWidth;  // restart animation
        pill.classList.add('pill-flash');
      });
    }
  });
}

function _recalcClusters(level) {
  const container = document.getElementById('cluster-container-' + level);
  if (!container) return;
  const clusters = (window.IIA_CLUSTERS || {})[level] || [];
  if (!clusters.length) return;

  const cardEls = {};
  clusters.forEach(c => {
    const el = document.getElementById(c.cardId);
    if (el) cardEls[c.cardId] = el;
  });

  // F — record positions BEFORE
  const firsts = {};
  Object.entries(cardEls).forEach(([id, el]) => {
    if (el.style.display !== 'none') firsts[id] = el.getBoundingClientRect();
  });

  // 1. focal pill 反灰
  clusters.forEach(c => {
    const el = cardEls[c.cardId];
    if (!el) return;
    el.querySelectorAll('[data-cluster-ticker]').forEach(pill => {
      pill.classList.toggle('pill-disabled', _univDis.has(pill.dataset.clusterTicker));
    });
  });

  // 2. 重算每個 cluster 的 active 狀態 + 6 維 sort 值(PE 跟 Python 一致 skip ≤ 0)
  const _mean = (arr) => {
    const xs = arr.filter(v => v != null);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  };
  const states = clusters.map(c => {
    const activeFocal = c.focal.filter(f => !_univDis.has(f.ticker));
    const disabledTv  = c.focal.reduce((s, f) => _univDis.has(f.ticker) ? s + f.tv : s, 0);
    return {
      cardId: c.cardId,
      activeFocal,
      activeTv: c.baseTv - disabledTv,
      visible: activeFocal.length > 0,
      avgChg:   _mean(activeFocal.map(f => f.chg)),
      avgBias:  _mean(activeFocal.map(f => f.bias)),
      avgPe:    _mean(activeFocal.map(f => (f.pe != null && f.pe > 0) ? f.pe : null)),
      avgPeg:   _mean(activeFocal.map(f => (f.peg != null && f.peg > 0) ? f.peg : null)),
    };
  });

  // 3. 卡片顯示 / 隱藏 + meta 更新(meta 依 _clusterSort 顯不同維度)
  // (2026-05-18 起殖利率/β 全站移除,META_FMT 只剩 tv / chg / bias / pe)
  const _fmtPct2 = (v) => v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + '%';
  const _pctCls = (v) => v == null ? 'neutral' : (v > 0 ? 'up' : v < 0 ? 'down' : 'flat');
  const META_FMT = {
    tv:    { label: '成交額',  val: (s) => (s.activeTv / 1e8).toFixed(0) + '億',         cls: (s) => 'neutral' },
    chg:   { label: '平均漲跌', val: (s) => _fmtPct2(s.avgChg),                          cls: (s) => _pctCls(s.avgChg) },
    bias:  { label: '平均乖離', val: (s) => _fmtPct2(s.avgBias),                         cls: (s) => _pctCls(s.avgBias) },
    pe:    { label: '平均 PE',  val: (s) => s.avgPe == null ? '—' : s.avgPe.toFixed(1),  cls: (s) => 'neutral' },
    peg:   { label: '平均 PEG', val: (s) => s.avgPeg == null ? '—' : s.avgPeg.toFixed(2), cls: (s) => 'neutral' },
  };
  const _sortKey = _getSortKey(level);
  const _sortDir = _getSortDir(level);
  const fmt = META_FMT[_sortKey] || META_FMT.tv;
  states.forEach(s => {
    const el = cardEls[s.cardId];
    if (!el) return;
    if (!s.visible) { el.style.display = 'none'; return; }
    el.style.display = '';
    // 2026-05-19 起 cluster-meta 文字 (「N 檔焦點 · X」) 移除 — focal 數一目了然,
    // metric 已變 sortable badges(漲跌 / 乖離 / PE / 成交)。.cluster-meta
    // span 仍存在僅作 cluster-hdr flex spacer(margin-left:auto)hook 把
    // spark-btn 推到最右。
  });

  // 4. 依 per-level _clusterSort 重排 DOM(None 排到最後,不受方向影響)
  const _key = (s) => {
    if (_sortKey === 'chg')   return s.avgChg;
    if (_sortKey === 'bias')  return s.avgBias;
    if (_sortKey === 'pe')    return s.avgPe;
    if (_sortKey === 'peg')   return s.avgPeg;
    return s.activeTv;  // 'tv' default
  };
  const _dirMul = _sortDir === 'asc' ? -1 : 1;
  const visibleSorted = states.filter(s => s.visible).sort((a, b) => {
    const va = _key(a), vb = _key(b);
    // null 永遠排尾段(無論 asc/desc),避免缺資料 cluster 卡在最前面
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (vb - va) * _dirMul;
  });
  visibleSorted.forEach(s => {
    const el = cardEls[s.cardId];
    if (el) container.appendChild(el);
  });

  // L+I+P — FLIP
  const lasts = {};
  Object.entries(cardEls).forEach(([id, el]) => {
    if (el.style.display !== 'none') lasts[id] = el.getBoundingClientRect();
  });
  const animated = [];
  Object.keys(firsts).forEach(id => {
    const el = cardEls[id];
    if (!el || !lasts[id]) return;
    const dy = firsts[id].top - lasts[id].top;
    if (Math.abs(dy) < 1) return;
    el.style.transition = 'none';
    el.style.transform = 'translateY(' + dy + 'px)';
    animated.push(el);
  });
  if (animated.length) {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      animated.forEach(el => {
        el.style.transition = 'transform .38s cubic-bezier(.25,.46,.45,.94)';
        el.style.transform = '';
      });
    }));
  }
}

/* ── Theme chart modal — 6 個月 TV / 平均漲跌 趨勢 ────────────────────────── */
/* IIA_HISTORY / IIA_INDEX_HISTORY 不再 inline(~1 MB),改 fetch history.json,
 * 由 openThemeChart 首次點擊時觸發。後續同 session 一次就好。 */
let _historyLoadPromise = null;
function _loadHistory() {
  if (window.IIA_HISTORY) return Promise.resolve();
  if (_historyLoadPromise) return _historyLoadPromise;
  _historyLoadPromise = fetch('history.json', { cache: 'no-cache' })
    .then(r => { if (!r.ok) throw new Error('history.json ' + r.status); return r.json(); })
    .then(data => {
      window.IIA_HISTORY = data.history || {};
      window.IIA_INDEX_HISTORY = data.index || {};
      window.IIA_TICKER_CLOSE = data.ticker_close || {};  // Q13 per-ticker 400 天 close+shares
      // ticker_net_inst:per-ticker daily 法人淨買賣股數;hl_sub cluster
      // 也能拿到 net_inst(從 focal ticker 在「其他 main」row 內 backfill)
      const tni = data.ticker_net_inst || {};
      const tniIdx = {};
      for (const tk in tni) {
        const m = {};
        (tni[tk] || []).forEach(p => { m[p.d] = p.n; });
        tniIdx[tk] = m;
      }
      window.IIA_TICKER_NET_INST = tniIdx;
    })
    .catch(err => {
      _historyLoadPromise = null;  // 失敗時可重試
      throw err;
    });
  return _historyLoadPromise;
}

let _lwcLoadPromise = null;
let _openThemeCardId = null;       // 目前打開的 cluster cardId(null = 關)
let _tcSort = 'chg';               // chart modal 自己的排序 key(獨立於外層頁面)
let _tcCharts = { net: null, price: null, netSeries: null,
                    clusterSeries: null, twiiSeries: null, tpexSeries: null };
const _lineVis = { cluster: true, twii: true, tpex: true };
// chart mode:'index' = 焦點股加權 vs 大盤(現狀);'strength' = focal 個股各
// 自一條 line(rebase 100 from startDate)互比強弱。左側 ticker chip toggle
// 在兩 mode 下都動態 hide/show。
let _chartMode = 'index';
// 時間粒度('1m'/'3m'/'6m'/'1y'/'all'),預設 6m,點 chip 切換
let _chartPeriod = '6m';
const _PERIOD_DAYS = { '1m': 30, '3m': 90, '6m': 180, '1y': 365 };
// Modal 內 ticker disable set(每次 openThemeChart 都會清空,不影響外層 _univDis)
let _modalTickerDis = new Set();
// 三大法人 histogram 模式:'daily'=當日值、'cum'=累計
let _netMode = 'daily';

/* 給定 series([{time:'YYYY-MM-DD',...}, ...]),按 _chartPeriod 截尾段。
 * cutoff 用 series 最末天往回推(不是 today),避免週末/假期讓 1m 變空。
 * 'all' 或無 mapping 不過濾。 */
function _filterByPeriod(series) {
  if (!series || !series.length || _chartPeriod === 'all') return series;
  const days = _PERIOD_DAYS[_chartPeriod];
  if (!days) return series;
  const lastTime = series[series.length - 1].time;
  const lastMs = new Date(lastTime + 'T00:00:00Z').getTime();
  const cutoffMs = lastMs - days * 86400000;
  const cutoff = new Date(cutoffMs).toISOString().slice(0, 10);
  return series.filter(p => p.time >= cutoff);
}

function setChartPeriod(p) {
  if (p === _chartPeriod) return;
  _chartPeriod = p;
  document.querySelectorAll('.tc-period-chip').forEach(b => {
    b.classList.toggle('active', b.dataset.period === p);
  });
  if (_openThemeCardId) _renderThemeChart(_openThemeCardId);
}

function _loadLightweightCharts() {
  if (window.LightweightCharts) return Promise.resolve();
  if (_lwcLoadPromise) return _lwcLoadPromise;
  _lwcLoadPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js';
    s.onload = () => resolve();
    s.onerror = (e) => { _lwcLoadPromise = null; reject(e); };
    document.head.appendChild(s);
  });
  return _lwcLoadPromise;
}

function _findClusterDef(cardId) {
  // 跨 sub-tab(hl_sub / pan_sub / sub legacy)找 cluster def
  const C = window.IIA_CLUSTERS || {};
  for (const lv of ['hl_sub', 'pan_sub', 'sub']) {
    const hit = (C[lv] || []).find(c => c.cardId === cardId);
    if (hit) return hit;
  }
  return null;
}

/* 算單一 cluster 的 daily series:
 *   - netSeries:三大法人淨流入(億),用真實當日值,不 forward-fill
 *     (法人買賣超是日結 transaction,沒交易=0,不能用昨日延伸)
 *   - priceSeries:market-cap = Σ(close × shares_out) per day,
 *     **per-ticker forward-fill**(歷史上焦點股不一定每天都在 top-50,
 *     缺的日子用該檔上一次有資料的 close × shares 延續,標準加權指數做法)
 *     之後 _rebaseSeries 把它 rebase 到 100。
 * payload 5-tuple [tv, chg, close, net_inst, shares_out]
 * 鎖定今天的 cluster.focal + cluster.sentinel ticker set(2026-05-24 起 sentinel
 * 也納入計算),**同時套 _univDis(外層) + _modalTickerDis(modal 內)** 過濾。 */
function _computeClusterSeries(cluster, opts) {
  // opts.ignoreModalDis: 算「全部標的」baseline 用 —— 忽略 _modalTickerDis 內 ticker,
  // 仍套 _univDis(外層概念股 disable 屬全域層級,不該被 modal 蓋掉)。
  opts = opts || {};
  const hist = window.IIA_HISTORY || {};
  const tch  = window.IIA_TICKER_CLOSE || {};       // Q13:per-ticker 400 天 close+shares
  const tnet = window.IIA_TICKER_NET_INST || {};    // per-ticker daily net_inst(跨 main 索引)
  const keys = cluster.memberKeys || [];
  // 2026-05-24 起 modal 圖表(加權指數 + 三大法人)計算納入 sentinel,讓
  // 題材完整面貌可見;原本只取 cluster.focal,sentinel 不進 modal 計算。
  const todayMembers = [...new Set([
    ...(cluster.focal || []).map(f => f.ticker),
    ...(cluster.sentinel || []).map(f => f.ticker),
  ])].filter(t => !_univDis.has(t) && (opts.ignoreModalDis || !_modalTickerDis.has(t)));

  // 收集所有出現過的 dates(ticker_close ∪ ticker_net_inst ∪ theme_history)
  const dateSet = new Set();
  todayMembers.forEach(t => (tch[t] || []).forEach(p => dateSet.add(p.d)));
  todayMembers.forEach(t => Object.keys(tnet[t] || {}).forEach(d => dateSet.add(d)));
  keys.forEach(k => (hist[k] || []).forEach(row => dateSet.add(row.d)));
  const dates = [...dateSet].sort();
  if (!dates.length) return { netSeries: [], priceSeries: [] };

  // 三個資料源:
  //   ticker_close[ticker] = [{d, c, s}, ...]  ← 400 天 close+shares,所有 focal 都有
  //   ticker_net_inst[ticker][date] = net_shares ← 跨 main 反向索引,hl_sub 也能拿
  //   hist[key].s[ticker] = [tv,chg,close,net,shares] ← 舊路徑當 fallback
  const raw = {};   // ticker -> {date -> {close, shares, net}}
  todayMembers.forEach(t => {
    raw[t] = {};
    // 1) ticker_close 的 close+shares
    (tch[t] || []).forEach(p => {
      raw[t][p.d] = { close: p.c, shares: p.s, net: null };
    });
    // 2) ticker_net_inst 的 net(per-ticker,跨 main 已合一)
    const tnetMap = tnet[t] || {};
    Object.entries(tnetMap).forEach(([d, n]) => {
      const slot = raw[t][d] || (raw[t][d] = { close: null, shares: null, net: null });
      slot.net = n;
    });
    // 3) fallback 從 hist 補 close/shares/net(舊路徑;新路徑沒值的話)
    keys.forEach(k => {
      (hist[k] || []).forEach(row => {
        const v = (row.s || {})[t];
        if (!v) return;
        const slot = raw[t][row.d] || (raw[t][row.d] = { close: null, shares: null, net: null });
        if (slot.close == null && v[2] != null) slot.close = v[2];
        if (slot.shares == null && v[4] != null) slot.shares = v[4];
        if (slot.net == null && v[3] != null) slot.net = v[3];
      });
    });
  });

  // per-ticker forward-fill close/shares (net 不 fill,法人買賣超是 daily transaction)
  const filled = {};
  todayMembers.forEach(t => {
    filled[t] = {};
    let lastClose = null, lastShares = null;
    dates.forEach(d => {
      const day = raw[t][d];
      if (day && day.close != null) lastClose = day.close;
      if (day && day.shares != null) lastShares = day.shares;
      if (lastClose != null && lastShares != null) {
        filled[t][d] = { close: lastClose, shares: lastShares };
      }
    });
  });

  // 合成 daily mcap (filled) + daily net (raw only)
  const netSeries = [];
  const priceSeries = [];
  dates.forEach(d => {
    let mcap = 0, net = 0;
    todayMembers.forEach(t => {
      const f = filled[t][d];
      if (f) mcap += f.close * f.shares;
      const r = raw[t][d];
      if (r && r.net != null) net += r.net;
    });
    const netBn = net / 1e8;
    netSeries.push({
      time: d, value: netBn,
      color: netBn >= 0 ? 'rgba(239,83,80,.8)' : 'rgba(38,166,154,.8)',
    });
    if (mcap > 0) priceSeries.push({ time: d, value: mcap });
  });
  return { netSeries, priceSeries };
}

/* rebase series to 100 at common start date,回傳 {time, value} list。
 * common start 取三條線的最晚開始日,確保起點對齊。
 * 若 series 為空 / 無 base 對應 → 回 [] */
function _rebaseSeries(series, startDate) {
  if (!series || !series.length) return [];
  const base = series.find(p => p.time >= startDate);
  if (!base || !base.value) return [];
  return series
    .filter(p => p.time >= startDate)
    .map(p => ({ time: p.time, value: +(p.value / base.value * 100).toFixed(2) }));
}

/* 從 IIA_INDEX_HISTORY 撈大盤 / 櫃買的 (time, close) series */
function _computeIndexSeries(key) {
  const arr = (window.IIA_INDEX_HISTORY || {})[key] || [];
  return arr.map(p => ({ time: p.d, value: p.close }));
}

function _disposeThemeCharts() {
  ['net', 'price'].forEach(k => {
    if (_tcCharts[k]) {
      try { _tcCharts[k].remove(); } catch (e) {}
      _tcCharts[k] = null;
    }
  });
  _tcCharts.netSeries = null;
  _tcCharts.clusterSeries = null;
  _tcCharts.twiiSeries = null;
  _tcCharts.tpexSeries = null;
  _tcCharts.tickerSeriesList = null;
}

/* 把當日 netSeries 轉成滾動累計;color 依累計值正負重算 */
function _applyNetMode(series) {
  if (_netMode !== 'cum' || !series.length) return series;
  let acc = 0;
  return series.map(p => {
    acc += p.value;
    return {
      time: p.time, value: +acc.toFixed(2),
      color: acc >= 0 ? 'rgba(239,83,80,.8)' : 'rgba(38,166,154,.8)',
    };
  });
}

/* JS 版本的 fmt_pct(對齊 Python helpers.fmt_pct 行為,亞洲紅漲綠跌) */
function _fmtPctJs(v) {
  if (v == null) return { str: '—', cls: 'neutral' };
  if (v > 0)  return { str: '+' + v.toFixed(2) + '%', cls: 'up' };
  if (v < 0)  return { str: v.toFixed(2) + '%', cls: 'down' };
  return { str: '0.00%', cls: 'flat' };
}
/* HTML escape — modal chip 內 ticker / name 都會塞回 DOM,防注入 */
function _escHtml(s) {
  s = String(s == null ? '' : s);
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* 顯示用 ticker:台股拿掉 .TW / .TWO 後綴。僅供畫面文字 ——
 * data-ticker / onclick 參數仍須用原值(history.json series key 比對吃完整 symbol) */
function _dispTk(t) {
  return String(t == null ? '' : t).replace(/\.TWO?$/i, '');
}

/* Modal 的 ticker chip 列表渲染。狀態 = _modalTickerDis ∪ _univDis(外層已 disable 的不顯示)。
 * 點擊 toggle modal-only disable,然後 re-render(setData 路徑,不 dispose)。
 * Chip 結構複用 .stk-pill 全站樣式(sp-ticker / mkt-badge / sp-name / sp-quote),
 * 加 .modal-tk-pill 給 cursor + disable 視覺 */
function _renderTickerChips(cluster) {
  const box = document.getElementById('tc-ticker-chips');
  if (!box) return;
  // 左欄垂直列表 = focal + sentinel(2026-05-24 起);依當日成交金額 desc 排序。
  // sentinel = 同題材今日 chg<-3 的成員,原本只在熱門題材卡的「前哨」摺疊區,
  // 不進 modal;改為一併納入(modal 圖表計算也含 sentinel,見 _computeClusterSeries)。
  const members = [...(cluster.focal || []), ...(cluster.sentinel || [])]
    .filter(f => !_univDis.has(f.ticker))
    .slice().sort((a, b) => (b.tv || 0) - (a.tv || 0));
  box.innerHTML = members.map(f => {
    const dis = _modalTickerDis.has(f.ticker) ? ' is-dis' : '';
    const pct = _fmtPctJs(f.chg);
    let quote;
    if (f.close != null) {
      quote = f.close.toFixed(2) + (f.chg != null ? '(' + pct.str + ')' : '');
    } else {
      quote = pct.str;
    }
    const nameHtml = f.n ? '<span class="sp-name">' + _escHtml(f.n) + '</span>' : '';
    const tk = _escHtml(f.ticker);
    // 不顯 mkt-badge(TW/US):modal 左欄空間有限,且全部都是同一 cluster 內的標的,
    // 市場類別由 cluster 上下文已表達,pill 內再標一次是 noise
    return '<div class="stk-pill modal-tk-pill' + dis + '" '
      + 'data-ticker="' + tk + '" '
      + 'onclick="toggleModalTicker(\'' + tk + '\')">'
      + '<span class="sp-ticker">' + _escHtml(_dispTk(f.ticker)) + '</span>'
      + nameHtml
      + '<span class="sp-quote ' + pct.cls + '">' + _escHtml(quote) + '</span>'
      + '</div>';
  }).join('');
}

function toggleModalTicker(ticker) {
  if (_modalTickerDis.has(ticker)) _modalTickerDis.delete(ticker);
  else _modalTickerDis.add(ticker);
  if (_openThemeCardId) _renderThemeChart(_openThemeCardId);
}

function setNetMode(mode) {
  if (mode === _netMode) return;
  _netMode = mode;
  // 只切 .tc-net-mode 內 chip,避免誤動 chart 1 的 .tc-price-mode 內 chip
  document.querySelectorAll('.tc-net-mode .tc-mode-chip').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  if (_openThemeCardId) _renderThemeChart(_openThemeCardId);
}

/* 兩張 chart crosshair 同步:hover 在 A 時 B 也畫出垂直虛線。
 * 用 flag 防止 setCrosshairPosition 觸發對方 subscribeCrosshairMove
 * 造成 feedback loop。clearCrosshairPosition 也要對稱。 */
let _crosshairLock = false;
function _syncCrosshair(srcChart, dstChart, dstSeries) {
  srcChart.subscribeCrosshairMove(param => {
    if (_crosshairLock || !dstChart || !dstSeries) return;
    _crosshairLock = true;
    try {
      if (param.time) {
        // 找到 dst series 該時間點的值;沒對到就用 0(用來定位垂直線)
        const dstData = dstSeries.data ? dstSeries.data() : null;
        let dstVal = 0;
        if (Array.isArray(dstData)) {
          const hit = dstData.find(p => p.time === param.time);
          if (hit) dstVal = hit.value;
        }
        dstChart.setCrosshairPosition(dstVal, param.time, dstSeries);
      } else {
        dstChart.clearCrosshairPosition();
      }
    } finally { _crosshairLock = false; }
  });
}

function _renderThemeChart(cardId) {
  const cluster = _findClusterDef(cardId);
  if (!cluster) return;
  _tcUpdateCounter(cardId);
  _renderTickerChips(cluster);
  document.getElementById('tc-title').textContent = '🔸 ' + cluster.name;
  let { netSeries, priceSeries } = _computeClusterSeries(cluster);
  let twiiRaw = _computeIndexSeries('TWII');
  let tpexRaw = _computeIndexSeries('TPEX');
  // 按 _chartPeriod 截尾段(1M/3M/6M/1Y/ALL)
  netSeries = _filterByPeriod(netSeries);
  priceSeries = _filterByPeriod(priceSeries);
  twiiRaw = _filterByPeriod(twiiRaw);
  tpexRaw = _filterByPeriod(tpexRaw);
  // **關鍵**:四條線必須對齊到同一個 startDate,crosshair 垂直線才會在兩張
  // chart 的相同 X pixel(時間軸對應 pixel 一致)。否則 net 比 price 早幾天
  // 開始,X 軸 mapping 不同 → 同時間在兩圖不同位置 → 虛線錯位。
  const starts = [
    priceSeries[0]?.time, twiiRaw[0]?.time, tpexRaw[0]?.time, netSeries[0]?.time
  ].filter(Boolean).sort();
  const startDate = starts[starts.length - 1];
  netSeries = netSeries.filter(p => p.time >= startDate);
  // accumulator 在對齊後重算(累計起點要跟 startDate 一致才有意義)
  netSeries = _applyNetMode(netSeries);
  const empty = document.getElementById('tc-empty');
  const netEl = document.getElementById('tc-chart-net');
  const priceEl = document.getElementById('tc-chart-price');
  if (!netSeries.length) {
    empty.style.display = '';
    netEl.style.display = 'none';
    priceEl.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  netEl.style.display = '';
  priceEl.style.display = '';

  _disposeThemeCharts();
  const chartOpts = {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#7c8290',
      attributionLogo: false,
    },
    grid: { vertLines: { color: 'rgba(255,255,255,.04)' }, horzLines: { color: 'rgba(255,255,255,.04)' } },
    rightPriceScale: { borderColor: 'rgba(255,255,255,.08)' },
    timeScale: { borderColor: 'rgba(255,255,255,.08)', timeVisible: false },
    crosshair: { mode: 1 },
    autoSize: true,
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { mouseWheel: false, axisPressedMouseMove: true, pinch: true },
  };

  // Chart 1(上):焦點股加權指數 vs 大盤(rebase 100 from startDate)
  const clusterRebased = _rebaseSeries(priceSeries, startDate);
  const twiiRebased = _rebaseSeries(twiiRaw, startDate);
  const tpexRebased = _rebaseSeries(tpexRaw, startDate);
  _tcCharts.price = LightweightCharts.createChart(priceEl, chartOpts);
  const lineOpts = (color) => ({
    color, lineWidth: 2,
    priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
  });
  if (_chartMode === 'strength') {
    // 個股強弱 mode:focal 內每檔 enabled ticker 各一條 line,rebase 100。
    // sentinel 不畫(只關注 cluster 主力 focal,sentinel 拉進來會太擠)。
    // disabled ticker 不畫(跟左側 toggle 同步)。
    // 此 mode 不畫大盤 / 櫃買(用戶要求,純看個股強弱);legend 改顯 ticker
    // → 顏色對照(_buildStrengthLegend)。
    const focalList = (cluster.focal || []).filter(f =>
      !_univDis.has(f.ticker) && !_modalTickerDis.has(f.ticker));
    const tch = window.IIA_TICKER_CLOSE || {};
    _tcCharts.tickerSeriesList = [];
    const legendItems = [];
    focalList.forEach((f, idx) => {
      const rows = (tch[f.ticker] || [])
        .filter(p => p.c != null)
        .map(p => ({ time: p.d, value: p.c }));
      const filtered = _filterByPeriod(rows).filter(p => p.time >= startDate);
      if (!filtered.length) return;
      const rebased = _rebaseSeries(filtered, startDate);
      const color = _pickTickerColor(idx, focalList.length);
      const series = _tcCharts.price.addLineSeries({
        color, lineWidth: 2, lastValueVisible: true, priceLineVisible: false,
        priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
        // title 不設 —— ticker 號已在上方 legend 顯示,chart 內 label 留純價格
      });
      series.setData(rebased);
      _tcCharts.tickerSeriesList.push({ ticker: f.ticker, series });
      // cluster.focal 物件結構 = {ticker, n, mkt, tv, chg, close, bias, pe, peg}
      // —— name 欄位是 `n` 縮寫,不是 `name`(server payload 節省大小)
      legendItems.push({ ticker: f.ticker, name: f.n || '', color });
    });
    _renderStrengthLegend(legendItems);
  } else {
    _tcCharts.clusterSeries = _tcCharts.price.addLineSeries(lineOpts('#10b981'));
    _tcCharts.clusterSeries.setData(clusterRebased);
    _tcCharts.clusterSeries.applyOptions({ visible: _lineVis.cluster });
    _tcCharts.twiiSeries = _tcCharts.price.addLineSeries(lineOpts('#f59e0b'));
    _tcCharts.twiiSeries.setData(twiiRebased);
    _tcCharts.twiiSeries.applyOptions({ visible: _lineVis.twii });
    _tcCharts.tpexSeries = _tcCharts.price.addLineSeries(lineOpts('#94aef7'));
    _tcCharts.tpexSeries.setData(tpexRebased);
    _tcCharts.tpexSeries.applyOptions({ visible: _lineVis.tpex });
    _renderStrengthLegend([]);   // index mode 清空 ticker legend
  }

  // Chart 2(下):資金淨流入流出 histogram
  _tcCharts.net = LightweightCharts.createChart(netEl, chartOpts);
  const netSer = _tcCharts.net.addHistogramSeries({
    priceFormat: { type: 'custom', formatter: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '億' },
    base: 0,
  });
  netSer.setData(netSeries);
  _tcCharts.netSeries = netSer;

  _tcCharts.price.timeScale().fitContent();
  _tcCharts.net.timeScale().fitContent();

  // **關鍵 crosshair 對齊**:lightweight-charts 的 right priceScale 寬度依
  // 內容自動撐(net 的「+800.0億」比 price 的「190.0」寬幾 px),導致兩張
  // chart 的 plot area 左邊起點錯位 → 同一時間 T 落在不同 X pixel →
  // 兩條垂直虛線會差幾 px。修法:render 完後 measure 兩邊實際寬度,
  // 取 max 套 minimumWidth(設 min 比實際寬只會多撐不會 truncate),
  // 兩張 chart 的 right scale 就完全同寬,plot area 對齊。
  // 用 requestAnimationFrame 確保 DOM layout 完成才 measure。
  requestAnimationFrame(() => {
    if (!_tcCharts.price || !_tcCharts.net) return;
    const pW = _tcCharts.price.priceScale('right').width();
    const nW = _tcCharts.net.priceScale('right').width();
    const maxW = Math.max(pW, nW);
    if (maxW > 0) {
      _tcCharts.price.priceScale('right').applyOptions({ minimumWidth: maxW });
      _tcCharts.net.priceScale('right').applyOptions({ minimumWidth: maxW });
    }
  });

  // Time-range sync(不用 logical-range):時間語意更穩,即使兩 series 點數不同
  // 也能精準對齊;搭配上面 startDate 對齊,X 軸 pixel 一致
  let _syncBusy = false;
  const syncRange = (src, dst) => src.timeScale().subscribeVisibleTimeRangeChange(r => {
    if (_syncBusy || !r || !dst) return;
    _syncBusy = true;
    try { dst.timeScale().setVisibleRange(r); } finally { _syncBusy = false; }
  });
  syncRange(_tcCharts.price, _tcCharts.net);
  syncRange(_tcCharts.net, _tcCharts.price);

  // crosshair 兩張圖雙向同步(垂直虛線貫穿兩張)
  _syncCrosshair(_tcCharts.price, _tcCharts.net, _tcCharts.netSeries);
  // strength mode 沒 clusterSeries,改用 twiiSeries(兩 mode 都存在)當參考
  _syncCrosshair(_tcCharts.net, _tcCharts.price,
    _tcCharts.clusterSeries || _tcCharts.twiiSeries);
}

function toggleIndexLine(key) {
  _lineVis[key] = !_lineVis[key];
  const seriesKeyMap = {
    cluster: 'clusterSeries', twii: 'twiiSeries', tpex: 'tpexSeries',
  };
  const seriesKey = seriesKeyMap[key];
  if (_tcCharts[seriesKey]) {
    _tcCharts[seriesKey].applyOptions({ visible: _lineVis[key] });
  }
  const btn = document.querySelector('.tc-leg-chip.leg-' + key);
  if (btn) btn.classList.toggle('active', _lineVis[key]);
}

/* 切 chart mode:index(加權指數 vs 大盤) ↔ strength(個股各自 line) */
function setChartMode(mode) {
  if (mode === _chartMode) return;
  _chartMode = mode;
  document.querySelectorAll('.tc-price-mode .tc-mode-chip').forEach(b =>
    b.classList.toggle('active', b.dataset.cmode === mode));
  // strength mode 隱「焦點股」legend chip(該 mode 沒這條總線,顯示反而混淆)
  const dlg = document.getElementById('theme-chart-dialog');
  if (dlg) dlg.classList.toggle('tc-strength', mode === 'strength');
  // index mode 顯加權 / strength mode 顯個股,只切 chart 1(下方三大法人不變)
  if (_openThemeCardId) _renderThemeChart(_openThemeCardId);
}

function _escAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
                  .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* strength mode legend:render 每檔 ticker 對應顏色色塊 + ticker 號 + 公司名 */
function _renderStrengthLegend(items) {
  const el = document.getElementById('tc-tk-legend');
  if (!el) return;
  el.innerHTML = items.map(it => {
    return `<span class="tc-tk-leg-item">`
         + `<span class="tc-tk-leg-sw" style="background:${it.color}"></span>`
         + `<span class="tc-tk-leg-tk">${_dispTk(it.ticker)}</span>`
         + (it.name ? `<span class="tc-tk-leg-nm">${_escAttr(it.name)}</span>` : '')
         + `</span>`;
  }).join('');
}

/* 個股 line palette:用 HSL hue 等分,saturation/lightness 固定。
   為了跟既有色(綠 / 橙 / 藍 / 紫)區隔,個股 hue 起點偏暖 + 高 saturation。 */
function _pickTickerColor(idx, total) {
  // 12 色 distinct palette;>12 檔 hue 開始接近但 lightness 變化區隔
  const palette = [
    '#ef4444','#f97316','#eab308','#84cc16','#22c55e','#14b8a6',
    '#06b6d4','#3b82f6','#6366f1','#8b5cf6','#d946ef','#ec4899',
  ];
  return palette[idx % palette.length];
}

/* openThemeByName: 焦點股頁「隸屬題材」chip 點擊 → 用 cluster name 反查
 * IIA_CLUSTERS.hl_sub 拿 cardId → 開熱門題材 cluster chart modal */
function openThemeByName(name) {
  const C = window.IIA_CLUSTERS || {};
  const cluster = (C.hl_sub || []).find(c => c.name === name);
  // 選股雷達點 fs-theme-chip 走 minimal 模式 —— 不顯排序 chip / counter /
  // 左右導覽(用戶要求 2026-05-25)。熱門題材 spark-btn 走預設(完整 UI)。
  if (cluster && cluster.cardId) openThemeChart(cluster.cardId, { minimal: true });
}

function openThemeChart(cardId, opts) {
  opts = opts || {};
  _openThemeCardId = cardId;
  const dlg = document.getElementById('theme-chart-dialog');
  if (!dlg) return;
  dlg.classList.toggle('tc-minimal', !!opts.minimal);
  // 首次開啟(dialog 尚未開)→ modal 排序預設 = 外層該 sub-tab 當前排序;
  // 之後 tcNavTheme / tcSetSort 在已開狀態重呼,不重設(modal 排序獨立)。
  // 已開啟時不可再 showModal(會丟 InvalidStateError)。
  if (!dlg.open) {
    const lvl = document.getElementById(cardId)?.closest('.focus-clusters')
                  ?.id.replace('cluster-container-', '');
    if (lvl && typeof _getSortKey === 'function') _tcSort = _getSortKey(lvl);
    dlg.showModal();
  }
  _tcSyncSortBar();
  // Reset modal-only state(disable set + histogram mode 都不跨 cluster 持久化)
  _modalTickerDis = new Set();
  _netMode = 'daily';
  _chartMode = 'index';
  // net chart 的「當日 / 累計」(.tc-net-mode 內)
  document.querySelectorAll('.tc-net-mode .tc-mode-chip').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === 'daily');
  });
  // chart 1 的「指數 / 個股」(.tc-price-mode 內,2026-05-26 新增)
  document.querySelectorAll('.tc-price-mode .tc-mode-chip').forEach(b => {
    b.classList.toggle('active', b.dataset.cmode === 'index');
  });
  dlg.classList.remove('tc-strength');
  // 顯示 loading hint(首次 fetch history.json 可能要 ~1 秒)
  const tcEmpty = document.getElementById('tc-empty');
  if (!window.IIA_HISTORY) {
    tcEmpty.textContent = '載入歷史資料中…';
    tcEmpty.style.display = '';
  }
  Promise.all([_loadLightweightCharts(), _loadHistory()])
    .then(() => _renderThemeChart(cardId))
    .catch(err => {
      console.error('Failed to load chart deps', err);
      tcEmpty.textContent = '圖表載入失敗';
      tcEmpty.style.display = '';
    });
}

/* _tcSortedClusters: 回傳 IIA_CLUSTERS[level] 依 modal 排序 _tcSort「由高至低」
 * 排序後的陣列。指標由 cluster.focal + cluster.sentinel 聚合(2026-05-24 起
 * sentinel 也納入計算)。tv 用 baseTv(focal-only,維持題材「熱度」基線);
 * chg/bias/pe/peg 用 focal+sentinel 平均。modal 左右導覽順序即用此 —— 與外層
 * 頁面排序無關。 */
function _tcSortedClusters(level) {
  const arr = ((window.IIA_CLUSTERS || {})[level] || []).slice();
  const avg = (members, sel) => {
    const v = members.map(sel).filter(x => x != null);
    return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
  };
  const metric = (c) => {
    const all = [...(c.focal || []), ...(c.sentinel || [])];
    if (!all.length) return null;
    if (_tcSort === 'chg')  return avg(all, f => f.chg);
    if (_tcSort === 'bias') return avg(all, f => f.bias);
    if (_tcSort === 'pe')   return avg(all, f => (f.pe != null && f.pe > 0) ? f.pe : null);
    if (_tcSort === 'peg')  return avg(all, f => (f.peg != null && f.peg > 0) ? f.peg : null);
    return (c.baseTv != null) ? c.baseTv      // 'tv'
      : all.reduce((s, f) => s + (f.tv || 0), 0);
  };
  return arr.sort((a, b) => {
    const va = metric(a), vb = metric(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;    // 缺值排尾
    if (vb == null) return -1;
    return vb - va;              // 由高至低
  });
}

/* tcNavTheme: chart modal 切換題材。dir='next'→排序中下一個(右箭頭 →)、
 * 'prev'→上一個(左箭頭 ←)。順序 = modal 自己的排序 _tcSortedClusters,
 * 環狀循環。.tc-body 做水平滑動動畫,中途 openThemeChart 重渲染。 */
function tcNavTheme(dir) {
  if (!_openThemeCardId) return;
  const container = document.getElementById(_openThemeCardId)
                      ?.closest('.focus-clusters');
  if (!container) return;
  const level = container.id.replace('cluster-container-', '');
  const sorted = _tcSortedClusters(level);
  const idx = sorted.findIndex(c => c.cardId === _openThemeCardId);
  if (idx < 0 || sorted.length < 2) return;
  const n = sorted.length;
  const tIdx = dir === 'next' ? (idx + 1) % n : (idx - 1 + n) % n;
  const targetId = sorted[tIdx].cardId;
  if (!targetId) return;
  const body = document.querySelector('#theme-chart-dialog .tc-body');
  if (!body) { openThemeChart(targetId); return; }
  const outX = dir === 'next' ? -34 : 34;
  body.animate(
    [{ opacity: 1, transform: 'translateX(0)' },
     { opacity: 0, transform: `translateX(${outX}px)` }],
    { duration: 130, easing: 'ease-in' }
  ).onfinish = () => {
    openThemeChart(targetId);
    const b2 = document.querySelector('#theme-chart-dialog .tc-body');
    if (b2) b2.animate(
      [{ opacity: 0, transform: `translateX(${-outX}px)` },
       { opacity: 1, transform: 'translateX(0)' }],
      { duration: 190, easing: 'ease-out' });
  };
}

/* tcSetSort: chart modal 上方排序長條。只改 modal 自己的排序(_tcSort,
 * 永遠由高至低)→ 決定左右導覽順序;不動外層頁面、不關 modal。
 * 切換後 modal 跳到新排序的第一個(最高)題材。 */
function tcSetSort(key) {
  if (!_openThemeCardId) return;
  const container = document.getElementById(_openThemeCardId)
                      ?.closest('.focus-clusters');
  if (!container) return;
  const level = container.id.replace('cluster-container-', '');
  _tcSort = key;
  _tcSyncSortBar();
  const sorted = _tcSortedClusters(level);
  if (sorted.length && sorted[0].cardId) openThemeChart(sorted[0].cardId);
}

/* 排序長條高亮同步 = modal 當前排序 _tcSort */
function _tcSyncSortBar() {
  document.querySelectorAll('#theme-chart-dialog .tc-sort-chip').forEach(c =>
    c.classList.toggle('active', c.dataset.sort === _tcSort));
}

/* 更新排序長條右側題材編號 N/total。N = 目前題材在 _tcSortedClusters
 * 的位次(與左右導覽 tcNavTheme 同順序),total = 該 sub-tab 題材數。
 * 點排序 chip → tcSetSort 跳第一個 → N=1;按 → 導覽 → N 遞增。 */
function _tcUpdateCounter(cardId) {
  const el = document.getElementById('tc-counter');
  if (!el) return;
  const container = document.getElementById(cardId)?.closest('.focus-clusters');
  if (!container) { el.textContent = ''; return; }
  const level = container.id.replace('cluster-container-', '');
  const sorted = _tcSortedClusters(level);
  const idx = sorted.findIndex(c => c.cardId === cardId);
  el.textContent = idx >= 0 ? (idx + 1) + '/' + sorted.length : '';
}

// 關 dialog 時清理
(function () {
  const dlg = document.getElementById('theme-chart-dialog');
  if (!dlg) return;
  dlg.addEventListener('close', () => {
    _openThemeCardId = null;
    _disposeThemeCharts();
  });
  // dim 區點擊關閉:dialog 是滿版容器,.tc-shell(唯一子節點)是整個 modal
  // 單元。暗色區是 dlg 本身未被 tc-shell 覆蓋的部分 → 只有 e.target === dlg
  // 才算點到暗色區。不可用 e.target.closest('.tc-shell'):點 ticker pill 時
  // toggleModalTicker 會同步重繪 #tc-ticker-chips 的 innerHTML,被點的 pill
  // 在事件冒泡到 dlg 前已脫離 DOM,closest() 對孤兒節點回傳 null → 誤關 modal。
  dlg.addEventListener('click', (e) => {
    if (e.target === dlg) dlg.close();
  });
  // 防止 wheel 滾動穿透到外層頁面:只有 target 在左欄 ticker 列表內才放行
  // (chart 自有 wheel zoom 處理,padding/標題等空白處則 preventDefault)
  dlg.addEventListener('wheel', (e) => {
    if (!e.target.closest('.tc-ticker-chips')) {
      e.preventDefault();
    }
  }, {passive: false});
})();

// Close modal on backdrop click
document.getElementById('art-modal').addEventListener('click', function(e) {
  if (e.target === this) this.close();
});
// modal 關閉時 dispose K 線 chart 釋放資源(lightweight-charts 物件不會自動 GC)
// + 清 scope / 導覽 state
document.getElementById('art-modal').addEventListener('close', () => {
  if (_klineChart) {
    try { _klineChart.remove(); } catch (e) {}
    _klineChart = null;
  }
  _klineData = null;
  _artScope = [];
  _artScopeIdx = -1;
  _artCurrentTicker = null;
  _artScopeContainer = null;
  if (_artScopeObserver) { _artScopeObserver.disconnect(); _artScopeObserver = null; }
});

/* ── 分享報告 ─────────────────────────────────────────────────────────────── */
/* 桌機 → 對應社群 share URL 開新視窗;手機(支援 navigator.share)→ 原生 sheet。
 * 標題 + 描述從 <meta> 取,免再 hard-code。 */
// 2026-05-19 分享按鈕(shareReport / _shareToast / navigator.share opt-in)
// 全移除,公開站頁尾不再有分享區塊。

/* ── 站內搜尋 ─────────────────────────────────────────────────────────────── */
/* 從 IIA_CLUSTERS 全部 sub-tab(hl_sub / pan_sub / sub legacy)建反向索引
 * (ticker → cluster cardId + name)。預先建一次,後續每次按鍵 O(N) linear。 */
const _searchIdx = (() => {
  const out = [];
  const seen = new Set();
  const C = window.IIA_CLUSTERS || {};
  ['hl_sub', 'pan_sub', 'sub'].flatMap(lv => C[lv] || []).forEach(c => {
    (c.focal || []).forEach(f => {
      if (seen.has(f.ticker)) return;
      seen.add(f.ticker);
      out.push({ ticker: f.ticker, name: f.n || '', cardId: c.cardId, cluster: c.name });
    });
  });
  return out;
})();

let _searchKbIdx = -1;

function onSearchInput(q) {
  const dd = document.getElementById('search-dropdown');
  q = (q || '').trim().toLowerCase();
  if (!q) { dd.hidden = true; return; }
  // ticker / 公司名 / cluster 名(子產業)三軸搜尋。dedup by ticker,
  // 同 ticker 在多 cluster 只取第一個(scrollIntoView 跳哪都合理)。
  const hits = _searchIdx.filter(it =>
    it.ticker.toLowerCase().includes(q) ||
    (it.name    && it.name.toLowerCase().includes(q)) ||
    (it.cluster && it.cluster.toLowerCase().includes(q))
  ).slice(0, 12);
  if (!hits.length) {
    dd.innerHTML = '<div class="search-empty">無相符結果(只搜尋熱門題材內的焦點股)</div>';
  } else {
    dd.innerHTML = hits.map((it, i) =>
      '<div class="search-item" data-i="' + i +
      '" data-ticker="' + it.ticker +
      '" data-card="' + it.cardId +
      '" onclick="onSearchPick(this)">' +
      '<span class="si-ticker">' + it.ticker + '</span>' +
      '<span class="si-name">' + it.name + '</span>' +
      '<span class="si-cluster">' + it.cluster + '</span>' +
      '</div>'
    ).join('');
  }
  dd.hidden = false;
  _searchKbIdx = -1;
}

function onSearchKey(e) {
  const dd = document.getElementById('search-dropdown');
  if (e.key === 'Escape') {
    dd.hidden = true;
    e.target.blur();
    return;
  }
  const items = dd.querySelectorAll('.search-item');
  if (!items.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    _searchKbIdx = e.key === 'ArrowDown'
      ? Math.min(_searchKbIdx + 1, items.length - 1)
      : Math.max(_searchKbIdx - 1, 0);
    items.forEach((it, i) => it.classList.toggle('kb-active', i === _searchKbIdx));
    items[_searchKbIdx]?.scrollIntoView({ block: 'nearest' });
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    const target = _searchKbIdx >= 0 ? items[_searchKbIdx] : items[0];
    if (target) onSearchPick(target);
  }
}

function onSearchPick(el) {
  const cardId = el.dataset.card;
  const ticker = el.dataset.ticker;
  showTab('focus');
  // 切到 cluster 所在的 sub-tab(看 cardId 開頭判 hl_sub / pan_sub)
  const card = document.getElementById(cardId);
  if (card) {
    const pane = card.closest('.sub-tab-pane');
    if (pane && pane.id) {
      const stab = pane.id.replace(/^stab-/, '');
      if (stab) showSubTab(stab);
    }
    setTimeout(() => {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.remove('search-hi');
      void card.offsetWidth;  // restart animation
      card.classList.add('search-hi');
      // 被搜尋的那檔 stk-pill 外框閃爍 3 秒
      if (ticker) {
        const pill = card.querySelector(
          '.stk-pill[data-cluster-ticker="' + (window.CSS && CSS.escape ? CSS.escape(ticker) : ticker) + '"]');
        if (pill) {
          pill.classList.remove('pill-flash');
          void pill.offsetWidth;
          pill.classList.add('pill-flash');
          setTimeout(() => pill.classList.remove('pill-flash'), 6000);
        }
      }
    }, 80);
  }
  document.getElementById('search-dropdown').hidden = true;
  document.getElementById('site-search').value = '';
}

// 點 search-box 外面 → 收 dropdown
document.addEventListener('click', e => {
  if (!e.target.closest('.search-box')) {
    const dd = document.getElementById('search-dropdown');
    if (dd) dd.hidden = true;
  }
});

/* 回到頂端 button — scroll > 300px 才顯示 */
window.addEventListener('scroll', () => {
  const btn = document.getElementById('scroll-top-btn');
  if (btn) btn.classList.toggle('show', window.scrollY > 300);
}, { passive: true });

/* ── 動畫 <details> ─────────────────────────────────────────────────────────
 * 攔截 .anim-details summary click,跑 max-height + opacity transition。
 * 注意:transitionend 對每個 property 都會 fire,opacity (.22s) 早於
 * max-height (.28s),必須 filter propertyName === 'max-height' 才不會
 * 在 opacity 完成時誤清 inline maxHeight 導致 panel collapse。 */
function _animDetailsOpen(details) {
  const panel = details.querySelector('.anim-panel');
  if (!panel) return;
  details.open = true;
  panel.style.maxHeight = '0px';
  panel.style.opacity = '0';
  void panel.offsetWidth;
  const targetH = panel.scrollHeight;
  panel.style.maxHeight = targetH + 'px';
  panel.style.opacity = '1';
  panel.addEventListener('transitionend', function te(e) {
    if (e.propertyName !== 'max-height') return;
    panel.style.maxHeight = 'none';  // 完成後設 none,讓 [open] 規則接手
    panel.removeEventListener('transitionend', te);
  });
}
function _animDetailsClose(details) {
  const panel = details.querySelector('.anim-panel');
  if (!panel) { details.open = false; return; }
  panel.style.maxHeight = panel.scrollHeight + 'px';
  void panel.offsetWidth;
  panel.style.maxHeight = '0px';
  panel.style.opacity = '0';
  panel.addEventListener('transitionend', function te(e) {
    if (e.propertyName !== 'max-height') return;
    details.open = false;
    panel.style.maxHeight = '';
    panel.style.opacity = '';
    panel.removeEventListener('transitionend', te);
  });
}
document.addEventListener('click', e => {
  const summary = e.target.closest('summary');
  if (!summary) return;
  const details = summary.parentElement;
  if (!details || !details.classList.contains('anim-details')) return;
  e.preventDefault();
  if (details.open) _animDetailsClose(details);
  else _animDetailsOpen(details);
});

/* 點 anim-details 外面 → 收起(避免 panel 一直浮在上面擋畫面) */
document.addEventListener('click', e => {
  if (e.target.closest('.anim-details')) return;
  document.querySelectorAll('.anim-details[open]').forEach(d => _animDetailsClose(d));
});

/* 前哨 inline toggle:button 在 focal-stocks div 內、panel 在 div 下方 sibling,
 * data-target 對應 panel id。max-height + opacity transition,跟 anim-details
 * 同 pattern 但不需要 <details>/<summary> 結構限制(讓 button 能 inline 在
 * 一排焦點 chip 之間)。 */
function toggleSentinelInline(btn) {
  const panel = document.getElementById(btn.dataset.target);
  if (!panel) return;
  const isHidden = panel.hidden;
  if (isHidden) {
    panel.hidden = false;
    panel.style.maxHeight = '0px';
    panel.style.opacity = '0';
    void panel.offsetWidth;
    panel.style.maxHeight = panel.scrollHeight + 'px';
    panel.style.opacity = '1';
    btn.classList.add('expanded');
    panel.addEventListener('transitionend', function te(e) {
      if (e.propertyName !== 'max-height') return;
      panel.style.maxHeight = 'none';
      panel.removeEventListener('transitionend', te);
    });
  } else {
    panel.style.maxHeight = panel.scrollHeight + 'px';
    void panel.offsetWidth;
    panel.style.maxHeight = '0px';
    panel.style.opacity = '0';
    btn.classList.remove('expanded');
    panel.addEventListener('transitionend', function te(e) {
      if (e.propertyName !== 'max-height') return;
      panel.hidden = true;
      panel.style.maxHeight = '';
      panel.style.opacity = '';
      panel.removeEventListener('transitionend', te);
    });
  }
}

/* downloadRankCSV 隨焦點排行 tab 2026-05-19 移除 */

