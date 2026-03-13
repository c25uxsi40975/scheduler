/**
 * 外勤リマインダー・通知スクリプト — 共通モジュール
 *
 * 【設計方針: 集中型GAS】
 * 土曜・平日すべての通知を1つのGASプロジェクトで処理する。
 * GASプロジェクト内の全ファイルは1つの名前空間を共有するため、
 * ファイル分割はコード整理目的であり、配置先SSの分離ではない。
 *
 * - common.gs : 設定変数、doPost ディスパッチャー、共通ヘルパー
 * - saturday.gs : 土曜外勤の通知ハンドラ・トリガー関数
 * - weekday.gs : 平日外勤の通知ハンドラ・トリガー関数
 *
 * 3ファイルすべてを土曜運用SS（外勤調整_土曜外勤）に配置する。
 * 平日セクション別SSにはGAS不要（openById でデータ参照のみ）。
 * → セクション追加時にGASの変更・再デプロイは不要。
 *
 * セットアップ:
 *   1. 土曜運用SS（外勤調整_土曜外勤）で「拡張機能 > Apps Script」を開く
 *   2. common.gs / saturday.gs / weekday.gs の3ファイルを作成し内容を貼り付ける
 *   3. MASTER_SPREADSHEET_ID を設定（必須）
 *   4. トリガーを登録:
 *      - sendFridayReminder: 毎週金曜 18:00-19:00
 *      - checkDeadline: 毎日 9:00-10:00
 *      - checkWeekdayDeadlines: 毎日 9:00-10:00
 *      - sendWeekdayDayBeforeReminder: 毎日 18:00-19:00
 *   5. Web Appとしてデプロイ（確定通知用）
 */

// ---- 設定 ----

// 送信者として表示する名前
var SENDER_NAME = "外勤調整システム";

// マスタデータ用スプレッドシートID（必須）
var MASTER_SPREADSHEET_ID = "";

// 管理者メールアドレス（カンマ区切りで複数指定可）
// 例: "admin1@example.com, admin2@example.com"
var ADMIN_EMAIL = "";

// テストモード（本番運用時は false に変更してください）
var TEST_MODE = true;
var TEST_NOTICE = "【テスト送信】このメールはテストです。記載の外勤先は実際のものではありません。実際の外勤先は別途ご確認ください。\n\n";

// ---- スプレッドシート取得 ----

/**
 * 運用データ用スプレッドシート（このスクリプトが設置されているスプレッドシート）
 */
function getOperationalSpreadsheet() {
  return SpreadsheetApp.getActiveSpreadsheet();
}

/**
 * マスタ用スプレッドシート（IDで別スプレッドシートを開く）
 */
function getMasterSpreadsheet() {
  if (!MASTER_SPREADSHEET_ID) {
    throw new Error("MASTER_SPREADSHEET_ID が未設定です。マスタ用スプレッドシートのIDを設定してください。");
  }
  return SpreadsheetApp.openById(MASTER_SPREADSHEET_ID);
}

// ---- Web App エンドポイント ----

/**
 * Streamlitアプリからのリクエストを受信し、アクションに応じて各ハンドラに振り分け
 */
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);

    // 土曜関連
    if (data.action === "schedule_confirmed") {
      sendConfirmationEmails(data.year_month, data.plan_name);
    } else if (data.action === "preference_confirmed_to_doctor") {
      sendDoctorConfirmation(data.year_month, data.doctor_name, data.doctor_email, data.date_summary, data.free_text);
    } else if (data.action === "all_preferences_complete") {
      sendAllCompleteNotification(data.year_month, data.doctor_count);

    // 共通
    } else if (data.action === "password_reset_code") {
      sendPasswordResetCode(data.account_name, data.doctor_email, data.reset_code);

    // スプレッドシート作成
    } else if (data.action === "create_spreadsheet") {
      var result = createSpreadsheetForSection(data.title, data.share_with);
      return ContentService.createTextOutput(
        JSON.stringify({ status: "ok", spreadsheet_id: result.id, url: result.url })
      ).setMimeType(ContentService.MimeType.JSON);

    // 平日関連
    } else if (data.action === "weekday_schedule_confirmed") {
      sendWeekdayScheduleConfirmed(data);
    } else if (data.action === "weekday_preference_confirmed") {
      sendWeekdayPreferenceConfirmed(data);
    } else if (data.action === "weekday_all_preferences_complete") {
      sendWeekdayAllPreferencesComplete(data);
    } else if (data.action === "shift_swap_executed") {
      sendShiftSwapNotification(data);
    }

    return ContentService.createTextOutput(
      JSON.stringify({ status: "ok" })
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    Logger.log("doPost error: " + err.message);
    return ContentService.createTextOutput(
      JSON.stringify({ status: "error", message: err.message })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}

// ---- パスワードリセットコード送信 ----

/**
 * パスワードリセットコードを医員にメール送信
 */
function sendPasswordResetCode(accountName, doctorEmail, resetCode) {
  if (!doctorEmail) {
    Logger.log("パスワードリセット: メールアドレスなし (account: " + accountName + ")");
    return;
  }

  var subject = "【外勤調整システム】パスワードリセットコード";
  var body = accountName + " 様\n\n"
    + "パスワードリセットが要求されました。\n"
    + "以下のリセットコードをアプリの画面に入力してください。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  リセットコード: " + resetCode + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "※ このコードは15分間有効です。\n"
    + "※ 心当たりがない場合はこのメールを無視してください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  try {
    GmailApp.sendEmail(doctorEmail, subject, body, { name: SENDER_NAME });
    Logger.log("パスワードリセットコード 送信成功: " + accountName + " (" + doctorEmail + ")");
  } catch (e) {
    Logger.log("パスワードリセットコード 送信失敗: " + accountName + " - " + e.message);
  }
}

// ---- 共通ヘルパー関数 ----

/**
 * ADMIN_EMAIL をパースして有効なメールアドレスの配列を返す
 */
function getAdminEmails() {
  if (!ADMIN_EMAIL) return [];
  return ADMIN_EMAIL.split(",").map(function(e) { return e.trim(); }).filter(function(e) { return e.length > 0; });
}

/**
 * 全管理者にメールを送信
 */
function sendToAdmins(subject, body) {
  var emails = getAdminEmails();
  var sentCount = 0;
  for (var i = 0; i < emails.length; i++) {
    try {
      GmailApp.sendEmail(emails[i], subject, body, { name: SENDER_NAME });
      sentCount++;
    } catch (e) {
      Logger.log("管理者メール送信失敗: " + emails[i] + " - " + e.message);
    }
  }
  return sentCount;
}

/**
 * シートを名前で取得（存在しなければ null）
 */
function getSheet(ss, name) {
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (sheets[i].getName() === name) {
      return sheets[i];
    }
  }
  return null;
}

/**
 * 医員マスタを {id: {name, email}} のマップで取得
 */
function getDoctorMap(ss) {
  var sheet = getSheet(ss, "医員マスタ");
  if (!sheet) return {};

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return {};

  var headers = data[0];
  var colId = headers.indexOf("id");
  var colName = headers.indexOf("name");
  var colEmail = headers.indexOf("email");
  var colActive = headers.indexOf("is_active");

  var map = {};
  for (var i = 1; i < data.length; i++) {
    var row = data[i];
    if (String(row[colActive]) === "0") continue;
    map[String(row[colId])] = {
      name: String(row[colName]),
      email: String(row[colEmail] || "").trim()
    };
  }
  return map;
}

/**
 * 外勤先マスタを {id: name} のマップで取得
 */
function getClinicMap(ss) {
  var sheet = getSheet(ss, "外勤先マスタ");
  if (!sheet) return {};

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return {};

  var headers = data[0];
  var colId = headers.indexOf("id");
  var colName = headers.indexOf("name");

  var map = {};
  for (var i = 1; i < data.length; i++) {
    map[String(data[i][colId])] = String(data[i][colName]);
  }
  return map;
}

// ---- スプレッドシート作成 ----

/**
 * 平日セクション用スプレッドシートを作成し、サービスアカウントに編集権限を付与
 * @param {string} title - スプレッドシート名
 * @param {string} shareWith - 共有先メールアドレス（サービスアカウント）
 * @returns {{id: string, url: string}}
 */
function createSpreadsheetForSection(title, shareWith) {
  var ss = SpreadsheetApp.create(title);
  if (shareWith) {
    ss.addEditor(shareWith);
  }
  return { id: ss.getId(), url: ss.getUrl() };
}

// ---- テスト用 ----

/**
 * テスト用：次の土曜日のリマインダーを送信（実際にメール送信します）
 */
function testSendReminder() {
  sendFridayReminder();
}
