"""共用設定常數。

跨 repo(stockgg ↔ StockGG-ingest)各自維護一份,沒辦法直接 import,
維持人工同步。任何加入這裡的常數,改完都要記得也在 ingest 那邊改。
"""

# 成交值排行 fetch / 顯示共用上限。改這裡會同時影響 SQL LIMIT 與顯示文字
# ("成交值前 N")。與 StockGG-ingest 的 src/utils/config.py 保持一致。
RANKINGS_TOP_N = 50
