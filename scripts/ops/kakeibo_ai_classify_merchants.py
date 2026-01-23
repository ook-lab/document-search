from __future__ import annotations

import argparse

from shared.kakeibo.merchant_classifier import KakeiboAICacheUpdater, NullClassifier
from shared.kakeibo.openai_classifier import OpenAIClassifier


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
