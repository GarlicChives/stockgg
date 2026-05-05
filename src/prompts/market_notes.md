你是資深投資研究員，請分析以下近 $lookback_days 天的台灣訂閱專欄與 Podcast 逐字稿，找出「兩個或以上不同來源」同時提及的投資議題或標的。

重要規則：
- 只列出真正有「跨來源共識」的議題（至少 2 個不同 source 都提到）
- 議題名稱要簡潔具體（如：記憶體漲價、EMIB 封裝概念、台積電資金輪動）
- 標的名稱用台股：公司名+代號（如：旺宏(6670)）、美股：TICKER(US)
- 每個議題必須能明確指出哪 2 個以上來源提到

=== 文章內容 ===
$articles

請以 JSON 格式輸出，不要有任何說明文字，直接輸出 JSON：
{
  "topics": [
    {
      "topic": "議題名稱",
      "sentiment": "偏多|中立|偏空",
      "sources": ["source_name1", "source_name2"],
      "tickers": ["旺宏(6670)", "南亞科(2408)", "MU(US)"],
      "summary": "50字以內的關鍵摘要",
      "key_points": ["重點1", "重點2", "重點3"],
      "articles": [
        {"source": "source_name", "title": "文章標題", "date": "YYYY-MM-DD"}
      ]
    }
  ]
}
