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

// ---- セッション管理（CacheService ベース） ----

/**
 * LINEセッションシートを取得（なければ作成）
 * 移行期間中の互換性のため残す
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
 * ユーザーのセッションを取得（CacheService）
 * @return {Object|null} セッションデータ
 */
function getSession(userId) {
  var cache = CacheService.getScriptCache();
  var raw = cache.get("line_session_" + userId);
  if (!raw) return null;
  try {
    var session = JSON.parse(raw);
    return session;
  } catch (e) {
    return null;
  }
}

/**
 * セッションを作成または更新（CacheService）
 */
function upsertSession(userId, updates) {
  var cache = CacheService.getScriptCache();
  var existing = getSession(userId) || {};
  for (var key in updates) {
    existing[key] = updates[key];
  }
  existing.user_id = userId;
  existing.updated_at = new Date().toISOString();
  // 30分 = 1800秒
  cache.put("line_session_" + userId, JSON.stringify(existing), 1800);
}

/**
 * セッションを削除（CacheService）
 */
function deleteSession(userId) {
  var cache = CacheService.getScriptCache();
  cache.remove("line_session_" + userId);
}

// ---- 設定シート読み取り（リクエスト内キャッシュ） ----

var _settingsCache = null;

/**
 * 設定シートの全値をマップで取得（リクエスト内キャッシュ）
 */
function getAllSettings() {
  if (_settingsCache) return _settingsCache;
  var ss = getMasterSpreadsheet();
  var sheet = getSheet(ss, "設定");
  if (!sheet) return {};
  var data = sheet.getDataRange().getValues();
  var map = {};
  for (var i = 1; i < data.length; i++) {
    map[String(data[i][0])] = String(data[i][1]);
  }
  _settingsCache = map;
  return map;
}

/**
 * 設定シートから値を取得
 */
function getSettingValue(key) {
  var settings = getAllSettings();
  return settings[key] || null;
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
 * ユーザーに応じた公開月を返す（開発者テスト分離）
 * dev_doctor_ids に含まれる医員には dev_open_month を返し、
 * それ以外には通常の open_month を返す。
 * @returns {{month: string|null, isDev: boolean}}
 */
function getOpenMonthForUser(doctor) {
  var devMonth = getSettingValue("dev_open_month");
  if (devMonth && doctor) {
    var devIdsRaw = getSettingValue("dev_doctor_ids");
    if (devIdsRaw) {
      try {
        var devIds = JSON.parse(devIdsRaw);
        for (var i = 0; i < devIds.length; i++) {
          if (String(devIds[i]) === String(doctor.id)) {
            // dev_open_month を正規化
            var normalized = devMonth;
            if (devMonth.match && devMonth.match(/^\d{4}-\d{2}$/)) {
              normalized = devMonth;
            } else {
              try {
                var d = new Date(devMonth);
                if (!isNaN(d.getTime())) {
                  normalized = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2);
                }
              } catch (e) {}
            }
            return { month: normalized, isDev: true };
          }
        }
      } catch (e) {}
    }
  }
  return { month: getOpenMonth(), isDev: false };
}

/**
 * 入力期限を取得
 */
function getInputDeadline() {
  return getSettingValue("input_deadline");
}
