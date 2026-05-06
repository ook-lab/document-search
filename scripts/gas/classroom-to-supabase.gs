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

    var stats = { sent: 0, skipped: 0, filtered: 0, planned: 0 };
    var categories = ['announcements', 'courseWork', 'courseWorkMaterials'];

    for (var ci = 0; ci < courses.length; ci++) {
      var course = courses[ci];
      log("INFO", "COURSE_PROCESSING", "コース処理開始: " + course.name + " (ID: " + course.id + ")", "EXECUTING");

      for (var catI = 0; catI < categories.length; catI++) {
        var category = categories[catI];
        log("INFO", "CATEGORY_FETCH", "カテゴリー取得試行: " + category, "EXECUTING");
        var items = listCategoryItems_(course.id, category);

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

    log("INFO", "END_PROCESS", "すべての同期工程が完了しました。", "SUCCESS", stats);
  } catch (e) {
    log("ERROR", "FATAL_ERROR", e.toString(), "FAILED");
  } finally {
    lock.releaseLock();
    log("INFO", "LOCK_RELEASE", "スクリプトロックを解放しました。", "EXECUTING");
  }
}

function classroomDueDateToIso_(dueDate) {
  if (!dueDate || dueDate.year == null) return null;
  var mo = dueDate.month != null ? dueDate.month : 1;
  var da = dueDate.day != null ? dueDate.day : 1;
  function z(n) { return (n < 10 ? '0' : '') + n; }
  return dueDate.year + '-' + z(mo) + '-' + z(da);
}

function classroomDueTimeToText_(t) {
  if (!t) return null;
  if (typeof t === 'string') return t;
  if (t.hours == null && t.minutes == null) return null;
  var h = t.hours != null ? t.hours : 0;
  var m = t.minutes != null ? t.minutes : 0;
  function z(n) { return (n < 10 ? '0' : '') + n; }
  return z(h) + ':' + z(m);
}

/**
 * 01_raw + file_id（DB 重複抑止）用の行。Drive コピーは file_name があるときのみ。
 */
function buildRecordsFromItems_(items, course, category, cfg, thresholdDate, log) {
  var out = [];
  var thresholdMs = thresholdDate.getTime();

  items.forEach(function(it) {
    var sentAt = it.creationTime || it.updateTime;
    if (new Date(sentAt).getTime() < thresholdMs) return;

    var postUrl = 'https://classroom.google.com/u/0/c/' + course.id + '/a/' + it.id;

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
      description: it.text || it.description || null,
      state: it.state || null,
      due_date: classroomDueDateToIso_(it.dueDate),
      due_time: classroomDueTimeToText_(it.dueTime),
      creator_email: it.creatorProfile ? it.creatorProfile.emailAddress : null,
      creator_name: (it.creatorProfile && it.creatorProfile.name) ? it.creatorProfile.name.fullName : null,
      source_url: postUrl,
      created_at: sentAt,
      updated_at: it.updateTime || sentAt,
      file_url: null,
      file_name: null,
      file_id: null
    };

    var mats = it.materials || it.material || [];
    var driveFiles = mats.filter(function(m) { return m.driveFile; });

    if (driveFiles.length > 0) {
      driveFiles.forEach(function(m) {
        var df = m.driveFile.driveFile;
        out.push(Object.assign({}, base, {
          file_id: df.id,
          file_name: df.title || df.name
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

function buildManagedCopyFileName_(r) {
  var base = r.file_name || 'Classroom';
  var safe = String(base).replace(/[\\/:*?"<>|]+/g, '_').trim();
  if (!safe.length) safe = 'Classroom';
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
    var resp = Classroom.Courses.list({ pageSize: 50, pageToken: pageToken || undefined, courseStates: ['ACTIVE'] });
    courses = courses.concat(resp.courses || []);
    pageToken = resp.nextPageToken;
  } while (pageToken);
  return courses;
}

function listCategoryItems_(courseId, cat) {
  var items = [], pageToken = null;
  var methods = { announcements: 'Announcements', courseWork: 'CourseWork', courseWorkMaterials: 'CourseWorkMaterials' };
  do {
    var resp;
    try {
      resp = Classroom.Courses[methods[cat]].list(courseId, { pageSize: 50, pageToken: pageToken || undefined });
      var key = (cat === 'courseWorkMaterials') ? 'courseWorkMaterial' : cat;
      items = items.concat(resp[key] || []);
      pageToken = resp.nextPageToken;
    } catch (e) { break; }
  } while (pageToken);
  return items;
}
