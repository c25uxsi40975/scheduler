/**
 * 外勤リマインダー・通知スクリプト — カレンダー連携モジュール
 *
 * スケジュール確定・再調整時にGoogleカレンダーへ終日イベントを同期する。
 * 共有カレンダーに全予定を作成し、notify_calendar が有効な医員には
 * ゲストとして追加することで個人カレンダーにも表示する。
 *
 * 前提: Google Calendar API (Advanced Service) を有効化すること
 *   Apps Script エディタ → サービス（+）→ Google Calendar API → 追加
 *
 * 設定シートのキー:
 *   calendar_id_saturday  : 土曜外勤用カレンダーID
 *   calendar_id_weekday_{section} : 平日セクション別カレンダーID
 *   ※ カレンダーIDが登録されていれば自動的に同期が有効になる
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
      result[key] = String(data[i][1]).trim();
    }
  }
  return result;
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

// ---- Advanced Service によるイベント操作 ----

/**
 * 終日イベントをゲスト付きで作成（招待メールなし）
 * Calendar Advanced Service (v3 REST API) を使用し、sendUpdates: "none" で
 * 招待メール送信を抑止する。
 *
 * @param {string} calendarId カレンダーID
 * @param {string} title イベントタイトル
 * @param {Date} eventDate イベント日付
 * @param {string} description イベント説明
 * @param {string} guestEmail ゲストのメールアドレス（空なら追加しない）
 * @return {Object|null} 作成されたイベントリソース
 */
function createAllDayEventWithGuest(calendarId, title, eventDate, description, guestEmail) {
  var dateStr = Utilities.formatDate(eventDate, "Asia/Tokyo", "yyyy-MM-dd");
  var endDate = new Date(eventDate);
  endDate.setDate(endDate.getDate() + 1);
  var endDateStr = Utilities.formatDate(endDate, "Asia/Tokyo", "yyyy-MM-dd");

  var event = {
    summary: title,
    description: description,
    start: { date: dateStr },
    end: { date: endDateStr },
    transparency: "transparent"
  };

  if (guestEmail) {
    event.attendees = [{ email: guestEmail, responseStatus: "accepted" }];
  }

  try {
    return Calendar.Events.insert(event, calendarId, { sendUpdates: "none" });
  } catch (e) {
    Logger.log("イベント作成失敗 (Advanced Service): " + title + " " + dateStr + " - " + e.message);
    // フォールバック: CalendarApp で作成（ゲストなし）
    try {
      var cal = CalendarApp.getCalendarById(calendarId);
      if (cal) {
        cal.createAllDayEvent(title, eventDate, { description: description });
        return {};
      }
    } catch (e2) {
      Logger.log("イベント作成失敗 (フォールバック): " + title + " - " + e2.message);
    }
    return null;
  }
}

/**
 * Calendar.Events.list() で指定期間のイベントを一括取得（ページネーション対応）
 * attendees 情報込みで取得できるため、個別 get が不要になる。
 *
 * @param {string} calendarId カレンダーID
 * @param {Date} startDate 開始日
 * @param {Date} endDate 終了日
 * @return {Array} イベントリソースの配列
 */
function listTaggedEvents(calendarId, startDate, endDate) {
  var events = [];
  var pageToken = null;
  do {
    var params = {
      timeMin: startDate.toISOString(),
      timeMax: endDate.toISOString(),
      maxResults: 250,
      singleEvents: true
    };
    if (pageToken) params.pageToken = pageToken;
    var response = Calendar.Events.list(calendarId, params);
    var items = response.items || [];
    for (var i = 0; i < items.length; i++) {
      var desc = items[i].description || "";
      if (desc.indexOf("[外勤調整:") !== -1) {
        events.push(items[i]);
      }
    }
    pageToken = response.nextPageToken;
  } while (pageToken);
  return events;
}

// ---- タグベースのイベント管理 ----

/**
 * タグ付きイベントを期間内で削除（ゲストへの通知なし）
 * description にタグ文字列が含まれるイベントを対象とする。
 *
 * @param {Calendar} calendar 対象カレンダー (CalendarApp)
 * @param {string} calendarId カレンダーID文字列（Advanced Service用）
 * @param {string} tag 識別タグ（例: "[外勤調整:saturday:2026-03]"）
 * @param {Date} startDate 開始日
 * @param {Date} endDate 終了日（この日を含む）
 * @return {number} 削除件数
 */
function deleteTaggedEvents(calendar, calendarId, tag, startDate, endDate) {
  // endDate を翌日にして getEvents の範囲に含める
  var searchEnd = new Date(endDate);
  searchEnd.setDate(searchEnd.getDate() + 1);

  var events = calendar.getEvents(startDate, searchEnd);
  var count = 0;
  for (var i = 0; i < events.length; i++) {
    var desc = events[i].getDescription() || "";
    if (desc.indexOf(tag) !== -1) {
      try {
        // Advanced Service で削除（ゲストへの通知を抑止）
        var eventId = events[i].getId().replace("@google.com", "");
        Calendar.Events.delete(calendarId, eventId, { sendUpdates: "none" });
      } catch (e) {
        // フォールバック
        try { events[i].deleteEvent(); } catch (e2) {}
      }
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
  deleteTaggedEvents(calendar, calId, tag, range.start, range.end);

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

    // notify_calendar が有効な医員のみゲスト追加
    var guestEmail = "";
    if (doc && doc.email && doc.notify_calendar) {
      guestEmail = doc.email;
    }

    var result = createAllDayEventWithGuest(calId, title, eventDate, description, guestEmail);
    if (result !== null) createdCount++;
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
  var settings = getCalendarSettings(ssMaster);
  var section = data.section;
  var clinicName = data.clinic_name || "";
  var yearMonths = data.year_months || [];
  var calId = settings["calendar_id_weekday_" + section];
  var calendar = getCalendarSafe(calId);
  if (!calendar) return;

  var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
  if (!ssSec) {
    Logger.log("平日カレンダー同期: セクションSS未設定: " + section);
    return;
  }

  var doctors = getDoctorMap(ssMaster);

  var createdCount = 0;
  for (var m = 0; m < yearMonths.length; m++) {
    var ym = yearMonths[m];
    var tag = "[外勤調整:weekday:" + section + ":" + ym + "]";
    var range = getMonthRange(ym);
    deleteTaggedEvents(calendar, calId, tag, range.start, range.end);

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

      // notify_calendar が有効な医員のみゲスト追加
      var guestEmail = "";
      var doc = a.doctor_id ? doctors[String(a.doctor_id)] : null;
      if (doc && doc.email && doc.notify_calendar) {
        guestEmail = doc.email;
      }

      var result = createAllDayEventWithGuest(calId, title, eventDate, description, guestEmail);
      if (result !== null) createdCount++;
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
  var settings = getCalendarSettings(ssMaster);
  var section = data.section;
  var clinicName = data.clinic_name || "";
  var calId = settings["calendar_id_weekday_" + section];
  var calendar = getCalendarSafe(calId);
  if (!calendar) return;

  var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
  if (!ssSec) return;

  var doctors = getDoctorMap(ssMaster);

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
    deleteTaggedEvents(calendar, calId, tag, dayStart, dayEnd);

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

      // notify_calendar が有効な医員のみゲスト追加
      var guestEmail = "";
      var doc = a.doctor_id ? doctors[String(a.doctor_id)] : null;
      if (doc && doc.email && doc.notify_calendar) {
        guestEmail = doc.email;
      }

      var result = createAllDayEventWithGuest(calId, title, eventDate, description, guestEmail);
      if (result !== null) createdCount++;
    }
  }

  Logger.log("シフト交換カレンダー同期完了: " + createdCount + " 件作成");
}

// ---- 医員単位のカレンダー再同期 ----

/**
 * 特定の医員のカレンダー連携状態が変わったときに再同期
 * 医員が通知設定を保存した際に doPost 経由で呼ばれる。
 *
 * Calendar.Events.list() で一括取得し、医員名でフィルタして
 * ゲストの追加/削除を行う。個別 get は不要。
 *
 * enabled=true: 既存イベントにゲストとして追加
 * enabled=false: 既存イベントからゲストを削除
 *
 * @param {Object} data {doctor_id, doctor_email, enabled}
 */
function resyncCalendarForDoctor(data) {
  var ssMaster = getMasterSpreadsheet();
  var settings = getCalendarSettings(ssMaster);
  var doctors = getDoctorMap(ssMaster);

  var doctorId = String(data.doctor_id);
  var doctorEmail = String(data.doctor_email || "");
  var enabled = data.enabled === true || data.enabled === "true";

  if (!doctorEmail) {
    Logger.log("カレンダー再同期: メールアドレスなし (doctor_id=" + doctorId + ")");
    return;
  }

  var targetDoc = doctors[doctorId];
  if (!targetDoc) {
    Logger.log("カレンダー再同期: 医員不明 (doctor_id=" + doctorId + ")");
    return;
  }

  var now = new Date();
  var start = new Date(now.getFullYear(), now.getMonth(), 1);
  var end = new Date(now.getFullYear(), now.getMonth() + 3, 0);

  // 全カレンダーIDを収集
  var calendarIds = [];
  if (settings["calendar_id_saturday"]) {
    calendarIds.push({ id: settings["calendar_id_saturday"], label: "saturday" });
  }
  for (var key in settings) {
    if (key.indexOf("calendar_id_weekday_") === 0 && settings[key]) {
      calendarIds.push({ id: settings[key], label: key.replace("calendar_id_", "") });
    }
  }

  var totalUpdated = 0;
  for (var c = 0; c < calendarIds.length; c++) {
    var calId = calendarIds[c].id;
    var label = calendarIds[c].label;

    // Calendar.Events.list() で一括取得（attendees 込み）
    var events = listTaggedEvents(calId, start, end);
    var updatedCount = 0;

    for (var i = 0; i < events.length; i++) {
      var desc = events[i].description || "";
      var doctorNameMatch = desc.match(/医員: (.+)/);
      if (!doctorNameMatch) continue;
      if (doctorNameMatch[1].trim() !== targetDoc.name) continue;

      var attendees = events[i].attendees || [];
      var hasGuest = false;
      var guestIndex = -1;
      for (var j = 0; j < attendees.length; j++) {
        if (attendees[j].email === doctorEmail) {
          hasGuest = true;
          guestIndex = j;
          break;
        }
      }

      if (enabled && !hasGuest) {
        attendees.push({ email: doctorEmail, responseStatus: "accepted" });
        Calendar.Events.patch({ attendees: attendees }, calId, events[i].id, { sendUpdates: "none" });
        updatedCount++;
      } else if (!enabled && hasGuest) {
        attendees.splice(guestIndex, 1);
        Calendar.Events.patch({ attendees: attendees }, calId, events[i].id, { sendUpdates: "none" });
        updatedCount++;
      }
    }

    if (updatedCount > 0) {
      Logger.log("カレンダー再同期 (" + label + "): " + updatedCount + " 件更新");
    }
    totalUpdated += updatedCount;
  }

  Logger.log("カレンダー再同期完了: doctor_id=" + doctorId + ", enabled=" + enabled + ", 合計" + totalUpdated + "件");
}

/**
 * 全医員のカレンダーゲストを一括再同期
 * 管理者がカレンダー設定を保存した際に doPost 経由で呼ばれる。
 *
 * Calendar.Events.list() でカレンダーごとに1回だけイベントを取得し、
 * 全医員の notify_calendar フラグに基づいてゲストを追加/削除する。
 *
 * API呼び出し: list 1〜2回/カレンダー + patch 変更イベント数
 */
function resyncCalendarForAllDoctors() {
  var ssMaster = getMasterSpreadsheet();
  var settings = getCalendarSettings(ssMaster);
  var doctors = getDoctorMap(ssMaster);

  var now = new Date();
  var start = new Date(now.getFullYear(), now.getMonth(), 1);
  var end = new Date(now.getFullYear(), now.getMonth() + 3, 0);

  // notify_calendar が有効な医員の {名前: メール} マップを作成
  var enabledDoctors = {};  // name -> email
  var allDoctorEmails = {}; // name -> email (全医員)
  for (var id in doctors) {
    var doc = doctors[id];
    if (doc.email) {
      allDoctorEmails[doc.name] = doc.email;
      if (doc.notify_calendar) {
        enabledDoctors[doc.name] = doc.email;
      }
    }
  }

  // 全カレンダーIDを収集
  var calendarIds = [];
  if (settings["calendar_id_saturday"]) {
    calendarIds.push({ id: settings["calendar_id_saturday"], label: "saturday" });
  }
  for (var key in settings) {
    if (key.indexOf("calendar_id_weekday_") === 0 && settings[key]) {
      calendarIds.push({ id: settings[key], label: key.replace("calendar_id_", "") });
    }
  }

  var totalUpdated = 0;
  for (var c = 0; c < calendarIds.length; c++) {
    var calId = calendarIds[c].id;
    var label = calendarIds[c].label;

    // Calendar.Events.list() で一括取得
    var events = listTaggedEvents(calId, start, end);
    var updatedCount = 0;

    for (var i = 0; i < events.length; i++) {
      var desc = events[i].description || "";
      var doctorNameMatch = desc.match(/医員: (.+)/);
      if (!doctorNameMatch) continue;
      var eventDoctorName = doctorNameMatch[1].trim();

      var shouldHaveGuest = !!enabledDoctors[eventDoctorName];
      var expectedEmail = enabledDoctors[eventDoctorName] || allDoctorEmails[eventDoctorName] || "";
      if (!expectedEmail) continue;

      var attendees = events[i].attendees || [];
      var hasGuest = false;
      var guestIndex = -1;
      for (var j = 0; j < attendees.length; j++) {
        if (attendees[j].email === expectedEmail) {
          hasGuest = true;
          guestIndex = j;
          break;
        }
      }

      var needsUpdate = false;
      if (shouldHaveGuest && !hasGuest) {
        attendees.push({ email: expectedEmail, responseStatus: "accepted" });
        needsUpdate = true;
      } else if (!shouldHaveGuest && hasGuest) {
        attendees.splice(guestIndex, 1);
        needsUpdate = true;
      }

      if (needsUpdate) {
        try {
          Calendar.Events.patch({ attendees: attendees }, calId, events[i].id, { sendUpdates: "none" });
          updatedCount++;
        } catch (e) {
          Logger.log("全医員再同期 イベント更新失敗: " + events[i].id + " - " + e.message);
        }
      }
    }

    if (updatedCount > 0) {
      Logger.log("全医員カレンダー再同期 (" + label + "): " + updatedCount + " 件更新");
    }
    totalUpdated += updatedCount;
  }

  Logger.log("全医員カレンダー再同期完了: 合計" + totalUpdated + "件");
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
