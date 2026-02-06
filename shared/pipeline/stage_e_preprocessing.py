"""
Stage E: Pre-processing (前処理) - 物理抽出専用（AI排除版）

【設計 2026-01-26】コスト最適化のためAIを完全排除

役割: ファイルからの「物理的テキスト抽出」のみ
- PDF: pdfplumber / PyMuPDF でテキスト抽出
- Office: OfficeProcessor でテキスト抽出
- テキスト: エンコーディング検出して読み込み
- 画像/音声/動画: raw_text="" で返す（Stage F-7で処理）

処理フロー（E-3で終了）:
- E-1: ファイル検証
- E-2: MIMEタイプルーティング
- E-3: 物理テキスト抽出（AI不使用）

【重要】
- 添付なしの場合は Stage E をスキップし、Stage G からスタート
- 画像/音声/動画は raw_text="" で返し、Stage F-7 で AI 処理
"""
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

from shared.common.processors.pdf import PDFProcessor
from shared.common.processors.office import OfficeProcessor


class StageEPreprocessor:
    """Stage E: 前処理（物理抽出専用、AI排除版）"""

    # MIMEタイプ分類
    DOCUMENT_MIME_TYPES = {
        'application/pdf',
        'text/plain',
        'text/html',
        'text/csv',
        'text/markdown',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # .docx
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',        # .xlsx
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',  # .pptx
        'application/msword',  # .doc
        'application/vnd.ms-excel',  # .xls
        'application/vnd.ms-powerpoint',  # .ppt
    }

    IMAGE_MIME_TYPES = {
        'image/png',
        'image/jpeg',
        'image/jpg',
        'image/gif',
        'image/webp',
        'image/bmp',
        'image/tiff',
    }

    AUDIO_MIME_TYPES = {
        'audio/mpeg',
        'audio/mp3',
        'audio/wav',
        'audio/x-wav',
        'audio/ogg',
        'audio/flac',
        'audio/aac',
        'audio/m4a',
        'audio/x-m4a',
        'audio/webm',
    }

    VIDEO_MIME_TYPES = {
        'video/mp4',
        'video/mpeg',
        'video/quicktime',
        'video/x-msvideo',
        'video/webm',
        'video/x-matroska',
        'video/avi',
        'video/mov',
    }

    def __init__(self):
        """Stage E 前処理（AI不使用）"""
        self.pdf_processor = PDFProcessor(llm_client=None)
        self.office_processor = OfficeProcessor()

    def extract_text(
        self,
        file_path: Path,
        mime_type: str,
        pre_extracted_text: Optional[str] = None,
        workspace: Optional[str] = None,
        progress_callback=None
    ) -> Dict[str, Any]:
        """
        ファイルからテキストを物理的に抽出（AI不使用）

        Args:
            file_path: ファイルパス
            mime_type: MIMEタイプ
            pre_extracted_text: 既に抽出済みのテキスト（HTML→PNG等の場合）
            workspace: ワークスペース（未使用）
            progress_callback: 進捗コールバック

        Returns:
            {
                'success': bool,
                'content': str,  # 物理抽出テキスト（画像/音声/動画は空）
                'char_count': int,
                'method': str,
                'metadata': dict,
                'requires_vision': bool,  # Stage F で Vision 処理が必要か
                'requires_transcription': bool  # Stage F で音声書き起こしが必要か
            }
        """
        logger.info("=" * 60)
        logger.info("[Stage E] 物理抽出開始（AI排除版）")

        # ============================================
        # [E-1] ファイル検証
        # ============================================
        if progress_callback:
            progress_callback("E-1")

        # ファイルなしの場合
        if file_path is None:
            logger.info("  └─ ファイルなし → Stage E スキップ推奨")
            content = pre_extracted_text or ""
            return {
                'success': True,
                'content': content,
                'char_count': len(content),
                'method': 'no_file',
                'metadata': {'skip_stage_e': True},
                'requires_vision': False,
                'requires_transcription': False
            }

        file_path = Path(file_path)
        logger.info(f"  ├─ ファイル: {file_path.name}")
        logger.info(f"  └─ MIMEタイプ: {mime_type}")

        if not file_path.exists():
            logger.error(f"[Stage E] ファイルが存在しません: {file_path}")
            return {
                'success': False,
                'content': '',
                'char_count': 0,
                'method': 'error',
                'error': f'File not found: {file_path}',
                'requires_vision': False,
                'requires_transcription': False
            }

        # ============================================
        # [E-2] MIMEタイプルーティング
        # ============================================
        if progress_callback:
            progress_callback("E-2")

        try:
            # ============================================
            # [E-3] 物理テキスト抽出
            # ============================================
            if progress_callback:
                progress_callback("E-3")

            # --- ドキュメント処理（物理抽出） ---
            if mime_type in self.DOCUMENT_MIME_TYPES or mime_type == 'application/pdf':
                content, method, metadata = self._extract_document(
                    file_path, mime_type, pre_extracted_text
                )
                return {
                    'success': True,
                    'content': content,
                    'char_count': len(content),
                    'method': method,
                    'metadata': metadata,
                    'requires_vision': True,  # PDF/Officeも画像ページがある可能性
                    'requires_transcription': False
                }

            # --- 画像処理（物理抽出不可 → Stage F-7へ） ---
            elif mime_type in self.IMAGE_MIME_TYPES or mime_type.startswith('image/'):
                logger.info("[E-3] 画像ファイル → 物理抽出不可、Stage F-7 で処理")
                content = pre_extracted_text or ""
                return {
                    'success': True,
                    'content': content,
                    'char_count': len(content),
                    'method': 'image_passthrough',
                    'metadata': {'mime_type': mime_type, 'file_path': str(file_path)},
                    'requires_vision': True,
                    'requires_transcription': False
                }

            # --- 音声処理（物理抽出不可 → Stage F-7へ） ---
            elif mime_type in self.AUDIO_MIME_TYPES or mime_type.startswith('audio/'):
                logger.info("[E-3] 音声ファイル → 物理抽出不可、Stage F-7 で処理")
                return {
                    'success': True,
                    'content': '',
                    'char_count': 0,
                    'method': 'audio_passthrough',
                    'metadata': {'mime_type': mime_type, 'file_path': str(file_path)},
                    'requires_vision': False,
                    'requires_transcription': True
                }

            # --- 動画処理（物理抽出不可 → Stage F-7へ） ---
            elif mime_type in self.VIDEO_MIME_TYPES or mime_type.startswith('video/'):
                logger.info("[E-3] 動画ファイル → 物理抽出不可、Stage F-7 で処理")
                return {
                    'success': True,
                    'content': '',
                    'char_count': 0,
                    'method': 'video_passthrough',
                    'metadata': {'mime_type': mime_type, 'file_path': str(file_path)},
                    'requires_vision': True,  # 動画のフレーム解析
                    'requires_transcription': True  # 音声書き起こし
                }

            # --- 未対応MIMEタイプ ---
            else:
                logger.warning(f"[E-3] 未対応のMIMEタイプ: {mime_type}")
                return {
                    'success': True,
                    'content': pre_extracted_text or '',
                    'char_count': len(pre_extracted_text or ''),
                    'method': 'unsupported',
                    'metadata': {'mime_type': mime_type},
                    'requires_vision': False,
                    'requires_transcription': False
                }

        except Exception as e:
            logger.error(f"[Stage E エラー] 物理抽出失敗: {e}", exc_info=True)
            return {
                'success': False,
                'content': '',
                'char_count': 0,
                'method': 'error',
                'error': str(e),
                'requires_vision': False,
                'requires_transcription': False
            }

    def _extract_document(
        self,
        file_path: Path,
        mime_type: str,
        pre_extracted_text: Optional[str]
    ) -> tuple:
        """
        ドキュメントから物理的にテキストを抽出（AI不使用）

        Returns:
            (content, method, metadata)
        """
        logger.info("[E-3] ドキュメント物理抽出開始")

        content = ""
        method = "document"
        metadata = {}

        # PDF処理
        if mime_type == 'application/pdf':
            logger.info("  ├─ PDF処理（pdfplumber/PyMuPDF）")
            result = self.pdf_processor.extract_text(str(file_path), progress_callback=None)
            if result.get('success'):
                content = result.get('content', '')
                method = 'pdf_physical'
                metadata = result.get('metadata', {})

            # 【Ver 6.4】座標付き1文字リストを抽出（物理証拠）
            try:
                import pdfplumber
                physical_chars = []
                with pdfplumber.open(str(file_path)) as pdf:
                    for page_idx, page in enumerate(pdf.pages):
                        w, h = float(page.width), float(page.height)
                        for char in page.chars:
                            # Stage F と同じ 1000x1000 グリッドに正規化
                            physical_chars.append({
                                "text": char.get("text", ""),
                                "bbox": [
                                    int(char.get("x0", 0) * 1000 / w) if w > 0 else 0,
                                    int(char.get("top", 0) * 1000 / h) if h > 0 else 0,
                                    int(char.get("x1", 0) * 1000 / w) if w > 0 else 0,
                                    int(char.get("bottom", 0) * 1000 / h) if h > 0 else 0
                                ],
                                "page": page_idx
                            })
                metadata['physical_chars'] = physical_chars
                logger.info(f"  ├─ 座標付き文字: {len(physical_chars)} 文字")

                # 【E-1】全文字ログ出力
                logger.info(f"[E-1] ===== 生成物ログ開始（physical_chars） =====")
                logger.info(f"[E-1] physical_chars数: {len(physical_chars)}")
                for i, char in enumerate(physical_chars):
                    text = char.get('text', '')
                    bbox = char.get('bbox', [])
                    page = char.get('page', 0)
                    logger.info(f"[E-1]   [{i+1}] page={page}, bbox={bbox}, text='{text}'")
                logger.info(f"[E-1] ===== 生成物ログ終了 =====")
            except Exception as e:
                logger.warning(f"  ├─ 座標抽出失敗: {e}")

            logger.info(f"  └─ PDF抽出完了: {len(content)} 文字")

        # Office文書処理
        elif 'openxmlformats' in mime_type or 'msword' in mime_type or 'ms-excel' in mime_type or 'ms-powerpoint' in mime_type:
            # ファイルタイプ判定
            if 'word' in mime_type:
                file_type = 'docx'
            elif 'sheet' in mime_type or 'excel' in mime_type:
                file_type = 'xlsx'
            elif 'presentation' in mime_type or 'powerpoint' in mime_type:
                file_type = 'pptx'
            else:
                file_type = 'office'

            logger.info(f"  ├─ Office処理 (type: {file_type})")
            result = self.office_processor.extract_text(str(file_path))
            if result.get('success'):
                content = result.get('content', '')
            method = f'{file_type}_physical'
            logger.info(f"  └─ Office抽出完了: {len(content)} 文字")

        # テキスト系ファイル処理
        elif mime_type.startswith('text/'):
            logger.info("  ├─ テキストファイル処理")
            try:
                # 複数のエンコーディングを試行
                encodings = ['utf-8', 'shift_jis', 'cp932', 'euc-jp', 'iso-2022-jp']
                for encoding in encodings:
                    try:
                        content = file_path.read_text(encoding=encoding)
                        logger.info(f"  ├─ エンコーディング: {encoding}")
                        break
                    except UnicodeDecodeError:
                        continue
                method = 'text_physical'
            except Exception as e:
                logger.error(f"  └─ テキスト読み込みエラー: {e}")
            logger.info(f"  └─ テキスト抽出完了: {len(content)} 文字")

        # pre_extracted_textがある場合は追加
        if pre_extracted_text:
            if content:
                content = pre_extracted_text + "\n\n---\n\n" + content
            else:
                content = pre_extracted_text

        logger.info("=" * 60)
        logger.info(f"[Stage E完了] 物理抽出結果:")
        logger.info(f"  ├─ 処理方式: {method}")
        logger.info(f"  └─ テキスト: {len(content)} 文字")
        logger.info("=" * 60)

        return content, method, metadata

    def process(self, file_path: Path, mime_type: str) -> str:
        """
        ファイルからテキストを抽出（process() エイリアス）

        Args:
            file_path: ファイルパス
            mime_type: MIMEタイプ

        Returns:
            extracted_text: 抽出されたテキスト
        """
        result = self.extract_text(file_path, mime_type)
        return result.get('content', '') if result.get('success') else ''
