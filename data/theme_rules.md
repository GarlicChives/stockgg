# 台美股投資主題字典 — 人類審閱指南

> **目的：** 字典已能透過 Search+LLM pipeline 自動發現主題（`auto_created: true`），但 LLM 偶爾會建出太籠統或重複的條目。
> 這份文件是**人類定期審閱 auto_created 主題時的判斷準則**，告訴你哪些 theme 該保留、哪些該合併、哪些該刪。
>
> Pipeline 的 prompt 在 [`src/prompts/theme_classifier.md`](../src/prompts/theme_classifier.md)，整體架構在 [`ARCHITECTURE.md`](../ARCHITECTURE.md)。

---

## 何時審閱

每月跑一次：

```bash
jq '.themes[] | select(.auto_created==true) | {id, name, auto_created_at}' data/theme_dictionary.json
```

---

## 一、顆粒度要求（最重要）

主題必須「細粒度」，越細越好。

**好例子（✅）：**
HBM記憶體、CoWoS先進封裝、ABF載板、光通訊800G、MLCC、石英晶體、PCB鑽針、
液冷散熱、氣冷散熱、無塵室、廠務工程、InP基板、磊晶片、CCL銅箔基板、
玻纖布、銅箔、光阻、CMP耗材、乾蝕刻設備、薄膜沉積設備、光通訊雷射元件、
CPO共封裝光學、SiC基板、GaN元件、先進封裝CoWoS/SoIC/2.5D/3DIC

**壞例子（❌）：**
半導體（太廣）、科技股（太廣）、電子業（太廣）、財報分析（不是主題）、AI 應用（太籠統）

審 auto_created 主題時：**遇到上面壞例子的請刪除或合併。**

---

## 二、必須涵蓋的供應鏈層次

以下子題材常被忽略但投資上非常重要，若 auto_created 沒涵蓋可手動補：

- **晶圓廠廠務**：無塵室、廠務工程、超純水、特氣
- **封裝材料**：ABF、CCL、玻纖布、銅箔、底膠、EMC模封料
- **半導體設備**：EUV/DUV、乾蝕刻、CVD/ALD、CMP、清洗、量測
- **光通訊元件**：LD/PD、磊晶片、InP 基板、800G/1.6T、CPO
- **PCB 材料**：玻纖布、銅箔、PPO/BT 樹脂、HDI、ABF 載板
- **記憶體**：HBM、DRAM(DDR4/5/LPDDR5)、NAND、NOR
- **先進封裝**：CoWoS、SoIC、EMIB、PLP、玻璃基板、2.5D/3DIC
- **散熱**：液冷（CDU/冷板/浸沒）、氣冷、均熱板、TIM
- **半導體化學品**：光阻、顯影液、蝕刻液、前驅體、CMP 漿料
- **特殊應用**：SiC 車用、GaN 功率、無人機、國防電子、核能、低軌衛星

---

## 三、keyword 欄位規則

`keyword` 是**單一字串**，給 `focus_themes.py` 在文章正文做 `str.count()` 計數用。

### 規則一：取核心識別詞，不要加通用後綴
| ✅ 正確 | ❌ 錯誤 | 說明 |
|---------|---------|------|
| `"ABF"` | `"ABF載板"` | 「載板」太廣，會把 HDI 載板也算進來 |
| `"CoWoS"` | `"CoWoS封裝"` | 「封裝」是通用詞 |
| `"HBM"` | `"HBM記憶體"` | 「記憶體」太廣 |
| `"MLCC"` | `"MLCC電容"` | 「電容」太廣 |
| `"液冷散熱"` | `"液冷"` | 整體詞夠精準，不必拆開 |

### 規則二：核心詞太短可能誤判時，保留最短能識別的組合
| ✅ 正確 | ❌ 錯誤 | 說明 |
|---------|---------|------|
| `"石英晶體"` | `"晶體"` | SiC 晶體、磁晶體都中 |
| `"無塵室"` | `"塵"` | 顯然太短 |
| `"特氣"` | `"氣"` | 太廣 |
| `"底膠"` | `"膠"` | 太廣 |
| `"玻纖布"` | `"布"` | 太廣 |

### 規則三：英文專有名詞直接用原文
`"CoWoS"`、`"HBM"`、`"MLCC"`、`"ABF"`、`"CPO"`、`"SiC"`、`"GaN"`、`"EUV"`、`"EMIB"`、`"PLP"`

### 黑名單（禁用 keyword）
`"載板"`、`"基板"`、`"模組"`、`"記憶體"`、`"散熱"`、`"封裝"`、`"設備"`、`"元件"`、`"晶片"`、`"半導體"`

審 auto_created 主題時：**keyword 在黑名單內請改寫或刪除。**

---

## 四、id 命名規則

- snake_case 英文，3-40 字元，反映主題核心
- 範例：`abf_substrate`、`cowos_advanced_packaging`、`hbm_memory`、`liquid_cooling`、`leo_satellite`
- 避免：`semiconductor`、`tech_stock`、`concept_1`（太通用）
- LLM auto_created 的 ID 偶爾會有底線錯位或包含非 ASCII 字元，可手動改正

---

## 五、欄位語義

| 欄位 | 維護者 | 說明 |
|------|--------|------|
| `id` | 人工或 auto | 永久識別碼，建立後不可改（會破壞 cache） |
| `name` | 人工或 auto | 台灣金融媒體常見中文名稱 |
| `keyword` | 人工或 auto | 用於文章計數的核心識別詞（見規則三） |
| `supply_chain.upstream/downstream` | **人工** | 上下游關係，最多各 4 項，3-8 字 |
| `tw_stocks` / `us_stocks` | **程式自動 upsert** | 由 build_theme_dictionary 維護 |
| `auto_created` | 程式自動 | `true` 表示由 LLM 提議建立，需人類審閱 |
| `auto_created_at` | 程式自動 | 建立日期 |

> **約定：** 人工審完一個 auto_created theme 並補了 supply_chain 之後，可把 `auto_created` 改為 `false` 表示已 reviewed。

---

## 六、審閱動作清單

針對每個 auto_created theme，問自己這幾個問題：

1. **太廣？** 對到「壞例子」清單？→ 刪除
2. **與既有 theme 重複？** 跟某個 theme 的 name/keyword 90% 同義？→ 合併（把股票搬過去後刪掉這個）
3. **keyword 在黑名單？** → 改寫成更精準的詞
4. **缺供應鏈？** → 補 `supply_chain.upstream` / `downstream`
5. **股票不夠？** → 字典應主動補全同族群所有重要標的，不限於本次搜尋出現者

通過所有問題的 theme 把 `auto_created` 改為 `false` 表示已通過審閱。

---

## 七、JSON 範例（人工新建主題的標準格式）

```json
{
  "id": "abf_substrate",
  "name": "ABF載板",
  "keyword": "ABF",
  "supply_chain": {
    "upstream": ["玻纖布", "銅箔", "樹脂"],
    "downstream": ["AI伺服器", "CoWoS先進封裝"]
  },
  "tw_stocks": [
    {"code": "3037", "name": "欣興"},
    {"code": "8046", "name": "南電"},
    {"code": "3189", "name": "景碩"}
  ],
  "us_stocks": []
}
```
