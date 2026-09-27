/**
 * Google Classroom → Supabase（01_raw + pipeline_meta）
 *
 * このファイル 1 本だけを GAS に貼る。IKUYA / EMA の違いは実行する関数とスクリプトプロパティで分ける。
 *
 * owner_id は 01_raw には書かず pipeline_meta のみ。
 *
 * 実行エントリ:
 *   syncClassroom_Ikuya() … プロパティは IKUYA_* を優先し、無ければ接頭辞なし（従来キー）を読む
 *   syncClassroom_Ema()   … 同上 EMA_*
 *   syncAllClassroomsToDocuments() … 接頭辞なしのみ（単一デプロイ用）
 *
 * 必須（上記いずれかの読み方で最終的に値が入ること）:
 *   DEST_FOLDER_ID, OWNER_ID, SUPABASE_URL,
 *   SUPABASE_KEY … Project Settings → API の anon（publishable）キー。
 *     service_role（secret）は GAS の UrlFetchApp から Supabase に拒否されることがある。
 *   TABLE_NAME（例: 04_ikuya_classroom_01_raw / 03_ema_classroom_01_raw）,
 *   WORKSPACE_NAME, PERSON
 *
 * DB 側: 01_raw / pipeline_meta に anon 用 GRANT が付いていること（マイグレーション 20260506140000 参照）。
 *
 * 任意（数値の既定あり）: MAX_RECORDS_PER_RUN, LOOKBACK_DAYS, BATCH_SIZE, SLEEP_MS
 * 任意（文字列）: PIPELINE_TABLE（省略時 pipeline_meta）
 */

function syncClassroom_Ikuya() {
  runClassroomSyncForPrefix_('IKUYA_');
}

function syncClassroom_Ema() {
  runClassroomSyncForPrefix_('EMA_');
}

function syncAllClassroomsToDocuments() {
  runClassroomSyncForPrefix_('');
}

function runClassroomSyncForPrefix_(propertyPrefix) {
  const log = function(level, cmd, msg, status, detail) {
    var now = Utilities.formatDate(new Date(), "GMT+9", "yyyy-MM-dd HH:mm:ss");
    var d = detail ? " | Detail: " + (typeof detail === 'object' ? JSON.stringify(detail) : detail) : "";
    console.log("[" + now + "] [" + level + "] [CMD: " + cmd + "] [MSG: " + msg + "] [STATUS: " + status + "]" + d);
  };

  var profile = propertyPrefix ? propertyPrefix.replace(/_$/, '') : 'DEFAULT';
  log("INFO", "START_PROCESS", "同期処理を開始します。PROFILE=" + profile, "EXECUTING");
  var lock = LockService.getScriptLock();

  log("INFO", "LOCK_ACQUIRE", "スクリプトロックの取得を試行します。", "EXECUTING");
  if (!lock.tryLock(30000)) {
    log("ERROR", "LOCK_FAILED", "ロック取得に失敗しました。二重実行の可能性があります。", "FAILED");
    return;
  }
  log("INFO", "LOCK_SUCCESS", "スクリプトロックを取得しました。", "EXECUTING");

  try {
    log("INFO", "CONFIG_LOAD", "設定値を読み込みます。", "EXECUTING");
    var CONFIG = loadConfig_(propertyPrefix);
    if (!validateConfig_(CONFIG, log, propertyPrefix)) {
      log("ERROR", "CONFIG_INVALID", "スクリプトプロパティが不足しています。PROFILE=" + profile +
        " のときは " + (propertyPrefix || '(接頭辞なし)') + "TABLE_NAME 等、または無接頭辞の TABLE_NAME 等を設定してください。", "FAILED");
      return;
    }
    log("INFO", "CONFIG_VERIFIED", "設定値の検証完了。PROFILE=" + profile +
      " TABLE=" + CONFIG.TABLE_NAME + " / PERSON=" + CONFIG.PERSON + " / SOURCE=" + CONFIG.WORKSPACE_NAME, "EXECUTING");

    var thresholdDate = new Date(Date.now() - (CONFIG.LOOKBACK_DAYS * 24 * 60 * 60 * 1000));
    log("INFO", "DATE_THRESHOLD", "判定基準日: " + thresholdDate.toLocaleString(), "EXECUTING");

    log("INFO", "CLASSROOM_FETCH", "コース一覧を取得します。", "EXECUTING");
    var courses = listAllCourses_();
    log("INFO", "CLASSROOM_FETCH_SUCCESS", "取得コース数: " + courses.length, "EXECUTING");

    var stats = { sent: 0, skipped: 0, filtered: 0, planned: 0, failed: 0 };
    var categories = ['announcements', 'courseWork', 'courseWorkMaterials'];

    for (var ci = 0; ci < courses.length; ci++) {
      var course = courses[ci];
      log("INFO", "COURSE_PROCESSING", "コース処理開始: " + course.name + " (ID: " + course.id + ")", "EXECUTING");

      for (var catI = 0; catI < categories.length; catI++) {
        var category = categories[catI];
        log("INFO", "CATEGORY_FETCH", "カテゴリー取得試行: " + category, "EXECUTING");
        var items;
        try {
          items = listCategoryItems_(course.id, category);
        } catch (e) {
          stats.failed++;
          log("ERROR", "CATEGORY_FETCH_FAILED", "コースID=" + course.id + " カテゴリー=" + category + " エラー=" + e.toString(), "FAILED", e.toString());
          continue;
        }

        if (!items || !items.length) {
          log("INFO", "CATEGORY_EMPTY", "アイテムが存在しません: " + category, "EXECUTING");
          continue;
        }
        log("INFO", "CATEGORY_FETCH_SUCCESS", "取得アイテム数 (" + category + "): " + items.length, "EXECUTING");

        log("INFO", "RECORD_BUILD_START", "送信レコードを構築します。", "EXECUTING");
        var records = buildRecordsFromItems_(items, course, category, CONFIG, thresholdDate, log);
        log("INFO", "RECORD_BUILD_COMPLETE", "構築完了。有効レコード: " + records.length, "EXECUTING");

        if (!records.length) continue;

        if (stats.planned + records.length > CONFIG.MAX_RECORDS_PER_RUN) {
          log("INFO", "LIMIT_TRUNCATE", "実行上限により件数を調整します。", "EXECUTING");
          records = records.slice(0, CONFIG.MAX_RECORDS_PER_RUN - stats.planned);
        }
        stats.planned += records.length;

        var result = sendRecordsWithFullLogging_(records, CONFIG, log);
        stats.sent += result.sent;
        stats.skipped += result.skipped;

        Utilities.sleep(CONFIG.SLEEP_MS);
        if (stats.planned >= CONFIG.MAX_RECORDS_PER_RUN) break;
      }
      if (stats.planned >= CONFIG.MAX_RECORDS_PER_RUN) break;
    }

    var endStatus = stats.failed > 0 ? "FAILED" : "SUCCESS";
    var endLevel = stats.failed > 0 ? "ERROR" : "INFO";
    log(endLevel, "END_PROCESS", "すべての同期工程が完了しました。", endStatus, stats);
  } catch (e) {
    log("ERROR", "FATAL_ERROR", e.toString(), "FAILED");
  } finally {
    lock.releaseLock();
    log("INFO", "LOCK_RELEASE", "スクリプトロックを解放しました。", "EXECUTING");
  }
}

function classroomDueDateToIso_(dueDate) {
  if (!dueDate || dueDate.year == null || dueDate.month == null || dueDate.day == null) return null;
  function z(n) { return (n < 10 ? '0' : '') + n; }
  return dueDate.year + '-' + z(dueDate.month) + '-' + z(dueDate.day);
}

function classroomDueTimeToText_(t) {
  if (!t) return null;
  if (typeof t === 'string') return t;
  if (t.hours == null || t.minutes == null) return null;
  function z(n) { return (n < 10 ? '0' : '') + n; }
  return z(t.hours) + ':' + z(t.minutes);
}

/**
 * 01_raw + file_id（DB 重複抑止）用の行。Drive コピーは file_name があるときのみ。
 */
function buildRecordsFromItems_(items, course, category, cfg, thresholdDate, log) {
  var out = [];
  var thresholdMs = thresholdDate.getTime();

  items.forEach(function(it) {
    if (!it.creationTime) {
      log("ERROR", "CREATION_TIME_MISSING", "投稿の作成日時(creationTime)が存在しません。itemId=" + it.id, "FAILED");
      return;
    }
    var sentAt = it.creationTime;
    if (new Date(sentAt).getTime() < thresholdMs) return;

    var postUrl = 'https://classroom.google.com/u/0/c/' + course.id + '/a/' + it.id;

    var mats = [];
    if (it.materials) {
      if (Array.isArray(it.materials)) {
        mats = it.materials;
      } else {
        log("ERROR", "MATERIALS_NOT_ARRAY", "materials が配列ではありません。itemId=" + it.id, "FAILED", it.materials);
      }
    }
    var nonDriveText = extractNonDriveAttachmentsText_(mats, course.id, it.id, log);

    var rawDesc = null;
    if (category === 'announcements') {
      rawDesc = it.text ? String(it.text) : null;
    } else if (category === 'courseWork' || category === 'courseWorkMaterials') {
      rawDesc = it.description ? String(it.description) : null;
    }

    var finalDesc = rawDesc;
    if (nonDriveText) {
      if (finalDesc && String(finalDesc).trim().length > 0) {
        finalDesc = String(finalDesc) + '\n\n' + nonDriveText;
      } else {
        finalDesc = nonDriveText;
      }
    }

    var base = {
      person: cfg.PERSON,
      source: cfg.WORKSPACE_NAME,
      category: category,
      post_id: String(it.id),
      post_type: category,
      course_id: String(course.id),
      course_name: course.name || null,
      topic_id: it.topicId ? String(it.topicId) : null,
      topic_name: null,
      title: it.title || null,
      description: finalDesc,
      state: it.state || null,
      due_date: classroomDueDateToIso_(it.dueDate),
      due_time: classroomDueTimeToText_(it.dueTime),
      creator_email: it.creatorProfile ? it.creatorProfile.emailAddress : null,
      creator_name: (it.creatorProfile && it.creatorProfile.name) ? it.creatorProfile.name.fullName : null,
      source_url: postUrl,
      created_at: sentAt,
      updated_at: it.updateTime ? it.updateTime : null,
      file_url: null,
      file_name: null,
      file_id: null
    };

    var driveFiles = mats.filter(function(m) { return m && m.driveFile; });

    if (driveFiles.length > 0) {
      driveFiles.forEach(function(m) {
        if (!m.driveFile || !m.driveFile.driveFile) {
          log("ERROR", "DRIVE_FILE_PAYLOAD_INVALID", "driveFile オブジェクトが不正です。courseId=" + course.id + " itemId=" + it.id, "FAILED", m);
          return;
        }
        var df = m.driveFile.driveFile;
        if (!df.id) {
          log("ERROR", "DRIVE_FILE_ID_MISSING", "driveFile の id が存在しません。courseId=" + course.id + " itemId=" + it.id, "FAILED", df);
          return;
        }
        if (!df.title) {
          log("ERROR", "DRIVE_FILE_TITLE_MISSING", "driveFile の title が存在しません。courseId=" + course.id + " itemId=" + it.id + " fileId=" + df.id, "FAILED", df);
        }
        out.push(Object.assign({}, base, {
          file_id: String(df.id),
          file_name: df.title ? String(df.title).trim() : null
        }));
      });
    } else {
      out.push(Object.assign({}, base, {
        file_id: String(it.id) + '_text',
        file_name: null
      }));
    }
  });
  return out;
}

/**
 * Drive以外の添付（YouTube動画、リンク、フォーム等）から題名とURLを抽出して文字列化する。
 * 制約（フォールバック絶対禁止）:
 * - URLが無い添付は不完全データとして除外し、エラーログを出力して明示的に扱う。
 * - 題名・URL両方が欠損している場合もエラーログを出力し除外する。
 * - 推測値・既定値・別キーで埋めて不完全な添付を出力行に含めない。
 */
function extractNonDriveAttachmentsText_(mats, courseId, itemId, log) {
  if (!mats || !mats.length) return null;

  var lines = [];
  for (var i = 0; i < mats.length; i++) {
    var m = mats[i];
    if (!m || m.driveFile) continue;

    var title = null;
    var url = null;
    var type = null;

    if (m.youtubeVideo) {
      type = 'youtubeVideo';
      if (m.youtubeVideo.title && String(m.youtubeVideo.title).trim()) {
        title = String(m.youtubeVideo.title).trim();
      }
      if (m.youtubeVideo.alternateLink && String(m.youtubeVideo.alternateLink).trim()) {
        url = String(m.youtubeVideo.alternateLink).trim();
      }
    } else if (m.link) {
      type = 'link';
      if (m.link.title && String(m.link.title).trim()) {
        title = String(m.link.title).trim();
      }
      if (m.link.url && String(m.link.url).trim()) {
        url = String(m.link.url).trim();
      }
    } else if (m.form) {
      type = 'form';
      if (m.form.title && String(m.form.title).trim()) {
        title = String(m.form.title).trim();
      }
      if (m.form.formUrl && String(m.form.formUrl).trim()) {
        url = String(m.form.formUrl).trim();
      }
    } else if (m.gem) {
      type = 'gem';
      if (m.gem.title && String(m.gem.title).trim()) title = String(m.gem.title).trim();
      if (m.gem.url && String(m.gem.url).trim()) url = String(m.gem.url).trim();
    } else if (m.notebook) {
      type = 'notebook';
      if (m.notebook.title && String(m.notebook.title).trim()) title = String(m.notebook.title).trim();
      if (m.notebook.url && String(m.notebook.url).trim()) url = String(m.notebook.url).trim();
    } else {
      type = 'unknown';
    }

    if (!url) {
      if (title) {
        log("ERROR", "ATTACHMENT_URL_MISSING", "添付のURLが存在しないため除外します。type=" + type + " title=" + title + " courseId=" + courseId + " itemId=" + itemId, "FAILED", m);
      } else {
        log("ERROR", "ATTACHMENT_DATA_EMPTY", "添付の題名・URLが両方とも存在しません。type=" + type + " courseId=" + courseId + " itemId=" + itemId, "FAILED", m);
      }
      continue;
    }

    if (title) {
      lines.push(title + ': ' + url);
    } else {
      lines.push(url);
    }
  }

  return lines.length > 0 ? lines.join('\n') : null;
}

function buildManagedCopyFileName_(r) {
  if (!r.file_name || !String(r.file_name).trim()) {
    throw new Error("ファイル名が欠損しています。file_id=" + r.file_id);
  }
  var safe = String(r.file_name).replace(/[\\/:*?"<>|]+/g, '_').trim();
  if (!safe.length) {
    throw new Error("サニタイズ後のファイル名が空です。元のfile_name=" + r.file_name + " file_id=" + r.file_id);
  }
  return safe + ' [' + r.file_id + ']';
}

function getOrCreateManagedCopy_(r, destFolder, log) {
  var destName = buildManagedCopyFileName_(r);
  var iter = destFolder.getFilesByName(destName);
  if (iter.hasNext()) {
    var existing = iter.next();
    log("INFO", "FILE_REUSE", "既存コピーを再利用: " + destName, "EXECUTING");
    return existing;
  }
  log("INFO", "FILE_COPY_START", "Google Driveファイルのコピーを開始します。ID: " + r.file_id, "EXECUTING");
  return DriveApp.getFileById(r.file_id).makeCopy(destName, destFolder);
}

function sendRecordsWithFullLogging_(records, cfg, log) {
  var sent = 0;
  var skipped = 0;
  var destFolder = DriveApp.getFolderById(cfg.DEST_FOLDER_ID);

  for (var start = 0; start < records.length; start += cfg.BATCH_SIZE) {
    var batch = records.slice(start, start + cfg.BATCH_SIZE);

    log("INFO", "DB_DUPLICATE_CHECK_START", "既存レコードの重複確認を試行します。", "EXECUTING");
    var fileIds = batch.map(function(r) { return r.file_id; });
    var existMap = fetchExistingFileIdsMap_(fileIds, cfg, log);

    if (existMap === null) {
      log("ERROR", "DUPLICATE_CHECK_ABORT", "重複確認に失敗したためこのバッチをスキップします（Drive コピーなし）。", "FAILED");
      skipped += batch.length;
      continue;
    }

    var filteredBatch = [];
    for (var i = 0; i < batch.length; i++) {
      var r = batch[i];
      if (existMap[r.file_id]) {
        log("INFO", "SKIP_DUPLICATE", "既存データのためスキップ: " + r.file_id, "EXECUTING");
        skipped++;
        continue;
      }

      if (r.file_name) {
        try {
          var newFile = getOrCreateManagedCopy_(r, destFolder, log);
          r.file_url = newFile.getUrl();
          log("INFO", "FILE_COPY_SUCCESS", "コピーまたは再利用完了。URL: " + r.file_url, "EXECUTING");
        } catch (e) {
          log("ERROR", "FILE_COPY_FAILED", "コピー失敗: " + r.file_name, "FAILED", e.toString());
          skipped++;
          continue;
        }
      }
      filteredBatch.push(r);
    }

    if (filteredBatch.length > 0) {
      log("INFO", "SUPABASE_INSERT_START", "01_raw へ送信します。件数: " + filteredBatch.length, "EXECUTING");
      var inserted = insertRawRowsReturning_(filteredBatch, cfg, log);
      if (inserted === null) {
        skipped += filteredBatch.length;
        log("ERROR", "SUPABASE_INSERT_FAILED", "01_raw 書き込みに失敗しました。", "FAILED");
      } else {
        sent += inserted.length;
        log("INFO", "SUPABASE_INSERT_SUCCESS", "01_raw 送信完了。新規行数: " + inserted.length, "EXECUTING");
        if (inserted.length > 0) {
          if (!insertPipelineMetaForRawRows_(inserted, cfg, log)) {
            log("ERROR", "PIPELINE_INSERT_FAILED", "pipeline_meta への書き込みに失敗しました（01_raw は登録済み）。", "FAILED");
          }
        }
      }
    }
  }
  return { sent: sent, skipped: skipped };
}

/**
 * @return {Object|null} 成功時は { file_id: true }、失敗時は null
 */
function fetchExistingFileIdsMap_(ids, cfg, log) {
  if (!ids || !ids.length) return {};

  var inner = ids.map(function(id) { return encodeURIComponent(id); }).join(',');
  var url = cfg.SUPABASE_URL + '/rest/v1/' + cfg.TABLE_NAME + '?select=file_id&file_id=in.(' + inner + ')';

  log("INFO", "EXTERNAL_API_REQUEST", "DB重複確認リクエスト送信。", "EXECUTING");
  var res = UrlFetchApp.fetch(url, {
    headers: { apikey: cfg.SUPABASE_KEY, Authorization: 'Bearer ' + cfg.SUPABASE_KEY },
    muteHttpExceptions: true
  });

  if (res.getResponseCode() !== 200) {
    log("ERROR", "EXTERNAL_API_ERROR", "既存データ取得失敗。", "FAILED", res.getContentText());
    return null;
  }

  try {
    var rows = JSON.parse(res.getContentText() || '[]');
    if (!Array.isArray(rows)) {
      log("ERROR", "EXTERNAL_API_PARSE", "既存データの JSON が配列ではありません。", "FAILED", res.getContentText());
      return null;
    }
    var map = {};
    rows.forEach(function(row) { map[row.file_id] = true; });
    log("INFO", "EXTERNAL_API_SUCCESS", "既存データ取得成功。取得数: " + rows.length, "EXECUTING");
    return map;
  } catch (e) {
    log("ERROR", "EXTERNAL_API_PARSE", "既存データの JSON 解析に失敗。", "FAILED", e.toString());
    return null;
  }
}

/**
 * @return {Array|null} 今回新規 INSERT された行（id 付き）。失敗時 null。
 */
function insertRawRowsReturning_(records, cfg, log) {
  var url = cfg.SUPABASE_URL + '/rest/v1/' + cfg.TABLE_NAME + '?on_conflict=file_id';
  var res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      apikey: cfg.SUPABASE_KEY,
      Authorization: 'Bearer ' + cfg.SUPABASE_KEY,
      Prefer: 'return=representation,resolution=ignore-duplicates'
    },
    payload: JSON.stringify(records),
    muteHttpExceptions: true
  });

  var code = res.getResponseCode();
  if (code < 200 || code >= 300) {
    log("ERROR", "DB_WRITE_ERROR", "01_raw 書き込み失敗。コード: " + code, "FAILED", res.getContentText());
    return null;
  }

  try {
    var rows = JSON.parse(res.getContentText() || '[]');
    return Array.isArray(rows) ? rows : null;
  } catch (e) {
    log("ERROR", "DB_WRITE_PARSE", "01_raw 応答の JSON 解析に失敗。", "FAILED", e.toString());
    return null;
  }
}

/**
 * 新規 01_raw 行ごとに pipeline_meta を 1 行ずつ投入する。
 */
function insertPipelineMetaForRawRows_(rawRows, cfg, log) {
  var metaTable = cfg.PIPELINE_TABLE || 'pipeline_meta';
  var rows = rawRows.map(function(row) {
    return {
      raw_id: row.id,
      raw_table: cfg.TABLE_NAME,
      person: cfg.PERSON,
      source: cfg.WORKSPACE_NAME,
      owner_id: cfg.OWNER_ID,
      processing_status: 'pending'
    };
  });

  var url = cfg.SUPABASE_URL + '/rest/v1/' + metaTable + '?on_conflict=raw_id,raw_table';
  var res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      apikey: cfg.SUPABASE_KEY,
      Authorization: 'Bearer ' + cfg.SUPABASE_KEY,
      Prefer: 'return=minimal,resolution=ignore-duplicates'
    },
    payload: JSON.stringify(rows),
    muteHttpExceptions: true
  });

  var code = res.getResponseCode();
  if (code >= 200 && code < 300) {
    log("INFO", "PIPELINE_INSERT_SUCCESS", "pipeline_meta 送信完了。件数: " + rows.length, "EXECUTING");
    return true;
  }
  log("ERROR", "PIPELINE_WRITE_ERROR", "pipeline_meta 失敗。コード: " + code, "FAILED", res.getContentText());
  return false;
}

/**
 * @param {string} propertyPrefix 例 'IKUYA_' / 'EMA_' / ''（空は接頭辞なしのみ読む）
 */
function loadConfig_(propertyPrefix) {
  var p = PropertiesService.getScriptProperties();
  var pre = propertyPrefix || '';

  function s(key) {
    var v = '';
    if (pre) {
      var pv = p.getProperty(pre + key);
      v = pv == null ? '' : String(pv).trim();
    }
    if (!v) {
      var bv = p.getProperty(key);
      v = bv == null ? '' : String(bv).trim();
    }
    return v;
  }

  function n(key, defStr) {
    var raw = '';
    if (pre) {
      var pn = p.getProperty(pre + key);
      raw = pn == null ? '' : String(pn).trim();
    }
    if (!raw) {
      var bn = p.getProperty(key);
      raw = bn == null ? '' : String(bn).trim();
    }
    if (!raw) raw = defStr;
    return parseInt(raw, 10);
  }

  return {
    _propertyPrefix: pre,
    DEST_FOLDER_ID: s('DEST_FOLDER_ID'),
    OWNER_ID: s('OWNER_ID'),
    SUPABASE_URL: s('SUPABASE_URL').replace(/\/+$/, ''),
    SUPABASE_KEY: s('SUPABASE_KEY'),
    TABLE_NAME: s('TABLE_NAME'),
    PIPELINE_TABLE: s('PIPELINE_TABLE') || 'pipeline_meta',
    WORKSPACE_NAME: s('WORKSPACE_NAME'),
    PERSON: s('PERSON'),
    MAX_RECORDS_PER_RUN: n('MAX_RECORDS_PER_RUN', '500'),
    LOOKBACK_DAYS: n('LOOKBACK_DAYS', '365'),
    BATCH_SIZE: n('BATCH_SIZE', '25'),
    SLEEP_MS: n('SLEEP_MS', '600')
  };
}

/**
 * 文字列の必須キーはすべて非空（数値は loadConfig_ 側で既定あり）。
 */
function validateConfig_(cfg, log, propertyPrefix) {
  var req = ['DEST_FOLDER_ID', 'OWNER_ID', 'SUPABASE_URL', 'SUPABASE_KEY', 'TABLE_NAME', 'WORKSPACE_NAME', 'PERSON'];
  var pre = propertyPrefix || '';
  for (var i = 0; i < req.length; i++) {
    var k = req[i];
    if (!cfg[k]) {
      if (log) {
        log("ERROR", "CONFIG_MISSING", "プロパティが未設定または空: " + k +
          "（" + (pre ? "試行キー: " + pre + k + " または " + k : "キー: " + k) + "）", "FAILED");
      }
      return false;
    }
  }
  return true;
}

function listAllCourses_() {
  var courses = [], pageToken = null;
  do {
    var params = { pageSize: 50, courseStates: ['ACTIVE'] };
    if (pageToken) params.pageToken = pageToken;
    var resp = Classroom.Courses.list(params);
    if (resp && resp.courses && Array.isArray(resp.courses)) {
      courses = courses.concat(resp.courses);
    }
    pageToken = resp ? resp.nextPageToken : null;
  } while (pageToken);
  return courses;
}

function listCategoryItems_(courseId, cat) {
  var items = [], pageToken = null;
  var methods = { announcements: 'Announcements', courseWork: 'CourseWork', courseWorkMaterials: 'CourseWorkMaterials' };
  if (!methods[cat]) {
    throw new Error("未対応のカテゴリーです: " + cat);
  }
  do {
    var params = { pageSize: 50 };
    if (pageToken) params.pageToken = pageToken;
    var resp = Classroom.Courses[methods[cat]].list(courseId, params);
    var key = (cat === 'courseWorkMaterials') ? 'courseWorkMaterial' : cat;
    if (resp && resp[key] && Array.isArray(resp[key])) {
      items = items.concat(resp[key]);
    }
    pageToken = resp ? resp.nextPageToken : null;
  } while (pageToken);
  return items;
}
