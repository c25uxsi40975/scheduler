# LINE連携 技術調査レポート

## 概要

外勤調整システム（Streamlit + Google Sheets）に、医員向けのLINE連携機能を追加するための技術調査。

### 対象機能
1. LINEでのスケジュール入力
2. LINEでのリマインダー
3. LINEでのスケジュール通知

---

## 1. LINE Messaging API 基本情報

### 必要なもの
- **LINE公式アカウント** (LINE Official Account Manager で作成)
- **Messaging APIチャネル** (LINE Developers Console で設定)
- **チャネルシークレット** + **チャネルアクセストークン**

### 注意事項
- LINE Notifyは2025年3月31日にサービス終了済み → Messaging APIを使用
- 2026年中にBusiness Manager連携が必須化予定

---

## 2. アーキテクチャ

StreamlitはWebhookサーバーとして動作できないため、別途Webhookサーバーが必要。

```
[LINEユーザー（医員）]
    |
    v
[LINEプラットフォーム]
    |
    v (Webhook POST)
[Flask/FastAPIサーバー]  <-- Webhook受信・メッセージ処理・Push送信
    |
    v (読み書き)
[Google Sheets]          <-- 共有データストア
    ^
    | (読み取り)
[Streamlitアプリ]        <-- 管理者UI（既存）
```

### サーバー共存パターン

| パターン | 説明 | 適用 |
|---------|------|------|
| 同一サーバー別ポート | Streamlit:8501 / Flask:5000 + nginx | VPS運用時 |
| 別サーバー | Webhook: Cloud Run等 | クラウド分離 |
| FastAPI統合 | WebhookもAPIも1つのFastAPIで | シンプル構成 |

---

## 3. 料金体系

### メッセージ種別と課金

| API | 用途 | 課金 |
|-----|------|------|
| **Reply API** | ユーザーメッセージへの応答 | **無料（カウントされない）** |
| **Push API** | サーバーから任意タイミングで送信 | **カウントされる** |
| **Multicast API** | 複数ユーザーへ一括送信 | **カウントされる** |

### 料金プラン（日本 / 2026年時点）

| プラン | 月額 | 無料メッセージ | 追加メッセージ |
|--------|------|---------------|---------------|
| コミュニケーション | 無料 | 200通/月 | 不可 |
| ライト | 5,000円 | 5,000通/月 | 不可 |
| スタンダード | 15,000円 | 30,000通/月 | ~3円/通 |

※ 通数カウント = 送信先の **人数**（1人に複数メッセージオブジェクト送信しても1通）

### 本プロジェクトの試算（医員20名想定）

| 種別 | 通数/月 | 課金 |
|------|---------|------|
| スケジュール入力応答 (Reply) | ~100通 | 無料 |
| 入力リマインダー (Push) | ~40通 | カウント |
| スケジュール確定通知 (Push) | ~20通 | カウント |
| その他通知 (Push) | ~20通 | カウント |
| **Push合計** | **~80通/月** | **無料枠(200通)内** |

**結論: コミュニケーションプラン（無料）で運用可能。安全を見てもライトプラン（月5,000円）で十分。**

---

## 4. Python SDK

### インストール
```bash
pip install line-bot-sdk
```

### バージョン
- **v3系**（3.x）を使用。v2は非推奨。
- Python >= 3.10 必須
- `linebot.v3` モジュールを使用

### 基本構造

```python
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage, FlexMessage, FlexContainer,
    QuickReply, QuickReplyItem,
    DatetimePickerAction, PostbackAction, MessageAction,
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent,
    FollowEvent, PostbackEvent,
)

configuration = Configuration(access_token='CHANNEL_ACCESS_TOKEN')
handler = WebhookHandler('CHANNEL_SECRET')
```

---

## 5. 機能別設計

### 5-1. LINEでのスケジュール入力

#### 方式A: チャットベース（Quick Reply + Datetime Picker）

```
医員: リッチメニューの「スケジュール入力」をタップ
ボット: 「入力モードを選択」 → [希望日登録] [不可日登録] [確認]
医員: 「希望日登録」をタップ
ボット: Datetime Picker表示
医員: 日付を選択
ボット: 「4/7(月)を希望日として登録しました。続けますか？」
```

- Reply APIで応答 → **完全無料**
- 日付選択はDatetime Picker Actionでネイティブカレンダーを表示

#### 方式B: LIFF経由でStreamlit画面を開く

- リッチメニューからLIFF URLをタップ → LINEアプリ内でStreamlitのスケジュール入力画面を表示
- LINE User IDで自動ログイン
- 複雑な入力（外勤先の希望など）に適する

#### 推奨
- 簡単な入力（希望日・不可日）→ **方式A（チャットベース）**
- 複雑な入力 → **方式B（LIFF）**
- 両方を併用するのがベスト

### 5-2. LINEでのリマインダー

```python
def send_schedule_reminder():
    """未入力の医員にリマインダー送信"""
    doctors = get_doctors_without_schedule(target_month)
    for doctor in doctors:
        if doctor.get('line_user_id'):
            push_message(
                user_id=doctor['line_user_id'],
                text="【リマインダー】4月のスケジュール希望が未入力です。期限: 3/20まで"
            )
```

- **Push API**を使用（課金対象）
- cron or APSchedulerで定期実行
- 未入力者のみに送信 → 通数節約

### 5-3. LINEでのスケジュール通知

```python
def notify_confirmed_schedule(month):
    """確定スケジュールをFlex Messageで各医員に通知"""
    for doctor_id, assignments in get_confirmed_schedules(month).items():
        doctor = get_doctor(doctor_id)
        if doctor.get('line_user_id'):
            flex = build_schedule_flex_message(doctor['name'], assignments)
            push_flex_message(doctor['line_user_id'], flex)
```

- **Flex Message**で見やすいスケジュール表を表示
- Push APIを使用

---

## 6. アカウント連携

### 医員マスタへの変更
`line_user_id` カラムを追加。

### 連携フロー（簡易方式）

```
1. 医員がLINE公式アカウントを友だち追加
2. 「連携 {account_name}」とメッセージ送信
3. ボットがaccount_nameで医員マスタを検索
4. 見つかったらLINE User IDを医員マスタに保存
5. 連携完了メッセージを応答
```

※ セキュリティ強化が必要なら、パスワード確認やワンタイムコードを追加

---

## 7. UI要素

### リッチメニュー
トーク画面下部に常時表示されるメニュー（画像ベース）。

```
┌──────────────┬──────────────┐
│ スケジュール  │  スケジュール  │
│   入力       │    確認      │
├──────────────┼──────────────┤
│  希望日登録   │  Webアプリ   │
│              │   を開く     │
└──────────────┴──────────────┘
```

### Flex Message
CSS Flexboxベースのリッチなメッセージ。スケジュール表示に最適。

### Quick Reply
メッセージ下部のボタン。選択肢を提示する。

### Datetime Picker
ネイティブの日付選択UI。スケジュール入力に最適。

---

## 8. Webhookサーバー要件

- **HTTPS必須**（LINEはHTTPSのみにWebhookを送信）
- **署名検証**: `X-Line-Signature`ヘッダーで検証
- **即座にHTTP 200を返す**（重い処理は非同期で）

### 開発環境
```bash
# ngrokでHTTPSトンネル
ngrok http 5000
# → https://xxxx.ngrok-free.app を Webhook URLに設定
```

### 本番環境の候補
- Google Cloud Run（既にGCPのGoogle Sheets APIを使用しているなら相性良い）
- Heroku / Railway / Render
- VPS（Streamlitと同居可能）

---

## 9. 必要なファイル構成

```
scheduler/
  line_bot/
    __init__.py
    config.py            # LINE API設定
    webhook_server.py    # Flask Webhookサーバー
    handlers.py          # メッセージハンドラー
    messages.py          # Flex Message / Quick Reply構築
    notifications.py     # Push Message送信（リマインダー・通知）
    account_link.py      # アカウント連携ロジック
  database/
    master.py            # line_user_idカラム追加
```

---

## 10. 実装ステップ

### Phase 1: 基盤構築
1. LINE公式アカウント作成 & Messaging API有効化
2. `line_bot/` パッケージ作成
3. Flask Webhookサーバー実装
4. 医員マスタに `line_user_id` カラム追加
5. アカウント連携機能実装

### Phase 2: スケジュール通知
6. スケジュール確定通知（Flex Message）
7. リマインダー送信機能
8. 管理画面から通知送信のUI

### Phase 3: スケジュール入力
9. チャットベースのスケジュール入力（Quick Reply + Datetime Picker）
10. リッチメニュー設定
11. （オプション）LIFF連携

---

## 参考リンク

- [Messaging API概要](https://developers.line.biz/en/docs/messaging-api/overview/)
- [line-bot-sdk-python](https://github.com/line/line-bot-sdk-python)
- [Flex Message Simulator](https://developers.line.biz/flex-message-simulator/)
- [LINE公式アカウント料金プラン](https://www.lycbiz.com/jp/service/line-official-account/plan/)
- [LIFF概要](https://developers.line.biz/en/docs/liff/overview/)
