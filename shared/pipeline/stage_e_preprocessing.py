"""
Stage E: Pre-processing (前処理) - 完全網羅型マルチモーダル実装

役割: 情報の「網羅的収集」
AIによる「要約」「省略」「まとめ」は一切禁止。
入力ファイルに含まれる情報を物理的に拾える限りすべてテキスト化して出力。

処理フロー:
- E-4a: ドキュメント処理 (PDF, text/*, Office系)
- E-4b: 画像処理 (image/*) - 並列OCR + Description
- E-4c: 音声・映像処理 (audio/*, video/*) - Transcription + Visual Log
- E-5: 出力フォーマット統一（タグ付け）
"""
from pathlib import Path
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger

from shared.common.processors.pdf import PDFProcessor
from shared.common.processors.office import OfficeProcessor
from shared.ai.llm_client.llm_client import LLMClient


class StageEPreprocessor:
    """Stage E: 前処理（完全網羅型マルチモーダル実装）"""

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

    def __init__(self, llm_client: LLMClient):
        """
        Args:
            llm_client: LLMクライアント
        """
        self.llm_client = llm_client
        self.pdf_processor = PDFProcessor(llm_client=llm_client)
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
        ファイルからテキストを抽出（MIMEタイプベースのルーター）

        Args:
            file_path: ファイルパス
            mime_type: MIMEタイプ
            pre_extracted_text: 既に抽出済みのテキスト（HTML→PNG等の場合）
            workspace: ワークスペース（gmail判定に使用）
            progress_callback: 進捗コールバック

        Returns:
            {
                'success': bool,
                'content': str,  # タグ付きフォーマットの完全テキスト
                'char_count': int,
                'method': str,
                'metadata': dict
            }
        """
        logger.info("=" * 60)
        logger.info("[Stage E] Pre-processing開始（完全網羅型）")

        # テキストのみ（ファイルなし）の場合
        if file_path is None:
            logger.info("  ├─ ファイル: なし（テキストのみ）")
            logger.info(f"  └─ MIMEタイプ: {mime_type}")
            logger.info("[Stage E] ファイルなし → pre_extracted_text を使用")

            content = pre_extracted_text or ""
            if content:
                content = self._format_output(
                    mime_type=mime_type or "text/plain",
                    ocr_text=content,
                    visual_log=None,
                    audio_transcript=None
                )

            logger.info("=" * 60)
            logger.info(f"[Stage E完了] テキストのみモード: {len(content)}文字")
            return {
                'success': True,
                'content': content,
                'char_count': len(content),
                'method': 'text_only',
                'metadata': {'text_only': True}
            }

        logger.info(f"  ├─ ファイル: {file_path.name if isinstance(file_path, Path) else file_path}")
        logger.info(f"  └─ MIMEタイプ: {mime_type}")

        file_path = Path(file_path)
        content = ""
        method = "none"
        metadata = {}

        try:
            # MIMEタイプに基づくルーティング
            if mime_type in self.DOCUMENT_MIME_TYPES or mime_type == 'application/pdf':
                # E-4a: ドキュメント処理
                content, method, metadata = self._process_document_e4a(
                    file_path, mime_type, pre_extracted_text, progress_callback
                )

            elif mime_type.startswith('image/'):
                # E-4b: 画像処理（並列OCR + Description）
                content, method, metadata = self._process_image_e4b(
                    file_path, mime_type, pre_extracted_text, workspace, progress_callback
                )

            elif mime_type in self.AUDIO_MIME_TYPES or mime_type.startswith('audio/'):
                # E-4c: 音声処理
                content, method, metadata = self._process_av_e4c(
                    file_path, mime_type, is_video=False, progress_callback=progress_callback
                )

            elif mime_type in self.VIDEO_MIME_TYPES or mime_type.startswith('video/'):
                # E-4c: 映像処理
                content, method, metadata = self._process_av_e4c(
                    file_path, mime_type, is_video=True, progress_callback=progress_callback
                )

            else:
                # 未対応MIMEタイプ: メタデータのみ
                logger.warning(f"[Stage E] 未対応のMIMEタイプ: {mime_type}")
                content = self._format_output(
                    mime_type=mime_type,
                    ocr_text="(未対応のファイル形式)",
                    visual_log=None,
                    audio_transcript=None
                )
                method = "unsupported"

            # 最終ログ
            logger.info("=" * 60)
            logger.info(f"[Stage E完了] 最終結果:")
            logger.info(f"  ├─ 処理方式: {method}")
            logger.info(f"  └─ 最終テキスト: {len(content)} 文字")
            logger.info("=" * 60)

            return {
                'success': True,
                'content': content,
                'char_count': len(content),
                'method': method,
                'metadata': metadata
            }

        except Exception as e:
            logger.error(f"[Stage E エラー] テキスト抽出失敗: {e}", exc_info=True)
            return {
                'success': False,
                'content': '',
                'char_count': 0,
                'method': 'error',
                'error': str(e)
            }

    def _process_document_e4a(
        self,
        file_path: Path,
        mime_type: str,
        pre_extracted_text: Optional[str],
        progress_callback=None
    ) -> tuple:
        """
        [E-4a] ドキュメント処理
        対象: application/pdf, text/*, Office系
        既存ロジックを維持し、ドキュメント内の全テキストを抽出

        Returns:
            (formatted_content, method, metadata)
        """
        logger.info("[E-4a] ドキュメント処理開始")

        ocr_text = ""
        method = "document"
        metadata = {}

        # PDF処理
        if mime_type == 'application/pdf':
            logger.info("  ├─ PDF処理（E1-E5はpdf.py内で実行）")
            result = self.pdf_processor.extract_text(str(file_path), progress_callback=progress_callback)
            if result.get('success'):
                ocr_text = result.get('content', '')
                method = 'pdf'
                metadata = result.get('metadata', {})
            logger.info(f"  └─ PDF抽出完了: {len(ocr_text)} 文字")

        # Office文書処理
        elif 'openxmlformats' in mime_type or 'msword' in mime_type or 'ms-excel' in mime_type or 'ms-powerpoint' in mime_type:
            if progress_callback:
                progress_callback("E4")

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
                ocr_text = result.get('content', '')
            method = file_type
            logger.info(f"  └─ Office抽出完了: {len(ocr_text)} 文字")

        # テキスト系ファイル処理
        elif mime_type.startswith('text/'):
            if progress_callback:
                progress_callback("E4")

            logger.info(f"  ├─ テキストファイル処理")
            try:
                # 複数のエンコーディングを試行
                encodings = ['utf-8', 'shift_jis', 'cp932', 'euc-jp', 'iso-2022-jp']
                for encoding in encodings:
                    try:
                        ocr_text = file_path.read_text(encoding=encoding)
                        logger.info(f"  ├─ エンコーディング: {encoding}")
                        break
                    except UnicodeDecodeError:
                        continue
                method = 'text'
            except Exception as e:
                logger.error(f"  └─ テキスト読み込みエラー: {e}")
            logger.info(f"  └─ テキスト抽出完了: {len(ocr_text)} 文字")

        # pre_extracted_textがある場合は追加
        if pre_extracted_text:
            if ocr_text:
                ocr_text = pre_extracted_text + "\n\n---\n\n" + ocr_text
            else:
                ocr_text = pre_extracted_text

        if progress_callback:
            progress_callback("E5")

        # E-5: 出力フォーマット
        formatted_content = self._format_output(
            mime_type=mime_type,
            ocr_text=ocr_text,
            visual_log=None,
            audio_transcript=None
        )

        return formatted_content, method, metadata

    def _process_image_e4b(
        self,
        file_path: Path,
        mime_type: str,
        pre_extracted_text: Optional[str],
        workspace: Optional[str],
        progress_callback=None
    ) -> tuple:
        """
        [E-4b] 画像処理
        対象: image/*
        モデル: gemini-2.5-flash (temperature=0.0)
        並列処理: OCR Task + Description Task を asyncio.gather で同時実行

        Returns:
            (formatted_content, method, metadata)
        """
        logger.info("[E-4b] 画像処理開始（並列OCR + Description）")

        if progress_callback:
            progress_callback("E4")

        ocr_text = ""
        visual_log = ""
        metadata = {}

        # Gmail処理の場合はコスト削減のためflash-liteを使用
        is_gmail = workspace == 'gmail' if workspace else False
        vision_model = "gemini-2.5-flash-lite" if is_gmail else "gemini-2.5-flash"
        logger.info(f"  ├─ モデル: {vision_model}")

        if not self.llm_client:
            logger.warning("  └─ LLMクライアント未設定のためスキップ")
            formatted_content = self._format_output(
                mime_type=mime_type,
                ocr_text=pre_extracted_text or "(LLMクライアント未設定)",
                visual_log=None,
                audio_transcript=None
            )
            return formatted_content, 'image', metadata

        # OCRプロンプト（全文字抽出）
        ocr_prompt = """この画像から、全ての文字を徹底的に拾い尽くしてください。

【あなたの役割】
画像から全ての文字を漏らさず拾ってください。

【文字拾いの徹底指示】
- **小さな文字**: 注釈、脚注、コピーライト表記なども全て拾う
- **ロゴ化された文字**: 画像として埋め込まれたタイトル、会社名、ブランド名なども全て読み取る
- **装飾された文字**: 太字、斜体、色付きなど、装飾に関わらず全て拾う
- **背景に埋もれた文字**: 薄い色、透かし文字なども可能な限り読み取る
- **手書き文字**: 判読可能な範囲で全て拾う
- **表構造**: 表がある場合は、Markdown table形式で出力

【出力形式】
画像内の全てのテキストを、Markdown形式で構造化して出力してください。
表がある場合は必ずMarkdown table形式で出力してください。

**重要**: 1文字も見逃さないでください。"""

        # 情景描写プロンプト（網羅的羅列、要約禁止）
        description_prompt = """List every visible physical object and detail exhaustively. Do not summarize. Do not interpret emotions.

【出力形式】
全ての視覚情報を箇条書きで羅列してください：
- 物体（何が見えるか）
- 色（各物体の色）
- 配置（どこに何があるか）
- 状態（物体の状態、向き、サイズ）
- テクスチャ（表面の質感）
- 光源・影（照明の方向、影の位置）
- 背景の詳細

【禁止事項】
- 「〜のような画像です」といった要約
- 感情的解釈
- 推測や意見

【例】
- 左上: 白い長方形のテーブル（木目調）
- テーブル上: 黒いノートパソコン（画面点灯、角度約120度）
- ノートパソコンの右: 白いマグカップ（取っ手は右向き）
- 背景: ベージュ色の壁（無地）"""

        # 並列処理でOCRとDescriptionを同時実行
        def run_ocr():
            return self.llm_client.transcribe_image(
                image_path=file_path,
                model=vision_model,
                prompt=ocr_prompt
            )

        def run_description():
            return self.llm_client.transcribe_image(
                image_path=file_path,
                model=vision_model,
                prompt=description_prompt
            )

        logger.info("  ├─ 並列処理開始（OCR + Description）")

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_ocr = executor.submit(run_ocr)
            future_desc = executor.submit(run_description)

            # 結果を取得
            for future in as_completed([future_ocr, future_desc]):
                try:
                    if future == future_ocr:
                        ocr_result = future.result()
                        if ocr_result.get('success'):
                            ocr_text = ocr_result.get('content', '')
                            logger.info(f"  ├─ OCR完了: {len(ocr_text)} 文字")
                        else:
                            logger.warning(f"  ├─ OCR失敗: {ocr_result.get('error')}")
                    else:
                        desc_result = future.result()
                        if desc_result.get('success'):
                            visual_log = desc_result.get('content', '')
                            logger.info(f"  ├─ Description完了: {len(visual_log)} 文字")
                        else:
                            logger.warning(f"  ├─ Description失敗: {desc_result.get('error')}")
                except Exception as e:
                    logger.error(f"  ├─ 並列処理エラー: {e}")

        # pre_extracted_textがある場合は追加
        if pre_extracted_text:
            if ocr_text:
                ocr_text = pre_extracted_text + "\n\n---\n\n" + ocr_text
            else:
                ocr_text = pre_extracted_text

        if progress_callback:
            progress_callback("E5")

        logger.info(f"  └─ 画像処理完了（OCR: {len(ocr_text)}文字, Description: {len(visual_log)}文字）")

        # E-5: 出力フォーマット
        formatted_content = self._format_output(
            mime_type=mime_type,
            ocr_text=ocr_text,
            visual_log=visual_log,
            audio_transcript=None
        )

        return formatted_content, 'image', metadata

    def _process_av_e4c(
        self,
        file_path: Path,
        mime_type: str,
        is_video: bool,
        progress_callback=None
    ) -> tuple:
        """
        [E-4c] 音声・映像処理
        対象: audio/*, video/*
        モデル: gemini-2.5-flash-lite (temperature=0.0)

        処理:
        1. Transcription: 「あー」「えー」などのフィラーも含めた完全な書き起こし (Verbatim)
        2. Visual Log (動画のみ): シーンごとの視覚的変化を時系列で羅列

        Returns:
            (formatted_content, method, metadata)
        """
        media_type = "映像" if is_video else "音声"
        logger.info(f"[E-4c] {media_type}処理開始")

        if progress_callback:
            progress_callback("E4")

        audio_transcript = ""
        visual_log = ""
        metadata = {}

        if not self.llm_client:
            logger.warning("  └─ LLMクライアント未設定のためスキップ")
            formatted_content = self._format_output(
                mime_type=mime_type,
                ocr_text=None,
                visual_log=None,
                audio_transcript="(LLMクライアント未設定)"
            )
            return formatted_content, 'audio' if not is_video else 'video', metadata

        # Gemini 2.5 Flash Liteはマルチモーダル（音声・動画対応）
        av_model = "gemini-2.5-flash-lite"
        logger.info(f"  ├─ モデル: {av_model}")

        # 書き起こしプロンプト（Verbatim、フィラー含む）
        transcription_prompt = """この音声/映像から、一言一句完全な書き起こしを行ってください。

【重要な指示】
- 「あー」「えー」「うーん」などのフィラー（つなぎ言葉）も全て書き起こす
- 言い淀み、言い直しもそのまま記録
- 笑い声、咳払い、ため息なども [笑い]、[咳払い]、[ため息] のように記録
- 沈黙が長い場合は [沈黙 約5秒] のように記録
- 複数人の場合は話者を識別（話者A、話者B、または識別可能な名前）
- 聞き取れない部分は [聞き取り不明] と記載

【禁止事項】
- 要約は絶対に禁止
- 文章の整理や言い換えは禁止
- 内容の省略は禁止

【出力形式】
タイムスタンプ付きで出力してください：
[00:00] 話者A: えー、本日はですね、あの...
[00:05] 話者A: プロジェクトの進捗について報告させていただきます。"""

        # 映像ログプロンプト（動画のみ）
        visual_log_prompt = """この動画の視覚的な変化を時系列でログとして記録してください。

【重要な指示】
- シーンごとの視覚的変化を時系列で羅列
- 「誰が」「何を」「どの方向に」動いたかを具体的に記録
- 画面上のテキスト、図表、グラフの変化も記録
- カメラワーク（ズーム、パン、カット）も記録

【禁止事項】
- 概要や要約は禁止
- 「全体的に〜」といった抽象的な表現は禁止

【出力形式】
タイムスタンプ付きのログ形式：
[00:00] 黒い背景、中央に白いロゴが表示
[00:03] ロゴがフェードアウト、オフィスの会議室が映る
[00:05] 3人の人物がテーブルを囲んで座っている
[00:08] 左側の男性（青いシャツ）がスライドを指差す
[00:12] スライドにグラフが表示（棒グラフ、5本の棒）
[00:15] 右側の女性がうなずく"""

        # Gemini File APIを使用してファイルをアップロードし処理
        import google.generativeai as genai

        uploaded_file = None
        try:
            # ファイルをアップロード
            logger.info(f"  ├─ ファイルアップロード中: {file_path.name}")
            uploaded_file = genai.upload_file(path=str(file_path), mime_type=mime_type)
            logger.info(f"  ├─ アップロード完了: {uploaded_file.name}")

            # ファイルの処理完了を待機
            import time
            while uploaded_file.state.name == "PROCESSING":
                logger.info("  ├─ ファイル処理中...")
                time.sleep(2)
                uploaded_file = genai.get_file(uploaded_file.name)

            if uploaded_file.state.name == "FAILED":
                raise ValueError(f"ファイル処理失敗: {uploaded_file.state.name}")

            logger.info(f"  ├─ ファイル処理完了: {uploaded_file.state.name}")

            # モデル初期化
            model = genai.GenerativeModel(av_model)

            # 安全フィルター設定
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            generation_config = genai.GenerationConfig(
                max_output_tokens=65536,
                temperature=0.0
            )

            if is_video:
                # 動画: 並列で書き起こし + 映像ログ
                logger.info("  ├─ 並列処理開始（Transcription + Visual Log）")

                def run_transcription():
                    response = model.generate_content(
                        [transcription_prompt, uploaded_file],
                        generation_config=generation_config,
                        safety_settings=safety_settings,
                        request_options={"timeout": 600}  # 10分タイムアウト
                    )
                    if response.candidates and response.candidates[0].content.parts:
                        return response.candidates[0].content.parts[0].text
                    return ""

                def run_visual_log():
                    response = model.generate_content(
                        [visual_log_prompt, uploaded_file],
                        generation_config=generation_config,
                        safety_settings=safety_settings,
                        request_options={"timeout": 600}  # 10分タイムアウト
                    )
                    if response.candidates and response.candidates[0].content.parts:
                        return response.candidates[0].content.parts[0].text
                    return ""

                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_trans = executor.submit(run_transcription)
                    future_visual = executor.submit(run_visual_log)

                    for future in as_completed([future_trans, future_visual]):
                        try:
                            if future == future_trans:
                                audio_transcript = future.result()
                                logger.info(f"  ├─ Transcription完了: {len(audio_transcript)} 文字")
                            else:
                                visual_log = future.result()
                                logger.info(f"  ├─ Visual Log完了: {len(visual_log)} 文字")
                        except Exception as e:
                            logger.error(f"  ├─ 並列処理エラー: {e}")

            else:
                # 音声のみ: 書き起こしのみ
                logger.info("  ├─ Transcription処理中...")
                response = model.generate_content(
                    [transcription_prompt, uploaded_file],
                    generation_config=generation_config,
                    safety_settings=safety_settings,
                    request_options={"timeout": 600}  # 10分タイムアウト
                )

                if response.candidates and response.candidates[0].content.parts:
                    audio_transcript = response.candidates[0].content.parts[0].text
                    logger.info(f"  ├─ Transcription完了: {len(audio_transcript)} 文字")
                else:
                    logger.warning("  ├─ Transcription: レスポンスなし")

        except Exception as e:
            logger.error(f"  ├─ {media_type}処理エラー: {e}", exc_info=True)

        finally:
            # アップロードしたファイルを削除
            if uploaded_file:
                try:
                    genai.delete_file(name=uploaded_file.name)
                    logger.info(f"  ├─ アップロードファイル削除完了")
                except Exception:
                    pass

        if progress_callback:
            progress_callback("E5")

        logger.info(f"  └─ {media_type}処理完了（Transcript: {len(audio_transcript)}文字, Visual Log: {len(visual_log)}文字）")

        # E-5: 出力フォーマット
        formatted_content = self._format_output(
            mime_type=mime_type,
            ocr_text=None,
            visual_log=visual_log if is_video else None,
            audio_transcript=audio_transcript
        )

        return formatted_content, 'video' if is_video else 'audio', metadata

    def _format_output(
        self,
        mime_type: str,
        ocr_text: Optional[str],
        visual_log: Optional[str],
        audio_transcript: Optional[str]
    ) -> str:
        """
        [E-5] 出力フォーマット統一（タグ付け）
        Stage Fが情報の種類を識別できるよう、各出力を明確なヘッダーで結合

        Args:
            mime_type: MIMEタイプ
            ocr_text: OCRテキスト（E-4a, E-4bの文字情報）
            visual_log: 情景羅列（E-4bの情景 / E-4cの映像ログ）
            audio_transcript: 書き起こし（E-4cの音声書き起こし）

        Returns:
            タグ付きフォーマットの統合テキスト
        """
        sections = []

        # メタデータセクション
        sections.append(f"=== [SOURCE: METADATA] ===\nMimeType: {mime_type}")

        # OCRテキストセクション
        if ocr_text:
            sections.append(f"=== [SOURCE: OCR_TEXT] ===\n{ocr_text}")

        # 情景ログセクション
        if visual_log:
            sections.append(f"=== [SOURCE: VISUAL_SCENE_LOG] ===\n{visual_log}")

        # 音声書き起こしセクション
        if audio_transcript:
            sections.append(f"=== [SOURCE: AUDIO_TRANSCRIPT] ===\n{audio_transcript}")

        return "\n\n".join(sections)

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
