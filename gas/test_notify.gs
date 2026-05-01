/**
 * 開発テスト用通知ハンドラ
 *
 * Python（Streamlit）からダミーデータ＋送信先をJSON payloadで受け取り、
 * 指定された1つのメールアドレス / LINE User ID にのみ送信する。
 * 既存の通知と同じフォーマットで【開発テスト】プレフィックスを付与。
 * 送信結果をJSONレスポンスで返す。
 */

var DEV_TEST_PREFIX = "【開発テスト】";

// ---- 土曜メールテスト ----

/**
 * テスト: 土曜スケジュール確定通知
 */
function testSatScheduleConfirmed(data) {
  var email = data.test_email;
  var yearMonth = data.year_month || "";
  var assignments = data.assignments || [];
  var clinics = data.clinics || [];
  var doctorName = data.doctor_name || "太郎";

  // クリニックIDからname引き
  var clinicMap = {};
  for (var i = 0; i < clinics.length; i++) {
    clinicMap[clinics[i].id] = clinics[i].name;
  }

  var subject = DEV_TEST_PREFIX + "【外勤スケジュール確定】" + yearMonth;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + yearMonth + " の外勤スケジュールが確定しました。\n\n";

  var myAssignments = assignments.filter(function(a) { return a.doctor_name === doctorName; });
  if (myAssignments.length > 0) {
    body += "━━━━━━━━━━━━━━━━━━━━\n";
    myAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var j = 0; j < myAssignments.length; j++) {
      var clinicName = clinicMap[myAssignments[j].clinic_id] || myAssignments[j].clinic_name || "不明";
      body += "  " + formatTestDate(myAssignments[j].date) + "：" + clinicName + "\n";
    }
    body += "━━━━━━━━━━━━━━━━━━━━\n";
  } else {
    body += "今月の外勤割り当てはありません。\n";
  }

  body += "\n詳細はWebアプリのスケジュール確認タブからご確認ください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 希望入力確認メール
 */
function testSatPreferenceConfirmed(data) {
  var email = data.test_email;
  var yearMonth = data.year_month || "";
  var doctorName = data.doctor_name || "太郎";
  var dateSummary = data.date_summary || "";
  var freeText = data.free_text || "";

  var subject = DEV_TEST_PREFIX + "【希望入力確認】" + yearMonth;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
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

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 全員入力完了通知
 */
function testSatAllComplete(data) {
  var email = data.test_email;
  var yearMonth = data.year_month || "";
  var doctorCount = data.doctor_count || 0;

  var subject = DEV_TEST_PREFIX + "【全員入力完了】" + yearMonth;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + yearMonth + " の希望入力が全員完了しました。\n\n"
    + "入力済み: " + doctorCount + " 名\n\n"
    + "管理画面の「希望状況一覧」タブから内容を確認し、\n"
    + "スケジュール生成に進んでください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 入力期限リマインダー
 */
function testSatDeadlineReminder(data) {
  var email = data.test_email;
  var yearMonth = data.year_month || "";
  var deadline = data.deadline || "";
  var submitted = data.submitted || false;
  var doctorName = data.doctor_name || "太郎";

  var subject = DEV_TEST_PREFIX + "【入力期限】本日が " + yearMonth + " の希望入力期限です";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + yearMonth + " の希望入力の期限は本日（" + deadline + "）です。\n\n";

  if (submitted) {
    body += "入力状況: 入力済み ✓\n\n"
      + "内容を変更する場合はWebアプリから再度入力してください。\n";
  } else {
    body += "入力状況: 未入力\n\n"
      + "Webアプリから希望を入力してください。\n"
      + "※期限後も入力は可能ですが、お早めにお願いいたします。\n";
  }

  body += "\n※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 期限超過通知（管理者向け）
 */
function testSatDeadlineOverdue(data) {
  var email = data.test_email;
  var yearMonth = data.year_month || "";
  var missingNames = data.missing_names || [];
  var totalCount = data.total_count || 0;

  var subject = DEV_TEST_PREFIX + "【期限超過】" + yearMonth + " - " + missingNames.length + "名 未入力";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + yearMonth + " の希望入力の期限を過ぎました。\n\n"
    + "以下の " + missingNames.length + " 名が未入力です:\n\n";

  for (var i = 0; i < missingNames.length; i++) {
    body += "  ・" + missingNames[i] + " 先生\n";
  }

  body += "\n入力済み: " + (totalCount - missingNames.length) + "/" + totalCount + " 名\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 金曜リマインダー
 */
function testSatFridayReminder(data) {
  var email = data.test_email;
  var dateStr = data.date || "";
  var clinicName = data.clinic_name || "";
  var doctorName = data.doctor_name || "太郎";
  var displayDate = formatTestDate(dateStr);

  var subject = DEV_TEST_PREFIX + "【外勤リマインダー】明日 " + displayDate + " " + clinicName;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + "明日の外勤予定をお知らせします。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  日付：" + displayDate + "\n"
    + "  外勤先：" + clinicName + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "よろしくお願いいたします。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: パスワードリセットコード
 */
function testPasswordReset(data) {
  var email = data.test_email;
  var accountName = data.account_name || "test_user";
  var resetCode = data.reset_code || "000000";

  var subject = DEV_TEST_PREFIX + "【外勤調整システム】パスワードリセットコード";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + accountName + " 様\n\n"
    + "パスワードリセットが要求されました。\n"
    + "以下のリセットコードをアプリの画面に入力してください。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  リセットコード: " + resetCode + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "※ このコードは15分間有効です。\n"
    + "※ 心当たりがない場合はこのメールを無視してください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

// ---- 平日メールテスト ----

/**
 * テスト: 平日スケジュール確定通知
 */
function testWdScheduleConfirmed(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";
  var yearMonths = data.year_months || [];
  var assignments = data.assignments || [];
  var doctorName = data.doctor_name || "太郎";
  var periodLabel = yearMonths.length === 1 ? yearMonths[0] : yearMonths[0] + "〜" + yearMonths[yearMonths.length - 1];

  var myAssignments = assignments.filter(function(a) { return a.doctor_name === doctorName; });

  var subject = DEV_TEST_PREFIX + "【平日外勤確定】" + clinicName + " " + periodLabel;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + clinicName + " の " + periodLabel + " の外勤スケジュールが確定しました。\n\n";

  if (myAssignments.length > 0) {
    body += "━━━━━━━━━━━━━━━━━━━━\n";
    myAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var j = 0; j < myAssignments.length; j++) {
      body += "  " + formatTestDate(myAssignments[j].date) + "：" + (myAssignments[j].clinic_name || "") + "\n";
    }
    body += "━━━━━━━━━━━━━━━━━━━━\n";
  } else {
    body += "この期間の割り当てはありません。\n";
  }

  body += "\n詳細はWebアプリからご確認ください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 平日希望入力確認
 */
function testWdPreferenceConfirmed(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";
  var doctorName = data.doctor_name || "太郎";
  var dateSummary = data.date_summary || "";
  var freeText = data.free_text || "";

  var subject = DEV_TEST_PREFIX + "【平日希望入力確認】" + clinicName;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + clinicName + " の希望を保存しました。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + dateSummary + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n";

  if (freeText) {
    body += "\n備考: " + freeText + "\n";
  }

  body += "\n内容を変更する場合はWebアプリから再度入力してください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 平日全員入力完了
 */
function testWdAllComplete(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";
  var doctorCount = data.doctor_count || 0;

  var subject = DEV_TEST_PREFIX + "【全員入力完了】" + clinicName;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + clinicName + " の希望入力が全員完了しました。\n\n"
    + "入力済み: " + doctorCount + " 名\n\n"
    + "管理画面の「希望状況一覧」タブから内容を確認し、\n"
    + "スケジュール生成に進んでください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 平日入力期限リマインダー
 */
function testWdDeadlineReminder(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";
  var deadline = data.deadline || "";
  var submitted = data.submitted || false;
  var doctorName = data.doctor_name || "太郎";

  var subject = DEV_TEST_PREFIX + "【入力期限】" + clinicName + " 本日が希望入力期限です";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + clinicName + " の希望入力の期限は本日（" + deadline + "）です。\n\n";

  if (submitted) {
    body += "入力状況: 入力済み ✓\n\n"
      + "内容を変更する場合はWebアプリから再度入力してください。\n";
  } else {
    body += "入力状況: 未入力\n\n"
      + "Webアプリから希望を入力してください。\n"
      + "※期限後も入力は可能ですが、お早めにお願いいたします。\n";
  }

  body += "\n※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 平日期限超過通知
 */
function testWdDeadlineOverdue(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";
  var missingNames = data.missing_names || [];
  var totalCount = data.total_count || 0;

  var subject = DEV_TEST_PREFIX + "【期限超過】" + clinicName + " " + missingNames.length + "名 未入力";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + clinicName + " の希望入力の期限を過ぎました。\n\n"
    + "以下の " + missingNames.length + " 名が未入力です:\n\n";

  for (var i = 0; i < missingNames.length; i++) {
    body += "  ・" + missingNames[i] + " 先生\n";
  }

  body += "\n入力済み: " + (totalCount - missingNames.length) + "/" + totalCount + " 名\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 平日前日リマインダー
 */
function testWdDayBeforeReminder(data) {
  var email = data.test_email;
  var dateStr = data.date || "";
  var clinicName = data.clinic_name || "";
  var doctorName = data.doctor_name || "太郎";
  var slotName = data.slot_name || "";
  var displayDate = formatTestDate(dateStr);

  var subject = DEV_TEST_PREFIX + "【外勤リマインダー】明日 " + displayDate + " " + clinicName;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + "明日の外勤予定をお知らせします。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  日付：" + displayDate + "\n"
    + "  外勤先：" + clinicName + "\n"
    + "  スロット：" + slotName + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "よろしくお願いいたします。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: シフト交換通知
 */
function testWdShiftSwap(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";

  var subject = DEV_TEST_PREFIX + "【シフト交換】" + clinicName;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + "シフト交換が実行されました。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  依頼者: " + (data.requester_name || "") + "\n"
    + "  依頼者のシフト: " + (data.requester_shift || "") + "\n"
    + "  交換相手: " + (data.target_name || "") + "\n"
    + "  交換相手のシフト: " + (data.target_shift || "") + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "詳細はWebアプリからご確認ください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: シフト変更通知
 */
function testWdShiftChange(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";

  var subject = DEV_TEST_PREFIX + "【シフト変更】" + clinicName;
  var actorLine = data.actor_name ? ("  操作者: " + data.actor_name + "\n") : "";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + "シフト変更が実行されました。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  外勤先: " + clinicName + "\n"
    + "  対象日: " + (data.date || "") + "\n"
    + "  スロット: " + (data.slot_name || "") + "\n"
    + "  変更元: " + (data.original_doctor_name || "") + "\n"
    + "  変更先: " + (data.new_doctor_name || "") + "\n"
    + actorLine
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "詳細はWebアプリからご確認ください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 再調整希望入力依頼
 */
function testWdReadjustRequest(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";
  var deadline = data.deadline || "";
  var dateCount = data.target_date_count || 0;
  var mode = data.mode || "fill";
  var doctorName = data.doctor_name || "太郎";
  var modeLabel = mode === "fill" ? "補填" : "再構成";

  var subject = DEV_TEST_PREFIX + "【希望入力依頼】" + clinicName + " スケジュール再調整（" + modeLabel + "）";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + clinicName + " の外勤スケジュールが再調整（" + modeLabel + "）されます。\n"
    + "以下の日程について、NG日・避けたい日の希望を入力してください。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  対象日数: " + dateCount + " 日\n"
    + "  入力期限: " + deadline + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "※ Webアプリから希望を入力してください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

/**
 * テスト: 再調整完了通知
 */
function testWdReadjusted(data) {
  var email = data.test_email;
  var clinicName = data.clinic_name || "";
  var assignments = data.assignments || [];
  var mode = data.mode || "fill";
  var period = data.period || "";
  var doctorName = data.doctor_name || "太郎";
  var modeLabel = mode === "fill" ? "補填" : "再構成";

  var myAssignments = assignments.filter(function(a) { return a.doctor_name === doctorName; });

  var subject = DEV_TEST_PREFIX + "【平日外勤再調整】" + clinicName + "（" + modeLabel + "）";
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + clinicName + " の外勤スケジュールが再調整（" + modeLabel + "）されました。\n"
    + "対象期間: " + period + "\n\n";

  if (myAssignments.length > 0) {
    body += "━━━ 更新後のスケジュール ━━━\n";
    myAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var j = 0; j < myAssignments.length; j++) {
      body += "  " + formatTestDate(myAssignments[j].date) + "：" + (myAssignments[j].clinic_name || "") + "\n";
    }
    body += "━━━━━━━━━━━━━━━━━━━━\n";
  } else {
    body += "この期間の割り当てはありません。\n";
  }

  body += "\n詳細はWebアプリからご確認ください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

// ---- 統合メールテスト ----

/**
 * テスト: 週間統合スケジュール通知（土曜＋平日）
 */
function testWeeklyIntegrated(data) {
  var email = data.test_email;
  var satAssignments = data.sat_assignments || [];
  var wdAssignments = data.wd_assignments || [];
  var satClinics = data.sat_clinics || [];
  var wdClinicName = data.wd_clinic_name || "";
  var yearMonths = data.year_months || [];
  var doctorName = data.doctor_name || "太郎";

  var periodLabel = yearMonths.length === 1 ? yearMonths[0] : yearMonths[0] + "〜" + yearMonths[yearMonths.length - 1];

  // クリニックIDからname引き
  var clinicMap = {};
  for (var i = 0; i < satClinics.length; i++) {
    clinicMap[satClinics[i].id] = satClinics[i].name;
  }

  var mySatAssignments = satAssignments.filter(function(a) { return a.doctor_name === doctorName; });
  var myWdAssignments = wdAssignments.filter(function(a) { return a.doctor_name === doctorName; });

  var subject = DEV_TEST_PREFIX + "【週間スケジュール】" + periodLabel;
  var body = DEV_TEST_PREFIX + "このメールは開発テストです。\n\n"
    + doctorName + " 先生\n\n"
    + periodLabel + " の外勤スケジュール（統合）をお知らせします。\n\n";

  // 土曜
  body += "━━━ 土曜外勤 ━━━\n";
  if (mySatAssignments.length > 0) {
    mySatAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var s = 0; s < mySatAssignments.length; s++) {
      var cn = clinicMap[mySatAssignments[s].clinic_id] || mySatAssignments[s].clinic_name || "不明";
      body += "  " + formatTestDate(mySatAssignments[s].date) + "：" + cn + "\n";
    }
  } else {
    body += "  割り当てなし\n";
  }

  // 平日
  body += "\n━━━ 平日外勤（" + wdClinicName + "） ━━━\n";
  if (myWdAssignments.length > 0) {
    myWdAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var w = 0; w < myWdAssignments.length; w++) {
      body += "  " + formatTestDate(myWdAssignments[w].date) + "：" + (myWdAssignments[w].clinic_name || wdClinicName) + "\n";
    }
  } else {
    body += "  割り当てなし\n";
  }

  body += "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "詳細はWebアプリからご確認ください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  return sendTestEmail(email, subject, body);
}

// ---- 土曜LINEテスト ----

/**
 * テスト: LINE 土曜スケジュール確定通知
 */
function testLineSatScheduleConfirmed(data) {
  var userId = data.line_user_id;
  var yearMonth = data.year_month || "";
  var assignments = data.assignments || [];
  var clinics = data.clinics || [];
  var doctorName = data.doctor_name || "太郎";

  var clinicMap = {};
  for (var i = 0; i < clinics.length; i++) {
    clinicMap[clinics[i].id] = clinics[i].name;
  }

  var monthLabel = yearMonth.replace("-", "年") + "月";
  var myAssignments = assignments.filter(function(a) { return a.doctor_name === doctorName; });

  var lines = [DEV_TEST_PREFIX + "【スケジュール確定】" + monthLabel + "\n"];
  if (myAssignments.length > 0) {
    myAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var j = 0; j < myAssignments.length; j++) {
      var cn = clinicMap[myAssignments[j].clinic_id] || myAssignments[j].clinic_name || "不明";
      lines.push("  " + formatTestDate(myAssignments[j].date) + " : " + cn);
    }
  } else {
    lines.push("  割り当てなし");
  }

  // 画像URLがあればテキスト＋画像の2通で送信（本番と同じ流れ）
  var scheduleImageUrl = data.schedule_image_url || null;
  if (scheduleImageUrl) {
    return sendTestLinePushWithImage(userId, lines.join("\n"), scheduleImageUrl);
  }
  return sendTestLinePush(userId, lines.join("\n"));
}

/**
 * テスト: LINE 希望入力リマインダー
 */
function testLineSatDeadlineReminder(data) {
  var userId = data.line_user_id;
  var yearMonth = data.year_month || "";
  var submitted = data.submitted || false;
  var monthLabel = yearMonth.replace("-", "年") + "月";

  var status = submitted ? "入力済み ✓" : "未入力";
  var text = DEV_TEST_PREFIX + "【希望入力リマインダー】\n"
    + monthLabel + " の希望入力状況: " + status + "\n";

  if (!submitted) {
    text += "\nメニューの「希望入力」から入力をお願いします。";
  }

  return sendTestLinePush(userId, text);
}

/**
 * テスト: LINE 金曜リマインダー
 */
function testLineSatFridayReminder(data) {
  var userId = data.line_user_id;
  var dateStr = data.date || "";
  var clinicName = data.clinic_name || "";

  var text = DEV_TEST_PREFIX + "【外勤リマインダー】明日の予定\n\n"
    + "  " + formatTestDate(dateStr) + " : " + clinicName;

  return sendTestLinePush(userId, text);
}

// ---- 平日LINEテスト ----

/**
 * テスト: LINE 平日スケジュール確定通知
 */
function testLineWdScheduleConfirmed(data) {
  var userId = data.line_user_id;
  var clinicName = data.clinic_name || "";
  var assignments = data.assignments || [];
  var yearMonths = data.year_months || [];
  var doctorName = data.doctor_name || "太郎";
  var periodLabel = yearMonths.length === 1 ? yearMonths[0] : yearMonths[0] + "〜" + yearMonths[yearMonths.length - 1];

  var myAssignments = assignments.filter(function(a) { return a.doctor_name === doctorName; });

  var lines = [DEV_TEST_PREFIX + "【平日外勤確定】" + clinicName + " " + periodLabel + "\n"];
  if (myAssignments.length > 0) {
    myAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var j = 0; j < myAssignments.length; j++) {
      lines.push("  " + formatTestDate(myAssignments[j].date) + " : " + (myAssignments[j].clinic_name || clinicName));
    }
  } else {
    lines.push("  割り当てなし");
  }

  return sendTestLinePush(userId, lines.join("\n"));
}

/**
 * テスト: LINE 平日前日リマインダー
 */
function testLineWdDayBeforeReminder(data) {
  var userId = data.line_user_id;
  var dateStr = data.date || "";
  var clinicName = data.clinic_name || "";
  var slotName = data.slot_name || "";

  var text = DEV_TEST_PREFIX + "【外勤リマインダー】明日の予定\n\n"
    + "  " + formatTestDate(dateStr) + " : " + clinicName;
  if (slotName) {
    text += "（" + slotName + "）";
  }

  return sendTestLinePush(userId, text);
}

// ---- 統合LINEテスト ----

/**
 * テスト: LINE 週間統合スケジュール
 */
function testLineWeeklyIntegrated(data) {
  var userId = data.line_user_id;
  var satAssignments = data.sat_assignments || [];
  var wdAssignments = data.wd_assignments || [];
  var satClinics = data.sat_clinics || [];
  var wdClinicName = data.wd_clinic_name || "";
  var yearMonths = data.year_months || [];
  var doctorName = data.doctor_name || "太郎";

  var clinicMap = {};
  for (var i = 0; i < satClinics.length; i++) {
    clinicMap[satClinics[i].id] = satClinics[i].name;
  }

  var periodLabel = yearMonths.length === 1 ? yearMonths[0] : yearMonths[0] + "〜" + yearMonths[yearMonths.length - 1];

  var mySatAssignments = satAssignments.filter(function(a) { return a.doctor_name === doctorName; });
  var myWdAssignments = wdAssignments.filter(function(a) { return a.doctor_name === doctorName; });

  var lines = [DEV_TEST_PREFIX + "【週間スケジュール】" + periodLabel + "\n"];

  lines.push("■ 土曜外勤");
  if (mySatAssignments.length > 0) {
    mySatAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var s = 0; s < mySatAssignments.length; s++) {
      var cn = clinicMap[mySatAssignments[s].clinic_id] || mySatAssignments[s].clinic_name || "不明";
      lines.push("  " + formatTestDate(mySatAssignments[s].date) + " : " + cn);
    }
  } else {
    lines.push("  割り当てなし");
  }

  lines.push("\n■ 平日外勤（" + wdClinicName + "）");
  if (myWdAssignments.length > 0) {
    myWdAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
    for (var w = 0; w < myWdAssignments.length; w++) {
      lines.push("  " + formatTestDate(myWdAssignments[w].date) + " : " + (myWdAssignments[w].clinic_name || wdClinicName));
    }
  } else {
    lines.push("  割り当てなし");
  }

  return sendTestLinePush(userId, lines.join("\n"));
}

// ---- LINE Push残数取得 ----

/**
 * LINE Push API の当月消費数を取得
 */
function getLineQuota() {
  var token = getLineChannelAccessToken();
  if (!token) {
    return { status: "error", message: "LINE_CHANNEL_ACCESS_TOKEN が未設定" };
  }

  try {
    var resp = UrlFetchApp.fetch("https://api.line.me/v2/bot/message/quota/consumption", {
      "method": "get",
      "headers": {
        "Authorization": "Bearer " + token
      }
    });
    var result = JSON.parse(resp.getContentText());
    return { status: "ok", totalUsage: result.totalUsage || 0 };
  } catch (e) {
    return { status: "error", message: e.message };
  }
}

// ---- 共通ヘルパー ----

/**
 * テスト用メール送信（1通のみ）
 */
function sendTestEmail(email, subject, body) {
  try {
    GmailApp.sendEmail(email, subject, body, { name: SENDER_NAME + "（テスト）" });
    Logger.log("テストメール送信成功: " + email + " / " + subject);
    return { status: "ok", sent_to: email };
  } catch (e) {
    Logger.log("テストメール送信失敗: " + email + " - " + e.message);
    return { status: "error", message: e.message };
  }
}

/**
 * テスト用LINE Push送信（1通のみ）
 */
function sendTestLinePush(userId, text) {
  var token = getLineChannelAccessToken();
  if (!userId || !token) {
    return { status: "error", message: "LINE User ID またはアクセストークンが未設定" };
  }
  try {
    UrlFetchApp.fetch("https://api.line.me/v2/bot/message/push", {
      "method": "post",
      "headers": {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json"
      },
      "payload": JSON.stringify({
        "to": userId,
        "messages": [{"type": "text", "text": text}]
      })
    });
    Logger.log("テストLINE Push送信成功: " + userId);
    return { status: "ok", sent_to: userId };
  } catch (e) {
    Logger.log("テストLINE Push送信失敗: " + userId + " - " + e.message);
    return { status: "error", message: e.message };
  }
}

/**
 * テスト用LINE Push送信（テキスト＋画像の2メッセージ）
 */
function sendTestLinePushWithImage(userId, text, imageUrl) {
  var token = getLineChannelAccessToken();
  if (!userId || !token) {
    return { status: "error", message: "LINE User ID またはアクセストークンが未設定" };
  }
  try {
    UrlFetchApp.fetch("https://api.line.me/v2/bot/message/push", {
      "method": "post",
      "headers": {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json"
      },
      "payload": JSON.stringify({
        "to": userId,
        "messages": [
          {"type": "text", "text": text},
          {"type": "image", "originalContentUrl": imageUrl, "previewImageUrl": imageUrl}
        ]
      })
    });
    Logger.log("テストLINE Push(画像付き)送信成功: " + userId);
    return { status: "ok", sent_to: userId, image: true };
  } catch (e) {
    Logger.log("テストLINE Push(画像付き)送信失敗: " + userId + " - " + e.message);
    return { status: "error", message: e.message };
  }
}

/**
 * テスト用日付フォーマット (YYYY-MM-DD → M/d(曜))
 */
function formatTestDate(dateStr) {
  if (!dateStr) return "";
  try {
    var d = new Date(dateStr + "T00:00:00+09:00");
    return Utilities.formatDate(d, "Asia/Tokyo", "M/d(E)");
  } catch (e) {
    return dateStr;
  }
}
