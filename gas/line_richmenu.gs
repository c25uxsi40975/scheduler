/**
 * LINE Bot リッチメニュー作成・管理
 *
 * 初回セットアップ時に以下の順で実行:
 *   1. createUnlinkedRichMenu()  → 未連携用メニュー作成
 *   2. createLinkedRichMenu()    → 連携済み用メニュー作成
 *   3. 各メニューに画像をアップロード
 *   4. setDefaultRichMenu()      → 未連携用をデフォルトに設定
 *
 * リッチメニューIDはスクリプトプロパティに保存される:
 *   RICH_MENU_UNLINKED : 未連携用メニューID
 *   RICH_MENU_LINKED   : 連携済み用メニューID
 */

// ---- リッチメニュー作成 ----

/**
 * 未連携用リッチメニューを作成（「連携」のみアクティブ、他はグレー）
 */
function createUnlinkedRichMenu() {
  var menu = {
    "size": {"width": 2500, "height": 843},
    "selected": true,
    "name": "未連携メニュー",
    "chatBarText": "メニュー",
    "areas": [
      {
        "bounds": {"x": 0, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "連携"}
      },
      {
        "bounds": {"x": 625, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "希望入力"}
      },
      {
        "bounds": {"x": 1250, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "予定確認"}
      },
      {
        "bounds": {"x": 1875, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "ヘルプ"}
      }
    ]
  };

  var richMenuId = createRichMenuApi(menu);
  PropertiesService.getScriptProperties().setProperty("RICH_MENU_UNLINKED", richMenuId);
  Logger.log("未連携メニュー作成完了: " + richMenuId);
  return richMenuId;
}

/**
 * 連携済み用リッチメニューを作成（4ボタン全てアクティブ）
 */
function createLinkedRichMenu() {
  var menu = {
    "size": {"width": 2500, "height": 843},
    "selected": true,
    "name": "連携済みメニュー",
    "chatBarText": "メニュー",
    "areas": [
      {
        "bounds": {"x": 0, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "連携"}
      },
      {
        "bounds": {"x": 625, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "希望入力"}
      },
      {
        "bounds": {"x": 1250, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "予定確認"}
      },
      {
        "bounds": {"x": 1875, "y": 0, "width": 625, "height": 843},
        "action": {"type": "message", "text": "ヘルプ"}
      }
    ]
  };

  var richMenuId = createRichMenuApi(menu);
  PropertiesService.getScriptProperties().setProperty("RICH_MENU_LINKED", richMenuId);
  Logger.log("連携済みメニュー作成完了: " + richMenuId);
  return richMenuId;
}

// ---- リッチメニュー画像アップロード ----

/**
 * リッチメニューに画像をアップロード
 * Google Drive 上の画像ファイルIDを指定
 * @param {string} richMenuId リッチメニューID
 * @param {string} driveFileId Google DriveのファイルID
 */
function uploadRichMenuImage(richMenuId, driveFileId) {
  var file = DriveApp.getFileById(driveFileId);
  var blob = file.getBlob();

  UrlFetchApp.fetch(
    "https://api-data.line.me/v2/bot/richmenu/" + richMenuId + "/content",
    {
      "method": "post",
      "headers": {
        "Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN,
        "Content-Type": blob.getContentType()
      },
      "payload": blob.getBytes()
    }
  );
  Logger.log("画像アップロード完了: " + richMenuId);
}

// ---- デフォルトメニュー設定 ----

/**
 * 未連携メニューをデフォルトに設定（全ユーザーの初期メニュー）
 */
function setDefaultRichMenu() {
  var richMenuId = PropertiesService.getScriptProperties()
      .getProperty("RICH_MENU_UNLINKED");
  if (!richMenuId) {
    Logger.log("RICH_MENU_UNLINKED が未設定です");
    return;
  }
  UrlFetchApp.fetch(
    "https://api.line.me/v2/bot/user/all/richmenu/" + richMenuId,
    {
      "method": "post",
      "headers": {"Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN}
    }
  );
  Logger.log("デフォルトメニュー設定完了: " + richMenuId);
}

// ---- ユーザー別メニュー切替 ----

/**
 * ユーザーを連携済みメニューに切り替える（連携完了時に呼ぶ）
 */
function switchToLinkedRichMenu(userId) {
  var richMenuId = PropertiesService.getScriptProperties()
      .getProperty("RICH_MENU_LINKED");
  if (!richMenuId) return;

  try {
    UrlFetchApp.fetch(
      "https://api.line.me/v2/bot/user/" + userId + "/richmenu/" + richMenuId,
      {
        "method": "post",
        "headers": {"Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN}
      }
    );
  } catch (e) {
    Logger.log("リッチメニュー切替エラー: " + e.message);
  }
}

// ---- 内部ヘルパー ----

/**
 * LINE API でリッチメニューを作成
 * @return {string} 作成されたリッチメニューID
 */
function createRichMenuApi(menuData) {
  var res = UrlFetchApp.fetch("https://api.line.me/v2/bot/richmenu", {
    "method": "post",
    "headers": {
      "Authorization": "Bearer " + LINE_CHANNEL_ACCESS_TOKEN,
      "Content-Type": "application/json"
    },
    "payload": JSON.stringify(menuData)
  });
  return JSON.parse(res.getContentText()).richMenuId;
}
