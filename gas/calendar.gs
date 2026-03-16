/**
 * 外勤リマインダー・通知スクリプト — カレンダー連携モジュール
 *
 * スケジュール確定・再調整時にGoogleカレンダーへ終日イベントを同期する。
 *
 * ■ 管理者共有カレンダー（全医員の予定を一覧）
 *   - 土曜カレンダー / 平日セクション別カレンダー
 *
 * ■ 医員個別カレンダー（自分の予定のみ）
 *   - 医員がカレンダー連携を有効にすると自動作成
 *   - 土曜 + 平日全セクションの予定を1つのカレンダーにまとめる
 *   - 共有招待メール（1通）が届き、承認後Googleカレンダーに表示
 *
 * 前提: Google Calendar API (Advanced Service) を有効化すること
 *   Apps Script エディタ → サービス（+）→ Google Calendar API → 追加
 *
 * 設定シートのキー:
 *   calendar_id_saturday  : 土曜外勤用カレンダーID（管理者共有）
 *   calendar_id_weekday_{section} : 平日セクション別カレンダーID（管理者共有）
 *   ※ カレンダーIDが登録されていれば自動的に同期が有効になる
 *
 * 医員マスタのカラム:
 *   personal_calendar_id : 医員個別カレンダーID（GASが自動管理）
 */

// ---- カレンダー設定 ----

/**
 * 設定シートからカレンダー関連の設定を一括取得
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

// ---- イベント作成・削除 ----

/**
 * 終日イベントを作成（招待メールなし）
 */
function createAllDayEvent(calendarId, title, eventDate, description) {
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

  try {
    return Calendar.Events.insert(event, calendarId, { sendUpdates: "none" });
  } catch (e) {
    Logger.log("イベント作成失敗: " + title + " " + dateStr + " - " + e.message);
    // フォールバック: CalendarApp で作成
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
 * タグ付きイベントを期間内で削除（通知なし）
 */
function deleteTaggedEvents(calendar, calendarId, tag, startDate, endDate) {
  var searchEnd = new Date(endDate);
  searchEnd.setDate(searchEnd.getDate() + 1);

  var events = calendar.getEvents(startDate, searchEnd);
  var count = 0;
  for (var i = 0; i < events.length; i++) {
    var desc = events[i].getDescription() || "";
    if (desc.indexOf(tag) !== -1) {
      try {
        var eventId = events[i].getId().replace("@google.com", "");
        Calendar.Events.delete(calendarId, eventId, { sendUpdates: "none" });
      } catch (e) {
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
 * 指定カレンダー上の [外勤調整:] タグ付きイベントを全削除
 */
function deleteAllTaggedEvents(calendarId) {
  var cal = getCalendarSafe(calendarId);
  if (!cal) return;
  var now = new Date();
  var start = new Date(2020, 0, 1);
  var end = new Date(now.getFullYear() + 2, 0, 1);
  var events = cal.getEvents(start, end);
  var count = 0;
  for (var i = 0; i < events.length; i++) {
    var desc = events[i].getDescription() || "";
    if (desc.indexOf("[外勤調整:") !== -1) {
      try {
        var eventId = events[i].getId().replace("@google.com", "");
        Calendar.Events.delete(calendarId, eventId, { sendUpdates: "none" });
      } catch (e) {
        try { events[i].deleteEvent(); } catch (e2) {}
      }
      count++;
    }
  }
  Logger.log("全イベント削除: " + count + " 件 (calendar=" + calendarId + ")");
}

/**
 * 年月文字列から月の開始日・終了日を取得
 */
function getMonthRange(yearMonth) {
  var parts = yearMonth.split("-");
  var year = parseInt(parts[0], 10);
  var month = parseInt(parts[1], 10) - 1;
  var start = new Date(year, month, 1);
  var end = new Date(year, month + 1, 0);
  return { start: start, end: end };
}

// ---- 医員個別カレンダー管理 ----

/**
 * 医員個別カレンダーを作成し、医員に共有する
 * @return {string} 作成したカレンダーID（失敗時は空文字）
 */
function createPersonalCalendar(doctorName, doctorEmail) {
  var calName = "外勤スケジュール - " + doctorName;
  try {
    var cal = CalendarApp.createCalendar(calName, { timeZone: "Asia/Tokyo" });
    var calId = cal.getId();

    // 医員に共有（readerで十分 — 招待メールが1通届く）
    try {
      Calendar.Acl.insert({
        role: "reader",
        scope: { type: "user", value: doctorEmail }
      }, calId);
    } catch (aclErr) {
      Logger.log("ACL設定失敗（CalendarAppで再試行）: " + aclErr.message);
      // フォールバック: CalendarApp で共有
      cal.addViewer(doctorEmail);
    }

    Logger.log("個別カレンダー作成: " + calName + " (" + calId + ") → " + doctorEmail);
    return calId;
  } catch (e) {
    Logger.log("個別カレンダー作成失敗: " + calName + " - " + e.message);
    return "";
  }
}

/**
 * 医員個別カレンダーを削除
 */
function deletePersonalCalendar(calendarId) {
  if (!calendarId) return;
  try {
    var cal = CalendarApp.getCalendarById(calendarId);
    if (cal) {
      cal.deleteCalendar();
      Logger.log("個別カレンダー削除: " + calendarId);
    }
  } catch (e) {
    Logger.log("個別カレンダー削除失敗: " + calendarId + " - " + e.message);
  }
}

/**
 * 医員マスタの personal_calendar_id を更新
 */
function savePersonalCalendarId(doctorId, calendarId, ssMaster) {
  var sheet = getSheet(ssMaster, "医員マスタ");
  if (!sheet) return;
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colIdx = headers.indexOf("personal_calendar_id");
  if (colIdx < 0) return;

  var idCol = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
  for (var i = 0; i < idCol.length; i++) {
    if (String(idCol[i][0]) === String(doctorId)) {
      sheet.getRange(i + 2, colIdx + 1).setValue(calendarId);
      return;
    }
  }
}

// ---- 医員個別カレンダーへのイベント同期 ----

/**
 * 指定医員の個別カレンダーに確定済み全スケジュールを同期
 * （土曜 + 平日全セクション）
 */
function syncPersonalCalendarForDoctor(doctorId, ssMaster) {
  var doctors = getDoctorMap(ssMaster);
  var doc = doctors[String(doctorId)];
  if (!doc || !doc.personal_calendar_id) return;

  var calId = doc.personal_calendar_id;
  var cal = getCalendarSafe(calId);
  if (!cal) {
    // カレンダーが外部で削除された場合、IDをクリア
    savePersonalCalendarId(doctorId, "", ssMaster);
    Logger.log("個別カレンダーが見つからないためIDクリア: doctor_id=" + doctorId);
    return;
  }

  // 既存イベントを全削除して再作成
  deleteAllTaggedEvents(calId);

  var settings = getCalendarSettings(ssMaster);
  var clinics = getClinicMap(ssMaster);
  var createdCount = 0;

  // ---- 土曜スケジュール ----
  if (settings["calendar_id_saturday"]) {
    var ssOp = getOperationalSpreadsheet();
    // 今月から3ヶ月先まで
    var now = new Date();
    for (var mOff = 0; mOff < 4; mOff++) {
      var d = new Date(now.getFullYear(), now.getMonth() + mOff, 1);
      var ym = Utilities.formatDate(d, "Asia/Tokyo", "yyyy-MM");
      var schedSheet = getSheet(ssOp, "スケジュール_" + ym);
      if (!schedSheet) continue;

      var assignments = getConfirmedAssignments(schedSheet, null);
      var tag = "[外勤調整:saturday:" + ym + "]";
      for (var i = 0; i < assignments.length; i++) {
        var a = assignments[i];
        if (String(a.doctor_id) !== String(doctorId)) continue;
        var clinicName = clinics[String(a.clinic_id)] || "（不明）";
        var eventDate = new Date(String(a.date) + "T00:00:00+09:00");
        var title = clinicName + "（土曜）";
        var description = tag + "\n医員: " + doc.name + "\n外勤先: " + clinicName;
        if (createAllDayEvent(calId, title, eventDate, description)) createdCount++;
      }
    }
  }

  // ---- 平日スケジュール（全セクション） ----
  for (var key in settings) {
    if (key.indexOf("calendar_id_weekday_") !== 0 || !settings[key]) continue;
    var section = key.replace("calendar_id_weekday_", "");
    var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
    if (!ssSec) continue;

    // 今月から12ヶ月先まで（平日は年度単位のため）
    for (var mOff2 = 0; mOff2 < 13; mOff2++) {
      var d2 = new Date(now.getFullYear(), now.getMonth() + mOff2, 1);
      var ym2 = Utilities.formatDate(d2, "Asia/Tokyo", "yyyy-MM");
      var wdAssignments = getWeekdayAssignments(ssSec, ym2, null);
      if (wdAssignments.length === 0) continue;

      var tag2 = "[外勤調整:weekday:" + section + ":" + ym2 + "]";
      for (var j = 0; j < wdAssignments.length; j++) {
        var wa = wdAssignments[j];
        if (String(wa.doctor_id) !== String(doctorId)) continue;
        var eventDate2 = new Date(String(wa.date) + "T00:00:00+09:00");
        var title2 = (wa.slot_name || "") + "（平日）";
        var description2 = tag2 + "\nセクション: " + section
          + "\n医員: " + doc.name
          + "\nスロット: " + (wa.slot_name || "");
        if (createAllDayEvent(calId, title2, eventDate2, description2)) createdCount++;
      }
    }
  }

  Logger.log("個別カレンダー同期完了: " + doc.name + " (" + createdCount + " 件)");
}

/**
 * スケジュール確定時に、対象月・対象医員の個別カレンダーを更新
 * 共有カレンダー同期の後に呼ばれるヘルパー関数
 *
 * @param {Array} doctorIds 更新対象の医員IDリスト
 * @param {Object} doctors getDoctorMap の結果
 * @param {string} tag イベントタグ
 * @param {Array} events [{doctor_id, title, date, description}, ...]
 */
function syncPersonalCalendarEvents(doctorIds, doctors, tag, events) {
  for (var d = 0; d < doctorIds.length; d++) {
    var did = doctorIds[d];
    var doc = doctors[did];
    if (!doc || !doc.notify_calendar || !doc.personal_calendar_id) continue;

    var calId = doc.personal_calendar_id;
    var cal = getCalendarSafe(calId);
    if (!cal) continue;

    // この医員のイベントだけフィルタ
    var myEvents = [];
    for (var i = 0; i < events.length; i++) {
      if (String(events[i].doctor_id) === String(did)) {
        myEvents.push(events[i]);
      }
    }
    if (myEvents.length === 0) continue;

    // タグで古いイベントを削除
    var dates = [];
    for (var j = 0; j < myEvents.length; j++) {
      var dt = String(myEvents[j].date);
      if (dates.indexOf(dt) === -1) dates.push(dt);
    }
    // 月の範囲を算出（全日付が同じ月想定）
    var ym = dates[0].substring(0, 7);
    var range = getMonthRange(ym);
    deleteTaggedEvents(cal, calId, tag, range.start, range.end);

    // イベント作成
    for (var k = 0; k < myEvents.length; k++) {
      var ev = myEvents[k];
      var eventDate = new Date(String(ev.date) + "T00:00:00+09:00");
      createAllDayEvent(calId, ev.title, eventDate, ev.description);
    }
  }
}

// ---- 土曜カレンダー同期 ----

/**
 * 土曜スケジュール確定時にカレンダーへイベントを同期
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

  // 管理者共有カレンダーの既存イベント削除→再作成
  var tag = "[外勤調整:saturday:" + yearMonth + "]";
  var range = getMonthRange(yearMonth);
  deleteTaggedEvents(calendar, calId, tag, range.start, range.end);

  var createdCount = 0;
  var personalEvents = [];
  var personalDoctorIds = [];

  for (var i = 0; i < allAssignments.length; i++) {
    var a = allAssignments[i];
    var doc = doctors[String(a.doctor_id)];
    var clinicName = clinics[String(a.clinic_id)] || "（不明）";
    var doctorName = doc ? doc.name : "（不明）";

    var eventDate = new Date(String(a.date) + "T00:00:00+09:00");
    var title = doctorName + " - " + clinicName;
    var description = tag + "\nセクション: 土曜外勤\n医員: " + doctorName + "\n外勤先: " + clinicName;

    // 管理者共有カレンダーにイベント作成
    if (createAllDayEvent(calId, title, eventDate, description)) createdCount++;

    // 個別カレンダー用データ収集
    if (doc && doc.notify_calendar && doc.personal_calendar_id) {
      var did = String(a.doctor_id);
      if (personalDoctorIds.indexOf(did) === -1) personalDoctorIds.push(did);
      personalEvents.push({
        doctor_id: did,
        title: clinicName + "（土曜）",
        date: String(a.date),
        description: tag + "\n医員: " + doctorName + "\n外勤先: " + clinicName
      });
    }
  }

  Logger.log("土曜カレンダー同期完了: " + createdCount + " 件作成 (" + yearMonth + ")");

  // 個別カレンダー同期
  if (personalEvents.length > 0) {
    syncPersonalCalendarEvents(personalDoctorIds, doctors, tag, personalEvents);
    Logger.log("土曜個別カレンダー同期: " + personalDoctorIds.length + " 名分");
  }
}

// ---- 平日カレンダー同期 ----

/**
 * 平日スケジュール確定時にカレンダーへイベントを同期
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
  var allPersonalEvents = [];
  var allPersonalDoctorIds = [];

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
      var description = tag + "\nセクション: " + section
        + "\n外勤先: " + clinicName
        + "\n医員: " + (a.doctor_name || "")
        + "\nスロット: " + (a.slot_name || "");

      if (createAllDayEvent(calId, title, eventDate, description)) createdCount++;

      // 個別カレンダー用データ収集
      var doc = a.doctor_id ? doctors[String(a.doctor_id)] : null;
      if (doc && doc.notify_calendar && doc.personal_calendar_id) {
        var did = String(a.doctor_id);
        if (allPersonalDoctorIds.indexOf(did) === -1) allPersonalDoctorIds.push(did);
        allPersonalEvents.push({
          doctor_id: did,
          title: (a.slot_name || "") + "（平日）",
          date: String(a.date),
          description: tag + "\nセクション: " + section
            + "\n医員: " + (a.doctor_name || "")
            + "\nスロット: " + (a.slot_name || "")
        });
      }
    }
  }

  Logger.log("平日カレンダー同期完了: " + createdCount + " 件作成 (" + section + ")");

  // 個別カレンダー同期
  if (allPersonalEvents.length > 0) {
    // 月ごとにタグが異なるため、医員ごとに全イベント削除→再作成
    for (var d = 0; d < allPersonalDoctorIds.length; d++) {
      var did2 = allPersonalDoctorIds[d];
      var doc2 = doctors[did2];
      if (!doc2 || !doc2.personal_calendar_id) continue;
      var pCal = getCalendarSafe(doc2.personal_calendar_id);
      if (!pCal) continue;

      for (var m2 = 0; m2 < yearMonths.length; m2++) {
        var ym2 = yearMonths[m2];
        var pTag = "[外勤調整:weekday:" + section + ":" + ym2 + "]";
        var pRange = getMonthRange(ym2);
        deleteTaggedEvents(pCal, doc2.personal_calendar_id, pTag, pRange.start, pRange.end);
      }

      // この医員のイベントを作成
      for (var k = 0; k < allPersonalEvents.length; k++) {
        var ev = allPersonalEvents[k];
        if (ev.doctor_id !== did2) continue;
        var evDate = new Date(String(ev.date) + "T00:00:00+09:00");
        createAllDayEvent(doc2.personal_calendar_id, ev.title, evDate, ev.description);
      }
    }
    Logger.log("平日個別カレンダー同期: " + allPersonalDoctorIds.length + " 名分");
  }
}

/**
 * 平日スケジュール再調整後にカレンダーを更新
 */
function syncWeekdayCalendarReadjusted(data, ssMaster) {
  syncWeekdayCalendar(data, ssMaster);
}

/**
 * シフト交換後にカレンダーを更新
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
  var affectedDoctorIds = [];

  for (var d = 0; d < dates.length; d++) {
    var dateStr = dates[d];
    var ym = dateStr.substring(0, 7);
    var tag = "[外勤調整:weekday:" + section + ":" + ym + "]";

    var dayStart = new Date(dateStr + "T00:00:00+09:00");
    var dayEnd = new Date(dateStr + "T00:00:00+09:00");
    deleteTaggedEvents(calendar, calId, tag, dayStart, dayEnd);

    var assignments = getWeekdayAssignments(ssSec, ym, dateStr);
    for (var i = 0; i < assignments.length; i++) {
      var a = assignments[i];
      var title = (a.doctor_name || "") + " - " + (a.slot_name || "");
      var eventDate = new Date(dateStr + "T00:00:00+09:00");
      var description = tag + "\nセクション: " + section
        + "\n外勤先: " + clinicName
        + "\n医員: " + (a.doctor_name || "")
        + "\nスロット: " + (a.slot_name || "");

      if (createAllDayEvent(calId, title, eventDate, description)) createdCount++;

      // 影響を受けた医員を記録
      if (a.doctor_id) {
        var did = String(a.doctor_id);
        if (affectedDoctorIds.indexOf(did) === -1) affectedDoctorIds.push(did);
      }
    }
  }

  Logger.log("シフト交換カレンダー同期完了: " + createdCount + " 件作成");

  // 影響を受けた医員の個別カレンダーを更新
  for (var j = 0; j < affectedDoctorIds.length; j++) {
    var doc = doctors[affectedDoctorIds[j]];
    if (!doc || !doc.notify_calendar || !doc.personal_calendar_id) continue;
    var pCal = getCalendarSafe(doc.personal_calendar_id);
    if (!pCal) continue;

    for (var d2 = 0; d2 < dates.length; d2++) {
      var dateStr2 = dates[d2];
      var ym2 = dateStr2.substring(0, 7);
      var pTag = "[外勤調整:weekday:" + section + ":" + ym2 + "]";
      var pDayStart = new Date(dateStr2 + "T00:00:00+09:00");
      var pDayEnd = new Date(dateStr2 + "T00:00:00+09:00");
      deleteTaggedEvents(pCal, doc.personal_calendar_id, pTag, pDayStart, pDayEnd);

      // この医員のこの日の割り当てを再作成
      var wdAssignments = getWeekdayAssignments(ssSec, ym2, dateStr2);
      for (var k = 0; k < wdAssignments.length; k++) {
        var wa = wdAssignments[k];
        if (String(wa.doctor_id) !== affectedDoctorIds[j]) continue;
        var evDate = new Date(dateStr2 + "T00:00:00+09:00");
        var evTitle = (wa.slot_name || "") + "（平日）";
        var evDesc = pTag + "\nセクション: " + section
          + "\n医員: " + doc.name
          + "\nスロット: " + (wa.slot_name || "");
        createAllDayEvent(doc.personal_calendar_id, evTitle, evDate, evDesc);
      }
    }
  }
}

// ---- 医員単位のカレンダー再同期 ----

/**
 * 医員がカレンダー連携を有効/無効にしたときに呼ばれる
 *
 * enabled=true: 個別カレンダーを作成し、確定済みスケジュールを同期
 * enabled=false: 個別カレンダーを削除
 */
function resyncCalendarForDoctor(data) {
  var ssMaster = getMasterSpreadsheet();
  var doctorId = String(data.doctor_id);
  var doctorName = String(data.doctor_name || "");
  var doctorEmail = String(data.doctor_email || "");
  var enabled = data.enabled === true || data.enabled === "true";

  if (!doctorEmail) {
    Logger.log("カレンダー再同期: メールアドレスなし (doctor_id=" + doctorId + ")");
    return;
  }

  var doctors = getDoctorMap(ssMaster);
  var doc = doctors[doctorId];
  if (!doc) {
    Logger.log("カレンダー再同期: 医員不明 (doctor_id=" + doctorId + ")");
    return;
  }
  // doctorName が未指定の場合はマスタから取得
  if (!doctorName) doctorName = doc.name;

  if (enabled) {
    // 既存カレンダーがあればスキップ（再同期のみ）
    var existingCalId = doc.personal_calendar_id;
    if (existingCalId) {
      var existingCal = getCalendarSafe(existingCalId);
      if (existingCal) {
        Logger.log("既存カレンダーを再同期: " + existingCalId);
        syncPersonalCalendarForDoctor(doctorId, ssMaster);
        return;
      }
      // カレンダーが見つからない場合は新規作成
    }

    // カレンダー作成
    var newCalId = createPersonalCalendar(doctorName, doctorEmail);
    if (!newCalId) {
      Logger.log("カレンダー作成失敗: " + doctorName);
      return;
    }
    savePersonalCalendarId(doctorId, newCalId, ssMaster);

    // getDoctorMap のキャッシュを更新するため再取得は不要
    // saveした後、直接calIdを使って同期
    // syncPersonalCalendarForDoctor は getDoctorMap を読むので、
    // personal_calendar_id がまだキャッシュに反映されていない可能性
    // → 直接同期ロジックを実行
    var settings = getCalendarSettings(ssMaster);
    var clinics = getClinicMap(ssMaster);
    var createdCount = 0;
    var now = new Date();

    // 土曜
    var ssOp = getOperationalSpreadsheet();
    for (var mOff = 0; mOff < 4; mOff++) {
      var dt = new Date(now.getFullYear(), now.getMonth() + mOff, 1);
      var ym = Utilities.formatDate(dt, "Asia/Tokyo", "yyyy-MM");
      var schedSheet = getSheet(ssOp, "スケジュール_" + ym);
      if (!schedSheet) continue;
      var assignments = getConfirmedAssignments(schedSheet, null);
      var tag = "[外勤調整:saturday:" + ym + "]";
      for (var i = 0; i < assignments.length; i++) {
        var a = assignments[i];
        if (String(a.doctor_id) !== doctorId) continue;
        var clinicNameS = clinics[String(a.clinic_id)] || "（不明）";
        var eventDate = new Date(String(a.date) + "T00:00:00+09:00");
        if (createAllDayEvent(newCalId, clinicNameS + "（土曜）", eventDate,
          tag + "\n医員: " + doctorName + "\n外勤先: " + clinicNameS)) createdCount++;
      }
    }

    // 平日
    for (var key in settings) {
      if (key.indexOf("calendar_id_weekday_") !== 0 || !settings[key]) continue;
      var section = key.replace("calendar_id_weekday_", "");
      var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
      if (!ssSec) continue;
      for (var mOff2 = 0; mOff2 < 13; mOff2++) {
        var dt2 = new Date(now.getFullYear(), now.getMonth() + mOff2, 1);
        var ym2 = Utilities.formatDate(dt2, "Asia/Tokyo", "yyyy-MM");
        var wdAssignments = getWeekdayAssignments(ssSec, ym2, null);
        var tag2 = "[外勤調整:weekday:" + section + ":" + ym2 + "]";
        for (var j = 0; j < wdAssignments.length; j++) {
          var wa = wdAssignments[j];
          if (String(wa.doctor_id) !== doctorId) continue;
          var evDate = new Date(String(wa.date) + "T00:00:00+09:00");
          if (createAllDayEvent(newCalId, (wa.slot_name || "") + "（平日）", evDate,
            tag2 + "\nセクション: " + section + "\n医員: " + doctorName
            + "\nスロット: " + (wa.slot_name || ""))) createdCount++;
        }
      }
    }

    Logger.log("個別カレンダー作成＋同期完了: " + doctorName + " (" + createdCount + " 件)");

  } else {
    // 無効化: カレンダー削除
    var calIdToDelete = doc.personal_calendar_id;
    if (calIdToDelete) {
      deletePersonalCalendar(calIdToDelete);
      savePersonalCalendarId(doctorId, "", ssMaster);
    }
    Logger.log("カレンダー連携無効化: " + doctorName);
  }
}

/**
 * 全医員の個別カレンダーを一括再同期
 * 管理者がカレンダー設定を保存した際に呼ばれる
 */
function resyncCalendarForAllDoctors() {
  var ssMaster = getMasterSpreadsheet();
  var doctors = getDoctorMap(ssMaster);

  for (var id in doctors) {
    var doc = doctors[id];
    if (doc.notify_calendar && doc.personal_calendar_id) {
      // 既存カレンダーを再同期
      syncPersonalCalendarForDoctor(id, ssMaster);
      Utilities.sleep(500); // レートリミット対策
    }
  }

  Logger.log("全医員カレンダー再同期完了");
}

// ---- テスト用 ----

function testSyncSaturdayCalendar() {
  var ssMaster = getMasterSpreadsheet();
  var yearMonth = "2026-04";
  Logger.log("テスト実行: syncSaturdayCalendar(" + yearMonth + ")");
  syncSaturdayCalendar(yearMonth, ssMaster);
}

function testSyncWeekdayCalendar() {
  var ssMaster = getMasterSpreadsheet();
  var configs = getWeekdayConfigs(ssMaster);
  for (var i = 0; i < configs.length; i++) {
    var cfg = configs[i];
    if (!cfg.is_active) continue;
    var yearMonths = [];
    for (var m = 4; m <= 12; m++) {
      yearMonths.push("2026-" + (m < 10 ? "0" : "") + m);
    }
    for (var m2 = 1; m2 <= 3; m2++) {
      yearMonths.push("2027-" + (m2 < 10 ? "0" : "") + m2);
    }
    Logger.log("テスト実行: syncWeekdayCalendar(" + cfg.section + ", " + yearMonths.length + "ヶ月)");
    syncWeekdayCalendar({
      section: cfg.section,
      clinic_name: cfg.clinic_name || "",
      year_months: yearMonths
    }, ssMaster);
  }
}
