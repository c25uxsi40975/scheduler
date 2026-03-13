/**
 * 外勤リマインダー・通知スクリプト — 土曜モジュール
 *
 * 土曜外勤に関する通知ハンドラ・トリガー関数を定義。
 * 共通関数は common.gs を参照。
 */

// ---- 土曜スケジュール確定通知 ----

/**
 * 確定通知メールを全医員に送信
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
  var clinics = getClinicMap(ssMaster);

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

    var body = (TEST_MODE ? TEST_NOTICE : "")
      + doctor.name + " 先生\n\n"
      + yearMonth + " の外勤スケジュールが確定しました。\n\n";

    if (assignments.length > 0) {
      body += "━━━━━━━━━━━━━━━━━━━━\n";
      assignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
      for (var j = 0; j < assignments.length; j++) {
        var dateObj = new Date(assignments[j].date + "T00:00:00+09:00");
        var dateStr = Utilities.formatDate(dateObj, "Asia/Tokyo", "M/d(E)");
        var clinicName = clinics[assignments[j].clinic_id] || "（不明）";
        body += "  " + dateStr + "：" + clinicName + "\n";
      }
      body += "━━━━━━━━━━━━━━━━━━━━\n";
    } else {
      body += "今月の外勤割り当てはありません。\n";
    }

    body += "\n詳細はWebアプリのスケジュール確認タブからご確認ください。\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";

    try {
      GmailApp.sendEmail(doctor.email, subject, body, { name: SENDER_NAME });
      Logger.log("確定通知 送信成功: " + doctor.name + " (" + doctor.email + ")");
      sentCount++;
    } catch (e) {
      Logger.log("確定通知 送信失敗: " + doctor.name + " - " + e.message);
    }
  }

  Logger.log("確定通知完了: " + sentCount + " 件送信");
}

// ---- 土曜希望入力通知 ----

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
 * トリガー設定: 毎週金曜 18:00-19:00
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

// ---- 土曜入力期限チェック ----

/**
 * 毎日実行：
 *   - 期限日当日 → 全医員に「本日が入力期限です」と通知
 *   - 期限日翌日 → 管理者に未入力者リストを通知
 * トリガー設定: 日ベースのタイマー（毎日 9:00-10:00）
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

// ---- 土曜ヘルパー関数 ----

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
