# -*- coding: utf-8 -*-
import re

file_path = r"C:\Users\ookub\document-management-system\services\pipeline-lab\blueprints\lab.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Locate _DIRECT_EXTRACT_PROMPT = """..."""
prompt_pattern = r'(_DIRECT_EXTRACT_PROMPT = """)([\s\S]*?)(""")'
match = re.search(prompt_pattern, content)

if match:
    prefix = match.group(1)
    prompt_content = match.group(2)
    suffix = match.group(3)
    
    # Restore the original format without any Few-shot example section,
    # keeping the generalized rule 10 instructions only.
    restored_format = """【出力形式（必須・厳守）】
以下の2セクション構成で出力してください。セクション名は一字一句変えないでください。

---

## 非表（F 地の文）

（表以外のタイトル・見出し・注釈・フッター・地の文を、論理的なブロックや段落ごとに適切に分割し、見出しには「#」や「##」、箇条書きには「-」、注記等には「>」などのマークダウン構造化記号を付与して記述。表の外にあるすべてのテキストを漏らさず含める。）

## 表（ui_data.tables）

（表ごとに `## T1`, `## T2`, ... と見出しを付け、その直後に `> 説明` を1行書いてからマークダウン表を記述。表が複数ある場合は順番に並める。表がない場合はこのセクションごと省略。）

---"""

    format_start_idx = prompt_content.find("【出力形式（必須・厳守）】")
    if format_start_idx != -1:
        new_prompt_content = prompt_content[:format_start_idx] + restored_format
        content = content[:match.start(2)] + new_prompt_content + content[match.end(2):]

with open(file_path, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(content)
print("SUCCESS")
