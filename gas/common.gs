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
// 環境依存の設定値（MASTER_SPREADSHEET_ID / ADMIN_EMAIL / TEST_MODE）は
// config.gs に分離している。config.gs は git 管理外（実値はローカルのみ）だが
// clasp では push されるため本番の設定は保持される。
// 新規セットアップ時は config.gs.example をコピーして config.gs を作成すること。

// 送信者として表示する名前
var SENDER_NAME = "外勤調整システム";

// テスト送信時の注記
var TEST_NOTICE = "【テスト送信】このメールはテストです。記載の外勤先は実際のものではありません。実際の外勤先は別途ご確認ください。\n\n";

// ---- スプレッドシート取得（リクエスト内キャッシュ） ----

var _masterSS = null;
var _operationalSS = null;

/**
 * 運用データ用スプレッドシート（このスクリプトが設置されているスプレッドシート）
 */
function getOperationalSpreadsheet() {
  if (!_operationalSS) {
    _operationalSS = SpreadsheetApp.getActiveSpreadsheet();
  }
  return _operationalSS;
}

/**
 * マスタ用スプレッドシート（IDで別スプレッドシートを開く）
 */
function getMasterSpreadsheet() {
  if (!MASTER_SPREADSHEET_ID) {
    throw new Error("MASTER_SPREADSHEET_ID が未設定です。マスタ用スプレッドシートのIDを設定してください。");
  }
  if (!_masterSS) {
    _masterSS = SpreadsheetApp.openById(MASTER_SPREADSHEET_ID);
  }
  return _masterSS;
}

// ---- Web App エンドポイント ----

/**
 * Streamlitアプリからのリクエストを受信し、アクションに応じて各ハンドラに振り分け
 */
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);

    // LINE Webhook（events 配列があれば LINE からのリクエスト）
    if (data.events !== undefined) {
      // LINE Webhook の処理（非同期で実行、即座に200を返す）
      try {
        var events = data.events || [];
        for (var i = 0; i < events.length; i++) {
          var event = events[i];
          if (event.type === "message" && event.message.type === "text") {
            handleTextMessage(event);
          } else if (event.type === "follow") {
            handleFollow(event);
          }
        }
      } catch (lineErr) {
        Logger.log("LINE Webhook error: " + lineErr.message);
      }
      return ContentService.createTextOutput(
        JSON.stringify({"status": "ok"})
      ).setMimeType(ContentService.MimeType.JSON);
    }

    // 土曜関連
    if (data.action === "schedule_confirmed") {
      sendConfirmationEmails(data.year_month, data.plan_name, data.schedule_image_file_id);
    } else if (data.action === "preference_confirmed_to_doctor") {
      sendDoctorConfirmation(data.year_month, data.doctor_name, data.doctor_email, data.date_summary, data.free_text);
    } else if (data.action === "all_preferences_complete") {
      sendAllCompleteNotification(data.year_month, data.doctor_count);
    } else if (data.action === "saturday_shift_change_executed") {
      sendSaturdayShiftChangeNotification(data);

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
    } else if (data.action === "shift_change_executed") {
      sendShiftChangeNotification(data);
    } else if (data.action === "weekday_readjust_preference_request") {
      sendWeekdayReadjustPreferenceRequest(data);
    } else if (data.action === "weekday_schedule_readjusted") {
      sendWeekdayScheduleReadjusted(data);

    // スケジュール確定解除（カレンダーイベント削除）
    } else if (data.action === "schedule_unconfirmed") {
      clearSaturdayCalendar(data.year_month);

    // カレンダー再同期
    } else if (data.action === "calendar_resync_doctor") {
      resyncCalendarForDoctor(data);
    } else if (data.action === "calendar_resync_all") {
      resyncCalendarForAllDoctors();
    } else if (data.action === "weekday_calendar_resync") {
      // 検体設定変更時などにカレンダーのみ再同期（メール通知なし）
      var ssMaster = getMasterSpreadsheet();
      syncWeekdayCalendar(data, ssMaster);

    // カレンダー共有更新
    } else if (data.action === "calendar_update_sharing") {
      handleCalendarSharingUpdate(data);

    // ---- 開発テスト用 ----
    // 土曜メールテスト
    } else if (data.action === "test_sat_schedule_confirmed") {
      var testResult = testSatScheduleConfirmed(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_sat_preference_confirmed") {
      var testResult = testSatPreferenceConfirmed(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_sat_all_complete") {
      var testResult = testSatAllComplete(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_sat_deadline_reminder") {
      var testResult = testSatDeadlineReminder(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_sat_deadline_overdue") {
      var testResult = testSatDeadlineOverdue(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_sat_friday_reminder") {
      var testResult = testSatFridayReminder(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_password_reset") {
      var testResult = testPasswordReset(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // 平日メールテスト
    } else if (data.action === "test_wd_schedule_confirmed") {
      var testResult = testWdScheduleConfirmed(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_preference_confirmed") {
      var testResult = testWdPreferenceConfirmed(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_all_complete") {
      var testResult = testWdAllComplete(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_deadline_reminder") {
      var testResult = testWdDeadlineReminder(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_deadline_overdue") {
      var testResult = testWdDeadlineOverdue(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_day_before_reminder") {
      var testResult = testWdDayBeforeReminder(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_shift_swap") {
      var testResult = testWdShiftSwap(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_shift_change") {
      var testResult = testWdShiftChange(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_readjust_request") {
      var testResult = testWdReadjustRequest(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_wd_readjusted") {
      var testResult = testWdReadjusted(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // 統合メールテスト
    } else if (data.action === "test_weekly_integrated") {
      var testResult = testWeeklyIntegrated(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // 土曜LINEテスト
    } else if (data.action === "test_line_sat_schedule_confirmed") {
      var testResult = testLineSatScheduleConfirmed(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_line_sat_deadline_reminder") {
      var testResult = testLineSatDeadlineReminder(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_line_sat_friday_reminder") {
      var testResult = testLineSatFridayReminder(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // 平日LINEテスト
    } else if (data.action === "test_line_wd_schedule_confirmed") {
      var testResult = testLineWdScheduleConfirmed(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);
    } else if (data.action === "test_line_wd_day_before_reminder") {
      var testResult = testLineWdDayBeforeReminder(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // 統合LINEテスト
    } else if (data.action === "test_line_weekly_integrated") {
      var testResult = testLineWeeklyIntegrated(data);
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // LINE テキストPush（汎用）
    } else if (data.action === "test_line_push_text") {
      pushText(data.line_user_id, data.text);
      return ContentService.createTextOutput(
        JSON.stringify({ status: "ok" })
      ).setMimeType(ContentService.MimeType.JSON);

    // LINE Push残数取得
    } else if (data.action === "get_line_quota") {
      var testResult = getLineQuota();
      return ContentService.createTextOutput(
        JSON.stringify(testResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // Drive 画像アップロード（サービスアカウントにはストレージ割り当てがないためGAS経由）
    } else if (data.action === "upload_drive_image") {
      var uploadResult = uploadDriveImage(data);
      return ContentService.createTextOutput(
        JSON.stringify(uploadResult)
      ).setMimeType(ContentService.MimeType.JSON);

    // LINE LIFF連携完了（Streamlitから呼ばれる）
    } else if (data.action === "line_link_complete") {
      switchToLinkedRichMenu(data.line_user_id);
      if (data.doctor_name) {
        pushText(data.line_user_id,
          data.doctor_name + " さん、アカウント連携が完了しました！\n" +
          "メニューの「希望入力」から、希望入力を開始できます。"
        );
      }
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
  return ss.getSheetByName(name);
}

/**
 * 医員マスタを {id: {name, email}} のマップで取得
 */
// includeInactive=true のとき is_active=0 の医員も含める（平日カレンダー同期用。
// 平日は土曜の無効化に依存しないため、名前解決・個人カレンダーで必要）。
// 既定(false)では従来どおり無効医員を除外（通知等が無効医員に届かないように）。
function getDoctorMap(ss, includeInactive) {
  var sheet = getSheet(ss, "医員マスタ");
  if (!sheet) return {};

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return {};

  var headers = data[0];
  var colId = headers.indexOf("id");
  var colName = headers.indexOf("name");
  var colEmail = headers.indexOf("email");
  var colActive = headers.indexOf("is_active");
  var colAccount = headers.indexOf("account");
  var colNotifyEmail = headers.indexOf("notify_email");
  var colNotifyCal = headers.indexOf("notify_calendar");
  var colPersonalCal = headers.indexOf("personal_calendar_id");
  var colLineId = headers.indexOf("line_user_id");

  var map = {};
  for (var i = 1; i < data.length; i++) {
    var row = data[i];
    if (!includeInactive && String(row[colActive]) === "0") continue;
    map[String(row[colId])] = {
      name: String(row[colName]),
      email: String(row[colEmail] || "").trim(),
      account: colAccount >= 0 ? String(row[colAccount] || "") : "",
      notify_email: colNotifyEmail >= 0 ? String(row[colNotifyEmail]) !== "0" : true,
      notify_calendar: colNotifyCal >= 0 ? String(row[colNotifyCal]) === "1" : false,
      personal_calendar_id: colPersonalCal >= 0 ? String(row[colPersonalCal] || "").trim() : "",
      line_user_id: colLineId >= 0 ? String(row[colLineId] || "").trim() : ""
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

// ---- 平日セクション別スプレッドシート取得 ----

var _weekdaySectionSSCache = {};

/**
 * 平日外勤設定シートから対象セクションのスプレッドシートを取得（リクエスト内キャッシュ）
 * @param {Spreadsheet} ssMaster マスタスプレッドシート
 * @param {string} section セクション名
 * @return {Spreadsheet|null}
 */
function getWeekdaySectionSpreadsheet(ssMaster, section) {
  if (_weekdaySectionSSCache[section] !== undefined) {
    return _weekdaySectionSSCache[section];
  }
  var sheet = getSheet(ssMaster, "平日外勤設定");
  if (!sheet) {
    _weekdaySectionSSCache[section] = null;
    return null;
  }
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) {
    _weekdaySectionSSCache[section] = null;
    return null;
  }
  var headers = data[0];
  var colSection = headers.indexOf("section");
  var colKey = headers.indexOf("spreadsheet_key");
  if (colSection === -1 || colKey === -1) {
    _weekdaySectionSSCache[section] = null;
    return null;
  }
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][colSection]) === section) {
      var key = String(data[i][colKey] || "");
      if (!key) {
        _weekdaySectionSSCache[section] = null;
        return null;
      }
      try {
        var result = SpreadsheetApp.openById(key);
        _weekdaySectionSSCache[section] = result;
        return result;
      } catch (e) {
        Logger.log("セクションSS取得失敗: " + section + " - " + e.message);
        _weekdaySectionSSCache[section] = null;
        return null;
      }
    }
  }
  _weekdaySectionSSCache[section] = null;
  return null;
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

// ---- Drive 画像アップロード ----

/**
 * base64 エンコードされた PNG 画像を Drive にアップロードし file_id を返す。
 * 同名ファイルが既にあれば削除してから再アップロードする。
 * folder_id 指定時はそのフォルダに保存し、直近 keep 枚以外を削除する。
 *
 * @param {Object} data - {image_base64, filename, folder_id?, keep?}
 * @returns {{status: string, file_id?: string, message?: string}}
 */
function uploadDriveImage(data) {
  try {
    var blob = Utilities.newBlob(
      Utilities.base64Decode(data.image_base64),
      "image/png",
      data.filename
    );

    var folderId = data.folder_id || null;
    var keep = data.keep || 3;

    // 保存先フォルダ
    var folder = folderId
      ? DriveApp.getFolderById(folderId)
      : DriveApp.getRootFolder();

    // 同名ファイルを削除
    var existing = folder.getFilesByName(data.filename);
    while (existing.hasNext()) {
      existing.next().setTrashed(true);
    }

    // アップロード
    var file = folder.createFile(blob);
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);

    // 古いファイルを整理（直近 keep 枚のみ残す）
    if (folderId) {
      var allFiles = folder.getFilesByType("image/png");
      var fileList = [];
      while (allFiles.hasNext()) {
        var f = allFiles.next();
        if (!f.isTrashed()) {
          fileList.push({ file: f, date: f.getDateCreated() });
        }
      }
      if (fileList.length > keep) {
        fileList.sort(function(a, b) { return b.date - a.date; });
        for (var i = keep; i < fileList.length; i++) {
          fileList[i].file.setTrashed(true);
        }
      }
    }

    Logger.log("Drive アップロード完了: " + data.filename + " (id=" + file.getId() + ")");
    return { status: "ok", file_id: file.getId() };
  } catch (e) {
    Logger.log("Drive アップロード失敗: " + e.message);
    return { status: "error", message: e.message };
  }
}

// ---- doGet ----

/**
 * GAS Web App の GET リクエストハンドラ（現在未使用）
 */
function doGet(e) {
  return ContentService.createTextOutput("OK");
}

