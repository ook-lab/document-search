/**
 * Kakeibo CSV Sync - Google Apps Script
 *
 * このコードをGASエディタにコピー&ペーストして使用してください。
 *
 * セットアップ手順:
 * 1. Google Drive に「家計簿」フォルダを作成
 * 2. その中に「import」と「processed」フォルダを作成
 * 3. Google Apps Script (https://script.google.com) で新規プロジェクト作成
 * 4. このコードを貼り付け、CONFIGを編集
 * 5. syncCsvToSupabase を実行してテスト
 */

/**
 * 設定：ここをご自身の環境に合わせて書き換えてください
 */
const CONFIG = {
  // Google DriveのフォルダID
  FOLDER_ID_IMPORT:    'ここに_importフォルダのIDを貼る',
  FOLDER_ID_PROCESSED: 'ここに_processedフォルダのIDを貼る',

  // Supabase接続情報
  SUPABASE_URL:         'https://your-project-ref.supabase.co', // あなたのSupabase URL
  SUPABASE_SERVICE_KEY: 'ここに_service_role_key_を貼る',     // Project Settings > API > service_role key

  // 設定定数
  TABLE_NAME: 'Rawdata_BANK_transactions',
  BATCH_SIZE: 100
};

/**
 * メイン関数：CSVを探してSupabaseへ同期する
 */
function syncCsvToSupabase() {
  // フォルダ取得チェック
  let folder, processedFolder;
  try {
    folder = DriveApp.getFolderById(CONFIG.FOLDER_ID_IMPORT);
    processedFolder = DriveApp.getFolderById(CONFIG.FOLDER_ID_PROCESSED);
  } catch (e) {
    console.error("【重大エラー】フォルダが見つかりません。IDを確認してください。");
    throw e;
  }

  const files = folder.getFiles();
  while (files.hasNext()) {
    const file = files.next();

    // CSVのみ対象
    if (file.getMimeType() === MimeType.CSV || file.getName().toLowerCase().endsWith('.csv')) {
      console.log(`Processing Start: ${file.getName()}`);

      try {
        processFile(file);
        // 成功したら移動
        file.moveTo(processedFolder);
        console.log(`[Success] Moved to processed: ${file.getName()}`);
      } catch (e) {
        // 失敗時は移動せずログに残す（再実行でリトライさせるため）
        console.error(`[Failure] File: ${file.getName()}, Error: ${e.message}`);
      }
    }
  }
}

/**
 * ファイル単位の処理：読み込み -> パース -> 分割送信
 */
function processFile(file) {
  // 日本の銀行CSVはShift_JISが多い
  const data = file.getBlob().getDataAsString('Shift_JIS');
  const csvData = Utilities.parseCsv(data);
  if (csvData.length < 2) {
    console.log("Skipping empty or header-only file.");
    return;
  }

  const headers = csvData[0];

  // CSVヘッダとDBカラムのマッピング
  // 左: CSVの列名, 右: DBのカラム名
  const colMap = {
    'ID': 'id',
    '日付' : 'date',
    '内容' : 'content',
    '金額（円）' : 'amount',
    '保有金融機関' : 'institution',
    '大項目' : 'category_major',
    '中項目' : 'category_minor',
    'メモ' : 'memo',
    '計算対象' : 'is_target',
    '振替' : 'is_transfer'
  };

  let payload = [];
  let batchCount = 0;

  // 1行目はヘッダなのでスキップ
  for (let i = 1; i < csvData.length; i++) {
    const row = csvData[i];
    let record = {};

    headers.forEach((header, index) => {
      const key = colMap[header];
      if (!key) return; // DBにない列は無視

      let value = row[index];

      // データ整形
      if (key === 'amount') {
        // "1,234" -> 1234 (カンマ除去)
        const s = (value ?? '').toString().replace(/,/g, '');
        value = s ? parseInt(s, 10) : 0;
      } else if (key === 'is_target' || key === 'is_transfer') {
        // "1" -> true, "0" -> false
        value = (parseInt(value, 10) === 1);
      } else if (key === 'date') {
        // "2025/01/01" -> "2025-01-01"
        value = (value ?? '').toString().replace(/\//g, '-');
      }

      record[key] = value;
    });

    // IDがない行はスキップ
    if (record.id) {
      payload.push(record);
    }

    // バッチサイズごとに送信
    if (payload.length >= CONFIG.BATCH_SIZE) {
      postToSupabase(payload, file.getName(), ++batchCount);
      payload = [];
    }
  }

  // 残りを送信
  if (payload.length > 0) {
    postToSupabase(payload, file.getName(), ++batchCount);
  }
}

/**
 * Supabaseへの送信処理 (Upsert)
 */
function postToSupabase(data, fileName, batchNum) {
  // on_conflict=id でUpsertを指定
  const url = `${CONFIG.SUPABASE_URL}/rest/v1/${CONFIG.TABLE_NAME}?on_conflict=id`;

  const options = {
    method: 'post',
    headers: {
      'apikey': CONFIG.SUPABASE_SERVICE_KEY,
      'Authorization': `Bearer ${CONFIG.SUPABASE_SERVICE_KEY}`,
      'Content-Type': 'application/json',
      'Prefer': 'resolution=merge-duplicates' // 重複時はUpdateする設定
    },
    payload: JSON.stringify(data),
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(url, options);
  const code = response.getResponseCode();

  if (code >= 400) {
    throw new Error(
      `Supabase Error: file=${fileName} batch=${batchNum} code=${code} body=${response.getContentText()}`
    );
  }
}
