"""
LLMクライアントのカスタム例外
"""


class MaxTokensExceededError(Exception):
    """max_tokens上限に達して出力が途中で切れた場合のエラー"""

    def __init__(self, message: str, partial_output: str, finish_reason_name: str):
        """
        Args:
            message: エラーメッセージ
            partial_output: 途中で切れた出力テキスト
            finish_reason_name: finish_reasonの名前
        """
        super().__init__(message)
        self.partial_output = partial_output
        self.finish_reason_name = finish_reason_name
