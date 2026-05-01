/**
 * LINE Bot Webhook 受信 + メッセージ処理
 *
 * 既存の common.gs の doPost() とは別の Web App としてデプロイする。
 * LINE Developers Console の Webhook URL にはこちらの URL を設定する。
 *
 * 注意: GAS プロジェクト内に doPost が2つあるとエラーになるため、
 * LINE用の doPost は doPostLine() として定義し、
 * common.gs の doPost() 内で LINE リクエストを判別して振り分ける。
 * → 実装: common.gs の doPost() に LINE 判別ロジックを追加
 */

// ---- LINE Webhook ハンドラ ----

/**
 * LINE Webhook イベントを処理する
 * common.gs の doPost() から呼ばれる
 */
function handleLineWebhook(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    var events = body.events || [];

    for (var i = 0; i < events.length; i++) {
      var event = events[i];
      if (event.type === "message" && event.message.type === "text") {
        handleTextMessage(event);
      } else if (event.type === "follow") {
        handleFollow(event);
      }
    }

    return ContentService.createTextOutput("OK");
  } catch (err) {
    Logger.log("handleLineWebhook error: " + err.message);
    return ContentService.createTextOutput("OK");
  }
}

/**
 * 友だち追加時の処理
 */
function handleFollow(event) {
  var replyToken = event.replyToken;
  replyText(replyToken,
    "外勤調整システムのLINE Botです。\n" +
    "メニューの「連携」ボタンから、アカウント連携を行ってください。"
  );
}

/**
 * テキストメッセージの処理（メインルーター）
 */
function handleTextMessage(event) {
  var userId = event.source.userId;
  var text = event.message.text.trim();
  var replyToken = event.replyToken;

  // 連携開始（リッチメニューから）
  if (text === "連携") {
    startAccountLink(userId, replyToken);
    return;
  }

  // セッション確認
  var session = getSession(userId);

  // 連携コード入力中の処理
  if (session && session.state === "awaiting_link_code") {
    handleLinkCodeInput(userId, text, replyToken, session);
    return;
  }

  // 連携チェック
  var doctor = findDoctorByLineId(userId);
  if (!doctor) {
    replyText(replyToken,
      "まずアカウント連携をしてください。\nメニューの「連携」ボタンをタップしてください。"
    );
    return;
  }

  // コマンド判定（入力フロー中でもコマンドが来たらフローを中断）
  var isCommand = ["希望入力", "入力", "予定確認", "ヘルプ", "help", "キャンセル", "連携"].indexOf(text) >= 0;
  if (isCommand && session) {
    // フロー中に別コマンド → セッションを破棄して新コマンドを処理
    deleteSession(userId);
    session = null;
  }

  // コマンド振り分け
  switch (text) {
    case "希望入力":
    case "入力":
      startPreferenceInput(doctor, userId, replyToken);
      break;
    case "予定確認":
      showSchedule(doctor, userId, replyToken);
      break;
    case "ヘルプ":
    case "help":
      showHelp(replyToken);
      break;
    case "キャンセル":
      replyText(replyToken, "キャンセルしました。");
      break;
    default:
      // 入力フロー中の応答を処理
      if (session) {
        handleSessionInput(doctor, userId, text, replyToken, session);
      } else {
        showHelp(replyToken);
      }
  }
}

// ---- アカウント連携（ワンタイムコード方式） ----

/**
 * 連携開始: コード入力を促す
 * Streamlit の設定画面で表示される6桁コードを入力してもらう
 */
function startAccountLink(userId, replyToken) {
  // 既に連携済みか確認
  var doctor = findDoctorByLineId(userId);
  if (doctor) {
    replyText(replyToken,
      doctor.name + " さんとして連携済みです。\n再連携する場合は管理者にお問い合わせください。"
    );
    return;
  }

  upsertSession(userId, {
    state: "awaiting_link_code",
    doctor_id: "",
    target_month: "",
    current_date_index: "",
    preferences_json: "",
    free_text: "",
    pending_account: ""
  });

  replyText(replyToken,
    "Webアプリにログインし、アカウント設定の「LINE連携」タブに表示されている連携コード（6桁）を入力してください。"
  );
}

/**
 * 連携コードを検証して LINE User ID を保存
 */
function handleLinkCodeInput(userId, text, replyToken, session) {
  var code = text.trim();

  // 6桁の数字以外は無視
  if (!/^\d{6}$/.test(code)) {
    replyText(replyToken, "6桁の連携コードを入力してください。\n\nキャンセルするには「キャンセル」と入力してください。");
    return;
  }

  // 設定シートから全ての連携コードを検索
  var ss = getMasterSpreadsheet();
  var sheet = getSheet(ss, "設定");
  if (!sheet) {
    replyText(replyToken, "システムエラーが発生しました。管理者にお問い合わせください。");
    deleteSession(userId);
    return;
  }

  var data = sheet.getDataRange().getValues();
  var matchedDoctorId = null;

  for (var i = 1; i < data.length; i++) {
    var key = String(data[i][0]);
    if (!key.startsWith("line_link_")) continue;

    var raw = String(data[i][1]);
    try {
      var parsed = JSON.parse(raw);
      // 有効期限チェック
      if (parsed.expires && new Date().getTime() / 1000 > parsed.expires) continue;
      if (parsed.code === code) {
        matchedDoctorId = key.replace("line_link_", "");
        // コードを削除（使い捨て）
        sheet.getRange(i + 1, 2).setValue("");
        break;
      }
    } catch (e) {
      continue;
    }
  }

  if (!matchedDoctorId) {
    replyText(replyToken, "コードが一致しないか、期限切れです。\nWebアプリで新しいコードを確認してください。");
    return;
  }

  // 医員マスタから該当医員を取得して line_user_id を保存
  var doctorMap = getDoctorMap(ss);
  var doctor = doctorMap[matchedDoctorId];
  if (!doctor) {
    replyText(replyToken, "該当するアカウントが見つかりません。管理者にお問い合わせください。");
    deleteSession(userId);
    return;
  }

  // 医員マスタの行番号を取得して保存
  var masterSheet = getSheet(ss, "医員マスタ");
  var masterData = masterSheet.getDataRange().getValues();
  var headers = masterData[0];
  var colId = headers.indexOf("id");
  var colLineId = headers.indexOf("line_user_id");

  for (var j = 1; j < masterData.length; j++) {
    if (String(masterData[j][colId]) === matchedDoctorId) {
      masterSheet.getRange(j + 1, colLineId + 1).setValue(userId);
      break;
    }
  }

  deleteSession(userId);

  // リッチメニュー切替
  switchToLinkedRichMenu(userId);

  replyText(replyToken,
    "アカウント連携が完了しました！\n" +
    doctor.name + " さん、ようこそ。\n\n" +
    "メニューの「希望入力」から、希望入力を開始できます。"
  );
}

// ---- 希望入力フロー ----

/**
 * 希望入力開始: 月選択
 */
function startPreferenceInput(doctor, userId, replyToken) {
  var openMonthResult = getOpenMonthForUser(doctor);
  var openMonth = openMonthResult.month;
  var isDevTest = openMonthResult.isDev;

  if (!openMonth) {
    replyText(replyToken, "現在、受付中の月はありません。");
    return;
  }

  // 確定済みチェック（dev_テスト時はスキップ）
  if (!isDevTest && isMonthConfirmed(openMonth)) {
    replyText(replyToken,
      openMonth.replace("-", "年") + "月 のスケジュールは確定済みです。\n希望の変更はできません。"
    );
    return;
  }

  // 対象土曜日を取得
  var dates = getTargetSaturdays(openMonth);
  if (dates.length === 0) {
    replyText(replyToken, openMonth + " の対象日がありません。");
    return;
  }

  // セッション開始
  upsertSession(userId, {
    state: "selecting_preference",
    doctor_id: doctor.id,
    target_month: openMonth,
    current_date_index: "0",
    preferences_json: "{}",
    free_text: "",
    is_dev_test: isDevTest ? "1" : ""
  });

  // 最初の日付を聞く
  var dateLabel = formatDateLabel(dates[0]);
  replyWithQuickReply(replyToken,
    openMonth.replace("-", "年") + "月 の希望入力を開始します。\n\n" +
    dateLabel + " の希望を選んでください。",
    [
      {label: "○", text: "○"},
      {label: "当直明け○", text: "当直明け○"},
      {label: "△", text: "△"},
      {label: "×", text: "×"}
    ]
  );
}

/**
 * セッション中の入力処理（日付希望・自由テキスト・確認）
 */
function handleSessionInput(doctor, userId, text, replyToken, session) {
  var state = session.state;

  if (state === "selecting_preference") {
    handlePreferenceSelection(doctor, userId, text, replyToken, session);
  } else if (state === "awaiting_free_text") {
    handleFreeTextInput(doctor, userId, text, replyToken, session);
  } else if (state === "confirming") {
    handleConfirmation(doctor, userId, text, replyToken, session);
  } else {
    showHelp(replyToken);
  }
}

/**
 * 日付ごとの希望選択を処理
 */
function handlePreferenceSelection(doctor, userId, text, replyToken, session) {
  var validChoices = ["○", "当直明け○", "△", "×"];
  if (validChoices.indexOf(text) < 0) {
    replyWithQuickReply(replyToken,
      "以下のボタンから選んでください。",
      [
        {label: "○", text: "○"},
        {label: "当直明け○", text: "当直明け○"},
        {label: "△", text: "△"},
        {label: "×", text: "×"}
      ]
    );
    return;
  }

  var dates = getTargetSaturdays(session.target_month);
  var idx = parseInt(session.current_date_index, 10);
  var prefs = JSON.parse(session.preferences_json || "{}");

  // 現在の日付の希望を保存
  prefs[dates[idx]] = text;

  var nextIdx = idx + 1;

  if (nextIdx < dates.length) {
    // 次の日付を聞く
    upsertSession(userId, {
      current_date_index: String(nextIdx),
      preferences_json: JSON.stringify(prefs)
    });
    var dateLabel = formatDateLabel(dates[nextIdx]);
    replyWithQuickReply(replyToken,
      dateLabel + " の希望を選んでください。",
      [
        {label: "○", text: "○"},
        {label: "当直明け○", text: "当直明け○"},
        {label: "△", text: "△"},
        {label: "×", text: "×"}
      ]
    );
  } else {
    // 全日付入力完了 → 自由テキストへ
    upsertSession(userId, {
      state: "awaiting_free_text",
      preferences_json: JSON.stringify(prefs)
    });
    replyWithQuickReply(replyToken,
      "備考・希望があればテキストを入力してください。",
      [{label: "なし", text: "なし"}]
    );
  }
}

/**
 * 自由テキスト入力を処理
 */
function handleFreeTextInput(doctor, userId, text, replyToken, session) {
  var freeText = (text === "なし") ? "" : text;

  upsertSession(userId, {
    state: "confirming",
    free_text: freeText
  });

  // 確認画面を表示
  var prefs = JSON.parse(session.preferences_json || "{}");
  var dates = getTargetSaturdays(session.target_month);
  var monthLabel = session.target_month.replace("-", "年") + "月";

  var lines = ["【" + doctor.name + "】さんの " + monthLabel + " の希望です\n"];
  for (var i = 0; i < dates.length; i++) {
    var dl = formatDateLabel(dates[i]);
    var pref = prefs[dates[i]] || "○";
    lines.push("  " + dl + " → " + pref);
  }
  if (freeText) {
    lines.push("\n備考: " + freeText);
  }

  replyWithQuickReply(replyToken,
    lines.join("\n"),
    [
      {label: "登録する", text: "登録する"},
      {label: "やり直す", text: "やり直す"}
    ]
  );
}

/**
 * 最終確認を処理
 */
function handleConfirmation(doctor, userId, text, replyToken, session) {
  if (text === "やり直す") {
    // 最初の日付からやり直し
    var dates = getTargetSaturdays(session.target_month);
    upsertSession(userId, {
      state: "selecting_preference",
      current_date_index: "0",
      preferences_json: "{}",
      free_text: ""
    });
    var dateLabel = formatDateLabel(dates[0]);
    replyWithQuickReply(replyToken,
      "最初からやり直します。\n\n" + dateLabel + " の希望を選んでください。",
      [
        {label: "○", text: "○"},
        {label: "当直明け○", text: "当直明け○"},
        {label: "△", text: "△"},
        {label: "×", text: "×"}
      ]
    );
    return;
  }

  if (text === "登録する") {
    // Google Sheets に保存
    var saveResult;
    try {
      saveResult = savePreference(session, doctor);
    } catch (saveErr) {
      Logger.log("savePreference 例外: doctor_id=" + doctor.id
        + " month=" + session.target_month
        + " is_dev=" + session.is_dev_test
        + " err=" + saveErr.message
        + " stack=" + (saveErr.stack || ""));
      replyText(replyToken,
        "希望の保存に失敗しました。管理者にご連絡ください。\n(" + saveErr.message + ")"
      );
      deleteSession(userId);
      return;
    }
    deleteSession(userId);

    var monthLabel = session.target_month.replace("-", "年") + "月";
    var sheetInfo = saveResult && saveResult.sheetName
      ? "\n[保存先: " + saveResult.sheetName + " / " + saveResult.action + "]"
      : "";
    replyText(replyToken, monthLabel + " の希望を登録しました！" + sheetInfo);
    return;
  }

  replyWithQuickReply(replyToken,
    "「登録する」または「やり直す」を選んでください。",
    [
      {label: "登録する", text: "登録する"},
      {label: "やり直す", text: "やり直す"}
    ]
  );
}

// ---- 予定確認（当月〜+2ヶ月を一括表示） ----

/**
 * 当月〜2ヶ月先の確定済みスケジュールを一括表示
 */
function showSchedule(doctor, userId, replyToken) {
  var ss = getOperationalSpreadsheet();
  var ssMaster = getMasterSpreadsheet();
  var clinicMap = getClinicMap(ssMaster);

  // 当月〜+2ヶ月
  var now = new Date();
  var targetMonths = [];
  for (var offset = 0; offset <= 2; offset++) {
    var d = new Date(now.getFullYear(), now.getMonth() + offset, 1);
    targetMonths.push(Utilities.formatDate(d, "Asia/Tokyo", "yyyy-MM"));
  }

  // 平日セクション設定を1回だけ取得
  var configs = getWeekdayConfigs(ssMaster);
  var myConfigs = [];
  for (var c = 0; c < configs.length; c++) {
    var cfg = configs[c];
    if (!cfg.is_active) continue;
    var isMember = cfg.assigned_doctors.some(function(id) {
      return String(id) === doctor.id;
    });
    if (isMember) myConfigs.push(cfg);
  }

  var allMessages = [];

  for (var mi = 0; mi < targetMonths.length; mi++) {
    var ym = targetMonths[mi];
    var monthMessages = [];

    // ---- 土曜外勤 ----
    var satSheet = ss.getSheetByName("スケジュール_" + ym);
    if (satSheet) {
      var satData = satSheet.getDataRange().getValues();
      if (satData.length > 1) {
        var satHeaders = satData[0];
        var colConfirmed = satHeaders.indexOf("is_confirmed");
        var colAssignments = satHeaders.indexOf("assignments");
        var satAssignments = [];
        for (var r = 1; r < satData.length; r++) {
          if (String(satData[r][colConfirmed]) !== "1") continue;
          try {
            var parsed = JSON.parse(satData[r][colAssignments]);
            parsed.forEach(function(a) {
              if (String(a.doctor_id) === doctor.id) satAssignments.push(a);
            });
          } catch (e) {}
        }
        if (satAssignments.length > 0) {
          satAssignments.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
          var satLines = ["■ 土曜外勤"];
          satAssignments.forEach(function(a) {
            var clinicName = clinicMap[String(a.clinic_id)] || "不明";
            satLines.push("  " + formatDateLabel(a.date) + " : " + clinicName);
          });
          monthMessages.push(satLines.join("\n"));
        }
      }
    }

    // ---- 平日外勤 ----
    for (var ci = 0; ci < myConfigs.length; ci++) {
      var cfg = myConfigs[ci];
      var ssSec = getWeekdaySectionSpreadsheet(ssMaster, cfg.section);
      if (!ssSec) continue;
      var wdSheet = ssSec.getSheetByName("平日スケジュール_" + ym);
      if (!wdSheet) continue;
      var wdData = wdSheet.getDataRange().getValues();
      if (wdData.length <= 1) continue;
      var wdHeaders = wdData[0];
      var colDoctorId = wdHeaders.indexOf("doctor_id");
      var colDate = wdHeaders.indexOf("date");
      var colSlotName = wdHeaders.indexOf("slot_name");
      var myWd = [];
      for (var wr = 1; wr < wdData.length; wr++) {
        if (String(wdData[wr][colDoctorId]) === doctor.id) {
          myWd.push({
            date: String(wdData[wr][colDate]),
            slot_name: colSlotName >= 0 ? String(wdData[wr][colSlotName] || "") : ""
          });
        }
      }
      if (myWd.length > 0) {
        myWd.sort(function(a, b) { return a.date > b.date ? 1 : -1; });
        var sectionLabel = cfg.clinic_name || cfg.section;
        var wdLines = ["■ 平日外勤（" + sectionLabel + "）"];
        myWd.forEach(function(a) {
          var slotLabel = a.slot_name ? " " + a.slot_name : "";
          wdLines.push("  " + formatDateLabel(a.date) + slotLabel);
        });
        monthMessages.push(wdLines.join("\n"));
      }
    }

    if (monthMessages.length > 0) {
      var monthLabel = ym.replace("-", "年") + "月";
      allMessages.push("【" + monthLabel + "】\n" + monthMessages.join("\n"));
    }
  }

  if (allMessages.length === 0) {
    replyText(replyToken, "現在、確定済みのスケジュールはありません。");
  } else {
    replyText(replyToken, allMessages.join("\n\n"));
  }
}

// ---- ヘルプ ----

function showHelp(replyToken) {
  replyText(replyToken,
    "【使い方】\n" +
    "■ 連携: Webアプリで表示される連携コード（6桁）で紐づけます（初回のみ）\n" +
    "■ 希望入力: 来月の出勤/休みの希望を登録します\n" +
    "■ 予定確認: 確定済みのスケジュールを表示します\n\n" +
    "困ったときは管理者にお問い合わせください。"
  );
}

// ---- ユーティリティ ----

/**
 * 対象月の土曜日リストを取得（祝日除外）
 * @return {string[]} 日付文字列の配列 ["2026-04-04", "2026-04-11", ...]
 */
function getTargetSaturdays(yearMonth) {
  var parts = yearMonth.split("-");
  var year = parseInt(parts[0], 10);
  var month = parseInt(parts[1], 10);
  var dates = [];

  // 追加日付・除外日付を設定シートから取得
  var extraDates = (getSettingValue("saturday_extra_dates") || "").split(",").map(function(s) { return s.trim(); }).filter(Boolean);
  var excludedDates = (getSettingValue("saturday_excluded_dates") || "").split(",").map(function(s) { return s.trim(); }).filter(Boolean);
  var excludedSet = {};
  excludedDates.forEach(function(d) { excludedSet[d] = true; });

  var d = new Date(year, month - 1, 1);
  while (d.getMonth() === month - 1) {
    if (d.getDay() === 6) { // 土曜日
      var dateStr = Utilities.formatDate(d, "Asia/Tokyo", "yyyy-MM-dd");
      if (!excludedSet[dateStr] && !isNenmatsuNenshi(d)) {
        dates.push(dateStr);
      }
    }
    d.setDate(d.getDate() + 1);
  }

  // 追加日付
  extraDates.forEach(function(dateStr) {
    if (!excludedSet[dateStr] && dates.indexOf(dateStr) < 0) {
      dates.push(dateStr);
    }
  });

  dates.sort();
  return dates;
}

/**
 * 年末年始チェック (12/29-1/3)
 */
function isNenmatsuNenshi(d) {
  var m = d.getMonth() + 1;
  var day = d.getDate();
  if (m === 12 && day >= 29) return true;
  if (m === 1 && day <= 3) return true;
  return false;
}

/**
 * 日付文字列をフォーマット (YYYY-MM-DD → M/D(曜日))
 */
function formatDateLabel(dateStr) {
  var weekdays = ["日", "月", "火", "水", "木", "金", "土"];
  var d = new Date(dateStr + "T00:00:00+09:00");
  return (d.getMonth() + 1) + "/" + d.getDate() + "(" + weekdays[d.getDay()] + ")";
}

/**
 * 希望データを Google Sheets に保存（既存の希望_YYYY-MM シートに上書き）
 */
function savePreference(session, doctor) {
  var ss = getOperationalSpreadsheet();
  var ssId = ss.getId();
  var ssName = ss.getName();
  var prefix = session.is_dev_test === "1" ? "dev_" : "";
  var sheetName = prefix + "希望_" + session.target_month;
  Logger.log("savePreference 開始: doctor_id=" + doctor.id
    + " sheet=" + sheetName
    + " ss_name=" + ssName
    + " ss_id=" + ssId);
  var sheet = getSheet(ss, sheetName);

  // シートがなければ作成
  if (!sheet) {
    Logger.log("savePreference: シート未存在のため作成 - " + sheetName);
    sheet = ss.insertSheet(sheetName);
    sheet.appendRow([
      "doctor_id", "doctor_name", "ng_dates", "avoid_dates",
      "preferred_clinics", "date_clinic_requests", "free_text",
      "updated_at", "post_night_dates"
    ]);
  }

  var prefs = JSON.parse(session.preferences_json || "{}");
  var ngDates = [];
  var avoidDates = [];
  var postNightDates = [];

  var dates = Object.keys(prefs);
  dates.forEach(function(dateStr) {
    var choice = prefs[dateStr];
    if (choice === "×") ngDates.push(dateStr);
    else if (choice === "△") avoidDates.push(dateStr);
    else if (choice === "当直明け○") postNightDates.push(dateStr);
    // ○ は何もしない（= 出勤可能）
  });

  var now = Utilities.formatDate(new Date(), "Asia/Tokyo", "yyyy-MM-dd HH:mm:ss");

  // 実際のヘッダー順序に合わせてデータを配置
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var dataMap = {
    "doctor_id": doctor.id,
    "doctor_name": doctor.name,
    "ng_dates": JSON.stringify(ngDates),
    "avoid_dates": JSON.stringify(avoidDates),
    "preferred_clinics": "[]",
    "date_clinic_requests": "{}",
    "free_text": session.free_text || "",
    "updated_at": now,
    "post_night_dates": JSON.stringify(postNightDates)
  };
  var rowData = headers.map(function(h) { return dataMap[h] !== undefined ? dataMap[h] : ""; });

  // 既存行を探す
  var data = sheet.getDataRange().getValues();
  var colDoctorId = headers.indexOf("doctor_id");
  var existingRow = -1;
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][colDoctorId]) === doctor.id) {
      existingRow = i + 1;
      break;
    }
  }

  var action;
  if (existingRow > 0) {
    sheet.getRange(existingRow, 1, 1, rowData.length).setValues([rowData]);
    action = "update(row=" + existingRow + ")";
  } else {
    sheet.appendRow(rowData);
    action = "append";
  }
  SpreadsheetApp.flush();
  Logger.log("savePreference 完了: doctor_id=" + doctor.id
    + " sheet=" + sheetName + " " + action);
  return { sheetName: sheetName, action: action, ssName: ssName, ssId: ssId };
}

/**
 * 指定月のスケジュールが確定済みかチェック
 */
function isMonthConfirmed(yearMonth) {
  var ss = getOperationalSpreadsheet();
  var sheetName = "スケジュール_" + yearMonth;
  var sheet = getSheet(ss, sheetName);
  if (!sheet) return false;

  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return false;
  var headers = data[0];
  var colConfirmed = headers.indexOf("is_confirmed");
  if (colConfirmed < 0) return false;

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][colConfirmed]) === "1") return true;
  }
  return false;
}
