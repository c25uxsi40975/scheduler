# セキュリティ評価レポート

**対象アプリケーション**: 外勤調整システム (Streamlit + Google Sheets)
**デプロイ先**: Streamlit Community Cloud
**評価日**: 2026-03-01
**評価範囲**: アプリケーション全体（認証・データストア・通信・UI・GAS連携）

---

## Streamlit Cloud が提供するセキュリティ機能

本評価にあたり、Streamlit Community Cloud が標準で提供する以下のセキュリティ機能を前提条件とする。

| 機能 | 詳細 |
|------|------|
| **HTTPS強制** | 全通信がTLS暗号化される。パスワード等のフォーム送信も暗号化済み |
| **サーバーサイドSession State** | `st.session_state`はサーバー側で管理され、クライアントJavaScriptからの直接アクセス・改竄は不可 |
| **Secrets管理** | `st.secrets`は暗号化保存され、アプリのソースコードやGitリポジトリには含まれない |
| **WebSocket通信** | Streamlitはフォーム送信ではなくWebSocket（wss://）で通信するため、従来のCSRF攻撃の対象外 |
| **コンテナ分離** | 各アプリは独立したコンテナで実行される |

**これにより、前回レポートの以下の項目は緩和済みとする:**
- ~~HTTPSの非強制~~ → Streamlit Cloudでは自動的にHTTPS
- ~~CSRF攻撃~~ → WebSocketベースの通信で従来型CSRFは成立しにくい
- ~~セッションState改竄~~ → サーバーサイド管理のため直接改竄不可

---

## 重大度: Critical（致命的）

### 1. パスワードハッシュの脆弱性（ソルトなしSHA-256）

**該当箇所**: `database/connection.py:154`

```python
def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()
```

**問題点**:
- ソルト（salt）を使用していないため、同じパスワードは常に同じハッシュ値になる
- SHA-256は高速なハッシュ関数であり、パスワードハッシュ用途には不適切（GPUで毎秒数十億回の試行が可能）
- レインボーテーブル攻撃に脆弱
- 複数ユーザーが同じパスワードを使用している場合、ハッシュ値の一致から即座に判明する

**Streamlit Cloud環境での影響**:
- パスワードハッシュはスプレッドシート上に保存されるため（後述 #2）、スプレッドシートにアクセス権を持つ人物がハッシュ値を閲覧・逆引きできる
- Streamlit Cloud自体はこのリスクを緩和しない（アプリケーションロジックの問題）

**推奨対策**:
- `bcrypt`への移行（`requirements.txt`に`bcrypt`を追加）
  ```python
  import bcrypt
  def _hash_password(password: str) -> str:
      return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
  def _verify_password(password: str, hashed: str) -> bool:
      return bcrypt.checkpw(password.encode(), hashed.encode())
  ```

### 2. パスワードハッシュがスプレッドシート上に露出

**該当箇所**: `database/connection.py:161`（医員マスタスキーマ `password_hash`列）、`database/auth.py:51`（管理者パスワード）

**問題点**:
- 医員マスタシートの`password_hash`列に全医員のハッシュが記録されている
- 管理者パスワードハッシュも`設定`シートに保存されている
- スプレッドシートの共有設定で「編集者」権限を持つ人物は全ハッシュを閲覧可能
- Googleスプレッドシートの**変更履歴**から過去のハッシュ値も取得可能（削除しても復元可能）
- ソルトなしSHA-256との組み合わせで、辞書攻撃により短時間でパスワード平文が復元される

**Streamlit Cloud環境での影響**:
- Streamlit Cloudの`st.secrets`はサービスアカウントキーの保護に有効だが、**スプレッドシートの中身自体の保護はアプリの責任**
- スプレッドシートを管理目的で他のメンバーと共有している場合、パスワードハッシュも共有されてしまう

**推奨対策**:
- パスワード認証を外部サービス（Firebase Auth等）に委譲する
- 最低限、スプレッドシートの共有設定をサービスアカウントのみに制限し、人間ユーザーには「閲覧者」権限すら付与しない

### 3. デフォルトパスワード「1111」の自動設定

**該当箇所**: `database/connection.py:228-241`、`database/master.py:63`

```python
def add_doctor(name, account="", initial_password="1111"):
```

**問題点**:
- 全医員のデフォルトパスワードが`"1111"`で固定されている
- `init_db()`でパスワード未設定の医員に対して自動的に`"1111"`のハッシュを設定（`connection.py:236`）
- 新規医員追加時もデフォルト値が`"1111"`（`admin_master.py:89`でUIにも表示）
- パスワード変更を強制する仕組みがなく、変更しないまま運用されるリスクが高い

**Streamlit Cloud環境での影響**:
- Streamlit Community CloudのアプリURLは**公開**される（URLを知る者は誰でもアクセス可能）
- アカウント名（入局年度等）は推測しやすく、パスワード`"1111"`との組み合わせで**URLを知っているだけで任意の医員としてログイン可能**
- これは本レポートの中で最も現実的な攻撃シナリオである

**推奨対策**:
- デフォルトパスワードの使用を廃止
- 初回ログイン時にパスワード変更を強制する仕組みを追加
- パスワードの最低要件（長さ・複雑性）を設ける

---

## 重大度: High（高）

### 4. Streamlit Cloud上でのアプリ公開範囲の制御不能

**問題点**:
- Streamlit Community CloudにデプロイされたアプリはURLベースでアクセス可能
- URLを知っている人は誰でもログイン画面にアクセスできる（IPアドレス制限不可）
- Streamlit Community Cloudでは**認証ゲートウェイ**（Streamlit Teams/Enterprise機能）が利用できない
- アプリURLが検索エンジンにインデックスされる可能性がある
- GitHub公開リポジトリからデプロイしている場合、`app.py`の存在からアプリURLが推測可能

**推奨対策**:
- GitHubリポジトリをプライベートに設定（Streamlit Cloudでもプライベートリポジトリからデプロイ可能）
- `robots.txt`相当の対策はStreamlit Cloudでは不可のため、アプリケーション側で強固な認証を実装する必要がある
- Streamlit Teams/Enterprise（有料）への移行を検討し、SSO/組織レベルの認証ゲートウェイを利用

### 5. ログインに対するレート制限の欠如

**該当箇所**: `app.py:98-103`（管理者ログイン）、`app.py:119-131`（医員ログイン）

**問題点**:
- ログイン試行回数に制限がない
- ブルートフォース攻撃やパスワードスプレー攻撃に対する防御がない
- Streamlit Cloudではインフラレベルでのレート制限（WAF等）を設定できない
- 4桁数字のデフォルトパスワードの場合、10,000通りの総当たりが短時間で完了する

**Streamlit Cloud環境での影響**:
- Streamlit CloudにはWAFやIPベースの制限機能がないため、**レート制限はアプリケーション側で実装する必要がある**
- ただし、Streamlitの仕組み上各リクエストはWebSocket接続内で処理されるため、自動化ツールでの高速試行はHTTPフォームほど容易ではない
- とはいえ、Selenium等のブラウザ自動化ツールによる攻撃は依然として可能

**推奨対策**:
- `st.session_state`を用いたログイン試行回数の追跡と制限（例: 5回失敗で5分間ロック）
  ```python
  if st.session_state.get("login_attempts", 0) >= 5:
      lockout_until = st.session_state.get("lockout_until", 0)
      if time.time() < lockout_until:
          st.error("ログイン試行回数を超えました。しばらくお待ちください。")
          st.stop()
      else:
          st.session_state.login_attempts = 0
  ```
- 注意: `st.session_state`はセッション単位のため、新しいブラウザセッションで回避可能。完全な防御にはスプレッドシート側にロックアウト状態を保持する必要がある

### 6. セッションタイムアウトの欠如

**該当箇所**: `app.py:54-61`

**問題点**:
- 認証済みセッションのタイムアウトが設定されていない
- ブラウザタブを開いたまま放置すると、認証済み状態が無期限に維持される
- Streamlit Cloudではアプリが一定時間操作されないとスリープ状態になるが、再アクセス時にセッションが復元される場合がある

**Streamlit Cloud環境での影響**:
- Streamlit Community Cloudのアプリは無操作数分でスリープするが、これは認証セッションの無効化とは異なる
- 共有PC（病院内PC等）で使用される場合、ログアウトし忘れによる不正アクセスのリスクが高い

**推奨対策**:
- 認証時に`st.session_state.login_time = time.time()`を記録し、一定時間経過後に自動ログアウト
  ```python
  import time
  SESSION_TIMEOUT = 30 * 60  # 30分
  if time.time() - st.session_state.get("login_time", 0) > SESSION_TIMEOUT:
      # セッション無効化
      st.session_state.clear()
      st.rerun()
  ```

### 7. サービスアカウントキーのリスク集中

**該当箇所**: `database/connection.py:38-58`

**問題点**:
- 単一のGCPサービスアカウントが2つのスプレッドシートの**全シート・全セル**にフルアクセス権を持つ
- このキーが漏洩した場合の影響範囲: 全医員情報（氏名・メールアドレス・パスワードハッシュ）、全スケジュール、全学習データ
- Google Sheets APIでは行・列レベルのアクセス制御が不可能

**Streamlit Cloud環境での影響**:
- `st.secrets`による保護は適切（暗号化保存、ソースコードに含まれない）
- ただし、Streamlit CloudのSecretsにアクセスできるのはリポジトリの管理者のみという前提に依存
- サービスアカウントキーのローテーションがStreamlit Cloud上では手動操作が必要

**推奨対策**:
- サービスアカウントキーの定期的なローテーション（最低年1回）
- スプレッドシートの共有設定をサービスアカウントの編集権限のみに最小化
- 可能であればWorkload Identity Federation（キーレス認証）の採用を検討

---

## 重大度: Medium（中）

### 8. XSS（クロスサイトスクリプティング）のリスク

**該当箇所**: `app.py:33`、`admin_master.py:78`、`admin_master.py:136`、`admin_master.py:316`

```python
st.markdown(
    "<style>[data-testid='stSidebar']{display:none}</style>",
    unsafe_allow_html=True,
)
```

**問題点**:
- `unsafe_allow_html=True`が複数箇所で使用されている
- `app.py:33`の使用はCSS注入のみで直接的なXSSリスクは低い
- しかし`admin_master.py:136-137`ではユーザーデータがマークダウン文字列内に埋め込まれる:
  ```python
  st.markdown(f"**{d['name']}**　{status_label}　ID: {id_display}...")
  ```
- 医員名にHTMLタグを含む文字列（例: `<img onerror=alert(1)>`）が設定された場合、管理者画面でXSSが成立する可能性がある

**Streamlit Cloud環境での影響**:
- Streamlitはデフォルトで一定のCSP（Content Security Policy）ヘッダーを付与するが、`unsafe_allow_html=True`はこれを部分的にバイパスする
- XSSが成立した場合、Streamlit CloudのWebSocket通信を乗っ取り、管理者セッションでの任意操作が可能になりうる
- ただし、現実の攻撃シナリオとしては、攻撃者が医員名を自由に設定できる状況（管理者権限が必要）に限定される

**推奨対策**:
- `app.py:33`のCSS適用は`unsafe_allow_html=True`が必要だが、他の箇所では使用を避ける
- ユーザーデータをHTML内に埋め込む箇所では`html.escape()`でエスケープする
  ```python
  import html
  safe_name = html.escape(d['name'])
  st.markdown(f"**{safe_name}**　...")
  ```

### 9. GAS Web Appエンドポイントの認証不足

**該当箇所**: `gas/reminder.gs:56-75`、`pages/doctor_input.py:33-57`

**問題点**:
- GAS Web Appの`doPost()`にリクエスト元の認証がない
- エンドポイントURL（`gas_webapp_url`）を知る者は誰でもリクエスト送信可能
- 悪意あるリクエストにより任意のメールアドレスへの通知メール送信が可能
- `action`パラメータを操作して意図しないメール送信をトリガーできる
- `doctor_email`パラメータに任意のメールアドレスを指定してスパム送信の踏み台にできる

**Streamlit Cloud環境での影響**:
- GAS Web AppのURLはStreamlit Cloudの`st.secrets`に安全に保存されているが、**GAS側の認証が不在のためURL知識だけで悪用可能**
- Streamlit Cloud外からGASエンドポイントへ直接リクエストを送ることが可能

**推奨対策**:
- GAS側でシークレットトークンによる認証を追加:
  ```javascript
  function doPost(e) {
      var data = JSON.parse(e.postData.contents);
      if (data.token !== "YOUR_SECRET_TOKEN") {
          return ContentService.createTextOutput(
              JSON.stringify({ status: "unauthorized" })
          ).setMimeType(ContentService.MimeType.JSON);
      }
      // ... 以降の処理
  }
  ```
- Streamlit側で送信時にトークンを付与:
  ```python
  requests.post(gas_url, json={
      "token": st.secrets["gas_auth_token"],
      "action": "...",
      ...
  })
  ```

### 10. 入力値バリデーションの不足

**該当箇所**: `database/auth.py:138-148`（メールアドレス）、`database/master.py:63-85`（医員追加）

**問題点**:
- メールアドレスのフォーマット検証なし — 任意の文字列を`email`として保存可能
- 医員名・外勤先名に対する文字数制限なし
- 数値フィールド（`max_assignments`、`fee`等）の上下限チェックが一部欠如
- `free_text`（自由入力欄）に対するサイズ制限なし — Google Sheetsのセル容量（50,000文字）まで格納可能

**推奨対策**:
- メールアドレスの正規表現検証を追加
- 各フィールドに適切な文字数制限・範囲制限を追加
- バリデーションはUI側（Streamlit）とデータ操作側（database層）の両方で実施

### 11. エラーメッセージによる情報漏洩

**該当箇所**: `database/connection.py:68-81`（リトライ処理）、各所のAPIエラーハンドリング

**問題点**:
- `gspread.exceptions.APIError`がキャッチされずにStreamlitのエラー画面に表示された場合、スプレッドシートIDやサービスアカウント情報がスタックトレースに含まれる可能性がある
- Streamlit Cloudのデフォルト設定ではエラーのスタックトレースが画面に表示される

**Streamlit Cloud環境での影響**:
- エラー画面がアプリ利用者（医員）に直接表示され、内部情報が漏洩するリスク
- スプレッドシートキーが漏洩した場合、攻撃者はサービスアカウント情報なしでもシートの存在を確認できる

**推奨対策**:
- `.streamlit/config.toml`に`showErrorDetails = false`を追加:
  ```toml
  [client]
  showErrorDetails = false
  ```
- アプリケーションレベルで`try-except`を用いて汎用エラーメッセージを表示

### 12. 同時アクセス時のデータ競合

**該当箇所**: `database/connection.py:145-151`（`_next_id`）、`database/operational.py`

**問題点**:
- `_next_id()`が最大ID+1で採番するため、複数セッションが同時にIDを取得するとID重複が発生する
- `_find_row_index()` → `update_cell()` の間にレースコンディションが発生する可能性
- Google Sheets APIには楽観的ロック機能がない

**Streamlit Cloud環境での影響**:
- Streamlit Cloudでは各ユーザーセッションが独立したプロセスで動作するため、同時アクセスによる競合リスクが**ローカル実行時より高い**
- 管理者が同時にスケジュールを操作したり、複数の医員が同時に希望を保存した場合にデータ不整合が発生する可能性

**推奨対策**:
- ID採番にUUIDを使用
- 更新時に`updated_at`タイムスタンプの一致を確認する楽観的ロックの導入

---

## 重大度: Low（低）

### 13. 認可チェックの論理的不足

**該当箇所**: `pages/doctor_input.py`、`database/operational.py:76-109`

**問題点**:
- `upsert_preference()`は`doctor_id`パラメータを直接受け取り、呼び出し元セッションとの整合性チェックがない
- 理論的には、`st.session_state.doctor_id`を変更しても**Streamlitのサーバーサイドセッション管理により外部からの改竄は不可能**
- しかし、将来的にAPIエンドポイントが追加された場合、この設計は脆弱性の原因になりうる

**Streamlit Cloud環境での影響**:
- 現在のStreamlitアーキテクチャでは`session_state`は安全に管理されており、**現時点での実質的なリスクは低い**
- ただし防御的プログラミングの観点からは改善が望ましい

**推奨対策**:
- データ操作関数の呼び出し時に`session_state`の認証情報を照合するラッパーを追加

### 14. パスワード変更時の既存セッション維持

**該当箇所**: `app.py:238-252`

**問題点**:
- パスワード変更後、他のブラウザ/デバイスの既存セッションが無効化されない

**Streamlit Cloud環境での影響**:
- Streamlit Cloud上の各セッションは独立しており、セッション間の通信手段がない
- 完全なセッション無効化にはスプレッドシート上に「パスワード変更日時」を記録し、各セッションで定期的にチェックする必要がある

### 15. 機密データのキャッシュ

**該当箇所**: `database/master.py:42-43`

```python
@st.cache_data(ttl=120)
def get_doctors(active_only=True):
```

**問題点**:
- `password_hash`を含む医員データがStreamlitのプロセスメモリにキャッシュされる
- TTL（120秒）の間、パスワード変更が反映されない（旧パスワードで認証成功する窓）

**Streamlit Cloud環境での影響**:
- Streamlit Cloudのコンテナはアプリごとに分離されており、他アプリからのメモリアクセスリスクは低い
- 主なリスクはTTL内のパスワード変更未反映

---

## Google Sheets特有のリスク

### A. データベースとしてのGoogle Sheetsの根本的制限

| 観点 | リスク | Streamlit Cloudでの影響 |
|------|--------|------------------------|
| アクセス制御 | 行・列レベルの制御不可 | アプリの認可チェックが唯一の防御層 |
| 監査証跡 | アプリ経由とシート直接編集の区別不可 | 誰がいつ変更したかの追跡が困難 |
| トランザクション | ACID特性なし | 同時アクセス時のデータ不整合リスク（#12参照） |
| API制限 | 100リクエスト/100秒/ユーザー | 同時利用者増加時にAPI制限に到達する可能性 |
| データ容量 | 1シート最大1000万セル | 長期運用時のデータ量増加に注意 |
| データ分離 | マスタ/運用の2シートだが同一サービスアカウント | キー漏洩時に全データ流出 |

### B. スプレッドシート直接編集によるバイパス

**問題点**:
- スプレッドシートに「編集者」権限を持つユーザーは、Google Sheets UIから直接データを編集可能
- パスワードハッシュの書き換え → 任意のアカウント乗っ取り
- `is_active`フラグの操作 → 無効化されたアカウントの復活
- `is_confirmed`フラグの操作 → 未承認スケジュールの強制確定
- アプリのバリデーション・認可チェックを完全にバイパス可能

**推奨対策**:
- スプレッドシートの共有設定をサービスアカウントのみに制限（**最重要**）
- 運用担当者にもスプレッドシートの直接編集権限を与えない
- 必要に応じてGASトリガーによる変更監視を導入

---

## Streamlit Cloud特有の考慮事項

### C. アプリの公開性

- Community Cloudのアプリは**URLを知る者全員がアクセス可能**
- アクセス制限（IP制限、SSO等）を追加する手段がない
- **アプリの認証機能が唯一のアクセス制御層**であり、その品質が極めて重要

### D. リソース制限とDoS

- Streamlit Community Cloudのアプリには**リソース制限**（CPU・メモリ・帯域）がある
- 大量の同時アクセスやAPI呼び出しでアプリが応答不能になる可能性
- Google Sheets APIのレート制限（100リクエスト/100秒）を超えるとエラーが発生
- リトライ処理（`connection.py:68-81`）がある程度緩和するが、DoS攻撃への耐性は低い

### E. アプリスリープと状態喪失

- Community Cloudのアプリは無操作時にスリープする
- スリープ復帰時に`st.session_state`がリセットされ、再ログインが必要
- これはセキュリティ上は**ポジティブな効果**（自動セッション失効として機能）

---

## 攻撃シナリオの想定

Streamlit Cloud環境での現実的な攻撃シナリオを整理する。

### シナリオ1: デフォルトパスワードによるアカウント乗っ取り（リスク: 高）

1. 攻撃者がアプリのURLを取得（GitHub公開リポジトリ、口頭共有等）
2. 医員ログイン画面にアクセス
3. アカウント名（入局年度: 2020, 2021, ...等）を推測
4. デフォルトパスワード`"1111"`でログイン試行
5. **成功した場合**: 医員の希望データ閲覧・改竄、メールアドレスの取得が可能

### シナリオ2: スプレッドシート経由のデータ漏洩（リスク: 中）

1. スプレッドシートの共有リンクが漏洩、または共有設定が「リンクを知っている全員」になっている
2. 全医員の氏名・メールアドレス・パスワードハッシュが閲覧可能
3. ソルトなしSHA-256のため、辞書攻撃でパスワード平文を復元
4. 復元したパスワードで他サービスへの攻撃（パスワードの使い回しがある場合）

### シナリオ3: GASエンドポイントの悪用（リスク: 中）

1. GAS Web AppのURLが漏洩（ログ、ネットワーク監視等）
2. 攻撃者が`doPost()`に直接リクエストを送信
3. `action: "preference_confirmed_to_doctor"`で任意のメールアドレスに通知メールを送信
4. 組織名・システム名を含むメールが外部に送信される（情報漏洩+スパム踏み台）

---

## 対策の優先順位（Streamlit Cloud環境向け）

| 優先度 | 対策 | 工数 | 効果 |
|--------|------|------|------|
| **1** | デフォルトパスワード廃止 + 初回変更強制 | 小 | シナリオ1を直接防止 |
| **2** | パスワードハッシュをbcryptに移行 | 小 | シナリオ2の被害を大幅に軽減 |
| **3** | スプレッドシート共有設定の最小化 | 小 | シナリオ2を直接防止 |
| **4** | ログインレート制限の実装 | 小 | ブルートフォース攻撃を緩和 |
| **5** | セッションタイムアウトの追加 | 小 | 放置端末からの不正アクセスを防止 |
| **6** | GAS Web Appへの認証トークン追加 | 中 | シナリオ3を直接防止 |
| **7** | `showErrorDetails = false`の設定 | 小 | 内部情報漏洩を防止 |
| **8** | `unsafe_allow_html`箇所のサニタイズ | 中 | XSSリスクの軽減 |
| **9** | GitHubリポジトリのプライベート化 | 小 | アプリURLの推測を困難化 |

---

## 総合評価

本アプリケーションをStreamlit Community Cloudにデプロイする場合、Streamlit Cloudが提供するHTTPS・セッション管理・Secrets管理の恩恵を受けられるが、**アプリケーション層の認証・認可が唯一のアクセス制御層**となるため、その品質が特に重要となる。

**現状の最大リスク**:
デフォルトパスワード「1111」+ レート制限なし + URLが公開される環境 の組み合わせにより、**アプリURLを知る者は最小限のスキルで任意の医員アカウントにログインできる**状態にある。

**必須対策（最低限）**:
1. デフォルトパスワードの廃止と初回パスワード変更の強制
2. bcryptへの移行
3. スプレッドシートの共有設定の厳格化（サービスアカウントのみ）

**推奨対策（中期）**:
4. ログインレート制限
5. セッションタイムアウト
6. GASエンドポイントの認証追加

**検討事項（長期）**:
- Streamlit Teams/Enterprise（有料）への移行によるSSO・認証ゲートウェイの利用
- Google Sheetsからの段階的な移行（Supabase、Neon等のマネージドDB）
