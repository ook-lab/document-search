"""
設定ローダー

models.yaml と pipeline_routes.yaml を読み込み、
doc_type や workspace に応じて適切なプロンプトとモデルを返す
"""
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from loguru import logger


class ConfigLoader:
    """パイプライン設定ローダー"""

    def __init__(self, config_dir: Optional[Path] = None):
        """
        初期化

        Args:
            config_dir: 設定ディレクトリ（デフォルト: G_unified_pipeline/config/）
        """
        if config_dir is None:
            config_dir = Path(__file__).parent / "config"

        self.config_dir = Path(config_dir)
        self.models_config = self._load_yaml(self.config_dir / "models.yaml")

        # パイプラインルーティング設定を読み込み
        pipeline_routing = self.config_dir / "pipeline_routing.yaml"
        if pipeline_routing.exists():
            self.routes_config = self._load_yaml(pipeline_routing)
            logger.info(f"✅ pipeline_routing.yaml を読み込みました")
        else:
            # フォールバック: 旧 pipeline_routes.yaml
            self.routes_config = self._load_yaml(self.config_dir / "pipeline_routes.yaml")
            logger.warning(f"⚠️ pipeline_routing.yaml が見つかりません。pipeline_routes.yaml を使用します")

        # プロンプト設定を読み込み
        prompts_file = self.config_dir / "prompts.yaml"
        if prompts_file.exists():
            prompts_data = self._load_yaml(prompts_file)
            self.prompts_config = prompts_data.get('prompts', {})
            logger.info(f"✅ prompts.yaml を読み込みました")
        else:
            self.prompts_config = {}
            logger.warning(f"⚠️ prompts.yaml が見つかりません。MDファイルから読み込みます")

        logger.info(f"✅ 設定ローダー初期化完了: {self.config_dir}")

    def _load_yaml(self, file_path: Path) -> Dict[str, Any]:
        """YAML ファイルを読み込む"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"YAML読み込みエラー: {file_path} - {e}")
            return {}

    def get_route_config(self, doc_type: str, workspace: Optional[str] = None) -> Dict[str, Any]:
        """
        doc_type と workspace に基づいてルート設定を取得

        Args:
            doc_type: ドキュメントタイプ
            workspace: ワークスペース（オプション）

        Returns:
            ルート設定（stages ごとの prompt_key, model_key）
        """
        routing = self.routes_config.get('routing', {})

        # 優先順位1: workspace ベースのルート
        if workspace:
            by_workspace = routing.get('by_workspace', {})
            if workspace in by_workspace:
                logger.debug(f"ルート選択: workspace={workspace}")
                return by_workspace[workspace]

        # 優先順位2: doc_type ベースのルート
        by_doc_type = routing.get('by_doc_type', {})
        if doc_type in by_doc_type:
            logger.debug(f"ルート選択: doc_type={doc_type}")
            return by_doc_type[doc_type]

        # 優先順位3: デフォルト
        logger.debug("ルート選択: default")
        return by_doc_type.get('default', {})

    def get_prompt(self, stage: str, prompt_key: str) -> str:
        """
        プロンプトを取得（prompts.yaml または MDファイルから）

        Args:
            stage: ステージ名 (stage_f, stage_g, stage_h, stage_i)
            prompt_key: プロンプトキー (default, flyer, classroom など)

        Returns:
            プロンプトテキスト
        """
        # prompts.yaml から読み込み
        if self.prompts_config and stage in self.prompts_config:
            if prompt_key in self.prompts_config[stage]:
                prompt = self.prompts_config[stage][prompt_key]
                logger.debug(f"プロンプト読み込み: {stage}/{prompt_key} ({len(prompt)}文字)")
                return prompt
            elif prompt_key != 'default' and 'default' in self.prompts_config[stage]:
                # フォールバック: default プロンプトを試す
                logger.warning(f"プロンプトキー '{prompt_key}' が見つかりません。default を使用します: {stage}")
                prompt = self.prompts_config[stage]['default']
                logger.debug(f"プロンプト読み込み: {stage}/default ({len(prompt)}文字)")
                return prompt

        # フォールバック: MDファイルから読み込み（後方互換性）
        prompt_file = self.config_dir / "prompts" / stage / f"{stage}_{prompt_key}.md"
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt = f.read()
                logger.debug(f"プロンプト読み込み（MDファイル）: {stage}/{stage}_{prompt_key}.md ({len(prompt)}文字)")
                return prompt
        except FileNotFoundError:
            logger.warning(f"プロンプトが見つかりません: {stage}/{prompt_key}")
            # 最後のフォールバック: default プロンプトを試す
            if prompt_key != 'default':
                return self.get_prompt(stage, 'default')
            return ""
        except Exception as e:
            logger.error(f"プロンプト読み込みエラー: {prompt_file} - {e}")
            return ""

    def get_model(self, stage: str, model_key: str) -> str:
        """
        モデル名を取得

        Args:
            stage: ステージ名 (stage_f, stage_g, stage_h, stage_i, stage_k)
            model_key: モデルキー (default, flyer, classroom など)

        Returns:
            モデル名
        """
        models = self.models_config.get('models', {})
        stage_models = models.get(stage, {})

        model = stage_models.get(model_key)
        if model:
            logger.debug(f"モデル選択: {stage}/{model_key} → {model}")
            return model

        # フォールバック: default モデル
        default_model = stage_models.get('default')
        if default_model:
            logger.debug(f"モデル選択（フォールバック）: {stage}/default → {default_model}")
            return default_model

        logger.error(f"モデルが見つかりません: {stage}/{model_key}")
        return ""

    def get_stage_config(
        self,
        stage: str,
        doc_type: str,
        workspace: Optional[str] = None
    ) -> Dict[str, str]:
        """
        特定ステージの設定を取得（プロンプト + モデル + custom_handler）

        Args:
            stage: ステージ名 (stage_f, stage_g, stage_h, stage_i)
            doc_type: ドキュメントタイプ
            workspace: ワークスペース（オプション）

        Returns:
            {'prompt': str, 'model': str, 'custom_handler': str (optional), 'skip': bool (optional)}
        """
        route = self.get_route_config(doc_type, workspace)
        stages_config = route.get('stages', {})
        stage_config = stages_config.get(stage, {})

        prompt_key = stage_config.get('prompt_key', 'default')
        model_key = stage_config.get('model_key', 'default')

        result = {
            'prompt': self.get_prompt(stage, prompt_key),
            'model': self.get_model(stage, model_key)
        }

        # custom_handler がある場合は追加
        if 'custom_handler' in stage_config:
            result['custom_handler'] = stage_config['custom_handler']

        # skip フラグがある場合は追加
        if 'skip' in stage_config:
            result['skip'] = stage_config['skip']

        return result

    def get_hybrid_ocr_enabled(self, doc_type: str, workspace: Optional[str] = None) -> bool:
        """
        ハイブリッドOCR（Surya + PaddleOCR）が有効かどうかを取得

        Args:
            doc_type: ドキュメントタイプ
            workspace: ワークスペース（オプション）

        Returns:
            True: ハイブリッドOCR有効
            False: ハイブリッドOCR無効（Gemini Visionのみ）
        """
        hybrid_ocr_config = self.models_config.get('hybrid_ocr', {})

        # workspace ベースの設定を確認
        if workspace and workspace in hybrid_ocr_config:
            return hybrid_ocr_config[workspace]

        # doc_type ベースの設定を確認
        if doc_type in hybrid_ocr_config:
            return hybrid_ocr_config[doc_type]

        # デフォルト設定
        return hybrid_ocr_config.get('default', False)
