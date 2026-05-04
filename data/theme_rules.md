# 台美股投資主題字典 — 建構規則文件

> 此文件由 `build_theme_dictionary.py` 在 runtime 讀取，作為 Gemini prompt 的規則章節。
> 修改此文件即可調整 AI 的主題識別行為，無須修改 Python 程式碼。
> 上次人工審閱：2026-05-04

---

## 一、顆粒度要求（最重要）

主題必須「細粒度」，越細越好。

**好例子（✅）：**
HBM記憶體、CoWoS先進封裝、ABF載板、光通訊800G、MLCC、石英晶體、PCB鑽針、
液冷散熱、氣冷散熱、無塵室、廠務工程、InP基板、磊晶片、CCL銅箔基板、
玻纖布、銅箔、光阻、CMP耗材、乾蝕刻設備、薄膜沉積設備、光通訊雷射元件、
CPO共封裝光學、SiC基板、GaN元件、先進封裝CoWoS/SoIC/2.5D/3DIC

**壞例子（❌）：**
半導體（太廣）、科技股（太廣）、電子業（太廣）

---

## 二、必須涵蓋的供應鏈層次

以下子題材常被忽略但投資上非常重要，請務必納入：

### 晶圓廠廠務
- 無塵室（Cleanroom）
- 廠務工程（Facility Engineering）
- 超純水（UPW）
- 工業氣體 / 特殊氣體（特氣）

### 封裝材料
- ABF（Ajinomoto Build-up Film）
- CCL（覆銅基板）
- 玻纖布（Glass Fiber）
- 銅箔（Copper Foil）
- 底膠（Underfill）
- EMC模封料（Epoxy Molding Compound）

### 半導體設備
- 微影設備（EUV/DUV）
- 乾蝕刻設備
- CVD / ALD 薄膜沉積設備
- CMP（化學機械研磨）設備
- 清洗設備
- 量測／檢測設備

### 光通訊元件
- 雷射二極體（LD, Laser Diode）
- 光電探測器（PD, Photo Detector）
- 磊晶片（Epi Wafer）
- InP 基板
- 光通訊模組（800G / 1.6T）
- CPO（Co-Packaged Optics，共封裝光學）

### PCB 材料
- 玻纖布（Glass Cloth）
- 銅箔
- 樹脂（PPO / BT）
- HDI 板（高密度積層板）
- ABF 載板（Substrate）
- Ajinomoto 膜（ABF Film）

### 記憶體
- HBM（High Bandwidth Memory）
- DRAM（DDR4 / DDR5 / LPDDR5）
- NAND Flash（3D NAND）
- NOR Flash
- SLC / MLC / TLC 分類

### 先進封裝
- CoWoS（Chip on Wafer on Substrate）
- SoIC（System on Integrated Chips）
- EMIB（Intel 技術）
- 面板級封裝 PLP
- 玻璃基板（Glass Substrate）
- 2.5D / 3DIC

### 散熱
- 液冷（CDU 冷卻分配單元、冷板、浸沒式）
- 氣冷（風扇、散熱鰭片）
- 均熱板（Vapor Chamber）
- 熱界面材料（TIM）

### 半導體化學品
- 光阻（PR, Photoresist）
- 顯影液
- 蝕刻液
- 前驅體（Precursor，用於 ALD/CVD）
- CMP 漿料（Slurry）

### 特殊應用
- 車用 SiC 元件
- GaN 功率元件
- 無人機（商用 / 軍用）
- 國防電子
- 核能相關
- 太空衛星（低軌 LEO）

---

## 三、keyword 欄位規則（核心規則）

`keyword` 是**單一字串**，直接用 Python `str.count()` 在文章中計數。
命中次數越多 × 越近期，該主題在本檔期的熱度越高。

### 規則一：取核心識別詞，不要加通用後綴
| ✅ 正確 | ❌ 錯誤 | 說明 |
|---------|---------|------|
| `"ABF"` | `"ABF載板"` | "載板" 太廣，會把 HDI 載板也算進來 |
| `"CoWoS"` | `"CoWoS封裝"` | "封裝" 是通用詞 |
| `"HBM"` | `"HBM記憶體"` | "記憶體" 太廣 |
| `"MLCC"` | `"MLCC電容"` | "電容" 太廣 |
| `"液冷散熱"` | `"液冷"` | 整體詞夠精準，不必拆開 |

### 規則二：若核心詞太短可能誤判，保留最短能識別的組合
| ✅ 正確 | ❌ 錯誤 | 說明 |
|---------|---------|------|
| `"石英晶體"` | `"晶體"` | "晶體" 太廣，SiC 晶體、磁晶體都中 |
| `"無塵室"` | `"塵"` | 顯然太短 |
| `"特氣"` | `"氣"` | 太廣 |
| `"底膠"` | `"膠"` | 太廣 |
| `"玻纖布"` | `"布"` | 太廣 |

### 規則三：英文專有名詞直接用原文
`"CoWoS"`、`"HBM"`、`"MLCC"`、`"ABF"`、`"CPO"`、`"SiC"`、`"GaN"`、`"EUV"`、`"EMIB"`、`"PLP"`

### 禁止使用的過廣 keyword（黑名單）
`"載板"`、`"基板"`、`"模組"`、`"記憶體"`、`"散熱"`、`"封裝"`、`"設備"`、`"元件"`、`"晶片"`、`"半導體"`

---

## 四、股票標的 — 補全族群原則

- **不限於文章中出現者**，應主動補全同族群所有重要標的
- `tw_stocks`：格式 `{"code":"2330","name":"台積電"}`
- `us_stocks`：格式 `{"ticker":"NVDA","name":"Nvidia"}`
- 一個主題的核心股通常 2–6 家；若族群更大可適度擴充

**ABF 載板範例（正確）：**
```json
"tw_stocks": [
  {"code":"3037","name":"欣興"},
  {"code":"8046","name":"南電"},
  {"code":"3189","name":"景碩"}
]
```

---

## 五、supply_chain — 上下游關係

每個主題請標記：
- `upstream`：此主題的上游原材料、設備、前製程（**最多 4 項**，用簡短中文名稱 3-8 字）
- `downstream`：此主題的下游應用、組裝、終端市場（**最多 4 項**，同上）

**範例：**
```json
"supply_chain": {
  "upstream": ["玻纖布", "銅箔", "樹脂", "鑽孔設備"],
  "downstream": ["AI伺服器", "CoWoS先進封裝", "高階GPU"]
}
```

---

## 六、JSON 輸出格式

只輸出 JSON，不要任何說明文字。

```json
{
  "themes": [
    {
      "id": "abf_substrate",
      "name": "ABF載板",
      "keyword": "ABF",
      "supply_chain": {
        "upstream": ["玻纖布", "銅箔", "樹脂"],
        "downstream": ["AI伺服器", "CoWoS先進封裝"]
      },
      "tw_stocks": [
        {"code":"3037","name":"欣興"},
        {"code":"8046","name":"南電"},
        {"code":"3189","name":"景碩"}
      ],
      "us_stocks": []
    }
  ]
}
```

---

## 七、增量 Append 模式的額外規則

增量模式下，AI 只需找「現有清單沒有的全新主題」：

1. 若新內容沒有新主題 → 只輸出 `NO_NEW_THEMES`
2. 新主題的 keyword 不得與現有 keyword 重複或過於相近
3. 只補充真正「新出現」的題材，不要重複現有主題的細分變體

---

## 八、主題 id 命名規則

- 使用 snake_case 英文，反映主題核心內容
- 範例：`abf_substrate`、`cowos_advanced_packaging`、`hbm_memory`、`cleanroom_facility`、`liquid_cooling`
- 避免使用過於通用的 id 如 `semiconductor`、`tech_stock`
