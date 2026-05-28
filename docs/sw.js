/* IIA 投資情報 Service Worker — Phase 3a (2026-05-28)
 *
 * NO-OP 階段:
 * - install / activate 直接 skipWaiting + clients.claim,讓 SW 機制本身可控
 * - 不註冊 fetch handler → 所有資源走預設 network path,等同無 SW 介入
 * - 不 cache 任何東西 → 不會發生 cache 毒化或 stuck 在舊版的問題
 *
 * Kill switch:
 * - Page-side 偵測 ?sw=off → 把 unregister 訊息 post 給 SW + 自行 caches.delete + reload
 * - 即使 SW 沒 message handler,unregister 本身在 page 端就可呼叫
 *
 * 升級到 Phase 3b 時把版號改為 v2,並加入 fetch handler 與 cache 策略。
 */

const SW_VERSION = 'v1-noop-2026-05-28';

self.addEventListener('install', (event) => {
  // 不預載任何資源,立即進 activate
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // 立即接管所有 client(下一個導覽就走新 SW)
  event.waitUntil(self.clients.claim());
});

// 故意不註冊 fetch handler:沒 listener 等於 transparent,fetch 走 default 路徑。
// 如果未來要加 cache 策略(Phase 3b),在這裡 addEventListener('fetch', ...)

// Kill switch 訊息 channel(預留,Phase 3b 也可用同套機制)
self.addEventListener('message', (event) => {
  if (event.data && event.data.action === 'unregister') {
    self.registration.unregister().then(() => {
      return self.clients.matchAll();
    }).then((clients) => {
      clients.forEach((c) => {
        // 通知 client SW 已 unregistered,client 端負責 reload
        c.postMessage({ action: 'unregistered' });
      });
    });
  }
});
