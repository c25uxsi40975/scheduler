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
  var allConfigs = getWeekdayConfigs(ssMaster);
  for (var key in settings) {
    if (key.indexOf("calendar_id_weekday_") !== 0 || !settings[key]) continue;
    var section = key.replace("calendar_id_weekday_", "");
    var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
    if (!ssSec) continue;

    // このセクションの検体確認設定を取得
    var secCfg = null;
    for (var ci2 = 0; ci2 < allConfigs.length; ci2++) {
      if (allConfigs[ci2].section === section) { secCfg = allConfigs[ci2]; break; }
    }

    // 今月から12ヶ月先まで（平日は年度単位のため）
    for (var mOff2 = 0; mOff2 < 13; mOff2++) {
      var d2 = new Date(now.getFullYear(), now.getMonth() + mOff2, 1);
      var ym2 = Utilities.formatDate(d2, "Asia/Tokyo", "yyyy-MM");
      var wdAssignments = getWeekdayAssignments(ssSec, ym2, null);
      if (wdAssignments.length === 0) continue;

      // 検体確認担当を週単位で判定
      var specByDate2 = secCfg ? buildSpecimenByDate(secCfg, wdAssignments, doctors) : {};

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

      // 検体確認を別イベントとして作成（自分が担当の日のみ）
      for (var spd2 in specByDate2) {
        var sp2 = specByDate2[spd2];
        if (String(sp2.doctorId) !== String(doctorId)) continue;
        var spDate2 = new Date(spd2 + "T00:00:00+09:00");
        var spTitle2, spDesc2;
        if (sp2.conflict) {
          var pLabels2 = buildConflictLabels(sp2, doctorId);
          spTitle2 = "🧪 同意書・検体確認（" + pLabels2.join("・") + "と相談してください）";
          spDesc2 = tag2 + "\n同意書・検体確認\n※同じ優先順位のため" + pLabels2.join("・") + "と相談してください";
        } else {
          spTitle2 = "🧪 同意書・検体確認";
          spDesc2 = tag2 + "\n同意書・検体確認";
        }
        if (createAllDayEvent(calId, spTitle2, spDate2, spDesc2)) createdCount++;
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

/**
 * 土曜スケジュール確定解除時にカレンダーイベントを削除
 * 共有カレンダーおよび個別カレンダーから該当月のイベントを削除
 */
function clearSaturdayCalendar(yearMonth) {
  var ssMaster = getMasterSpreadsheet();
  var settings = getCalendarSettings(ssMaster);
  var calId = settings["calendar_id_saturday"];
  var calendar = getCalendarSafe(calId);

  var tag = "[外勤調整:saturday:" + yearMonth + "]";
  var range = getMonthRange(yearMonth);

  // 共有カレンダーのイベント削除
  if (calendar) {
    deleteTaggedEvents(calendar, calId, tag, range.start, range.end);
    Logger.log("土曜カレンダー削除完了（共有）: " + yearMonth);
  }

  // 個別カレンダーのイベント削除
  var doctors = getDoctorMap(ssMaster);
  var deletedCount = 0;
  for (var did in doctors) {
    var doc = doctors[did];
    if (doc.notify_calendar && doc.personal_calendar_id) {
      var pCal = getCalendarSafe(doc.personal_calendar_id);
      if (pCal) {
        deleteTaggedEvents(pCal, doc.personal_calendar_id, tag, range.start, range.end);
        deletedCount++;
      }
    }
  }
  Logger.log("土曜カレンダー削除完了（個別）: " + deletedCount + " 名分 (" + yearMonth + ")");
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

  // 検体確認設定を取得
  var configs = getWeekdayConfigs(ssMaster);
  var cfg = null;
  for (var ci = 0; ci < configs.length; ci++) {
    if (configs[ci].section === section) { cfg = configs[ci]; break; }
  }

  for (var m = 0; m < yearMonths.length; m++) {
    var ym = yearMonths[m];
    var tag = "[外勤調整:weekday:" + section + ":" + ym + "]";
    var range = getMonthRange(ym);
    deleteTaggedEvents(calendar, calId, tag, range.start, range.end);

    var assignments = getWeekdayAssignments(ssSec, ym, null);

    // 検体確認担当を週単位で判定（buildSpecimenByDate で統一）
    var specimenByDate = cfg ? buildSpecimenByDate(cfg, assignments, doctors) : {};
    Logger.log("検体確認結果: " + ym + " → " + Object.keys(specimenByDate).length + " 日");

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

    // 検体確認を別イベントとして作成（同じカレンダー・同じタグ）
    for (var sd in specimenByDate) {
      var sp = specimenByDate[sd];
      var spDate = new Date(sd + "T00:00:00+09:00");
      var spTitle, spDesc;
      if (sp.conflict) {
        var spLabels = buildConflictLabels(sp, sp.doctorId);
        spTitle = "🧪 同意書・検体確認 - " + sp.doctorName + "（" + spLabels.join("・") + "と相談してください）";
        spDesc = tag + "\n同意書・検体確認\n担当: " + sp.doctorName
          + "\n※同じ優先順位のため" + spLabels.join("・") + "と相談してください";
      } else {
        spTitle = "🧪 同意書・検体確認 - " + sp.doctorName;
        spDesc = tag + "\n同意書・検体確認\n担当: " + sp.doctorName;
      }
      if (createAllDayEvent(calId, spTitle, spDate, spDesc)) createdCount++;

      // 個別カレンダー用データ収集（担当医員のみ）
      var spDoc = doctors[String(sp.doctorId)];
      if (spDoc && spDoc.notify_calendar && spDoc.personal_calendar_id) {
        var spDid = String(sp.doctorId);
        if (allPersonalDoctorIds.indexOf(spDid) === -1) allPersonalDoctorIds.push(spDid);
        var pSpTitle, pSpDesc;
        if (sp.conflict) {
          var pSpLabels = buildConflictLabels(sp, spDid);
          pSpTitle = "🧪 同意書・検体確認（" + pSpLabels.join("・") + "と相談してください）";
          pSpDesc = tag + "\n同意書・検体確認\n※同じ優先順位のため" + pSpLabels.join("・") + "と相談してください";
        } else {
          pSpTitle = "🧪 同意書・検体確認";
          pSpDesc = tag + "\n同意書・検体確認";
        }
        allPersonalEvents.push({
          doctor_id: spDid,
          title: pSpTitle,
          date: sd,
          description: pSpDesc
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

  // 検体確認設定を取得
  var swConfigs = getWeekdayConfigs(ssMaster);
  var swCfg = null;
  for (var swci = 0; swci < swConfigs.length; swci++) {
    if (swConfigs[swci].section === section) { swCfg = swConfigs[swci]; break; }
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

    // 週単位で検体確認担当を判定（月全体のassignmentsが必要）
    var allMonthAssignments = getWeekdayAssignments(ssSec, ym, null);
    var swSpecResult = swCfg ? getSpecimenAssignee(swCfg, dateStr, allMonthAssignments, doctors) : null;

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

    // 検体確認を別イベントとして作成（共有カレンダー）
    if (swSpecResult) {
      var swSpDate = new Date(dateStr + "T00:00:00+09:00");
      var swSpTitle, swSpDesc;
      if (swSpecResult.conflict) {
        var swLabels = buildConflictLabels(swSpecResult, swSpecResult.doctorId);
        swSpTitle = "🧪 同意書・検体確認 - " + swSpecResult.doctorName + "（" + swLabels.join("・") + "と相談してください）";
        swSpDesc = tag + "\n同意書・検体確認\n担当: " + swSpecResult.doctorName
          + "\n※同じ優先順位のため" + swLabels.join("・") + "と相談してください";
      } else {
        swSpTitle = "🧪 同意書・検体確認 - " + swSpecResult.doctorName;
        swSpDesc = tag + "\n同意書・検体確認\n担当: " + swSpecResult.doctorName;
      }
      if (createAllDayEvent(calId, swSpTitle, swSpDate, swSpDesc)) createdCount++;

      // 担当医員を影響リストに追加
      var swSpDid = String(swSpecResult.doctorId);
      if (affectedDoctorIds.indexOf(swSpDid) === -1) affectedDoctorIds.push(swSpDid);
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

      // 検体確認を別イベントとして作成（個別カレンダー、自分が担当の場合のみ）
      var pSwAllMonth = getWeekdayAssignments(ssSec, ym2, null);
      var pSwSpec = swCfg ? getSpecimenAssignee(swCfg, dateStr2, pSwAllMonth, doctors) : null;
      if (pSwSpec && String(pSwSpec.doctorId) === affectedDoctorIds[j]) {
        var pSwDate = new Date(dateStr2 + "T00:00:00+09:00");
        var pSwTitle, pSwDesc;
        if (pSwSpec.conflict) {
          var pSwLabels = buildConflictLabels(pSwSpec, affectedDoctorIds[j]);
          pSwTitle = "🧪 同意書・検体確認（" + pSwLabels.join("・") + "と相談してください）";
          pSwDesc = pTag + "\n同意書・検体確認\n※同じ優先順位のため" + pSwLabels.join("・") + "と相談してください";
        } else {
          pSwTitle = "🧪 同意書・検体確認";
          pSwDesc = pTag + "\n同意書・検体確認";
        }
        createAllDayEvent(doc.personal_calendar_id, pSwTitle, pSwDate, pSwDesc);
      }
    }
  }
}

/**
 * 平日シフト変更時のカレンダー同期
 *
 * 変更対象の1日のみタグ付きイベントを削除→再作成し、
 * 変更元・変更先の個別カレンダーも更新する。
 * （構造はsyncShiftSwapCalendarと同様だが対象日が1日に限定）
 */
function syncShiftChangeCalendar(data, ssMaster) {
  var settings = getCalendarSettings(ssMaster);
  var section = data.section;
  var clinicName = data.clinic_name || "";
  var calId = settings["calendar_id_weekday_" + section];
  var calendar = getCalendarSafe(calId);
  if (!calendar) return;

  var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
  if (!ssSec) return;

  var doctors = getDoctorMap(ssMaster);

  var dateStr = data.date;
  if (!dateStr) {
    Logger.log("シフト変更カレンダー同期: 日付が指定されていません");
    return;
  }
  var ym = dateStr.substring(0, 7);
  var tag = "[外勤調整:weekday:" + section + ":" + ym + "]";

  // 検体確認設定を取得
  var swConfigs = getWeekdayConfigs(ssMaster);
  var swCfg = null;
  for (var swci = 0; swci < swConfigs.length; swci++) {
    if (swConfigs[swci].section === section) { swCfg = swConfigs[swci]; break; }
  }

  var dayStart = new Date(dateStr + "T00:00:00+09:00");
  var dayEnd = new Date(dateStr + "T00:00:00+09:00");
  deleteTaggedEvents(calendar, calId, tag, dayStart, dayEnd);

  var assignments = getWeekdayAssignments(ssSec, ym, dateStr);
  var allMonthAssignments = getWeekdayAssignments(ssSec, ym, null);
  var swSpecResult = swCfg ? getSpecimenAssignee(swCfg, dateStr, allMonthAssignments, doctors) : null;

  var createdCount = 0;
  for (var i = 0; i < assignments.length; i++) {
    var a = assignments[i];
    var title = (a.doctor_name || "") + " - " + (a.slot_name || "");
    var eventDate = new Date(dateStr + "T00:00:00+09:00");
    var description = tag + "\nセクション: " + section
      + "\n外勤先: " + clinicName
      + "\n医員: " + (a.doctor_name || "")
      + "\nスロット: " + (a.slot_name || "");
    if (createAllDayEvent(calId, title, eventDate, description)) createdCount++;
  }

  if (swSpecResult) {
    var swSpDate = new Date(dateStr + "T00:00:00+09:00");
    var swSpTitle, swSpDesc;
    if (swSpecResult.conflict) {
      var swLabels = buildConflictLabels(swSpecResult, swSpecResult.doctorId);
      swSpTitle = "🧪 同意書・検体確認 - " + swSpecResult.doctorName + "（" + swLabels.join("・") + "と相談してください）";
      swSpDesc = tag + "\n同意書・検体確認\n担当: " + swSpecResult.doctorName
        + "\n※同じ優先順位のため" + swLabels.join("・") + "と相談してください";
    } else {
      swSpTitle = "🧪 同意書・検体確認 - " + swSpecResult.doctorName;
      swSpDesc = tag + "\n同意書・検体確認\n担当: " + swSpecResult.doctorName;
    }
    if (createAllDayEvent(calId, swSpTitle, swSpDate, swSpDesc)) createdCount++;
  }

  Logger.log("シフト変更カレンダー同期完了: " + createdCount + " 件作成");

  // 影響を受けた2医員（変更元・変更先）の個別カレンダーを更新
  var affectedIds = [];
  if (data.original_doctor_id) affectedIds.push(String(data.original_doctor_id));
  if (data.new_doctor_id) {
    var newId = String(data.new_doctor_id);
    if (affectedIds.indexOf(newId) === -1) affectedIds.push(newId);
  }

  for (var j = 0; j < affectedIds.length; j++) {
    var doc = doctors[affectedIds[j]];
    if (!doc || !doc.notify_calendar || !doc.personal_calendar_id) continue;
    var pCal = getCalendarSafe(doc.personal_calendar_id);
    if (!pCal) continue;

    var pTag = "[外勤調整:weekday:" + section + ":" + ym + "]";
    var pDayStart = new Date(dateStr + "T00:00:00+09:00");
    var pDayEnd = new Date(dateStr + "T00:00:00+09:00");
    deleteTaggedEvents(pCal, doc.personal_calendar_id, pTag, pDayStart, pDayEnd);

    for (var k = 0; k < assignments.length; k++) {
      var wa = assignments[k];
      if (String(wa.doctor_id) !== affectedIds[j]) continue;
      var evDate = new Date(dateStr + "T00:00:00+09:00");
      var evTitle = (wa.slot_name || "") + "（平日）";
      var evDesc = pTag + "\nセクション: " + section
        + "\n医員: " + doc.name
        + "\nスロット: " + (wa.slot_name || "");
      createAllDayEvent(doc.personal_calendar_id, evTitle, evDate, evDesc);
    }

    if (swSpecResult && String(swSpecResult.doctorId) === affectedIds[j]) {
      var pSwDate = new Date(dateStr + "T00:00:00+09:00");
      var pSwTitle, pSwDesc;
      if (swSpecResult.conflict) {
        var pSwLabels = buildConflictLabels(swSpecResult, affectedIds[j]);
        pSwTitle = "🧪 同意書・検体確認（" + pSwLabels.join("・") + "と相談してください）";
        pSwDesc = pTag + "\n同意書・検体確認\n※同じ優先順位のため" + pSwLabels.join("・") + "と相談してください";
      } else {
        pSwTitle = "🧪 同意書・検体確認";
        pSwDesc = pTag + "\n同意書・検体確認";
      }
      createAllDayEvent(doc.personal_calendar_id, pSwTitle, pSwDate, pSwDesc);
    }
  }
}

/**
 * 土曜シフト変更時のカレンダー同期
 *
 * 変更対象の1日のみタグ付きイベントを削除→再作成し、
 * 影響を受けた2医員（変更前・変更後）の個別カレンダーも更新する。
 */
function syncSaturdayShiftChangeCalendar(data, ssMaster) {
  var settings = getCalendarSettings(ssMaster);
  var calId = settings["calendar_id_saturday"];
  var calendar = getCalendarSafe(calId);
  if (!calendar) return;

  var yearMonth = data.year_month;
  var changeDate = data.date;
  if (!yearMonth || !changeDate) {
    Logger.log("土曜シフト変更カレンダー同期: year_month/date なし");
    return;
  }

  var ssOp = getOperationalSpreadsheet();
  var schedSheet = getSheet(ssOp, "スケジュール_" + yearMonth);
  if (!schedSheet) {
    Logger.log("土曜シフト変更カレンダー同期: スケジュールシートなし: " + yearMonth);
    return;
  }

  var doctors = getDoctorMap(ssMaster);
  var clinics = getClinicMap(ssMaster);

  var tag = "[外勤調整:saturday:" + yearMonth + "]";
  var dayStart = new Date(changeDate + "T00:00:00+09:00");
  var dayEnd = new Date(changeDate + "T00:00:00+09:00");

  // 共有カレンダー: その日のタグ付きイベント削除→再作成
  deleteTaggedEvents(calendar, calId, tag, dayStart, dayEnd);

  var dayAssignments = getConfirmedAssignments(schedSheet, changeDate);
  var createdCount = 0;
  for (var i = 0; i < dayAssignments.length; i++) {
    var a = dayAssignments[i];
    var doc = doctors[String(a.doctor_id)];
    var clinicName = clinics[String(a.clinic_id)] || "（不明）";
    var doctorName = doc ? doc.name : "（不明）";
    var title = doctorName + " - " + clinicName;
    var description = tag + "\nセクション: 土曜外勤\n医員: " + doctorName + "\n外勤先: " + clinicName;
    if (createAllDayEvent(calId, title, new Date(changeDate + "T00:00:00+09:00"), description)) {
      createdCount++;
    }
  }
  Logger.log("土曜シフト変更カレンダー同期完了: " + createdCount + " 件作成 (" + changeDate + ")");

  // 影響を受けた医員（変更前・変更後）の個別カレンダー更新
  var affectedIds = [String(data.original_doctor_id), String(data.new_doctor_id)];
  for (var j = 0; j < affectedIds.length; j++) {
    var doc2 = doctors[affectedIds[j]];
    if (!doc2 || !doc2.notify_calendar || !doc2.personal_calendar_id) continue;
    var pCal = getCalendarSafe(doc2.personal_calendar_id);
    if (!pCal) continue;

    deleteTaggedEvents(pCal, doc2.personal_calendar_id, tag, dayStart, dayEnd);

    // この医員のこの日の割り当てを再作成
    for (var k = 0; k < dayAssignments.length; k++) {
      if (String(dayAssignments[k].doctor_id) !== affectedIds[j]) continue;
      var cName = clinics[String(dayAssignments[k].clinic_id)] || "（不明）";
      var evTitle = cName + "（土曜）";
      var evDesc = tag + "\n医員: " + doc2.name + "\n外勤先: " + cName;
      createAllDayEvent(doc2.personal_calendar_id, evTitle,
                        new Date(changeDate + "T00:00:00+09:00"), evDesc);
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
    var rAllConfigs = getWeekdayConfigs(ssMaster);
    for (var key in settings) {
      if (key.indexOf("calendar_id_weekday_") !== 0 || !settings[key]) continue;
      var section = key.replace("calendar_id_weekday_", "");
      var ssSec = getWeekdaySectionSpreadsheet(ssMaster, section);
      if (!ssSec) continue;

      // このセクションの検体確認設定を取得
      var rSecCfg = null;
      for (var rci = 0; rci < rAllConfigs.length; rci++) {
        if (rAllConfigs[rci].section === section) { rSecCfg = rAllConfigs[rci]; break; }
      }

      for (var mOff2 = 0; mOff2 < 13; mOff2++) {
        var dt2 = new Date(now.getFullYear(), now.getMonth() + mOff2, 1);
        var ym2 = Utilities.formatDate(dt2, "Asia/Tokyo", "yyyy-MM");
        var wdAssignments = getWeekdayAssignments(ssSec, ym2, null);
        if (wdAssignments.length === 0) continue;

        // 検体確認担当を週単位で判定
        var rSpecByDate = rSecCfg ? buildSpecimenByDate(rSecCfg, wdAssignments, doctors) : {};

        var tag2 = "[外勤調整:weekday:" + section + ":" + ym2 + "]";
        for (var j = 0; j < wdAssignments.length; j++) {
          var wa = wdAssignments[j];
          if (String(wa.doctor_id) !== doctorId) continue;
          var evDate = new Date(String(wa.date) + "T00:00:00+09:00");
          if (createAllDayEvent(newCalId, (wa.slot_name || "") + "（平日）", evDate,
            tag2 + "\nセクション: " + section + "\n医員: " + doctorName
            + "\nスロット: " + (wa.slot_name || ""))) createdCount++;
        }

        // 検体確認を別イベントとして作成（自分が担当の日のみ）
        for (var rsd in rSpecByDate) {
          var rsp = rSpecByDate[rsd];
          if (String(rsp.doctorId) !== doctorId) continue;
          var rSpDate = new Date(rsd + "T00:00:00+09:00");
          var rSpTitle, rSpDesc;
          if (rsp.conflict) {
            var rLabels = buildConflictLabels(rsp, doctorId);
            rSpTitle = "🧪 同意書・検体確認（" + rLabels.join("・") + "と相談してください）";
            rSpDesc = tag2 + "\n同意書・検体確認\n※同じ優先順位のため" + rLabels.join("・") + "と相談してください";
          } else {
            rSpTitle = "🧪 同意書・検体確認";
            rSpDesc = tag2 + "\n同意書・検体確認";
          }
          if (createAllDayEvent(newCalId, rSpTitle, rSpDate, rSpDesc)) createdCount++;
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

// ---- カレンダー共有管理 ----

/**
 * 医員個別カレンダーの共有先を更新する
 * 新しいメールリストと現在のACLを比較し、追加・削除を行う
 * @param {string} calendarId - 個別カレンダーID
 * @param {string[]} newEmails - 新しい共有先メールアドレスの配列
 * @param {string} ownerEmail - 医員本人のメールアドレス（除外対象）
 */
function updateCalendarSharing(calendarId, newEmails, ownerEmail) {
  if (!calendarId) {
    Logger.log("カレンダー共有更新: カレンダーIDが未設定");
    return;
  }

  // 現在のACLを取得
  var currentViewers = [];
  try {
    var acl = Calendar.Acl.list(calendarId);
    var items = acl.items || [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (item.role === "reader" && item.scope && item.scope.type === "user") {
        var email = item.scope.value.toLowerCase();
        // 医員本人は管理対象外
        if (email !== ownerEmail.toLowerCase()) {
          currentViewers.push({ email: email, ruleId: item.id });
        }
      }
    }
  } catch (e) {
    Logger.log("ACL取得失敗: " + calendarId + " - " + e.message);
    return;
  }

  // 正規化
  var newSet = {};
  for (var j = 0; j < newEmails.length; j++) {
    var em = newEmails[j].trim().toLowerCase();
    if (em) newSet[em] = true;
  }

  var currentSet = {};
  for (var k = 0; k < currentViewers.length; k++) {
    currentSet[currentViewers[k].email] = currentViewers[k].ruleId;
  }

  // 削除: 現在あるが新リストにないもの
  for (var removeEmail in currentSet) {
    if (!newSet[removeEmail]) {
      try {
        Calendar.Acl.remove(calendarId, currentSet[removeEmail]);
        Logger.log("カレンダー共有解除: " + removeEmail);
      } catch (e) {
        Logger.log("カレンダー共有解除失敗: " + removeEmail + " - " + e.message);
      }
    }
  }

  // 追加: 新リストにあるが現在ないもの（招待メールなし）
  for (var addEmail in newSet) {
    if (!currentSet[addEmail]) {
      try {
        Calendar.Acl.insert({
          role: "reader",
          scope: { type: "user", value: addEmail },
          sendNotifications: false
        }, calendarId);
        Logger.log("カレンダー共有追加: " + addEmail);
      } catch (e) {
        Logger.log("カレンダー共有追加失敗: " + addEmail + " - " + e.message);
        // フォールバック
        try {
          var cal = CalendarApp.getCalendarById(calendarId);
          if (cal) cal.addViewer(addEmail);
        } catch (e2) {
          Logger.log("カレンダー共有追加失敗 (フォールバック): " + addEmail + " - " + e2.message);
        }
      }
    }
  }
}

/**
 * Streamlitからのカレンダー共有更新リクエストを処理
 */
function handleCalendarSharingUpdate(data) {
  var calendarId = data.calendar_id || "";
  var emails = data.shared_emails || [];
  var ownerEmail = data.owner_email || "";

  if (!calendarId) {
    Logger.log("カレンダー共有更新: カレンダーIDが未指定");
    return;
  }

  updateCalendarSharing(calendarId, emails, ownerEmail);
  Logger.log("カレンダー共有更新完了: " + emails.length + "件");
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
  // 今月から12ヶ月先までを動的に生成
  var now = new Date();
  var yearMonths = [];
  for (var mOff = 0; mOff < 13; mOff++) {
    var d = new Date(now.getFullYear(), now.getMonth() + mOff, 1);
    yearMonths.push(Utilities.formatDate(d, "Asia/Tokyo", "yyyy-MM"));
  }
  for (var i = 0; i < configs.length; i++) {
    var cfg = configs[i];
    if (!cfg.is_active) continue;
    Logger.log("テスト実行: syncWeekdayCalendar(" + cfg.section + ", " + yearMonths.length + "ヶ月, 開始=" + yearMonths[0] + ")");
    syncWeekdayCalendar({
      section: cfg.section,
      clinic_name: cfg.clinic_name || "",
      year_months: yearMonths
    }, ssMaster);
  }
}
