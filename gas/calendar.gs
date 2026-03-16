/**
 * 外勤リマインダー・通知スクリプト — カレンダー連携モジュール
 *
 * スケジュール確定・再調整時にGoogleカレンダーへ終日イベントを同期する。
 * 共有カレンダーは管理者が作成し、医員には閲覧権限で共有する。
 *
 * 設定シートのキー:
 *   calendar_sync_enabled : "1" で有効
 *   calendar_id_saturday  : 土曜外勤用カレンダーID
 *   calendar_id_weekday_{section} : 平日セクション別カレンダーID
 */

// ---- カレンダー設定 ----

/**
 * 設定シートからカレンダー関連の設定を一括取得
 * @param {Spreadsheet} ssMaster マスタスプレッドシート
 * @return {Object} {key: value, ...}
 */
function getCalendarSettings(ssMaster) {
  var sheet = getSheet(ssMaster, "設定");
  if (!sheet) return {};
  var data = sheet.getDataRange().getValues();
  var result = {};
  for (var i = 1; i < data.length; i++) {
    var key = String(data[i][0]);
    if (key.indexOf("calendar_") === 0) {
      result[key] = String(data[i][1]);
    }
  }
  return result;
}

/**
 * カレンダー連携が有効かチェック
 */
function isCalendarSyncEnabled(ssMaster) {
  var settings = getCalendarSettings(ssMaster);
  return settings["calendar_sync_enabled"] === "1";
}

/**
 * カレンダーIDで安全にカレンダーを取得
 * @return {Calendar|null}
 */
function getCalendarSafe(calendarId) {
  if (!calendarId || calendarId === "undefined" || calendarId === "null") return null;
  try {
    var cal = CalendarApp.getCalendarById(calendarId);
    if (!cal) {
      Logger.log("カレンダーが見つかりません: " + calendarId);
    }
    return cal;
  } catch (e) {
    Logger.log("カレンダー取得失敗: " + calendarId + " - " + e.message);
    return null;
  }
}

// ---- タグベースのイベント管理 ----

/**
 * タグ付きイベントを期間内で削除
 * description にタグ文字列が含まれるイベントを対象とする。
 *
 * @param {Calendar} calendar 対象カレンダー
 * @param {string} tag 識別タグ（例: "[外勤調整:saturday:2026-03]"）
 * @param {Date} startDate 開始日
 * @param {Date} endDate 終了日（この日を含む）
 * @return {number} 削除件数
 */
function deleteTaggedEvents(calendar, tag, startDate, endDate) {
  // endDate を翌日にして getEvents の範囲に含める
  var searchEnd = new Date(endDate);
  searchEnd.setDate(searchEnd.getDate() + 1);

  var events = calendar.getEvents(startDate, searchEnd);
  var count = 0;
  for (var i = 0; i < events.length; i++) {
    var desc = events[i].getDescription() || "";
    if (desc.indexOf(tag) !== -1) {
      events[i].deleteEvent();
      count++;
    }
  }
  if (count > 0) {
    Logger.log("カレンダーイベント削除: " + count + " 件 (tag=" + tag + ")");
  }
  return count;
}

/**
 * 年月文字列から月の開始日・終了日を取得
 * @param {string} yearMonth "yyyy-MM"
 * @return {{start: Date, end: Date}}
 */
function getMonthRange(yearMonth) {
  var parts = yearMonth.split("-");
  var year = parseInt(parts[0], 10);
  var month = parseInt(parts[1], 10) - 1; // 0-indexed
  var start = new Date(year, month, 1);
  var end = new Date(year, month + 1, 0); // 月末日
  return { start: start, end: end };
}

// ---- 土曜カレンダー同期 ----

/**
 * 土曜スケジュール確定時にカレンダーへイベントを同期
 * sendConfirmationEmails() から呼ばれる。
 *
 * @param {string} yearMonth "yyyy-MM"
 * @param {Spreadsheet} ssMaster マスタスプレッドシート（呼び出し元から引き回し）
 */
function syncSaturdayCalendar(yearMonth, ssMaster) {
  if (!isCalendarSyncEnabled(ssMaster)) return;

  var settings = getCalendarSettings(ssMaster);
  var calId = settings["calendar_id_saturday"];
  var calendar = getCalendarSafe(calId);
  if (!calendar) return;

  var ssOp = getOperationalSpreadsheet();
  var schedSheet = getSheet(ssOp, "スケジュール_" + yearMonth);
  if (!schedSheet) {
    Logger.log("カレンダー同期: スケジュールシートなし: " + yearMonth);
    return;
  }

  var allAssignments = getConfirmedAssignments(schedSheet, null);
  if (allAssignments.length === 0) {
    Logger.log("カレンダー同期: 確定割り当てなし: " + yearMonth);
    return;
  }

  var doctors = getDoctorMap(ssMaster);
  var clinics = getClinicMap(ssMaster);

  // 月の範囲で既存タグ付きイベントを削除
  var tag = "[外勤調整:saturday:" + yearMonth + "]";
  var range = getMonthRange(yearMonth);
  deleteTaggedEvents(calendar, tag, range.start, range.end);

  // 新規イベントを作成
  var createdCount = 0;
  for (var i = 0; i < allAssignments.length; i++) {
    var a = allAssignments[i];
    var doc = doctors[String(a.doctor_id)];
    var clinicName = clinics[String(a.clinic_id)] || "（不明）";
    var doctorName = doc ? doc.name : "（不明）";

    var eventDate = new Date(String(a.date) + "T00:00:00+09:00");
    var title = doctorName + " - " + clinicName;
    var description = tag + "\n"
      + "セクション: 土曜外勤\n"
      + "医員: " + doctorName + "\n"
      + "外勤先: " + clinicName;

    try {
      calendar.createAllDayEvent(title, eventDate, { description: description });
      createdCount++;
    } catch (e) {
      Logger.log("土曜カレンダーイベント作成失敗: " + title + " " + a.date + " - " + e.message);
    }
  }

  Logger.log("土曜カレンダー同期完了: " + createdCount + " 件作成 (" + yearMonth + ")");
}

// ---- 平日カレンダー同期 ----

/**
 * 平日スケジュール確定時にカレンダーへイベントを同期
 * sendWeekdayScheduleConfirmed() から呼ばれる。
 *
 * @param {Object} data {section, clinic_name, year_months}
 * @param {Spreadsheet} ssMaster マスタスプレッドシート
 */
function syncWeekdayCalendar(data, ssMaster) {
  if (!isCalendarSyncEnabled(ssMaster)) return;

  var section = data.section;
  var clinicName = data.clinic_name || "";
  var yearMonths = data.year_months || [];

  var settings = getCalendarSettings(ssMaster);
  var calId = settings["calendar_id_weekday_" + section];
  var calendar = getCalendarSafe(calId);
  if (!calendar) return;

  var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
  if (!ssSec) {
    Logger.log("平日カレンダー同期: セクションSS未設定: " + section);
    return;
  }

  var createdCount = 0;
  for (var m = 0; m < yearMonths.length; m++) {
    var ym = yearMonths[m];
    var tag = "[外勤調整:weekday:" + section + ":" + ym + "]";
    var range = getMonthRange(ym);
    deleteTaggedEvents(calendar, tag, range.start, range.end);

    var assignments = getWeekdayAssignments(ssSec, ym, null);
    for (var i = 0; i < assignments.length; i++) {
      var a = assignments[i];
      var title = (a.doctor_name || "") + " - " + (a.slot_name || "");
      var eventDate = new Date(String(a.date) + "T00:00:00+09:00");
      var description = tag + "\n"
        + "セクション: " + section + "\n"
        + "外勤先: " + clinicName + "\n"
        + "医員: " + (a.doctor_name || "") + "\n"
        + "スロット: " + (a.slot_name || "");

      try {
        calendar.createAllDayEvent(title, eventDate, { description: description });
        createdCount++;
      } catch (e) {
        Logger.log("平日カレンダーイベント作成失敗: " + title + " " + a.date + " - " + e.message);
      }
    }
  }

  Logger.log("平日カレンダー同期完了: " + createdCount + " 件作成 (" + section + ")");
}

/**
 * 平日スケジュール再調整後にカレンダーを更新
 * sendWeekdayScheduleReadjusted() から呼ばれる。
 *
 * @param {Object} data {section, clinic_name, year_months, period}
 * @param {Spreadsheet} ssMaster マスタスプレッドシート
 */
function syncWeekdayCalendarReadjusted(data, ssMaster) {
  // 再調整は全月分を再同期する（period内だけでなく月全体のイベントを再構成）
  syncWeekdayCalendar(data, ssMaster);
}

/**
 * シフト交換後にカレンダーを更新
 * sendShiftSwapNotification() から呼ばれる。
 *
 * 交換対象の日付のイベントを削除し、更新後の割り当てでイベントを再作成する。
 * @param {Object} data {section, clinic_name, requester_date, target_date, ...}
 * @param {Spreadsheet} ssMaster マスタスプレッドシート
 */
function syncShiftSwapCalendar(data, ssMaster) {
  if (!isCalendarSyncEnabled(ssMaster)) return;

  var section = data.section;
  var clinicName = data.clinic_name || "";

  var settings = getCalendarSettings(ssMaster);
  var calId = settings["calendar_id_weekday_" + section];
  var calendar = getCalendarSafe(calId);
  if (!calendar) return;

  var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
  if (!ssSec) return;

  // 交換対象の日付を取得（requester_shift, target_shift から日付を抽出）
  var dates = [];
  if (data.requester_shift) {
    var rMatch = String(data.requester_shift).match(/\d{4}-\d{2}-\d{2}/);
    if (rMatch) dates.push(rMatch[0]);
  }
  if (data.target_shift) {
    var tMatch = String(data.target_shift).match(/\d{4}-\d{2}-\d{2}/);
    if (tMatch) dates.push(tMatch[0]);
  }

  if (dates.length === 0) {
    Logger.log("シフト交換カレンダー同期: 日付を特定できません");
    return;
  }

  var createdCount = 0;
  for (var d = 0; d < dates.length; d++) {
    var dateStr = dates[d];
    var ym = dateStr.substring(0, 7); // yyyy-MM
    var tag = "[外勤調整:weekday:" + section + ":" + ym + "]";

    // この日付のタグ付きイベントだけ削除
    var dayStart = new Date(dateStr + "T00:00:00+09:00");
    var dayEnd = new Date(dateStr + "T00:00:00+09:00");
    deleteTaggedEvents(calendar, tag, dayStart, dayEnd);

    // この日付の最新割り当てを取得して再作成
    var assignments = getWeekdayAssignments(ssSec, ym, dateStr);
    for (var i = 0; i < assignments.length; i++) {
      var a = assignments[i];
      var title = (a.doctor_name || "") + " - " + (a.slot_name || "");
      var eventDate = new Date(dateStr + "T00:00:00+09:00");
      var description = tag + "\n"
        + "セクション: " + section + "\n"
        + "外勤先: " + clinicName + "\n"
        + "医員: " + (a.doctor_name || "") + "\n"
        + "スロット: " + (a.slot_name || "");

      try {
        calendar.createAllDayEvent(title, eventDate, { description: description });
        createdCount++;
      } catch (e) {
        Logger.log("交換カレンダーイベント作成失敗: " + title + " " + dateStr + " - " + e.message);
      }
    }
  }

  Logger.log("シフト交換カレンダー同期完了: " + createdCount + " 件作成");
}

// ---- テスト用 ----

/**
 * テスト用: 土曜カレンダー同期を手動実行
 * Apps Scriptエディタから実行して動作確認に使用。
 */
function testSyncSaturdayCalendar() {
  var ssMaster = getMasterSpreadsheet();
  var now = new Date();
  var yearMonth = Utilities.formatDate(now, "Asia/Tokyo", "yyyy-MM");
  Logger.log("テスト実行: syncSaturdayCalendar(" + yearMonth + ")");
  syncSaturdayCalendar(yearMonth, ssMaster);
}
