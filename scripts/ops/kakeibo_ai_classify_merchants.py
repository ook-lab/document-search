from __future__ import annotations

import argparse
import sys
from pathlib import Path

_OPS = Path(__file__).resolve().parent
if str(_OPS) not in sys.path:
    sys.path.insert(0, str(_OPS))

from kakeibo_ai_lib.merchant_classifier import KakeiboAICacheUpdater, NullClassifier
from kakeibo_ai_lib.openai_classifier import OpenAIClassifier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--mode", type=str, default="null", choices=["null", "openai"])
    args = parser.parse_args()

    updater = KakeiboAICacheUpdater()

    if args.mode == "openai":
        classifier = OpenAIClassifier()
    else:
        classifier = NullClassifier()

    updater.run(classifier, limit=args.limit)


if __name__ == "__main__":
    main()
