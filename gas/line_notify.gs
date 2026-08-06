/**
 * LINE Bot Push通知（リマインダー・確定通知）
 *
 * 既存の saturday.gs のメール通知と連携し、
 * LINE連携済み医員にはメール＋LINEの両方で通知する。
 */

// ---- リマインダー（Push API） ----

/**
 * LINE連携済み医員に希望入力リマインダーを送信
 * 既存の checkDeadline() から呼ばれる想定
 *
 * @param {string} yearMonth 対象月 (YYYY-MM)
 * @param {Object} doctorMap getDoctorMap() の戻り値
 * @param {string[]} submittedIds 入力済み医員IDリスト
 */
function sendLineDeadlineReminder(yearMonth, doctorMap, submittedIds) {
  var submittedSet = {};
  submittedIds.forEach(function(id) { submittedSet[id] = true; });

  var monthLabel = yearMonth.replace("-", "年") + "月";

  for (var id in doctorMap) {
    var doc = doctorMap[id];
    var lineUserId = String(doc.line_user_id || "").trim();
    if (!lineUserId) continue;  // LINE未連携はスキップ

    var status = submittedSet[id] ? "入力済み ✓" : "未入力";
    var text = "【希望入力リマインダー】\n" +
      monthLabel + " の希望入力状況: " + status + "\n";

    if (!submittedSet[id]) {
      text += "\nメニューの「希望入力」から入力をお願いします。";
    }

    pushText(lineUserId, text);
  }
}

/**
 * LINE連携済み医員に金曜リマインダーを送信
 * 既存の sendFridayReminder() から呼ばれる想定
 *
 * @param {Object[]} assignments [{doctor_id, clinic_id, date}, ...]
 * @param {Object} doctorMap getDoctorMap() の戻り値
 * @param {Object} clinicMap getClinicMap() の戻り値
 */
function sendLineFridayReminder(assignments, doctorMap, clinicMap) {
  // 医員ごとにグループ化
  var byDoctor = {};
  assignments.forEach(function(a) {
    if (!byDoctor[a.doctor_id]) byDoctor[a.doctor_id] = [];
    byDoctor[a.doctor_id].push(a);
  });

  for (var doctorId in byDoctor) {
    var doc = doctorMap[doctorId];
    if (!doc) continue;
    var lineUserId = String(doc.line_user_id || "").trim();
    if (!lineUserId) continue;

    var lines = ["【外勤リマインダー】\n明日は外勤の予定があります。\n"];
    byDoctor[doctorId].forEach(function(a) {
      var clinicName = clinicMap[String(a.clinic_id)] || "不明";
      var dateLabel = formatDateLabel(a.date);
      lines.push("  " + dateLabel + " : " + clinicName);
    });

    pushText(lineUserId, lines.join("\n"));
  }
}

// ---- スケジュール確定通知 ----

/**
 * LINE連携済み医員にスケジュール確定を通知
 * 既存の sendConfirmationEmails() から呼ばれる想定
 *
 * @param {string} yearMonth 対象月 (YYYY-MM)
 * @param {Object[]} allAssignments 全医員の割り当て [{doctor_id, clinic_id, date}, ...]
 * @param {Object} doctorMap getDoctorMap() の戻り値
 * @param {Object} clinicMap getClinicMap() の戻り値
 * @param {string} [scheduleImageUrl] スケジュール画像のURL（Googleドライブ）
 */
function sendLineScheduleConfirmed(yearMonth, allAssignments, doctorMap, clinicMap, scheduleImageUrl) {
  var monthLabel = yearMonth.replace("-", "年") + "月";

  // 医員ごとにグループ化
  var byDoctor = {};
  allAssignments.forEach(function(a) {
    if (!byDoctor[a.doctor_id]) byDoctor[a.doctor_id] = [];
    byDoctor[a.doctor_id].push(a);
  });

  for (var doctorId in byDoctor) {
    var doc = doctorMap[doctorId];
    if (!doc) continue;
    var lineUserId = String(doc.line_user_id || "").trim();
    if (!lineUserId) continue;

    // テキストメッセージ: 自分の割り当て一覧
    var lines = ["【スケジュール確定】" + monthLabel + "\n"];
    var sorted = byDoctor[doctorId].sort(function(a, b) {
      return a.date > b.date ? 1 : -1;
    });
    sorted.forEach(function(a) {
      var clinicName = clinicMap[String(a.clinic_id)] || "不明";
      lines.push("  " + formatDateLabel(a.date) + " : " + clinicName);
    });

    var messages = [{"type": "text", "text": lines.join("\n")}];

    // スケジュール画像がある場合は追加
    if (scheduleImageUrl) {
      messages.push({
        "type": "image",
        "originalContentUrl": scheduleImageUrl,
        "previewImageUrl": scheduleImageUrl
      });
    }

    pushMessage(lineUserId, messages);
  }
}
