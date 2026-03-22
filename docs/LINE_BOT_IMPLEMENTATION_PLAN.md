# LINE Bot 希望入力機能 実装計画書（v2）

## 1. プロジェクト概要

### 目的
既存の外勤調整システム（Streamlit + Google Sheets）に、LINE公式アカウントを通じた**土曜外勤の希望入力機能**を追加する。医員がLINEのリッチメニューから、チャット形式で直感的にスケジュール希望を登録できるようにする。

### 背景
- 現在、医員はStreamlitのWeb画面からスケジュール希望を入力している
- LINEは日常的に使用するアプリであり、入力のハードルを下げられる
- **リッチメニュー → ユーザー起点 → Reply API** の流れで、**完全無料**運用が可能

### v1からの変更点
- **Flask/FastAPI → GAS（Google Apps Script）** に変更。別サーバー不要
- **リッチメニュー**を中心としたUI設計に変更
- 全ての希望入力フローを **Reply API（無料）** で完結させる設計

---

## 2. 全体アーキテクチャ

```
[LINEユーザー（医員）]
    |
    | リッチメニュー「希望入力」タップ
    | → 自動で「希望入力」テキスト送信（ユーザー起点）
    v
[LINEプラットフォーム]
    |
    v (Webhook POST / HTTPS)
[Google Apps Script (GAS)]     ← Webhook受信 + Reply応答
    |
    v (SpreadsheetApp で読み書き)
[Google Sheets]                ← 共有データストア（既存のマスタ＋運用SS）
    ^
    | (gspread で読み書き)
[Streamlit アプリ]             ← 管理者UI（既存）
```

### 技術スタック（追加分）

| 項目 | 技術 |
|------|------|
| Webhookサーバー | **GAS（Google Apps Script）** |
| LINE API呼び出し | GAS の `UrlFetchApp` |
| DB | Google Sheets（既存スプレッドシートを共用） |
| ホスティング | 不要（GAS = Google サーバーレス） |

### GASを選ぶ理由

| 比較項目 | Flask/FastAPI | GAS |
|---------|--------------|-----|
| 別サーバー | 必要（Cloud Run等） | **不要** |
| HTTPS | 自分で用意 | **自動** |
| Google Sheets連携 | gspread + 認証設定 | **SpreadsheetApp で直接** |
| コスト | サーバー費用あり | **完全無料** |
| 既存資産 | なし | **reminder.gs が既にある** |
| デプロイ | Docker/CI | **Web Appとしてデプロイ（1クリック）** |

---

## 3. LINE料金プラン

**コミュニケーションプラン（無料）で運用可能。**

| API | 用途 | 課金 |
|-----|------|------|
| Reply API | ユーザーメッセージへの応答（希望入力フロー全体） | **無料** |
| Push API | リマインダー・確定通知 | カウント対象 |

### 月間試算（医員20名想定）

| 種別 | 通数/月 | 課金 |
|------|---------|------|
| 希望入力の応答 (Reply) | ~100通 | 無料 |
| リマインダー (Push) | ~40通 | カウント |
| 確定通知 (Push) | ~20通 | カウント |
| **Push合計** | **~80通/月** | **無料枠200通内** |

### 重要: リッチメニュー起点 = 全て Reply

```
医員がリッチメニュー「希望入力」をタップ
  ↓ 自動で「希望入力」とテキスト送信（ユーザー起点）
Bot → Reply（無料）
  ↓
医員 → テキスト返答（ユーザー起点）
Bot → Reply（無料）
  ↓
  ... 以降、全てのやり取りが Reply（無料）
```

---

## 4. リッチメニュー設計

### レイアウト

```
┌──────────┬──────────┬──────────┬──────────┐
│  連携     │ 希望入力  │ 予定確認  │  ヘルプ   │
└──────────┴──────────┴──────────┴──────────┘
```

| ボタン | タップ時の動作 | 送信テキスト |
|--------|--------------|-------------|
| 連携 | アカウント連携を開始（初回のみ） | `連携` |
| 希望入力 | 来月の休み希望を入力開始 | `希望入力` |
| 予定確認 | 自分の確定スケジュールを表示 | `予定確認` |
| ヘルプ | 使い方を表示 | `ヘルプ` |

### GASでのリッチメニュー作成

```javascript
function createRichMenu() {
  var menu = {
    "size": {"width": 2500, "height": 843},
    "selected": true,
    "name": "メインメニュー",
    "chatBarText": "メニュー",
    "areas": [
      {
        "bounds": {"x": 0, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "連携"}
      },
      {
        "bounds": {"x": 625, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "希望入力"}
      },
      {
        "bounds": {"x": 1250, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "予定確認"}
      },
      {
        "bounds": {"x": 1875, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "ヘルプ"}
      }
    ]
  };

  var options = {
    "method": "post",
    "headers": {
      "Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN,
      "Content-Type": "application/json"
    },
    "payload": JSON.stringify(menu)
  };
  var res = UrlFetchApp.fetch(
    "https://api.line.me/v2/bot/richmenu", options
  );
  var richMenuId = JSON.parse(res.getContentText()).richMenuId;

  // メニュー画像をアップロード後、デフォルトに設定
  UrlFetchApp.fetch(
    "https://api.line.me/v2/bot/user/all/richmenu/" + richMenuId,
    {"method": "post", "headers": {"Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN}}
  );
}
```

---

## 5. メインフロー：希望入力（チャットベース）

### 5-1. フロー全体図

```
── ① アカウント連携（初回のみ・リッチメニューから起動） ──
User: リッチメニューの「連携」をタップ → 自動で「連携」と送信
Bot: 「アカウント名を入力してください」           ← Reply（無料）
User: 「yamada」
Bot: 「パスワードを入力してください」             ← Reply（無料）
User: 「mypassword」
Bot: 「山田太郎さんとして連携しました！」          ← Reply（無料）

── ② 希望入力開始（リッチメニュー） ──
User: リッチメニューの「希望入力」をタップ
      → 自動で「希望入力」と送信

── ③ 月選択 ──
Bot: 「希望を入力する月を選んでください」         ← Reply（無料）
     [4月] [5月]              ← Quick Reply ボタン
     ※公開月がない場合 → 「現在、受付中の月はありません」で終了

── ④ 日付ごとに順番に希望を聞く ──
Bot: 「4/5(土) の希望を選んでください」           ← Reply（無料）
     [出勤] [休み]            ← Quick Reply ボタン

Bot: 「4/12(土) の希望を選んでください」          ← Reply（無料）
     [出勤] [休み]            ← Quick Reply ボタン

... （対象日分繰り返し）

── ⑤ 最終確認 ──
Bot: 「【山田太郎】さんの4月の希望です            ← Reply（無料）
      4/5(土)  → 休み
      4/12(土) → 出勤
      4/19(土) → 休み
      4/26(土) → 出勤」
     [登録する] [やり直す]    ← Quick Reply ボタン

── ⑥ 登録完了 ──
Bot: 「4月の希望を登録しました！」                ← Reply（無料）
     or やり直す → ④に戻る
```

**全ステップ Reply = 完全無料**

### 5-2. 対象日の決定ロジック

既存システムの「スケジュール対象日」から取得：
- `section` が土曜外勤のもの
- `is_active` が TRUE のもの
- 選択された月に該当する日付のみフィルタ

### 5-3. 希望の選択肢

| 選択肢 | 意味 | 既存システムとの対応 |
|--------|------|---------------------|
| 出勤 | 出勤可能 | ○（希望日） |
| 休み | 出勤不可 | ×（NG日） |

> **注**: 既存システムの △（できれば避けたい）はLINE入力では省略し2択に簡略化。
> 必要に応じて [出勤] [できれば休み] [休み] の3択に拡張可能。

---

## 6. アカウント連携

### 6-1. 医員マスタへの変更

`医員マスタ` シートに `line_user_id` カラムを追加。

| カラム | 型 | 説明 |
|--------|-----|------|
| line_user_id | string | LINE User ID（連携時に保存） |

### 6-2. 連携フロー

```
1. 医員がLINE公式アカウントを友だち追加
2. リッチメニュー「連携」をタップ → 「連携」と自動送信
3. Bot: 「アカウント名を入力してください」
4. 医員がアカウント名を入力
5. GASがaccount_nameで医員マスタを検索
6. 見つからない場合 → 「アカウントが見つかりません」で終了
7. 見つかった場合 → Bot: 「パスワードを入力してください」
8. 医員がパスワードを入力
9. パスワード照合（SHA-256ハッシュ比較）
10. 一致 → LINE User IDを医員マスタに保存、「○○さんとして連携しました！」
11. 不一致 → 「パスワードが正しくありません」（リトライ可能）
```

### 6-3. セキュリティ

- アカウント名 + パスワードの2段階で本人確認
- パスワードはSHA-256ハッシュで照合（既存のpassword_hashカラムを使用）
- 連携済みユーザーが再度「連携」した場合は、既に連携済みであることを通知

---

## 7. データ保存先

### 7-1. Google Sheets との連携

LINE Botから入力された希望は、既存の運用スプレッドシートの `希望_YYYY-MM` シートに保存。

既存の希望シートのカラム：
```
doctor_id, doctor_name, ng_dates, avoid_dates, preferred_clinics,
date_clinic_requests, free_text, updated_at, post_night_dates
```

LINE入力時のマッピング：
| LINE入力 | 保存先カラム | 値 |
|----------|-------------|-----|
| 「休み」を選んだ日付 | `ng_dates` | カンマ区切りの日付リスト |
| （その他の日付） | （ng_datesに含まれない = 出勤可能） | - |

### 7-2. 既存入力との整合性

- LINE入力で登録すると、該当医員の既存希望データの `ng_dates` を**上書き**する
- Streamlit側からも引き続き入力可能（後から入力した方が優先）
- 最終確認で「登録する」を押した時点でGoogle Sheetsに書き込む

---

## 8. 通知機能（Push API）

### 8-1. リマインダー

- 希望入力の締め切り前に、未入力の医員にリマインダーを送信
- Push APIを使用（課金対象だが無料枠内）
- 既存GASリマインダー（reminder.gs）にLINE送信を追加
  - LINE連携済みの医員 → LINEで送信
  - 未連携の医員 → 従来通りメールで送信

### 8-2. スケジュール確定通知

- 管理者がスケジュールを確定した際に、LINE連携済みの医員に通知
- Flex Messageで見やすいスケジュール表を表示

---

## 9. GAS ファイル構成

```
gas/
├── reminder.gs              ← 既存（メールリマインダー・確定通知）
├── line_webhook.gs          ← 新規：LINE Webhook受信 + Reply応答
├── line_richmenu.gs         ← 新規：リッチメニュー作成（初回のみ実行）
├── line_notify.gs           ← 新規：LINE Push通知（リマインダー・確定通知）
└── line_config.gs           ← 新規：LINE API設定・共通関数
```

### 9-1. line_config.gs

```javascript
// LINE API設定
var LINE_CHANNEL_SECRET = PropertiesService.getScriptProperties()
    .getProperty('LINE_CHANNEL_SECRET');
var LINE_CHANNEL_ACCESS_TOKEN = PropertiesService.getScriptProperties()
    .getProperty('LINE_CHANNEL_ACCESS_TOKEN');

// Reply API呼び出し
function replyMessage(replyToken, messages) {
  UrlFetchApp.fetch("https://api.line.me/v2/bot/message/reply", {
    "method": "post",
    "headers": {
      "Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN,
      "Content-Type": "application/json"
    },
    "payload": JSON.stringify({
      "replyToken": replyToken,
      "messages": messages
    })
  });
}
```

### 9-2. line_webhook.gs（メイン処理）

```javascript
// Webhook受信エンドポイント（Web Appとしてデプロイ）
function doPost(e) {
  var events = JSON.parse(e.postData.contents).events;
  events.forEach(function(event) {
    if (event.type === "message" && event.message.type === "text") {
      handleTextMessage(event);
    }
  });
  return ContentService.createTextOutput("OK");
}

function handleTextMessage(event) {
  var userId = event.source.userId;
  var text = event.message.text.trim();
  var replyToken = event.replyToken;

  // 連携開始（リッチメニューから）
  if (text === "連携") {
    startAccountLink(userId, replyToken);  // → 「アカウント名を入力してください」
    return;
  }

  // 連携フロー中の入力（アカウント名・パスワード）を処理
  var linkSession = getLinkSession(userId);
  if (linkSession) {
    handleLinkInput(userId, text, replyToken, linkSession);
    return;
  }

  // 連携チェック
  var doctor = findDoctorByLineId(userId);
  if (!doctor) {
    replyMessage(replyToken, [{
      "type": "text",
      "text": "まずアカウント連携をしてください。\nメニューの「連携」ボタンをタップしてください"
    }]);
    return;
  }

  // コマンド振り分け
  switch (text) {
    case "希望入力":
      startPreferenceInput(doctor, userId, replyToken);
      break;
    case "予定確認":
      showSchedule(doctor, replyToken);
      break;
    case "ヘルプ":
      showHelp(replyToken);
      break;
    default:
      // 入力フロー中の応答を処理
      handleSessionInput(doctor, userId, text, replyToken);
  }
}
```

---

## 10. セッション管理

### GASでのセッション保存

GASにはメモリ内状態を持てないため、**Google Sheets の `LINEセッション` シート**でセッションを管理。

| カラム | 型 | 説明 |
|--------|-----|------|
| user_id | string | LINE User ID |
| state | string | 現在の状態 |
| doctor_id | string | 連携済み医員ID |
| target_month | string | 選択中の月（YYYY-MM） |
| current_date_index | number | 現在何番目の日付を聞いているか |
| preferences_json | string | 入力済み希望（JSON文字列） |
| updated_at | string | 最終更新日時 |

### 状態遷移

```
(初期状態)
    → "awaiting_account"     : アカウント名入力待ち（連携フロー）
    → "awaiting_password"    : パスワード入力待ち（連携フロー）
    → (セッション削除)        : 連携完了

(連携済みユーザー)
    → "selecting_month"      : 月選択中
    → "selecting_preference" : 日付ごとの希望入力中
    → "confirming"           : 最終確認中
    → (セッション削除)        : 登録完了
```

### タイムアウト

- 30分間操作がなければセッションを無効とみなす
- 次回操作時に最初からやり直し

---

## 11. エラーハンドリング

| ケース | 対応 |
|--------|------|
| 未連携ユーザーが希望入力 | 「まずアカウント連携をしてください」 |
| 受付中の月がない | 「現在、受付中の月はありません」で終了 |
| 想定外のメッセージ | ヘルプメッセージを表示 |
| Google Sheets API エラー | 「一時的にエラーが発生しました。しばらく待ってからお試しください」 |
| 入力フロー中に別コマンド | 現在のフローを中断し、新しいコマンドを処理 |

---

## 12. 実装フェーズ

### Phase 1: 基盤構築
1. LINE公式アカウント作成 & Messaging API有効化（手動作業）
2. `line_config.gs` 作成（API設定・共通関数）
3. `line_webhook.gs` 作成（Webhook受信・基本応答）
4. Web Appとしてデプロイ、LINE Developers でWebhook URL設定
5. 医員マスタに `line_user_id` カラム追加
6. アカウント連携機能実装

### Phase 2: リッチメニュー & 希望入力フロー
7. `line_richmenu.gs` 作成（リッチメニュー登録）
8. リッチメニュー画像作成・アップロード
9. セッション管理（`LINEセッション` シート）
10. 月選択機能（Quick Reply）
11. 日付ごとの希望入力機能（Quick Reply）
12. 最終確認画面（氏名＋日付一覧表示）
13. Google Sheetsへの希望データ書き込み
14. 「やり直す」機能

### Phase 3: 通知機能
15. `line_notify.gs` 作成（Push通知関数）
16. 既存 `reminder.gs` にLINE送信を統合
17. スケジュール確定通知（Flex Message）
18. 予定確認機能（「予定確認」コマンド）

### Phase 4: テスト・運用
19. テスト用LINEアカウントで動作確認
20. ヘルプメッセージ整備
21. 運用開始

---

## 13. 既存システムへの影響

### 変更が必要な箇所

| ファイル/シート | 変更内容 |
|----------------|---------|
| 医員マスタ（Google Sheets） | `line_user_id` カラム追加 |
| `database/master.py` | line_user_id の読み書きメソッド追加 |
| 運用SS | `LINEセッション` シート追加 |
| `gas/reminder.gs` | LINE連携済み医員へのLINE送信追加 |

### 変更不要な箇所

- `optimizer.py` （最適化ロジック）
- `ml_adjuster.py` （ML再調整）
- `database/operational.py` （既存の希望読み書きをそのまま使用）
- `pages/doctor_input.py` （Streamlit側の入力画面はそのまま維持）

---

## 14. 環境設定

### GAS スクリプトプロパティ（設定 > スクリプト プロパティ）

| キー | 値 |
|------|-----|
| `LINE_CHANNEL_SECRET` | チャネルシークレット |
| `LINE_CHANNEL_ACCESS_TOKEN` | チャネルアクセストークン |

### LINE Developers Console

| 設定項目 | 値 |
|---------|-----|
| Webhook URL | GAS Web App の URL |
| Webhook の利用 | ON |
| 応答メッセージ | OFF（GASで制御するため） |
| あいさつメッセージ | OFF or カスタム |

---

## 15. テスト計画

### テスト環境
- LINE Developers Console のテスト用チャネル
- GASのテスト実行（doPost をモック）

### テスト項目
- [ ] アカウント連携（正常系・異常系）
- [ ] リッチメニュー表示・タップ動作
- [ ] 月選択（受付中の月あり・なし）
- [ ] 日付ごとの希望入力（全日付入力）
- [ ] 最終確認画面の表示（氏名・日付・希望内容）
- [ ] 「登録する」でGoogle Sheetsに正しく保存
- [ ] 「やり直す」で入力に戻る
- [ ] 途中で別コマンド送信時の挙動
- [ ] セッションタイムアウト後の挙動
- [ ] 予定確認の表示
- [ ] リマインダーのLINE送信
- [ ] 確定通知のLINE送信

---

## 16. 将来の拡張案

- **3択化**: [出勤] [できれば休み] [休み] への拡張（△対応）
- **外勤先希望**: 特定の日に希望する外勤先を選択可能に
- **LIFF連携**: 複雑な入力はLINEアプリ内でStreamlit画面を表示
- **平日外勤対応**: 平日セクションの希望入力もLINEから
- **当番キャンセル連絡**: LINEから急な欠勤連絡を送信

---

## 付録: 用語対応表

| 本書の用語 | 既存システムの用語 | 説明 |
|-----------|-------------------|------|
| 出勤 | ○ | 出勤可能日 |
| 休み | × (NG日) | 出勤不可日 |
| できれば休み（将来） | △ (避けたい日) | できれば避けたい日 |
| 月選択 | 公開月 | 管理者が入力受付を開始した月 |
| 対象日 | スケジュール対象日 | 外勤のある土曜日 |
