/**
 * 外勤リマインダー・通知スクリプト — 平日モジュール
 *
 * 平日外勤に関する通知ハンドラ・トリガー関数・ヘルパーを定義。
 * 共通関数は common.gs を参照。
 */

// ---- 平日外勤ヘルパー関数 ----

var _weekdayConfigsCache = null;

/**
 * 平日外勤設定を全件取得（リクエスト内キャッシュ）
 * @return {Array} [{section, clinic_name, days_of_week, assigned_doctors, subadmin_doctors, is_active}, ...]
 */
function getWeekdayConfigs(ssMaster) {
  if (_weekdayConfigsCache) return _weekdayConfigsCache;
  var sheet = getSheet(ssMaster, "平日外勤設定");
  if (!sheet) return [];

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var headers = data[0];
  var result = [];
  for (var i = 1; i < data.length; i++) {
    var row = data[i];
    var obj = {};
    for (var j = 0; j < headers.length; j++) {
      obj[headers[j]] = row[j];
    }
    // JSONフィールドのパース
    try { obj.days_of_week = JSON.parse(obj.days_of_week || "[]"); } catch(e) { obj.days_of_week = []; }
    try { obj.assigned_doctors = JSON.parse(obj.assigned_doctors || "[]"); } catch(e) { obj.assigned_doctors = []; }
    try { obj.subadmin_doctors = JSON.parse(obj.subadmin_doctors || "[]"); } catch(e) { obj.subadmin_doctors = []; }
    obj.is_active = String(obj.is_active) === "1";
    obj.specimen_enabled = String(obj.specimen_enabled) === "1";
    try { obj.specimen_doctors = JSON.parse(obj.specimen_doctors || "[]"); } catch(e) { obj.specimen_doctors = []; }
    try { obj.specimen_days = JSON.parse(obj.specimen_days || "[]"); } catch(e) { obj.specimen_days = []; }
    try { obj.specimen_priority = JSON.parse(obj.specimen_priority || "{}"); } catch(e) { obj.specimen_priority = {}; }
    result.push(obj);
  }
  _weekdayConfigsCache = result;
  return result;
}

var DOW_LABELS_JA = ["月", "火", "水", "木", "金", "土", "日"];

/**
 * 日付文字列からISO週キー {year, week} を返す
 */
function getISOWeekKey(dateStr) {
  var d = new Date(dateStr + "T00:00:00+09:00");
  var tmp = new Date(d.getTime());
  tmp.setDate(tmp.getDate() + 3 - ((tmp.getDay() + 6) % 7));
  var yearStart = new Date(tmp.getFullYear(), 0, 4);
  var weekNo = 1 + Math.round(((tmp - yearStart) / 86400000 - 3
    + ((yearStart.getDay() + 6) % 7)) / 7);
  return { year: tmp.getFullYear(), week: weekNo };
}

/**
 * JS曜日(0=Sun)をPython曜日(0=Mon)に変換
 */
function jsDowToPyDow(jsDow) {
  return (jsDow === 0) ? 6 : jsDow - 1;
}

/**
 * 検体確認担当者を週単位で判定
 *
 * 同一ISO週に勤務する検体対象メンバーを集め、優先順位でランク付け。
 * 同優先順位が複数いる場合は conflict=true。
 *
 * @param {Object} cfg - セクション設定
 * @param {string} dateStr - yyyy-MM-dd（対象曜日チェック用）
 * @param {Array} allAssignments - 月全体（または週を含む範囲）の割り当て配列
 * @param {Object} doctors - getDoctorMap() の結果
 * @returns {Object|null} {doctorId, doctorName, conflict, conflictDoctors}
 *          conflictDoctors の各要素に weekdays (Python曜日配列) を含む
 */
function getSpecimenAssignee(cfg, dateStr, allAssignments, doctors) {
  if (!cfg.specimen_enabled) return null;

  var dt = new Date(dateStr + "T00:00:00+09:00");
  var pyDow = jsDowToPyDow(dt.getDay());

  var specimenDays = cfg.specimen_days || [];
  var isSpecimenDay = false;
  for (var i = 0; i < specimenDays.length; i++) {
    if (specimenDays[i] === pyDow) { isSpecimenDay = true; break; }
  }
  if (!isSpecimenDay) return null;

  var specimenDoctors = {};
  for (var s = 0; s < (cfg.specimen_doctors || []).length; s++) {
    specimenDoctors[String(cfg.specimen_doctors[s])] = true;
  }

  // 同一ISO週の全日付から候補を収集（週単位判定）
  var targetWeek = getISOWeekKey(dateStr);
  var weekScheduled = {}; // {doctorId: {pyDow: true, ...}}
  for (var j = 0; j < allAssignments.length; j++) {
    var aDate = String(allAssignments[j].date);
    var aWeek = getISOWeekKey(aDate);
    if (aWeek.year === targetWeek.year && aWeek.week === targetWeek.week) {
      var aDt = new Date(aDate + "T00:00:00+09:00");
      var aPyDow = jsDowToPyDow(aDt.getDay());
      var aDid = String(allAssignments[j].doctor_id);
      if (!weekScheduled[aDid]) weekScheduled[aDid] = {};
      weekScheduled[aDid][aPyDow] = true;
    }
  }

  var priorityMap = cfg.specimen_priority || {};
  var hasPriority = Object.keys(priorityMap).length > 0;

  var candidates = [];
  var allIds = Object.keys(specimenDoctors);
  for (var k = 0; k < allIds.length; k++) {
    var did = allIds[k];
    if (weekScheduled[did] && doctors[did]) {
      var rank;
      if (hasPriority) {
        rank = (priorityMap[did] !== undefined) ? Number(priorityMap[did]) : 9999;
      } else {
        var acct = doctors[did].account || "9999";
        rank = acct.substring(0, 4);
      }
      var weekdays = Object.keys(weekScheduled[did]).map(Number).sort();
      candidates.push({ id: did, name: doctors[did].name, rank: rank, weekdays: weekdays });
    }
  }

  if (candidates.length === 0) return null;
  candidates.sort(function(a, b) { return (a.rank < b.rank) ? -1 : (a.rank > b.rank ? 1 : 0); });

  var topRank = candidates[0].rank;
  var sameRank = [];
  for (var m = 0; m < candidates.length; m++) {
    if (candidates[m].rank === topRank) sameRank.push(candidates[m]);
  }

  // この日に実際に勤務している同ランク医員を特定
  var todayAssignees = [];
  for (var ta = 0; ta < sameRank.length; ta++) {
    if (sameRank[ta].weekdays.indexOf(pyDow) !== -1) {
      todayAssignees.push(sameRank[ta]);
    }
  }
  if (todayAssignees.length === 0) return null;

  return {
    doctorId: todayAssignees[0].id,
    doctorName: todayAssignees[0].name,
    conflict: sameRank.length > 1,
    conflictDoctors: sameRank
  };
}

/**
 * 月のassignmentsから検体確認担当を日付ごとにまとめて返す
 *
 * @param {Object} cfg - セクション設定
 * @param {Array} assignments - 月全体の割り当て配列
 * @param {Object} doctors - getDoctorMap() の結果
 * @returns {Object} {dateStr: specimenResult, ...}
 */
function buildSpecimenByDate(cfg, assignments, doctors) {
  if (!cfg || !cfg.specimen_enabled) return {};

  var specimenDays = cfg.specimen_days || [];
  if (specimenDays.length === 0) return {};

  // 対象曜日の日付を収集
  var dateSet = {};
  for (var i = 0; i < assignments.length; i++) {
    dateSet[String(assignments[i].date)] = true;
  }

  var result = {};
  var uniqueDates = Object.keys(dateSet);
  for (var j = 0; j < uniqueDates.length; j++) {
    var ds = uniqueDates[j];
    var specResult = getSpecimenAssignee(cfg, ds, assignments, doctors);
    if (specResult) {
      result[ds] = specResult;
    }
  }
  return result;
}

/**
 * conflict時のメッセージ用: 「{曜日}の{名前}先生」形式の配列を生成
 * @param {Object} specResult - getSpecimenAssignee の戻り値
 * @param {string} excludeDoctorId - 除外する医員ID（自分自身）
 * @returns {Array} ["金曜の田中先生", ...]
 */
function buildConflictLabels(specResult, excludeDoctorId) {
  var labels = [];
  for (var i = 0; i < specResult.conflictDoctors.length; i++) {
    var cd = specResult.conflictDoctors[i];
    if (cd.id === String(excludeDoctorId)) continue;
    var weekdays = cd.weekdays || [];
    var dowStr = [];
    for (var w = 0; w < weekdays.length; w++) {
      dowStr.push((DOW_LABELS_JA[weekdays[w]] || "?") + "曜");
    }
    labels.push(dowStr.join("・") + "の" + cd.name + "先生");
  }
  return labels;
}

/**
 * 副管理者のメールアドレスを取得
 */
function getSubadminEmails(ssMaster, section) {
  var configs = getWeekdayConfigs(ssMaster);
  var cfg = null;
  for (var i = 0; i < configs.length; i++) {
    if (configs[i].section === section) { cfg = configs[i]; break; }
  }
  if (!cfg || !cfg.subadmin_doctors || cfg.subadmin_doctors.length === 0) return [];

  var doctors = getDoctorMap(ssMaster);
  var emails = [];
  for (var j = 0; j < cfg.subadmin_doctors.length; j++) {
    var doc = doctors[String(cfg.subadmin_doctors[j])];
    if (doc && doc.email) emails.push(doc.email);
  }
  return emails;
}

/**
 * 平日スケジュールから割り当てを取得
 * @param {Spreadsheet} ss セクション別スプレッドシート
 * @param {string} yearMonth 対象年月 (yyyy-MM)
 * @param {string} dateStr 日付でフィルタ（nullなら全件）
 * @return {Array} [{date, slot_id, slot_name, doctor_id, doctor_name, section}, ...]
 */
function getWeekdayAssignments(ss, yearMonth, dateStr) {
  var sheet = getSheet(ss, "平日スケジュール_" + yearMonth);
  if (!sheet) return [];

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var headers = data[0];
  var result = [];
  for (var i = 1; i < data.length; i++) {
    var row = {};
    for (var j = 0; j < headers.length; j++) {
      row[headers[j]] = data[i][j];
    }
    if (dateStr && String(row.date) !== dateStr) continue;
    result.push(row);
  }
  return result;
}

// ---- 平日外勤通知ハンドラ ----

/**
 * 平日スケジュール確定通知: セクション全メンバー＋副管理者にメール送信
 */
function sendWeekdayScheduleConfirmed(data) {
  var section = data.section;
  var clinicName = data.clinic_name;
  var yearMonths = data.year_months || [];

  var ssMaster = getMasterSpreadsheet();
  var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
  if (!ssSec) {
    Logger.log("平日確定通知: セクション '" + section + "' のスプレッドシートが未設定");
    return;
  }
  var doctors = getDoctorMap(ssMaster);

  // 全対象月の割り当てを取得
  var allAssignments = [];
  for (var m = 0; m < yearMonths.length; m++) {
    var assignments = getWeekdayAssignments(ssSec, yearMonths[m], null);
    allAssignments = allAssignments.concat(assignments);
  }

  // 医員ごとにグループ化
  var doctorAssignments = {};
  for (var i = 0; i < allAssignments.length; i++) {
    var a = allAssignments[i];
    var did = String(a.doctor_id);
    if (!doctorAssignments[did]) doctorAssignments[did] = [];
    doctorAssignments[did].push(a);
  }

  var periodLabel = yearMonths.length === 1 ? yearMonths[0] : yearMonths[0] + "〜" + yearMonths[yearMonths.length - 1];
  var sentCount = 0;

  // セクションのメンバーにメール送信
  var configs = getWeekdayConfigs(ssMaster);
  var cfg = null;
  for (var c = 0; c < configs.length; c++) {
    if (configs[c].section === section) { cfg = configs[c]; break; }
  }
  var memberIds = cfg ? cfg.assigned_doctors : [];

  for (var k = 0; k < memberIds.length; k++) {
    var doc = doctors[String(memberIds[k])];
    if (!doc || !doc.email || !doc.notify_email) continue;

    var assignments = doctorAssignments[String(memberIds[k])] || [];
    var subject = (TEST_MODE ? "【テスト】" : "") + "【平日外勤確定】" + clinicName + " " + periodLabel;
    var body = (TEST_MODE ? TEST_NOTICE : "")
      + doc.name + " 先生\n\n"
      + clinicName + " の " + periodLabel + " の外勤スケジュールが確定しました。\n\n";

    if (assignments.length > 0) {
      body += "━━━━━━━━━━━━━━━━━━━━\n";
      assignments.sort(function(a, b) { return String(a.date) > String(b.date) ? 1 : -1; });
      for (var j = 0; j < assignments.length; j++) {
        var dateObj = new Date(String(assignments[j].date) + "T00:00:00+09:00");
        var ds = Utilities.formatDate(dateObj, "Asia/Tokyo", "M/d(E)");
        body += "  " + ds + "：" + (assignments[j].slot_name || "") + "\n";
      }
      body += "━━━━━━━━━━━━━━━━━━━━\n";
    } else {
      body += "この期間の割り当てはありません。\n";
    }

    body += "\n詳細はWebアプリからご確認ください。\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";

    try {
      GmailApp.sendEmail(doc.email, subject, body, { name: SENDER_NAME });
      sentCount++;
    } catch (e) {
      Logger.log("平日確定通知 送信失敗: " + doc.name + " - " + e.message);
    }
  }

  // 副管理者にもサマリを送信
  var subadminEmails = getSubadminEmails(ssMaster, section);
  for (var s = 0; s < subadminEmails.length; s++) {
    var subSubject = (TEST_MODE ? "【テスト】" : "") + "【平日外勤確定完了】" + clinicName + " " + periodLabel;
    var subBody = (TEST_MODE ? TEST_NOTICE : "")
      + clinicName + " の " + periodLabel + " のスケジュールが確定されました。\n\n"
      + "送信済み: " + sentCount + " 名\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";
    try {
      GmailApp.sendEmail(subadminEmails[s], subSubject, subBody, { name: SENDER_NAME });
    } catch (e) {
      Logger.log("副管理者確定通知 送信失敗: " + subadminEmails[s] + " - " + e.message);
    }
  }

  Logger.log("平日確定通知完了: " + sentCount + " 件送信");

  // カレンダー同期（失敗してもメール通知には影響しない）
  try {
    syncWeekdayCalendar(data, ssMaster);
  } catch (e) {
    Logger.log("平日カレンダー同期エラー: " + e.message);
  }
}

/**
 * 平日希望入力確認メール: 医員本人に送信
 */
function sendWeekdayPreferenceConfirmed(data) {
  var doctorEmail = data.doctor_email;
  if (!doctorEmail) {
    Logger.log("平日希望確認: メールアドレスなし: " + data.doctor_name);
    return;
  }

  var subject = (TEST_MODE ? "【テスト】" : "") + "【平日希望入力確認】" + data.clinic_name;
  var body = (TEST_MODE ? TEST_NOTICE : "")
    + data.doctor_name + " 先生\n\n"
    + data.clinic_name + " の希望を保存しました。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + data.date_summary + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n";

  if (data.free_text) {
    body += "\n備考: " + data.free_text + "\n";
  }

  body += "\n内容を変更する場合はWebアプリから再度入力してください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  try {
    GmailApp.sendEmail(doctorEmail, subject, body, { name: SENDER_NAME });
    Logger.log("平日希望確認 送信成功: " + data.doctor_name);
  } catch (e) {
    Logger.log("平日希望確認 送信失敗: " + data.doctor_name + " - " + e.message);
  }
}

/**
 * 平日全員入力完了通知: 副管理者に送信
 */
function sendWeekdayAllPreferencesComplete(data) {
  var ssMaster = getMasterSpreadsheet();
  var emails = getSubadminEmails(ssMaster, data.section);
  if (emails.length === 0) {
    Logger.log("副管理者メールなし: " + data.section);
    return;
  }

  var subject = (TEST_MODE ? "【テスト】" : "") + "【全員入力完了】" + data.clinic_name;
  var body = (TEST_MODE ? TEST_NOTICE : "")
    + data.clinic_name + " の希望入力が全員完了しました。\n\n"
    + "入力済み: " + data.doctor_count + " 名\n\n"
    + "管理画面の「希望状況一覧」タブから内容を確認し、\n"
    + "スケジュール生成に進んでください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  for (var i = 0; i < emails.length; i++) {
    try {
      GmailApp.sendEmail(emails[i], subject, body, { name: SENDER_NAME });
      Logger.log("全員完了通知 送信成功: " + emails[i]);
    } catch (e) {
      Logger.log("全員完了通知 送信失敗: " + emails[i] + " - " + e.message);
    }
  }
}

/**
 * シフト交換通知: 交換相手＋副管理者に送信
 */
function sendShiftSwapNotification(data) {
  var ssMaster = getMasterSpreadsheet();
  var doctors = getDoctorMap(ssMaster);

  // 交換相手にメール
  var targetDoc = null;
  var allDocs = Object.keys(doctors);
  for (var i = 0; i < allDocs.length; i++) {
    if (doctors[allDocs[i]].name === data.target_name) {
      targetDoc = doctors[allDocs[i]];
      break;
    }
  }

  var clinicName = data.clinic_name || "";
  var subject = (TEST_MODE ? "【テスト】" : "") + "【シフト交換】" + clinicName;
  var body = (TEST_MODE ? TEST_NOTICE : "")
    + "シフト交換が実行されました。\n\n"
    + "━━━━━━━━━━━━━━━━━━━━\n"
    + "  依頼者: " + data.requester_name + "\n"
    + "  依頼者のシフト: " + data.requester_shift + "\n"
    + "  交換相手: " + data.target_name + "\n"
    + "  交換相手のシフト: " + data.target_shift + "\n"
    + "━━━━━━━━━━━━━━━━━━━━\n\n"
    + "詳細はWebアプリからご確認ください。\n\n"
    + "※このメールは外勤調整システムから自動送信されています。";

  if (targetDoc && targetDoc.email) {
    try {
      GmailApp.sendEmail(targetDoc.email, subject, body, { name: SENDER_NAME });
      Logger.log("交換通知 送信成功(相手): " + data.target_name);
    } catch (e) {
      Logger.log("交換通知 送信失敗(相手): " + data.target_name + " - " + e.message);
    }
  }

  // 副管理者にも通知
  var subadminEmails = getSubadminEmails(ssMaster, data.section);
  for (var j = 0; j < subadminEmails.length; j++) {
    try {
      GmailApp.sendEmail(subadminEmails[j], subject, body, { name: SENDER_NAME });
      Logger.log("交換通知 送信成功(副管理者): " + subadminEmails[j]);
    } catch (e) {
      Logger.log("交換通知 送信失敗(副管理者): " + subadminEmails[j] + " - " + e.message);
    }
  }

  // カレンダー同期（失敗してもメール通知には影響しない）
  try {
    syncShiftSwapCalendar(data, ssMaster);
  } catch (e) {
    Logger.log("シフト交換カレンダー同期エラー: " + e.message);
  }
}

// ---- 平日外勤トリガー関数 ----

/**
 * 毎日実行: 平日セクションの入力期限チェック
 * - 期限日当日: セクション全メンバーにリマインド
 * - 期限翌日: 副管理者に未入力者リスト通知
 * トリガー設定: 日ベースのタイマー（毎日 9:00-10:00）
 */
function checkWeekdayDeadlines() {
  var ssMaster = getMasterSpreadsheet();
  var configs = getWeekdayConfigs(ssMaster);
  var doctors = getDoctorMap(ssMaster);

  var today = Utilities.formatDate(new Date(), "Asia/Tokyo", "yyyy-MM-dd");

  // 設定シートから各セクションの期限を取得
  var settingsSheet = getSheet(ssMaster, "設定");
  if (!settingsSheet) return;
  var settingsData = settingsSheet.getDataRange().getValues();
  var settings = {};
  for (var s = 1; s < settingsData.length; s++) {
    settings[String(settingsData[s][0])] = String(settingsData[s][1]);
  }

  for (var i = 0; i < configs.length; i++) {
    var cfg = configs[i];
    if (!cfg.is_active) continue;

    var section = cfg.section;
    var deadline = settings["weekday_deadline_" + section];
    var isOpen = settings["weekday_open_" + section];
    if (!deadline || isOpen !== "1") continue;

    var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
    if (!ssSec) {
      Logger.log("平日期限チェック: セクション '" + section + "' のスプレッドシートが未設定");
      continue;
    }

    var deadlineDate = new Date(deadline + "T00:00:00+09:00");
    var nextDay = new Date(deadlineDate);
    nextDay.setDate(nextDay.getDate() + 1);
    var nextDayStr = Utilities.formatDate(nextDay, "Asia/Tokyo", "yyyy-MM-dd");

    var isDeadlineDay = (today === deadline);
    var isDayAfter = (today === nextDayStr);
    if (!isDeadlineDay && !isDayAfter) continue;

    // 入力済み医員を取得
    var prefSheet = getSheet(ssSec, "平日希望_" + section);
    var submittedIds = {};
    if (prefSheet) {
      var prefData = prefSheet.getDataRange().getValues();
      if (prefData.length > 1) {
        var colDoctorId = prefData[0].indexOf("doctor_id");
        for (var p = 1; p < prefData.length; p++) {
          submittedIds[String(prefData[p][colDoctorId])] = true;
        }
      }
    }

    var memberIds = cfg.assigned_doctors || [];

    if (isDeadlineDay) {
      // 期限日当日: 全メンバーにリマインド
      var sentCount = 0;
      for (var k = 0; k < memberIds.length; k++) {
        var doc = doctors[String(memberIds[k])];
        if (!doc || !doc.email || !doc.notify_email) continue;
        var submitted = !!submittedIds[String(memberIds[k])];

        var subject = (TEST_MODE ? "【テスト】" : "") + "【入力期限】" + cfg.clinic_name + " 本日が希望入力期限です";
        var body = (TEST_MODE ? TEST_NOTICE : "")
          + doc.name + " 先生\n\n"
          + cfg.clinic_name + " の希望入力の期限は本日（" + deadline + "）です。\n\n";

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
          sentCount++;
        } catch (e) {
          Logger.log("平日期限リマインダー 送信失敗: " + doc.name + " - " + e.message);
        }
      }
      Logger.log("平日期限リマインダー(" + section + ")完了: " + sentCount + " 件");

    } else if (isDayAfter) {
      // 期限翌日: 副管理者に未入力者リスト
      var missing = [];
      for (var m = 0; m < memberIds.length; m++) {
        if (!submittedIds[String(memberIds[m])]) {
          var mDoc = doctors[String(memberIds[m])];
          if (mDoc) missing.push(mDoc.name);
        }
      }

      if (missing.length === 0) {
        Logger.log("平日(" + section + "): 全員入力済み");
        continue;
      }

      var subadminEmails = getSubadminEmails(ssMaster, section);
      if (subadminEmails.length === 0) {
        Logger.log("平日(" + section + "): 副管理者メールなし");
        continue;
      }

      var subSubject = (TEST_MODE ? "【テスト】" : "") + "【期限超過】" + cfg.clinic_name + " " + missing.length + "名 未入力";
      var subBody = (TEST_MODE ? TEST_NOTICE : "")
        + cfg.clinic_name + " の希望入力の期限（" + deadline + "）を過ぎました。\n\n"
        + "以下の " + missing.length + " 名が未入力です:\n\n";

      for (var n = 0; n < missing.length; n++) {
        subBody += "  ・" + missing[n] + " 先生\n";
      }
      subBody += "\n入力済み: " + (memberIds.length - missing.length) + "/" + memberIds.length + " 名\n\n"
        + "※このメールは外勤調整システムから自動送信されています。";

      for (var e = 0; e < subadminEmails.length; e++) {
        try {
          GmailApp.sendEmail(subadminEmails[e], subSubject, subBody, { name: SENDER_NAME });
        } catch (err) {
          Logger.log("未入力者通知 送信失敗: " + subadminEmails[e] + " - " + err.message);
        }
      }
      Logger.log("平日未入力者通知(" + section + ")完了: " + missing.length + " 名未入力");
    }
  }
}

/**
 * 毎日実行: 翌日に平日外勤がある医員にリマインドメール送信
 * トリガー設定: 日ベースのタイマー（毎日 18:00-19:00）
 */
function sendWeekdayDayBeforeReminder() {
  var tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  var tomorrowStr = Utilities.formatDate(tomorrow, "Asia/Tokyo", "yyyy-MM-dd");
  var yearMonth = Utilities.formatDate(tomorrow, "Asia/Tokyo", "yyyy-MM");
  var displayDate = Utilities.formatDate(tomorrow, "Asia/Tokyo", "M/d(E)");

  var ssMaster = getMasterSpreadsheet();
  var configs = getWeekdayConfigs(ssMaster);
  var doctors = getDoctorMap(ssMaster);

  var sentCount = 0;
  for (var i = 0; i < configs.length; i++) {
    var cfg = configs[i];
    if (!cfg.is_active) continue;

    var ssSec = getWeekdaySectionSpreadsheet(ssMaster, cfg.section);
    if (!ssSec) {
      Logger.log("平日前日リマインダー: セクション '" + cfg.section + "' のスプレッドシートが未設定");
      continue;
    }

    var assignments = getWeekdayAssignments(ssSec, yearMonth, tomorrowStr);
    if (assignments.length === 0) continue;

    // 検体確認担当の判定（週単位のため月全体のassignmentsを使用）
    var allMonthAsgn = getWeekdayAssignments(ssSec, yearMonth, null);
    var specResult = getSpecimenAssignee(cfg, tomorrowStr, allMonthAsgn, doctors);

    for (var j = 0; j < assignments.length; j++) {
      var a = assignments[j];
      var doc = doctors[String(a.doctor_id)];
      if (!doc || !doc.email || !doc.notify_email) continue;

      var subject = (TEST_MODE ? "【テスト】" : "") + "【外勤リマインダー】明日 " + displayDate + " " + cfg.clinic_name;
      var body = (TEST_MODE ? TEST_NOTICE : "")
        + doc.name + " 先生\n\n"
        + "明日の外勤予定をお知らせします。\n\n"
        + "━━━━━━━━━━━━━━━━━━━━\n"
        + "  日付：" + displayDate + "\n"
        + "  外勤先：" + cfg.clinic_name + "\n"
        + "  スロット：" + (a.slot_name || "") + "\n"
        + "━━━━━━━━━━━━━━━━━━━━\n\n";

      // 検体確認の追記
      if (specResult && String(specResult.doctorId) === String(a.doctor_id)) {
        if (specResult.conflict) {
          var conflictLabels = buildConflictLabels(specResult, a.doctor_id);
          body += "※ 同意書・検体確認（" + conflictLabels.join("・") + "と相談してください）\n\n";
        } else {
          body += "※ 同意書・検体確認担当日です\n\n";
        }
      }

      body += "よろしくお願いいたします。\n\n"
        + "※このメールは外勤調整システムから自動送信されています。";

      try {
        GmailApp.sendEmail(doc.email, subject, body, { name: SENDER_NAME });
        sentCount++;
      } catch (e) {
        Logger.log("平日リマインダー 送信失敗: " + doc.name + " - " + e.message);
      }
    }
  }

  Logger.log("平日前日リマインダー完了: " + sentCount + " 件送信");
}


// ---- スケジュール再調整通知 ----

/**
 * 平日スケジュール再調整に伴う希望入力依頼メールをメンバーに送信
 * @param {Object} data - {section, clinic_name, deadline, target_date_count, mode}
 */
function sendWeekdayReadjustPreferenceRequest(data) {
  var section = data.section;
  var clinicName = data.clinic_name;
  var deadline = data.deadline || "";
  var dateCount = data.target_date_count || 0;
  var mode = data.mode; // "fill" or "reconstruct"

  var ssMaster = getMasterSpreadsheet();
  var doctors = getDoctorMap(ssMaster);

  var modeLabel = mode === "fill" ? "補填" : "再構成";
  var sentCount = 0;

  // セクションのメンバーにメール送信
  var configs = getWeekdayConfigs(ssMaster);
  var cfg = null;
  for (var c = 0; c < configs.length; c++) {
    if (configs[c].section === section) { cfg = configs[c]; break; }
  }
  var memberIds = cfg ? cfg.assigned_doctors : [];

  for (var k = 0; k < memberIds.length; k++) {
    var doc = doctors[String(memberIds[k])];
    if (!doc || !doc.email || !doc.notify_email) continue;

    var subject = (TEST_MODE ? "【テスト】" : "") + "【希望入力依頼】" + clinicName + " スケジュール再調整（" + modeLabel + "）";
    var body = (TEST_MODE ? TEST_NOTICE : "")
      + doc.name + " 先生\n\n"
      + clinicName + " の外勤スケジュールが再調整（" + modeLabel + "）されます。\n"
      + "以下の日程について、NG日・避けたい日の希望を入力してください。\n\n"
      + "━━━━━━━━━━━━━━━━━━━━\n"
      + "  対象日数: " + dateCount + " 日\n"
      + "  入力期限: " + deadline + "\n"
      + "━━━━━━━━━━━━━━━━━━━━\n\n"
      + "※ Webアプリから希望を入力してください。\n"
      + "※ 期限までに入力がない場合、既存の希望がそのまま使用されます。\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";

    try {
      GmailApp.sendEmail(doc.email, subject, body, { name: SENDER_NAME });
      sentCount++;
    } catch (e) {
      Logger.log("平日再調整希望入力依頼 送信失敗: " + doc.name + " - " + e.message);
    }
  }

  // 管理者にもサマリ送信
  sendToAdmins(
    (TEST_MODE ? "【テスト】" : "") + "【希望入力依頼送信完了】" + clinicName,
    clinicName + " のスケジュール再調整（" + modeLabel + "）に伴い、\n"
    + "メンバー " + sentCount + " 名に希望入力依頼メールを送信しました。\n"
    + "対象日数: " + dateCount + " 日\n"
    + "入力期限: " + deadline
  );

  Logger.log("平日再調整希望入力依頼完了: " + sentCount + " 件送信 (mode=" + mode + ")");
}


/**
 * 平日スケジュール再調整後にメンバーへ通知メール送信
 * @param {Object} data - {section, clinic_name, year_months, mode, period}
 */
function sendWeekdayScheduleReadjusted(data) {
  var section = data.section;
  var clinicName = data.clinic_name;
  var yearMonths = data.year_months || [];
  var mode = data.mode; // "fill" or "reconstruct"
  var period = data.period || "";

  var ssMaster = getMasterSpreadsheet();
  var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
  if (!ssSec) {
    Logger.log("平日再調整通知: セクション '" + section + "' のスプレッドシートが未設定");
    return;
  }
  var doctors = getDoctorMap(ssMaster);

  // 全対象月の割り当てを取得
  var allAssignments = [];
  for (var m = 0; m < yearMonths.length; m++) {
    var assignments = getWeekdayAssignments(ssSec, yearMonths[m], null);
    allAssignments = allAssignments.concat(assignments);
  }

  // 医員ごとにグループ化
  var doctorAssignments = {};
  for (var i = 0; i < allAssignments.length; i++) {
    var a = allAssignments[i];
    var did = String(a.doctor_id);
    if (!doctorAssignments[did]) doctorAssignments[did] = [];
    doctorAssignments[did].push(a);
  }

  var modeLabel = mode === "fill" ? "補填" : "再構成";
  var sentCount = 0;

  // セクションのメンバーにメール送信
  var configs = getWeekdayConfigs(ssMaster);
  var cfg = null;
  for (var c = 0; c < configs.length; c++) {
    if (configs[c].section === section) { cfg = configs[c]; break; }
  }
  var memberIds = cfg ? cfg.assigned_doctors : [];

  for (var k = 0; k < memberIds.length; k++) {
    var doc = doctors[String(memberIds[k])];
    if (!doc || !doc.email || !doc.notify_email) continue;

    var memberAssignments = doctorAssignments[String(memberIds[k])] || [];
    var subject = (TEST_MODE ? "【テスト】" : "") + "【平日外勤再調整】" + clinicName + "（" + modeLabel + "）";
    var body = (TEST_MODE ? TEST_NOTICE : "")
      + doc.name + " 先生\n\n"
      + clinicName + " の外勤スケジュールが再調整（" + modeLabel + "）されました。\n"
      + "対象期間: " + period + "\n\n";

    if (memberAssignments.length > 0) {
      body += "━━━ 更新後のスケジュール ━━━\n";
      memberAssignments.sort(function(a, b) { return String(a.date) > String(b.date) ? 1 : -1; });
      for (var j = 0; j < memberAssignments.length; j++) {
        var dateObj = new Date(String(memberAssignments[j].date) + "T00:00:00+09:00");
        var ds = Utilities.formatDate(dateObj, "Asia/Tokyo", "M/d(E)");
        body += "  " + ds + "：" + (memberAssignments[j].slot_name || "") + "\n";
      }
      body += "━━━━━━━━━━━━━━━━━━━━\n";
    } else {
      body += "この期間の割り当てはありません。\n";
    }

    body += "\n詳細はWebアプリからご確認ください。\n\n"
      + "※このメールは外勤調整システムから自動送信されています。";

    try {
      GmailApp.sendEmail(doc.email, subject, body, { name: SENDER_NAME });
      sentCount++;
    } catch (e) {
      Logger.log("平日再調整通知 送信失敗: " + doc.name + " - " + e.message);
    }
  }

  // 管理者にもサマリ送信
  sendToAdmins(
    (TEST_MODE ? "【テスト】" : "") + "【平日外勤再調整完了】" + clinicName,
    clinicName + " の平日スケジュールが再調整されました。\n"
    + "モード: " + modeLabel + "\n"
    + "対象期間: " + period + "\n"
    + "通知送信: " + sentCount + " 名"
  );

  Logger.log("平日再調整通知完了: " + sentCount + " 件送信 (mode=" + mode + ")");

  // カレンダー同期（失敗してもメール通知には影響しない）
  try {
    syncWeekdayCalendarReadjusted(data, ssMaster);
  } catch (e) {
    Logger.log("平日再調整カレンダー同期エラー: " + e.message);
  }
}
