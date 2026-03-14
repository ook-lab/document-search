-- ============================================================
-- classify_results に rawdata_id を追加し、
-- v_classify_summary / v_classify_detail に processing_status を含める
-- ============================================================

-- rawdata_id カラムを追加（本番パイプライン経由で書き込まれた行のみ非NULL）
ALTER TABLE classify_results
    ADD COLUMN IF NOT EXISTS rawdata_id UUID REFERENCES "Rawdata_FILE_AND_MAIL"(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_classify_results_rawdata_id ON classify_results (rawdata_id);

-- ── サマリービュー（再定義）─────────────────────────────────────
DROP VIEW IF EXISTS v_classify_summary;
CREATE VIEW v_classify_summary AS
SELECT
    r.id                                                                        AS result_id,
    r.created_at,
    r.filename,
    r.creator,
    r.producer,
    r.verdict                                                                   AS doc_verdict,
    r.reason                                                                    AS doc_reason,
    r.page_count,
    r.error_msg,
    rd.processing_status,
    -- verdict別ページ内訳
    COUNT(p.id)                                                                 AS analyzed_pages,
    COUNT(p.id) FILTER (WHERE p.verdict = 'SCAN')                               AS scan_pages,
    COUNT(p.id) FILTER (WHERE p.verdict = 'WORD')                               AS word_pages,
    COUNT(p.id) FILTER (WHERE p.verdict = 'GOOGLE_DOCS')                        AS google_docs_pages,
    COUNT(p.id) FILTER (WHERE p.verdict = 'REPORT')                             AS report_pages,
    COUNT(p.id) FILTER (WHERE p.verdict = 'DTP')                                AS dtp_pages,
    COUNT(p.id) FILTER (WHERE p.verdict = 'EXCEL')                              AS excel_pages,
    COUNT(p.id) FILTER (WHERE p.verdict = 'UNKNOWN')                            AS unknown_pages,
    -- 特徴量集計
    SUM(p.chars)                                                                AS total_chars,
    SUM(p.images)                                                               AS total_images,
    COUNT(p.id) FILTER (WHERE p.has_selectable_text)                            AS selectable_pages,
    COUNT(p.id) FILTER (WHERE jsonb_array_length(COALESCE(p.wing_fonts,'[]'::jsonb)) > 0) AS wing_pages
FROM classify_results r
LEFT JOIN classify_page_details p ON p.result_id = r.id
LEFT JOIN "Rawdata_FILE_AND_MAIL" rd ON rd.id = r.rawdata_id
GROUP BY
    r.id, r.created_at, r.filename, r.creator, r.producer,
    r.verdict, r.reason, r.page_count, r.error_msg, rd.processing_status
ORDER BY r.created_at DESC;

COMMENT ON VIEW v_classify_summary IS '文書単位集計ビュー（verdict別ページ数・文字数）。通常の確認はこれを見る。';

-- ── 詳細ビュー（再定義）────────────────────────────────────────
DROP VIEW IF EXISTS v_classify_detail;
CREATE VIEW v_classify_detail AS
SELECT
    r.id                AS result_id,
    r.created_at,
    r.filename,
    r.creator,
    r.producer,
    r.pdf_title,
    r.verdict           AS doc_verdict,
    r.reason            AS doc_reason,
    r.page_count,
    r.error_msg,
    rd.processing_status,
    p.page_num,
    p.verdict           AS page_verdict,
    p.reason            AS page_reason,
    p.chars,
    p.images,
    p.vectors,
    p.has_selectable_text,
    p.x0_std,
    p.fonts,
    p.wing_fonts,
    p.colorspaces,
    p.filters,
    r.raw_meta,
    r.rawdata_id
FROM classify_results r
LEFT JOIN classify_page_details p ON p.result_id = r.id
LEFT JOIN "Rawdata_FILE_AND_MAIL" rd ON rd.id = r.rawdata_id
ORDER BY r.created_at DESC, r.filename, p.page_num;

COMMENT ON VIEW v_classify_detail IS 'ページ展開詳細ビュー（1行 = 1ページ）。processing_status は本番パイプライン経由の行のみ取得可能。';
