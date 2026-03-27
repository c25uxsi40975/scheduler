# LINE リッチメニュー レスポンス改善計画

## 背景

リッチメニュー押下後のレスポンスが遅い。
根本原因は GAS の `doPost()` 内で **Google Sheets API（`openById` / `getDataRange`）が同一リクエスト内で何度も呼ばれる**こと。

### 現状の想定遅延

| コマンド | openById回数 | Sheet読み込み回数 | 想定遅延 |
|---|---|---|---|
| ヘルプ | 1〜2 | 2〜3 | 1〜3秒 |
| 希望入力 | 6〜7 | 7〜8 | 3〜7秒 |
| 予定確認 | 5+N (Nはセクション数) | 10+ | 5〜15秒 |

---

## 改善1: スプレッドシート参照のリクエスト内キャッシュ

**効果: 大 / 難易度: 低 / 対象: common.gs**

### 現状の問題

`getMasterSpreadsheet()` と `getOperationalSpreadsheet()` がキャッシュなしで毎回呼ばれる。
1リクエスト内で `openById()` が5〜7回実行される。

### 変更内容

```js
// common.gs の先頭付近に追加
var _masterSS = null;
var _operationalSS = null;
```

```js
// getMasterSpreadsheet() を修正
function getMasterSpreadsheet() {
  if (!MASTER_SPREADSHEET_ID) {
    throw new Error("MASTER_SPREADSHEET_ID が未設定です。");
  }
  if (!_masterSS) {
    _masterSS = SpreadsheetApp.openById(MASTER_SPREADSHEET_ID);
  }
  return _masterSS;
}
```

```js
// getOperationalSpreadsheet() を修正
function getOperationalSpreadsheet() {
  if (!_operationalSS) {
    _operationalSS = SpreadsheetApp.getActiveSpreadsheet();
  }
  return _operationalSS;
}
```

### 効果

openById 呼び出し: 5〜7回 → **2回**（master + operational 各1回）
想定短縮: **2〜5秒**

---

## 改善2: `getSheet()` を `getSheetByName()` に変更

**効果: 中 / 難易度: 低 / 対象: common.gs**

### 現状の問題

```js
// 全シートを取得してループで名前比較（非効率）
function getSheet(ss, name) {
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (sheets[i].getName() === name) return sheets[i];
  }
  return null;
}
```

### 変更内容

```js
function getSheet(ss, name) {
  return ss.getSheetByName(name);
}
```

`getSheetByName()` は存在しない場合 `null` を返すため、既存のnullチェックとの互換性あり。

### 効果

シート数が多いほど効果が大きい。1呼び出しあたり数十〜数百ms短縮。

---

## 改善3: セッション管理を CacheService に移行

**効果: 中 / 難易度: 中 / 対象: line_config.gs**

### 現状の問題

`getSession()` / `upsertSession()` / `deleteSession()` が毎回 Google Sheets を読み書きする。
セッションは一時データ（30分TTL）であり、永続化の必要がない。

### 変更内容

```js
// line_config.gs のセッション管理を全面置換

function getSession(userId) {
  var cache = CacheService.getScriptCache();
  var raw = cache.get("line_session_" + userId);
  if (!raw) return null;
  try {
    var session = JSON.parse(raw);
    return session;
  } catch (e) {
    return null;
  }
}

function upsertSession(userId, updates) {
  var cache = CacheService.getScriptCache();
  var existing = getSession(userId) || {};
  for (var key in updates) {
    existing[key] = updates[key];
  }
  existing.user_id = userId;
  existing.updated_at = new Date().toISOString();
  // 30分 = 1800秒
  cache.put("line_session_" + userId, JSON.stringify(existing), 1800);
}

function deleteSession(userId) {
  var cache = CacheService.getScriptCache();
  cache.remove("line_session_" + userId);
}
```

### 注意事項

- `getSessionSheet()` 関数は不要になるが、既存の LINEセッション シートにデータが残っている場合を考慮し、移行期間中は残しておく
- `CacheService` の上限: キー1つあたり 100KB、全体 10MB。セッションデータは十分小さいので問題なし
- GAS の再デプロイ後、既存セッションはリセットされるが、元々30分TTLなので影響は軽微

### 効果

セッション読み書きのたびに発生していた Sheet API 呼び出し（各0.3〜1秒）がなくなる。
想定短縮: **0.5〜2秒**

---

## 改善4: 設定値の一括読み込みキャッシュ

**効果: 小〜中 / 難易度: 低 / 対象: line_config.gs**

### 現状の問題

`getSettingValue()` が呼ばれるたびに設定シート全体を `getDataRange().getValues()` で読む。
`getTargetSaturdays()` 内で `getSettingValue()` が2回呼ばれる（`saturday_extra_dates` と `saturday_excluded_dates`）。

### 変更内容

```js
var _settingsCache = null;

function getAllSettings() {
  if (_settingsCache) return _settingsCache;
  var ss = getMasterSpreadsheet();
  var sheet = getSheet(ss, "設定");
  if (!sheet) return {};
  var data = sheet.getDataRange().getValues();
  var map = {};
  for (var i = 1; i < data.length; i++) {
    map[String(data[i][0])] = String(data[i][1]);
  }
  _settingsCache = map;
  return map;
}

function getSettingValue(key) {
  var settings = getAllSettings();
  return settings[key] || null;
}
```

### 効果

設定シートの読み込み: N回 → **1回**。
想定短縮: **0.3〜1秒**

---

## 改善5:「予定確認」の処理最適化

**効果: 大（予定確認コマンド限定） / 難易度: 中 / 対象: line_webhook.gs**

### 現状の問題

`showSchedule()` (line_webhook.gs:458-541) が：
1. 運用SSの **全シートをループ** して `スケジュール_YYYY-MM` を探す
2. 各シートで `getDataRange().getValues()` で全データを読む
3. 平日外勤で **セクション数分の `openById()` を追加実行**

### 変更内容

#### 5a. シート名による早期絞り込み

```js
// before: 全シートのデータを読み込んでから判定
var sheets = ss.getSheets();
for (var i = 0; i < sheets.length; i++) {
  var name = sheets[i].getName();
  var match = name.match(/^スケジュール_(\d{4}-\d{2})$/);
  if (!match) continue;
  var data = sheets[i].getDataRange().getValues(); // ← 毎回全読み
  // ...
}

// after: getSheetByName で直接アクセス（過去12ヶ月分のみ走査）
function getRecentScheduleMonths() {
  var now = new Date();
  var months = [];
  for (var offset = -2; offset <= 3; offset++) {
    var d = new Date(now.getFullYear(), now.getMonth() + offset, 1);
    var ym = Utilities.formatDate(d, "Asia/Tokyo", "yyyy-MM");
    months.push(ym);
  }
  return months;
}

// showSchedule 内で
var candidates = getRecentScheduleMonths();
for (var i = 0; i < candidates.length; i++) {
  var sheet = ss.getSheetByName("スケジュール_" + candidates[i]);
  if (!sheet) continue;
  // ... データ読み込み
}
```

#### 5b. セクション別SSのキャッシュ

```js
var _weekdaySectionSSCache = {};

function getWeekdaySectionSpreadsheet(ssMaster, section) {
  if (_weekdaySectionSSCache[section] !== undefined) {
    return _weekdaySectionSSCache[section];
  }
  // ... 既存のロジック
  _weekdaySectionSSCache[section] = result; // null も含めてキャッシュ
  return result;
}
```

### 効果

不要なシート走査・openById の削減。
想定短縮: **2〜8秒**（セクション数・シート数に依存）

---

## 実装順序（推奨）

影響範囲が小さく効果が大きい順に実施する。

| 順序 | 改善 | 変更ファイル | 変更行数目安 |
|---|---|---|---|
| 1 | 改善1: SS参照キャッシュ | common.gs | ~10行 |
| 2 | 改善2: getSheetByName | common.gs | 1行（関数本体の置換） |
| 3 | 改善4: 設定値キャッシュ | line_config.gs | ~15行 |
| 4 | 改善3: CacheService セッション | line_config.gs | ~30行（置換） |
| 5 | 改善5: 予定確認の最適化 | line_webhook.gs, common.gs | ~30行 |

### 改善後の想定遅延

| コマンド | 現状 | 改善後 |
|---|---|---|
| ヘルプ | 1〜3秒 | ~0.5秒 |
| 希望入力 | 3〜7秒 | 1〜2秒 |
| 予定確認 | 5〜15秒 | 2〜4秒 |

---

## テスト方針

- GAS エディタの実行ログで各関数の実行時間を `console.time()` / `console.timeEnd()` で計測
- LINE Bot のテストアカウントで各コマンドの応答時間を体感確認
- 改善1→2→4 は単独でも効果検証可能。改善3 はセッションを使うフロー（希望入力の途中状態）で検証
