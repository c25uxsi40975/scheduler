/**
 * 外勤リマインダー・通知スクリプト（Google Apps Script）
 *
 * 機能:
 *   1. 毎週金曜18時に翌日（土曜）の外勤リマインダーをメール送信
 *   2. スケジュール確定時にWeb App経由で全医員に通知メール送信
 *      - HTMLメール + スケジュール表画像（Charts.newTableChart）のインライン埋め込み
 *   3. スケジュール確定時にGoogleカレンダーへイベント自動登録（共有カレンダー）
 *
 * セットアップ:
 *   1. 運用データ用スプレッドシートで「拡張機能 > Apps Script」を開く
 *   2. このファイルの内容を貼り付ける
 *   3. MASTER_SPREADSHEET_ID を設定（必須）
 *   4. CALENDAR_ID を設定（カレンダー連携を使用する場合）
 *   5. sendFridayReminder をトリガーに登録（毎週金曜 18:00-19:00）
 *   6. Web Appとしてデプロイ（確定通知用）
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

// Googleカレンダー連携: 共有カレンダーID（空欄でカレンダー連携無効）
// 例: "abc123@group.calendar.google.com"
var CALENDAR_ID = "";

// カレンダーイベントのデフォルト時間帯（time_slot未設定の外勤先用）
var DEFAULT_START_HOUR = 9;
var DEFAULT_END_HOUR = 13;

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

// ---- Web App エンドポイント（確定通知） ----

/**
 * Streamlitアプリからの確定通知リクエストを受信
 */
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    if (data.action === "schedule_confirmed") {
      sendConfirmationEmails(data.year_month, data.plan_name);
    } else if (data.action === "preference_confirmed_to_doctor") {
      sendDoctorConfirmation(data.year_month, data.doctor_name, data.doctor_email, data.date_summary, data.free_text);
    } else if (data.action === "all_preferences_complete") {
      sendAllCompleteNotification(data.year_month, data.doctor_count);
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

/**
 * 確定通知メールを全医員に送信（HTML + スケジュール表画像埋め込み）
 */
function sendConfirmationEmails(yearMonth, planName) {
  var ssOp = getOperationalSpreadsheet();
  var ssMaster = getMasterSpreadsheet();

  // 確定スケジュールを取得
  var schedSheet = getSheet(ssOp, "スケジュール_" + yearMonth);
  if (!schedSheet) {
    Logger.log("スケジュールシートが見つかりません: スケジュール_" + yearMonth);
    return;
  }

  var allAssignments = getConfirmedAssignments(schedSheet, null);
  if (allAssignments.length === 0) {
    Logger.log("確定済みの割り当てがありません");
    return;
  }

  var doctors = getDoctorMap(ssMaster);
  var clinicDetails = getClinicDetailMap(ssMaster);

  // Googleカレンダーにイベントを登録
  if (CALENDAR_ID) {
    try {
      createCalendarEvents(yearMonth, allAssignments, doctors, clinicDetails);
    } catch (e) {
      Logger.log("カレンダー登録でエラー: " + e.message);
    }
  }

  // 医員ごとの割り当てをグループ化
  var doctorAssignments = {};
  for (var i = 0; i < allAssignments.length; i++) {
    var a = allAssignments[i];
    var did = String(a.doctor_id);
    if (!doctorAssignments[did]) doctorAssignments[did] = [];
    doctorAssignments[did].push(a);
  }

  // 各医員にメール送信
  var sentCount = 0;
  for (var doctorId in doctors) {
    var doctor = doctors[doctorId];
    if (!doctor.email) continue;

    var assignments = doctorAssignments[doctorId] || [];
    var subject = (TEST_MODE ? "【テスト】" : "") + "【外勤スケジュール確定】" + yearMonth;

    // プレーンテキスト版（フォールバック）
    var plainBody = (TEST_MODE ? TEST_NOTICE : "")
      + doctor.name + " 先生\n\n"
      + yearMonth + " の外勤スケジュールが確定しました。\n\n";

    if (assignments.length > 0) {
      plainBody += "━━━━━━━━━━━━━━━━━━━━\n";
      assignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
      for (var j = 0; j < assignments.length; j++) {
        var dateObj = new Date(assignments[j].date + "T00:00:00+09:00");
        var dateStr = Utilities.formatDate(dateObj, "Asia/Tokyo", "M/d(E)");
        var clinicName = clinicDetails[String(assignments[j].clinic_id)]
          ? clinicDetails[String(assignments[j].clinic_id)].name : "（不明）";
        plainBody += "  " + dateStr + "：" + clinicName + "\n";
      }
      plainBody += "━━━━━━━━━━━━━━━━━━━━\n";
    } else {
      plainBody += "今月の外勤割り当てはありません。\n";
    }

    plainBody += "\n詳細はWebアプリのスケジュール確認タブからご確認ください。\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";

    // HTML版メール
    var htmlBody = "";
    if (TEST_MODE) {
      htmlBody += '<p style="color:red;font-weight:bold;border:2px solid red;padding:8px;">'
        + TEST_NOTICE.replace(/\n/g, "<br>") + "</p>";
    }
    htmlBody += "<p>" + doctor.name + " 先生</p>"
      + "<p>" + yearMonth + " の外勤スケジュールが確定しました。</p>";

    var emailOptions = { name: SENDER_NAME };

    if (assignments.length > 0) {
      // スケジュール表の画像を生成して埋め込み
      try {
        var tableBlob = buildDoctorScheduleImage(assignments, clinicDetails);
        htmlBody += '<p><img src="cid:scheduleTable" style="max-width:100%;" /></p>';
        emailOptions.inlineImages = { scheduleTable: tableBlob };
      } catch (imgErr) {
        Logger.log("テーブル画像生成失敗（テキスト代替）: " + imgErr.message);
        htmlBody += buildScheduleHtmlTable(assignments, clinicDetails);
      }
    } else {
      htmlBody += "<p>今月の外勤割り当てはありません。</p>";
    }

    htmlBody += "<p>詳細はWebアプリのスケジュール確認タブからご確認ください。</p>";
    if (CALENDAR_ID) {
      htmlBody += '<p style="color:#666;">※Googleカレンダーにも予定を登録しました。</p>';
    }
    htmlBody += '<p style="color:gray;font-size:small;">※このメールは外勤調整システムから自動送信されています。</p>';

    emailOptions.htmlBody = htmlBody;

    try {
      GmailApp.sendEmail(doctor.email, subject, plainBody, emailOptions);
      Logger.log("確定通知 送信成功: " + doctor.name + " (" + doctor.email + ")");
      sentCount++;
    } catch (e) {
      Logger.log("確定通知 送信失敗: " + doctor.name + " - " + e.message);
    }
  }

  Logger.log("確定通知完了: " + sentCount + " 件送信");
}

// ---- 希望入力通知 ----

/**
 * 医員本人へ希望入力の確認メールを送信
 */
function sendDoctorConfirmation(yearMonth, doctorName, doctorEmail, dateSummary, freeText) {
  if (!doctorEmail) {
    Logger.log("医員メールアドレスなし: " + doctorName);
    return;
  }

  var subject = (TEST_MODE ? "【テスト】" : "") + "【希望入力確認】" + yearMonth;
  var body = (TEST_MODE ? TEST_NOTICE : "")
    + doctorName + " 先生\n\n"
    + yearMonth + " の希望を保存しました。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + dateSummary + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n";

  if (freeText) {
    body += "\n備考: " + freeText + "\n";
  }

  body += "\n内容を変更する場合はWebアプリから再度入力してください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  try {
    GmailApp.sendEmail(doctorEmail, subject, body, { name: SENDER_NAME });
    Logger.log("医員確認メール 送信成功: " + doctorName + " (" + doctorEmail + ")");
  } catch (e) {
    Logger.log("医員確認メール 送信失敗: " + doctorName + " - " + e.message);
  }
}

/**
 * 全医員の希望入力が完了した際に管理者へ通知
 */
function sendAllCompleteNotification(yearMonth, doctorCount) {
  if (getAdminEmails().length === 0) {
    Logger.log("ADMIN_EMAIL が未設定のため全員完了通知をスキップ");
    return;
  }

  var subject = (TEST_MODE ? "【テスト】" : "") + "【全員入力完了】" + yearMonth;
  var body = (TEST_MODE ? TEST_NOTICE : "")
    + yearMonth + " の希望入力が全員完了しました。\n\n"
    + "入力済み: " + doctorCount + " 名\n\n"
    + "管理画面の「希望状況一覧」タブから内容を確認し、\n"
    + "スケジュール生成に進んでください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  var sent = sendToAdmins(subject, body);
  Logger.log("全員完了通知 送信完了: " + sent + " 件");
}

// ---- 毎週金曜リマインダー ----

/**
 * 毎週金曜に実行：翌日（土曜）の外勤リマインダーを送信
 */
function sendFridayReminder() {
  var tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);

  // 翌日が土曜日でなければ何もしない（安全装置）
  if (tomorrow.getDay() !== 6) {
    Logger.log("翌日は土曜日ではないため、スキップします");
    return;
  }

  var tomorrowStr = Utilities.formatDate(tomorrow, "Asia/Tokyo", "yyyy-MM-dd");
  var yearMonth = Utilities.formatDate(tomorrow, "Asia/Tokyo", "yyyy-MM");
  var displayDate = Utilities.formatDate(tomorrow, "Asia/Tokyo", "M/d(E)");

  Logger.log("対象日: " + tomorrowStr);

  var ssOp = getOperationalSpreadsheet();
  var ssMaster = getMasterSpreadsheet();

  // 確定スケジュールを取得
  var schedSheet = getSheet(ssOp, "スケジュール_" + yearMonth);
  if (!schedSheet) {
    Logger.log("スケジュールシートが見つかりません: スケジュール_" + yearMonth);
    return;
  }

  var confirmedAssignments = getConfirmedAssignments(schedSheet, tomorrowStr);
  if (confirmedAssignments.length === 0) {
    Logger.log("翌日の外勤割り当てはありません");
    return;
  }

  // マスタデータを取得
  var doctors = getDoctorMap(ssMaster);
  var clinics = getClinicMap(ssMaster);

  // 医員ごとにメール送信
  var sentCount = 0;
  for (var i = 0; i < confirmedAssignments.length; i++) {
    var a = confirmedAssignments[i];
    var doctor = doctors[a.doctor_id];
    if (!doctor) {
      Logger.log("医員ID " + a.doctor_id + " が見つかりません");
      continue;
    }
    if (!doctor.email) {
      Logger.log(doctor.name + ": メールアドレス未設定のためスキップ");
      continue;
    }

    var clinicName = clinics[a.clinic_id] || "（不明）";

    var subject = (TEST_MODE ? "【テスト】" : "") + "【外勤リマインダー】明日 " + displayDate + " " + clinicName;
    var body = (TEST_MODE ? TEST_NOTICE : "")
      + doctor.name + " 先生\n\n"
      + "明日の外勤予定をお知らせします。\n\n"
      + "━━━━━━━━━━━━━━━━━━━━\n"
      + "  日付：" + displayDate + "（土）\n"
      + "  外勤先：" + clinicName + "\n"
      + "━━━━━━━━━━━━━━━━━━━━\n\n"
      + "よろしくお願いいたします。\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";

    try {
      GmailApp.sendEmail(doctor.email, subject, body, { name: SENDER_NAME });
      Logger.log("送信成功: " + doctor.name + " (" + doctor.email + ")");
      sentCount++;
    } catch (e) {
      Logger.log("送信失敗: " + doctor.name + " (" + doctor.email + ") - " + e.message);
    }
  }

  Logger.log("送信完了: " + sentCount + "/" + confirmedAssignments.length + " 件");
}

// ---- 入力期限チェック ----

/**
 * 毎日実行：
 *   - 期限日当日 → 全医員に「本日が入力期限です」と通知
 *   - 期限日翌日 → 管理者に未入力者リストを通知
 * トリガー設定: 日ベースのタイマー（毎日 9:00-10:00 推奨）
 */
function checkDeadline() {
  var ssMaster = getMasterSpreadsheet();

  // 設定シートから input_deadline と open_month を取得
  var settingsSheet = getSheet(ssMaster, "設定");
  if (!settingsSheet) {
    Logger.log("設定シートが見つかりません");
    return;
  }

  var settingsData = settingsSheet.getDataRange().getValues();
  var settings = {};
  for (var i = 1; i < settingsData.length; i++) {
    settings[String(settingsData[i][0])] = String(settingsData[i][1]);
  }

  var deadline = settings["input_deadline"];
  var openMonth = settings["open_month"];
  if (!deadline || !openMonth) {
    Logger.log("input_deadline または open_month が未設定");
    return;
  }

  var today = Utilities.formatDate(new Date(), "Asia/Tokyo", "yyyy-MM-dd");

  // 期限日の翌日を計算
  var deadlineDate = new Date(deadline + "T00:00:00+09:00");
  var nextDay = new Date(deadlineDate);
  nextDay.setDate(nextDay.getDate() + 1);
  var nextDayStr = Utilities.formatDate(nextDay, "Asia/Tokyo", "yyyy-MM-dd");

  var isDeadlineDay = (today === deadline);
  var isDayAfter = (today === nextDayStr);

  if (!isDeadlineDay && !isDayAfter) {
    Logger.log("今日(" + today + ")は期限日(" + deadline + ")でも翌日(" + nextDayStr + ")でもないためスキップ");
    return;
  }

  // 有効な医員リストを取得
  var doctors = getDoctorMap(ssMaster);
  var doctorIds = Object.keys(doctors);
  if (doctorIds.length === 0) {
    Logger.log("有効な医員がいません");
    return;
  }

  // 希望シートから入力済み医員を取得
  var ssOp = getOperationalSpreadsheet();
  var prefSheet = getSheet(ssOp, "希望_" + openMonth);
  var submittedIds = {};
  if (prefSheet) {
    var prefData = prefSheet.getDataRange().getValues();
    if (prefData.length > 1) {
      var colDoctorId = prefData[0].indexOf("doctor_id");
      for (var j = 1; j < prefData.length; j++) {
        submittedIds[String(prefData[j][colDoctorId])] = true;
      }
    }
  }

  if (isDeadlineDay) {
    // ---- 期限日当日: 全医員に期限リマインダー ----
    var sentCount = 0;
    for (var k = 0; k < doctorIds.length; k++) {
      var doc = doctors[doctorIds[k]];
      if (!doc.email) continue;

      var submitted = !!submittedIds[doctorIds[k]];
      var subject = (TEST_MODE ? "【テスト】" : "") + "【入力期限】本日が " + openMonth + " の希望入力期限です";
      var body = (TEST_MODE ? TEST_NOTICE : "")
        + doc.name + " 先生\n\n"
        + openMonth + " の希望入力の期限は本日（" + deadline + "）です。\n\n";

      if (submitted) {
        body += "入力状況: 入力済み ✓\n\n"
          + "内容を変更する場合はWebアプリから再度入力してください。\n";
      } else {
        body += "入力状況: 未入力\n\n"
          + "Webアプリから希望を入力してください。\n"
          + "※期限後も入力は可能ですが、お早めにお願いいたします。\n";
      }

      body += "\n※このメールは外勤調整システムから自動送信されています。";

      try {
        GmailApp.sendEmail(doc.email, subject, body, { name: SENDER_NAME });
        Logger.log("期限リマインダー 送信成功: " + doc.name + (submitted ? " (入力済み)" : " (未入力)"));
        sentCount++;
      } catch (e) {
        Logger.log("期限リマインダー 送信失敗: " + doc.name + " - " + e.message);
      }
    }
    Logger.log("期限リマインダー完了: " + sentCount + " 件送信");

  } else if (isDayAfter) {
    // ---- 期限日翌日: 管理者に未入力者リストを通知 ----
    if (getAdminEmails().length === 0) {
      Logger.log("ADMIN_EMAIL が未設定のため未入力者通知をスキップ");
      return;
    }

    var missing = [];
    for (var m = 0; m < doctorIds.length; m++) {
      if (!submittedIds[doctorIds[m]]) {
        missing.push(doctors[doctorIds[m]].name);
      }
    }

    if (missing.length === 0) {
      Logger.log("全員入力済み。未入力者通知は不要");
      return;
    }

    var subjectAdmin = (TEST_MODE ? "【テスト】" : "") + "【期限超過】" + openMonth + " - " + missing.length + "名 未入力";
    var bodyAdmin = (TEST_MODE ? TEST_NOTICE : "")
      + openMonth + " の希望入力の期限（" + deadline + "）を過ぎました。\n\n"
      + "以下の " + missing.length + " 名が未入力です:\n\n";

    for (var n = 0; n < missing.length; n++) {
      bodyAdmin += "  ・" + missing[n] + " 先生\n";
    }

    bodyAdmin += "\n入力済み: " + (doctorIds.length - missing.length) + "/" + doctorIds.length + " 名\n\n"
      + "※医員は期限後も入力可能です。必要に応じて個別にご連絡ください。\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";

    var sent = sendToAdmins(subjectAdmin, bodyAdmin);
    Logger.log("未入力者通知 送信完了: " + sent + " 件 (" + missing.length + " 名未入力)");
  }
}

// ---- ヘルパー関数 ----

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
 * 確定スケジュールから割り当てを取得
 * dateStr が null の場合は全日付を返す
 */
function getConfirmedAssignments(schedSheet, dateStr) {
  var data = schedSheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var headers = data[0];
  var colConfirmed = headers.indexOf("is_confirmed");
  var colAssignments = headers.indexOf("assignments");

  var result = [];
  for (var i = 1; i < data.length; i++) {
    var row = data[i];
    if (String(row[colConfirmed]) !== "1") continue;

    var assignments;
    try {
      assignments = JSON.parse(row[colAssignments]);
    } catch (e) {
      continue;
    }

    for (var j = 0; j < assignments.length; j++) {
      if (dateStr === null || assignments[j].date === dateStr) {
        result.push(assignments[j]);
      }
    }
  }
  return result;
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

// ---- スケジュール表画像生成 ----

/**
 * 医員個別のスケジュール表を Charts.newTableChart で画像化
 * @param {Array} assignments - 医員の割り当て配列 [{date, clinic_id, doctor_id}, ...]
 * @param {Object} clinicDetailMap - {id: {name, time_slot, ...}}
 * @return {Blob} PNG画像のBlob
 */
function buildDoctorScheduleImage(assignments, clinicDetailMap) {
  var table = Charts.newDataTable()
    .addColumn(Charts.ColumnType.STRING, "日付")
    .addColumn(Charts.ColumnType.STRING, "外勤先");

  assignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });

  for (var i = 0; i < assignments.length; i++) {
    var a = assignments[i];
    var dateObj = new Date(a.date + "T00:00:00+09:00");
    var dateStr = Utilities.formatDate(dateObj, "Asia/Tokyo", "M/d(E)");
    var clinic = clinicDetailMap[String(a.clinic_id)];
    var clinicName = clinic ? clinic.name : "（不明）";
    table.addRow([dateStr, clinicName]);
  }

  var height = Math.max(120, 50 + assignments.length * 35);
  var chart = Charts.newTableChart()
    .setDataTable(table.build())
    .setDimensions(450, height)
    .setOption("alternatingRowStyle", true)
    .build();

  return chart.getBlob().setName("schedule.png");
}

/**
 * スケジュール表のHTMLテーブル（画像生成失敗時のフォールバック）
 */
function buildScheduleHtmlTable(assignments, clinicDetailMap) {
  assignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });

  var html = '<table style="border-collapse:collapse;margin:12px 0;">'
    + '<tr style="background:#4472C4;color:white;">'
    + '<th style="padding:8px 16px;border:1px solid #ddd;">日付</th>'
    + '<th style="padding:8px 16px;border:1px solid #ddd;">外勤先</th></tr>';

  for (var i = 0; i < assignments.length; i++) {
    var a = assignments[i];
    var dateObj = new Date(a.date + "T00:00:00+09:00");
    var dateStr = Utilities.formatDate(dateObj, "Asia/Tokyo", "M/d(E)");
    var clinic = clinicDetailMap[String(a.clinic_id)];
    var clinicName = clinic ? clinic.name : "（不明）";
    var bg = (i % 2 === 0) ? "#f8f9fa" : "#ffffff";
    html += '<tr style="background:' + bg + ';">'
      + '<td style="padding:6px 16px;border:1px solid #ddd;">' + dateStr + '</td>'
      + '<td style="padding:6px 16px;border:1px solid #ddd;">' + clinicName + '</td></tr>';
  }

  html += "</table>";
  return html;
}

// ---- Googleカレンダー連携 ----

/**
 * 外勤スケジュールをGoogleカレンダーに登録
 * CALENDAR_ID に指定された共有カレンダーにイベントを作成する
 */
function createCalendarEvents(yearMonth, allAssignments, doctors, clinicDetailMap) {
  if (!CALENDAR_ID) {
    Logger.log("CALENDAR_ID 未設定のためカレンダー登録をスキップ");
    return;
  }

  var calendar = CalendarApp.getCalendarById(CALENDAR_ID);
  if (!calendar) {
    Logger.log("カレンダーが見つかりません: " + CALENDAR_ID);
    return;
  }

  // 対象月の既存イベントを削除
  deleteCalendarEventsForMonth(calendar, yearMonth);

  var createdCount = 0;
  for (var i = 0; i < allAssignments.length; i++) {
    var a = allAssignments[i];
    var doctor = doctors[String(a.doctor_id)];
    var clinic = clinicDetailMap[String(a.clinic_id)];
    if (!doctor || !clinic) continue;

    // time_slot に基づいて開始・終了時刻を決定
    var startHour = DEFAULT_START_HOUR;
    var endHour = DEFAULT_END_HOUR;
    if (clinic.time_slot === "PM") {
      startHour = 13;
      endHour = 17;
    } else if (clinic.time_slot === "AM") {
      startHour = 9;
      endHour = 13;
    }

    var startTime = new Date(a.date + "T" + padZero(startHour) + ":00:00+09:00");
    var endTime = new Date(a.date + "T" + padZero(endHour) + ":00:00+09:00");

    var title = "【外勤】" + doctor.name + " → " + clinic.name;
    var description = "外勤調整システムにより自動登録\n"
      + "医員: " + doctor.name + "\n"
      + "外勤先: " + clinic.name;
    if (clinic.location) {
      description += "\n場所: " + clinic.location;
    }

    try {
      var event = calendar.createEvent(title, startTime, endTime, {
        description: description,
        location: clinic.location || ""
      });
      Logger.log("カレンダー登録: " + title + " (" + a.date + ")");
      createdCount++;
    } catch (e) {
      Logger.log("カレンダー登録失敗: " + title + " - " + e.message);
    }
  }

  Logger.log("カレンダー登録完了: " + createdCount + "/" + allAssignments.length + " 件");
}

/**
 * 対象月の【外勤】イベントを削除（再確定時の重複防止）
 */
function deleteCalendarEventsForMonth(calendar, yearMonth) {
  var parts = yearMonth.split("-");
  var year = parseInt(parts[0], 10);
  var month = parseInt(parts[1], 10);

  var startDate = new Date(year, month - 1, 1, 0, 0, 0);
  var endDate = new Date(year, month, 0, 23, 59, 59);

  var events = calendar.getEvents(startDate, endDate, { search: "【外勤】" });
  var deletedCount = 0;
  for (var i = 0; i < events.length; i++) {
    if (events[i].getTitle().indexOf("【外勤】") === 0) {
      events[i].deleteEvent();
      deletedCount++;
    }
  }
  if (deletedCount > 0) {
    Logger.log("既存カレンダーイベント削除: " + deletedCount + " 件 (" + yearMonth + ")");
  }
}

/**
 * 数値を2桁ゼロ埋め文字列に変換
 */
function padZero(num) {
  return num < 10 ? "0" + num : String(num);
}

/**
 * 外勤先マスタを {id: {name, time_slot, work_hours, location}} の詳細マップで取得
 */
function getClinicDetailMap(ss) {
  var sheet = getSheet(ss, "外勤先マスタ");
  if (!sheet) return {};

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return {};

  var headers = data[0];
  var colId = headers.indexOf("id");
  var colName = headers.indexOf("name");
  var colTimeSlot = headers.indexOf("time_slot");
  var colWorkHours = headers.indexOf("work_hours");
  var colLocation = headers.indexOf("location");

  var map = {};
  for (var i = 1; i < data.length; i++) {
    map[String(data[i][colId])] = {
      name: String(data[i][colName]),
      time_slot: colTimeSlot >= 0 ? String(data[i][colTimeSlot] || "") : "",
      work_hours: colWorkHours >= 0 ? String(data[i][colWorkHours] || "") : "",
      location: colLocation >= 0 ? String(data[i][colLocation] || "") : ""
    };
  }
  return map;
}

// ---- テスト・手動実行用 ----

/**
 * テスト用：次の土曜日のリマインダーを送信（実際にメール送信します）
 */
function testSendReminder() {
  sendFridayReminder();
}

/**
 * テスト用：翌日のスケジュール内容をログ出力（メール送信しない）
 */
function dryRunReminder() {
  var tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  var tomorrowStr = Utilities.formatDate(tomorrow, "Asia/Tokyo", "yyyy-MM-dd");
  var yearMonth = Utilities.formatDate(tomorrow, "Asia/Tokyo", "yyyy-MM");

  Logger.log("=== ドライラン ===");
  Logger.log("対象日: " + tomorrowStr);

  var ssOp = getOperationalSpreadsheet();
  var ssMaster = getMasterSpreadsheet();

  var schedSheet = getSheet(ssOp, "スケジュール_" + yearMonth);
  if (!schedSheet) {
    Logger.log("スケジュールシートなし");
    return;
  }

  var assignments = getConfirmedAssignments(schedSheet, tomorrowStr);
  Logger.log("割り当て件数: " + assignments.length);

  var doctors = getDoctorMap(ssMaster);
  var clinics = getClinicMap(ssMaster);

  for (var i = 0; i < assignments.length; i++) {
    var a = assignments[i];
    var doc = doctors[a.doctor_id] || { name: "不明", email: "" };
    var cli = clinics[a.clinic_id] || "不明";
    Logger.log("  " + doc.name + " → " + cli + " (email: " + (doc.email || "未設定") + ")");
  }
}

/**
 * テスト用：スケジュール表画像の生成テスト
 * ログにBlobサイズを出力し、Google Driveに画像を保存する
 */
function testTableImage() {
  var testAssignments = [
    { date: "2025-04-05", clinic_id: "1", doctor_id: "1" },
    { date: "2025-04-12", clinic_id: "2", doctor_id: "1" },
    { date: "2025-04-19", clinic_id: "1", doctor_id: "1" },
    { date: "2025-04-26", clinic_id: "3", doctor_id: "1" }
  ];

  var clinicDetailMap = {
    "1": { name: "Aクリニック", time_slot: "AM", work_hours: "", location: "" },
    "2": { name: "B病院", time_slot: "PM", work_hours: "", location: "" },
    "3": { name: "C医院", time_slot: "", work_hours: "", location: "" }
  };

  var blob = buildDoctorScheduleImage(testAssignments, clinicDetailMap);
  Logger.log("画像サイズ: " + blob.getBytes().length + " bytes");
  Logger.log("MIME: " + blob.getContentType());

  // Google Driveに保存して確認
  var file = DriveApp.createFile(blob);
  Logger.log("テスト画像URL: " + file.getUrl());
}

/**
 * テスト用：カレンダーイベント作成テスト（CALENDAR_ID 設定が必要）
 */
function testCalendarEvents() {
  if (!CALENDAR_ID) {
    Logger.log("CALENDAR_ID が未設定です。テストを実行するには設定してください。");
    return;
  }

  var calendar = CalendarApp.getCalendarById(CALENDAR_ID);
  if (!calendar) {
    Logger.log("カレンダーが見つかりません: " + CALENDAR_ID);
    return;
  }

  Logger.log("カレンダー名: " + calendar.getName());

  // テスト用イベントを1件作成
  var tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  var dateStr = Utilities.formatDate(tomorrow, "Asia/Tokyo", "yyyy-MM-dd");

  var start = new Date(dateStr + "T09:00:00+09:00");
  var end = new Date(dateStr + "T13:00:00+09:00");

  var event = calendar.createEvent("【外勤】テスト → テストクリニック", start, end, {
    description: "テストイベント（削除可）"
  });

  Logger.log("テストイベント作成完了: " + event.getId());
  Logger.log("日付: " + dateStr + " 09:00-13:00");
}
