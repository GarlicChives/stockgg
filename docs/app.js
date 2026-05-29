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
  // 📈 趨勢 tab:lazy-init lightweight-charts(只第一次切過去 init)
  if (name === 'trend' && !window._trendRendered) {
    _initTrendCharts();
  }
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
function toggleUniv(ticker) {
  if (_univDis.has(ticker)) _univDis.delete(ticker);
  else _univDis.add(ticker);
  document.querySelectorAll('.univ-chip[data-ticker="' + ticker + '"]').forEach(b => {
    b.classList.toggle('disabled', _univDis.has(ticker));
  });
  // 兩 sub-tab 的 cluster 都受影響(_univDis 是全域 state),都重算
  const C = window.IIA_CLUSTERS || {};
  ['hl_sub', 'pan_sub', 'sub'].forEach(lv => { if (C[lv]) _recalcClusters(lv); });
  // 若 theme chart modal 開著,連動重算
  const dlg = document.getElementById('theme-chart-dialog');
  if (dlg && dlg.open && _openThemeCardId) {
    _renderThemeChart(_openThemeCardId);
  }
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
  clusters.forEach(c => {
    const el = document.getElementById(c.cardId);
    if (!el) return;
    const shouldShow = (next == null) ||
      (c.focal || []).some(f => f.ticker === next);
    if (shouldShow) _expandCard(el);
    else _collapseCard(el);
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

function toggleEl(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const nowHidden = el.classList.toggle('hidden');
  const arrow = document.getElementById('arrow-' + id);
  if (arrow) arrow.textContent = nowHidden ? '▶' : '▼';
}

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

/* ── 📈 趨勢 tab — lazy init 兩張 chart(showTab('trend') 第一次觸發)──── */
/* ── 📈 趨勢 tab V3.2 重構(2026-05-29)──────────────────────────────────
 * 5 個 chart pane:
 *   1. trend-chart-twii    — 大盤 ^TWII K + MA10/60/200 + volume
 *   2. trend-chart-tpex    — 櫃買 ^TWOII K + MA10/60/200 + volume
 *   3. trend-chart-nh      — nh_count line + Q5(≥12)警示帶
 *   4. trend-chart-chip    — chip_count line + +1σ 進場 trigger 線
 *   5. trend-chart-ma60dist — 大盤 close / MA60 偏離 % + +8% 紅線
 * payload (window.IIA_TREND):
 *   index: {TWII, TPEX} OHLCV 1y+
 *   radar: [{d, nh, chip, growth, vol, intersect, universe}, ...] 半年聚合
 *   risk_today: V3.2 composite signal + level(顯示在 chip,JS 端不用算)
 */
let _trendCharts = null;

function _initTrendCharts() {
  const data = window.IIA_TREND;
  if (!data || !data.index || !data.index.TWII || !data.index.TWII.length) return;
  _loadLightweightCharts().then(() => _renderTrendCharts(data))
    .catch(err => {
      console.error('lightweight-charts load failed:', err);
      ['twii', 'tpex', 'nh', 'chip', 'ma60dist'].forEach(id => {
        const el = document.getElementById(`trend-chart-${id}`);
        if (el) el.innerHTML = '<p class="muted-note">圖表載入失敗</p>';
      });
    });
}

function _computeMA(rows, n, key = 'close') {
  // 回 [{time, value}],前 n-1 個 None。rows 須按時間 asc 排序。
  const out = [];
  for (let i = 0; i < rows.length; i++) {
    if (i + 1 < n) continue;
    let sum = 0, count = 0;
    for (let j = i - n + 1; j <= i; j++) {
      if (rows[j][key] != null) { sum += rows[j][key]; count++; }
    }
    if (count === n) out.push({ time: rows[i].d, value: sum / n });
  }
  return out;
}

function _renderTrendCharts(data) {
  const ids = ['twii', 'tpex', 'nh', 'chip', 'ma60dist'];
  const els = {};
  for (const id of ids) {
    els[id] = document.getElementById(`trend-chart-${id}`);
    if (!els[id]) return;
    els[id].innerHTML = '';
  }

  const css = getComputedStyle(document.documentElement);
  const textC = css.getPropertyValue('--text').trim() || '#e6e6e6';
  const gridC = 'rgba(255,255,255,0.06)';
  const baseOpts = {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: textC,
      attributionLogo: false,   // 移除右下角 TradingView icon
    },
    grid: { vertLines: { color: gridC }, horzLines: { color: gridC } },
    // 5 chart 對齊核心:
    //   leftPriceScale 顯式關掉(默認 invisible 仍占內部 layout 計算寬度)
    //   rightPriceScale minimumWidth 96(顯著大於 K 線「228.79」與 mini chart
    //     「150 檔」/「+19.6%」實際內容,確保所有 chart axis 同寬,plot area
    //     左緣對齊 → crosshair 同 time 落在同 X pixel)
    leftPriceScale:  { visible: false },
    rightPriceScale: { borderColor: gridC, minimumWidth: 96 },
    timeScale: { borderColor: gridC, timeVisible: false, secondsVisible: false },
    crosshair: { mode: 1 },
    autoSize: true,
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { mouseWheel: false, axisPressedMouseMove: true, pinch: true },
  };

  const _RED = '#ef4444';
  const _GREEN = '#10b981';

  // 日 K + MA10/60/200 + volume overlay。lastValueVisible 全關避 badge 擋 Y 軸
  function _kvolMA(el, rows) {
    if (!rows || !rows.length) {
      el.innerHTML = '<p class="muted-note">無資料</p>';
      return null;
    }
    const opts = JSON.parse(JSON.stringify(baseOpts));
    opts.rightPriceScale.scaleMargins = { top: 0.08, bottom: 0.28 };
    const ch = LightweightCharts.createChart(el, opts);

    const candle = ch.addCandlestickSeries({
      upColor: _RED, downColor: _GREEN,
      borderUpColor: _RED, borderDownColor: _GREEN,
      wickUpColor: _RED, wickDownColor: _GREEN,
      lastValueVisible: false, priceLineVisible: false,
    });
    const candleRows = rows.filter(p =>
      p.open != null && p.high != null && p.low != null && p.close != null
    ).map(p => ({ time: p.d, open: p.open, high: p.high, low: p.low, close: p.close }));
    candle.setData(candleRows);

    const ma10Data = _computeMA(rows, 10);
    const ma60Data = _computeMA(rows, 60);
    const ma200Data = _computeMA(rows, 200);
    const ma10 = ch.addLineSeries({
      color: '#fbbf24', lineWidth: 1, lineStyle: 0,
      priceLineVisible: false, lastValueVisible: false,
      title: 'MA10',
    });
    ma10.setData(ma10Data);
    const ma60 = ch.addLineSeries({
      color: '#60a5fa', lineWidth: 1, lineStyle: 0,
      priceLineVisible: false, lastValueVisible: false,
      title: 'MA60',
    });
    ma60.setData(ma60Data);
    const ma200 = ch.addLineSeries({
      color: '#a78bfa', lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'MA200',
    });
    ma200.setData(ma200Data);

    const vol = ch.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      lastValueVisible: false, priceLineVisible: false,
    });
    ch.priceScale('').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    const volRows = rows.filter(p => p.volume != null).map(p => {
      let color = 'rgba(124,138,242,0.45)';
      if (p.open != null && p.close != null) {
        color = p.close >= p.open ? 'rgba(239,68,68,0.45)' : 'rgba(16,185,129,0.45)';
      }
      return { time: p.d, value: p.volume, color };
    });
    vol.setData(volRows);

    ch.timeScale().fitContent();
    return { chart: ch, candle, ma10, ma60, ma200, vol };
  }

  // mini chart:單一 line + 可選 reference line。lastValueVisible 關掉(badge 擋 Y 軸)
  function _miniLine(el, rows, opts) {
    if (!rows || !rows.length) {
      el.innerHTML = '<p class="muted-note">無資料</p>';
      return null;
    }
    const chartOpts = JSON.parse(JSON.stringify(baseOpts));
    chartOpts.rightPriceScale.scaleMargins = { top: 0.1, bottom: 0.1 };
    const ch = LightweightCharts.createChart(el, chartOpts);
    const line = ch.addLineSeries({
      color: opts.color || '#60a5fa',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'custom', formatter: opts.formatter || (v => v.toFixed(2)) },
    });
    line.setData(rows);
    // reference lines:axisLabelVisible 關掉(label 比 axis tick 寬會擋到
    // Y 軸數字 + 撐 priceScale 寬度導致跟其他 chart crosshair 不對齊)。
    // 閾值文字改用 chart wrapper 上方既有的 .trend-chart-guide chip 說明。
    if (opts.refLines) {
      for (const ref of opts.refLines) {
        line.createPriceLine({
          price: ref.value,
          color: ref.color,
          lineWidth: 1,
          lineStyle: ref.dashed ? 2 : 0,
          axisLabelVisible: false,
        });
      }
    }
    ch.timeScale().fitContent();
    return { chart: ch, series: line };
  }

  // ── chart 1: ^TWII K + MA + volume ─────────────────────────────
  const twii = _kvolMA(els.twii, data.index.TWII || []);
  // ── chart 2: ^TWOII K + MA + volume ────────────────────────────
  const tpex = _kvolMA(els.tpex, data.index.TPEX || []);

  // ── chart 3: nh_count + Q5(>=12)警示線 ───────────────────────
  const nhRows = (data.radar || []).map(p => ({ time: p.d, value: p.nh }));
  const nhChart = _miniLine(els.nh, nhRows, {
    color: '#ef4444',
    formatter: v => v.toFixed(0) + ' 檔',
    refLines: [
      { value: 12, color: 'rgba(239,68,68,0.55)', title: '+12(Q5 警示)', dashed: false },
    ],
  });

  // ── chart 4: chip_count + 動態 +1σ trigger 線 ─────────────────
  // 計算 chip_count 全期間 mean+std,取整體均值±1σ 當 reference
  const chipVals = (data.radar || []).map(p => p.chip).filter(v => v != null);
  let chipMean = 0, chipSD = 0;
  if (chipVals.length) {
    chipMean = chipVals.reduce((a, b) => a + b, 0) / chipVals.length;
    chipSD = Math.sqrt(chipVals.reduce((a, b) => a + (b - chipMean) ** 2, 0) / chipVals.length);
  }
  const chipTrigger = chipMean + chipSD;
  const chipRows = (data.radar || []).map(p => ({ time: p.d, value: p.chip }));
  const chipChart = _miniLine(els.chip, chipRows, {
    color: '#10b981',
    formatter: v => v.toFixed(0) + ' 檔',
    refLines: [
      { value: chipTrigger, color: 'rgba(16,185,129,0.55)',
        title: `+1σ trigger(≈${chipTrigger.toFixed(1)})`, dashed: false },
    ],
  });

  // ── chart 5: TWII close / MA60 偏離 (%) + +8% 紅線 ─────────────
  const twiiClose = (data.index.TWII || []).map(p => ({ d: p.d, close: p.close }));
  const ma60Series = _computeMA(twiiClose, 60);
  const ma60ByDate = new Map(ma60Series.map(p => [p.time, p.value]));
  const ma60DistRows = twiiClose
    .filter(p => p.close != null && ma60ByDate.has(p.d))
    .map(p => ({
      time: p.d,
      value: (p.close / ma60ByDate.get(p.d) - 1) * 100,
    }));
  const ma60Chart = _miniLine(els.ma60dist, ma60DistRows, {
    color: '#fbbf24',
    formatter: v => v.toFixed(1) + '%',
    refLines: [
      { value: 8, color: 'rgba(239,68,68,0.55)', title: '+8%(Q5 危險)', dashed: false },
      { value: 0, color: 'rgba(255,255,255,0.25)', title: '0', dashed: true },
      { value: -8, color: 'rgba(16,185,129,0.55)', title: '-8%(超賣)', dashed: false },
    ],
  });

  // ── timeScale sync(5 個 chart 全 sync)─────────────────────────
  const allCharts = [twii, tpex, nhChart, chipChart, ma60Chart].filter(x => x).map(x => x.chart);
  let _syncBusy = false;
  const syncRange = (src, dsts) => {
    src.timeScale().subscribeVisibleTimeRangeChange(r => {
      if (_syncBusy || !r) return;
      _syncBusy = true;
      dsts.forEach(d => { try { d.timeScale().setVisibleRange(r); } catch (e) {} });
      _syncBusy = false;
    });
  };
  for (const c of allCharts) {
    const others = allCharts.filter(x => x !== c);
    syncRange(c, others);
  }

  // ── crosshair 對齊:5 chart 的 right priceScale 寬度依內容自動撐(K 線
  // "10000.00" 比 mini chart "12 檔" 寬幾 px → plot area 起點錯位 → 垂直虛線
  // 同 time 落在不同 X pixel)。修法:render 完 measure 各邊實際寬度,取
  // max 套 minimumWidth(只會多撐不會 truncate)。requestAnimationFrame 確保
  // DOM layout 完成才 measure。cluster modal _tcCharts 也用同樣技巧。
  // 對齊 priceScale 寬度 — RAF triple + 套完後第二輪 measure 確認落定。
  // measure 第一輪可能拿到 still-rendering width(autoSize 仍在 settle),三層
  // RAF 後 stable。也補加 ResizeObserver 在 viewport resize 時重 apply。
  function alignPriceScales() {
    try {
      const widths = allCharts.map(c => c.priceScale('right').width()).filter(w => w > 0);
      if (!widths.length) return;
      const maxW = Math.max(...widths, 96);
      for (const c of allCharts) {
        c.priceScale('right').applyOptions({ minimumWidth: maxW });
      }
    } catch (e) { console.warn('trend priceScale align failed', e); }
  }
  requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => {
    alignPriceScales();
    // 再 measure 一次確認套完真的對齊
    requestAnimationFrame(alignPriceScales);
  })));
  // viewport resize 觸發重 align(chart 內 axis 寬度可能因 font scaling 變化)
  if (window.ResizeObserver && allCharts.length) {
    const ro = new ResizeObserver(() => {
      requestAnimationFrame(alignPriceScales);
    });
    // 觀察任一 chart container,trigger 一次重算就好(各 chart 寬同步)
    const firstEl = document.getElementById('trend-chart-twii');
    if (firstEl) ro.observe(firstEl);
  }

  // crosshair sync(anchor series 用各 chart 的第一條)
  const anchors = [
    twii && twii.candle, tpex && tpex.candle,
    nhChart && nhChart.series, chipChart && chipChart.series, ma60Chart && ma60Chart.series,
  ];
  for (let i = 0; i < allCharts.length; i++) {
    const src = allCharts[i];
    src.subscribeCrosshairMove(p => {
      for (let j = 0; j < allCharts.length; j++) {
        if (i === j) continue;
        if (!p || !p.time) {
          try { allCharts[j].clearCrosshairPosition(); } catch (e) {}
        } else {
          try { allCharts[j].setCrosshairPosition(0, p.time, anchors[j]); } catch (e) {}
        }
      }
    });
  }

  _trendCharts = { twii: twii && twii.chart, tpex: tpex && tpex.chart,
                    nh: nhChart && nhChart.chart, chip: chipChart && chipChart.chart,
                    ma60dist: ma60Chart && ma60Chart.chart };
  window._trendRendered = true;
}
