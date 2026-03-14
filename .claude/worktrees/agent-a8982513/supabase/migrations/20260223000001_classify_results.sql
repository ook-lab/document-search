-- ============================================================
-- PDF分類検証アプリ（classify_app.py）の結果保存テーブル
-- Rawdata_FILE_AND_MAIL とは独立した検証専用テーブル
-- ============================================================

-- ── 文書単位テーブル ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS classify_results (
    id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT now(),

    -- ファイル識別
    filename    TEXT        NOT NULL,

    -- PDFメタデータ（classify_app.py が抽出したフィールド）
    creator     TEXT,
    producer    TEXT,
    pdf_title   TEXT,       -- PDFのTitleフィールド（Rawdataの title カラムと区別）
    raw_meta    JSONB,      -- pdfplumber + PyMuPDF の全メタデータ

    -- 分類結果
    verdict     TEXT        NOT NULL,
    reason      TEXT,
    page_count  INTEGER,
    error_msg   TEXT
);

COMMENT ON TABLE  classify_results           IS 'classify_app.py の文書単位分類結果';
COMMENT ON COLUMN classify_results.verdict   IS '分類判定 (WORD/SCAN/REPORT/DTP/EXCEL/GOODNOTES/GOOGLE_DOCS/GOOGLE_SHEETS/INDESIGN/ILLUSTRATOR/MIXED/UNKNOWN/ERROR)';
COMMENT ON COLUMN classify_results.pdf_title IS 'PDFメタデータの Title フィールド（Rawdata_FILE_AND_MAIL.title と区別するため pdf_title）';
COMMENT ON COLUMN classify_results.raw_meta  IS 'pdfplumber + PyMuPDF からマージした全メタデータフィールド';

CREATE INDEX IF NOT EXISTS idx_classify_results_created_at ON classify_results (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_classify_results_verdict    ON classify_results (verdict);
CREATE INDEX IF NOT EXISTS idx_classify_results_filename   ON classify_results (filename);

-- ── ページ単位テーブル ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS classify_page_details (
    id                  UUID             DEFAULT gen_random_uuid() PRIMARY KEY,
    result_id           UUID             NOT NULL REFERENCES classify_results(id) ON DELETE CASCADE,
    page_num            INTEGER          NOT NULL,   -- 1始まり
    verdict             TEXT             NOT NULL,
    reason              TEXT,
    chars               INTEGER,
    images              INTEGER,
    vectors             INTEGER,
    has_selectable_text BOOLEAN,
    x0_std              DOUBLE PRECISION,
    fonts               JSONB,
    wing_fonts          JSONB,
    colorspaces         JSONB,
    filters             JSONB
);

COMMENT ON TABLE  classify_page_details              IS 'classify_app.py のページ単位詳細（classify_results に1対多で紐づく）';
COMMENT ON COLUMN classify_page_details.page_num     IS 'ページ番号（1始まり）';
COMMENT ON COLUMN classify_page_details.vectors      IS 'ベクター要素数（lines + curves + rects）';
COMMENT ON COLUMN classify_page_details.x0_std       IS 'x0座標の標準偏差。小さいほど左揃え均一、大きいほど複雑レイアウト';
COMMENT ON COLUMN classify_page_details.fonts        IS '使用フォント名リスト（ABCDEF+ サブセットプレフィックス除去済み）';
COMMENT ON COLUMN classify_page_details.wing_fonts   IS 'WINGフォント検出リスト（REPORTページの根拠）';

CREATE INDEX IF NOT EXISTS idx_classify_page_result_id ON classify_page_details (result_id);
CREATE INDEX IF NOT EXISTS idx_classify_page_verdict   ON classify_page_details (verdict);

-- ============================================================
-- ビュー
-- ============================================================

-- ── サマリービュー（あなたが見る用・文書単位に集計）────────────
CREATE OR REPLACE VIEW v_classify_summary AS
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
GROUP BY
    r.id, r.created_at, r.filename, r.creator, r.producer,
    r.verdict, r.reason, r.page_count, r.error_msg
ORDER BY r.created_at DESC;

COMMENT ON VIEW v_classify_summary IS '文書単位集計ビュー（verdict別ページ数・文字数）。通常の確認はこれを見る。';

-- ── 詳細ビュー（深掘り用・1行 = 1ページ）───────────────────────
CREATE OR REPLACE VIEW v_classify_detail AS
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
    r.raw_meta
FROM classify_results r
LEFT JOIN classify_page_details p ON p.result_id = r.id
ORDER BY r.created_at DESC, r.filename, p.page_num;

COMMENT ON VIEW v_classify_detail IS 'ページ展開詳細ビュー（1行 = 1ページ）。特定ファイルのページ挙動を深掘りするときに使う。';
