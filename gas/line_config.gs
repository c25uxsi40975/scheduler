/**
 * LINE Bot 設定・共通関数
 *
 * スクリプトプロパティに以下を設定すること:
 *   LINE_CHANNEL_SECRET        : チャネルシークレット
 *   LINE_CHANNEL_ACCESS_TOKEN  : チャネルアクセストークン
 */

// ---- LINE API 認証情報 ----
// グローバル変数ではなく関数で都度取得（Web App実行時の評価タイミング問題を回避）

function getLineChannelSecret() {
  return PropertiesService.getScriptProperties().getProperty("LINE_CHANNEL_SECRET") || "";
}
function getLineChannelAccessToken() {
  return PropertiesService.getScriptProperties().getProperty("LINE_CHANNEL_ACCESS_TOKEN") || "";
}

// ---- セッション設定 ----

var LINE_SESSION_TIMEOUT_MIN = 30;  // セッションタイムアウト（分）
var LINE_SESSION_SHEET_NAME = "LINEセッション";

// ---- Reply API ----

/**
 * Reply API でメッセージを送信（無料）
 */
function replyMessage(replyToken, messages) {
  var token = getLineChannelAccessToken();
  if (!replyToken || !token) {
    Logger.log("replyMessage: トークンまたはアクセストークンが未設定");
    return;
  }
  try {
    UrlFetchApp.fetch("https://api.line.me/v2/bot/message/reply", {
      "method": "post",
      "headers": {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json"
      },
      "payload": JSON.stringify({
        "replyToken": replyToken,
        "messages": messages
      })
    });
  } catch (e) {
    Logger.log("replyMessage error: " + e.message);
  }
}

/**
 * テキストメッセージを返信（ヘルパー）
 */
function replyText(replyToken, text) {
  replyMessage(replyToken, [{"type": "text", "text": text}]);
}

/**
 * Quick Reply 付きテキストメッセージを返信
 * items: [{label: "ラベル", text: "送信テキスト"}, ...]
 */
function replyWithQuickReply(replyToken, text, items) {
  var qrItems = items.map(function(item) {
    return {
      "type": "action",
      "action": {"type": "message", "label": item.label, "text": item.text}
    };
  });
  replyMessage(replyToken, [{
    "type": "text",
    "text": text,
    "quickReply": {"items": qrItems}
  }]);
}

// ---- Push API ----

/**
 * Push API でメッセージを送信（課金対象）
 */
function pushMessage(userId, messages) {
  var token = getLineChannelAccessToken();
  if (!userId || !token) return;
  try {
    UrlFetchApp.fetch("https://api.line.me/v2/bot/message/push", {
      "method": "post",
      "headers": {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json"
      },
      "payload": JSON.stringify({
        "to": userId,
        "messages": messages
      })
    });
  } catch (e) {
    Logger.log("pushMessage error: " + e.message);
  }
}

/**
 * Push API でテキストを送信
 */
function pushText(userId, text) {
  pushMessage(userId, [{"type": "text", "text": text}]);
}

// ---- 医員マスタ操作 ----

/**
 * LINE User ID で医員を検索
 * @return {Object|null} {row: 行番号, id, name, email, account_name, password_hash, ...}
 */
function findDoctorByLineId(userId) {
  var ss = getMasterSpreadsheet();
  var sheet = getSheet(ss, "医員マスタ");
  if (!sheet) return null;

  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var colLineId = headers.indexOf("line_user_id");
  var colActive = headers.indexOf("is_active");
  if (colLineId < 0) return null;

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][colLineId]).trim() === userId &&
        String(data[i][colActive]) !== "0") {
      var doc = {row: i + 1};
      for (var j = 0; j < headers.length; j++) {
        doc[headers[j]] = data[i][j];
      }
      doc.id = String(doc.id);
      doc.name = String(doc.name);
      return doc;
    }
  }
  return null;
}

/**
 * アカウント名で医員を検索
 */
function findDoctorByAccountName(accountName) {
  var ss = getMasterSpreadsheet();
  var sheet = getSheet(ss, "医員マスタ");
  if (!sheet) return null;

  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var colAccountName = headers.indexOf("account_name");
  var colAccount = headers.indexOf("account");
  var colActive = headers.indexOf("is_active");

  for (var i = 1; i < data.length; i++) {
    var acctName = String(data[i][colAccountName] || data[i][colAccount] || "");
    if (acctName === accountName && String(data[i][colActive]) !== "0") {
      var doc = {row: i + 1};
      for (var j = 0; j < headers.length; j++) {
        doc[headers[j]] = data[i][j];
      }
      doc.id = String(doc.id);
      doc.name = String(doc.name);
      return doc;
    }
  }
  return null;
}

/**
 * 医員マスタに LINE User ID を保存
 */
function saveDoctorLineUserId(doctorRow, userId) {
  var ss = getMasterSpreadsheet();
  var sheet = getSheet(ss, "医員マスタ");
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colLineId = headers.indexOf("line_user_id");
  if (colLineId < 0) return;
  sheet.getRange(doctorRow, colLineId + 1).setValue(userId);
}

// パスワード検証は LIFF 経由で Streamlit（Python bcrypt）側で行うため、
// GAS 側の verifyPassword() は不要になりました。

// ---- セッション管理 ----

/**
 * LINEセッションシートを取得（なければ作成）
 */
function getSessionSheet() {
  var ss = getOperationalSpreadsheet();
  var sheet = getSheet(ss, LINE_SESSION_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(LINE_SESSION_SHEET_NAME);
    sheet.appendRow([
      "user_id", "state", "doctor_id", "target_month",
      "current_date_index", "preferences_json", "free_text",
      "pending_account", "updated_at"
    ]);
  }
  return sheet;
}

/**
 * ユーザーのセッションを取得
 * @return {Object|null} セッションデータ（タイムアウト済みなら null）
 */
function getSession(userId) {
  var sheet = getSessionSheet();
  var data = sheet.getDataRange().getValues();
  var headers = data[0];

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][headers.indexOf("user_id")]) === userId) {
      var updatedAt = data[i][headers.indexOf("updated_at")];
      // タイムアウトチェック
      if (updatedAt) {
        var elapsed = (new Date() - new Date(updatedAt)) / 60000;
        if (elapsed > LINE_SESSION_TIMEOUT_MIN) {
          deleteSession(userId);
          return null;
        }
      }
      var session = {row: i + 1};
      for (var j = 0; j < headers.length; j++) {
        session[headers[j]] = String(data[i][j] || "");
      }
      return session;
    }
  }
  return null;
}

/**
 * セッションを作成または更新
 */
function upsertSession(userId, updates) {
  var sheet = getSessionSheet();
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var now = Utilities.formatDate(new Date(), "Asia/Tokyo", "yyyy-MM-dd HH:mm:ss");

  // 既存セッションを探す
  var rowIdx = -1;
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][headers.indexOf("user_id")]) === userId) {
      rowIdx = i + 1;
      break;
    }
  }

  updates.updated_at = now;
  updates.user_id = userId;

  if (rowIdx > 0) {
    // 更新
    for (var key in updates) {
      var colIdx = headers.indexOf(key);
      if (colIdx >= 0) {
        sheet.getRange(rowIdx, colIdx + 1).setValue(updates[key]);
      }
    }
  } else {
    // 新規作成
    var row = headers.map(function(h) { return updates[h] || ""; });
    sheet.appendRow(row);
  }
}

/**
 * セッションを削除
 */
function deleteSession(userId) {
  var sheet = getSessionSheet();
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var colUserId = headers.indexOf("user_id");

  for (var i = data.length - 1; i >= 1; i--) {
    if (String(data[i][colUserId]) === userId) {
      sheet.deleteRow(i + 1);
    }
  }
}

// ---- 設定シート読み取り ----

/**
 * 設定シートから値を取得
 */
function getSettingValue(key) {
  var ss = getMasterSpreadsheet();
  var sheet = getSheet(ss, "設定");
  if (!sheet) return null;
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) === key) return String(data[i][1]);
  }
  return null;
}

/**
 * 公開月を取得
 */
function getOpenMonth() {
  var val = getSettingValue("open_month");
  if (!val) return null;
  // YYYY-MM 形式ならそのまま返す
  if (val.match && val.match(/^\d{4}-\d{2}$/)) return val;
  // Google Sheets が日付として解釈した場合の対応
  try {
    var d = new Date(val);
    if (!isNaN(d.getTime())) {
      var y = d.getFullYear();
      var m = ("0" + (d.getMonth() + 1)).slice(-2);
      return y + "-" + m;
    }
  } catch (e) {}
  return val;
}

/**
 * 入力期限を取得
 */
function getInputDeadline() {
  return getSettingValue("input_deadline");
}
