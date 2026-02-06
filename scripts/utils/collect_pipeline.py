"""
パイプライン検証用 プロンプト収集ツール Ver 2.0

【目的】
LLMに渡しているプロンプト全文を可視化し、
ハルシネーションの原因追跡を可能にする。

【出力】
1. 各Stageのプロンプト全文
2. LLMへの指示内容
3. 出力形式の指定
"""
import re
import ast
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple


# 収集対象ディレクトリ
TARGET_DIRS = [
    'shared/pipeline',
    'shared/ai',
    'config',  # プロンプトテンプレート
]

# プロンプト関数のパターン
PROMPT_FUNCTION_PATTERNS = [
    r'def (_build_.*prompt.*)\(self.*?\).*?:',
    r'def (_build_.*_prompt)\(self.*?\).*?:',
]


def extract_prompts_from_file(file_path: Path) -> List[Dict]:
    """ファイルからプロンプト定義を抽出"""
    prompts = []

    try:
        content = file_path.read_text(encoding='utf-8')
    except:
        return prompts

    # 方法1: _build_*_prompt 関数を抽出
    pattern = r'def (_build_\w*prompt\w*)\(self[^)]*\)[^:]*:\s*"""([^"]*)"""\s*return\s*"""(.*?)"""'
    matches = re.findall(pattern, content, re.DOTALL)

    for match in matches:
        func_name, docstring, prompt_body = match
        prompts.append({
            'type': 'function',
            'name': func_name,
            'docstring': docstring.strip(),
            'prompt': prompt_body.strip(),
            'file': str(file_path.name)
        })

    # 方法2: return """...""" パターンを直接抽出（docstringなし版）
    pattern2 = r'def (_build_\w*prompt\w*)\(self[^)]*\)[^:]*:[^"]*return\s*"""(.*?)"""'
    matches2 = re.findall(pattern2, content, re.DOTALL)

    for match in matches2:
        func_name, prompt_body = match
        if not any(p['name'] == func_name for p in prompts):
            prompts.append({
                'type': 'function',
                'name': func_name,
                'docstring': '',
                'prompt': prompt_body.strip(),
                'file': str(file_path.name)
            })

    # 方法3: 変数に格納されたプロンプト（複数行）
    pattern3 = r'(\w+_prompt)\s*=\s*"""(.*?)"""'
    matches3 = re.findall(pattern3, content, re.DOTALL)

    for match in matches3:
        var_name, prompt_body = match
        prompts.append({
            'type': 'variable',
            'name': var_name,
            'docstring': '',
            'prompt': prompt_body.strip(),
            'file': str(file_path.name)
        })

    # 方法4: f-string プロンプト（rescue_prompt等）
    pattern4 = r'(\w+_prompt)\s*=\s*f"""(.*?)"""'
    matches4 = re.findall(pattern4, content, re.DOTALL)

    for match in matches4:
        var_name, prompt_body = match
        if not any(p['name'] == var_name for p in prompts):
            prompts.append({
                'type': 'f-string',
                'name': var_name,
                'docstring': '動的パラメータ含む',
                'prompt': prompt_body.strip(),
                'file': str(file_path.name)
            })

    # 方法5: インラインプロンプト（prompt = """...""" を関数内で直接定義）
    pattern5 = r'prompt\s*=\s*"""(.*?)"""'
    matches5 = re.findall(pattern5, content, re.DOTALL)

    for idx, prompt_body in enumerate(matches5):
        # 既存と重複しないか確認
        body_stripped = prompt_body.strip()
        if len(body_stripped) > 50 and not any(body_stripped in p['prompt'] for p in prompts):
            prompts.append({
                'type': 'inline',
                'name': f'inline_prompt_{idx + 1}',
                'docstring': 'インライン定義',
                'prompt': body_stripped,
                'file': str(file_path.name)
            })

    # 方法6: f-string インラインプロンプト
    pattern6 = r'prompt\s*=\s*f"""(.*?)"""'
    matches6 = re.findall(pattern6, content, re.DOTALL)

    for idx, prompt_body in enumerate(matches6):
        body_stripped = prompt_body.strip()
        if len(body_stripped) > 50 and not any(body_stripped in p['prompt'] for p in prompts):
            prompts.append({
                'type': 'inline-f-string',
                'name': f'inline_f_prompt_{idx + 1}',
                'docstring': 'インライン定義（動的パラメータ含む）',
                'prompt': body_stripped,
                'file': str(file_path.name)
            })

    return prompts


def extract_llm_calls(file_path: Path) -> List[Dict]:
    """LLM呼び出し箇所を抽出"""
    calls = []

    try:
        content = file_path.read_text(encoding='utf-8')
        lines = content.split('\n')
    except:
        return calls

    for i, line in enumerate(lines):
        # generate_with_vision / generate 呼び出しを検出
        if 'llm_client.generate' in line or 'generate_with_vision' in line:
            # 前後のコンテキストを取得
            start = max(0, i - 5)
            end = min(len(lines), i + 10)
            context = '\n'.join(lines[start:end])

            # どのプロンプトを使っているか抽出
            prompt_match = re.search(r'prompt\s*=\s*(\w+)', context)
            prompt_var = prompt_match.group(1) if prompt_match else 'unknown'

            # モデル名を抽出
            model_match = re.search(r'model\s*=\s*(\w+)', context)
            model_var = model_match.group(1) if model_match else 'unknown'

            calls.append({
                'line': i + 1,
                'prompt_var': prompt_var,
                'model_var': model_var,
                'context': context,
                'file': str(file_path.name)
            })

    return calls


def extract_stage_info(file_path: Path) -> Dict:
    """ステージ情報（docstring）を抽出"""
    try:
        content = file_path.read_text(encoding='utf-8')
    except:
        return {}

    # モジュールdocstringを抽出
    match = re.match(r'^"""(.*?)"""', content, re.DOTALL)
    if match:
        return {
            'file': str(file_path.name),
            'docstring': match.group(1).strip()
        }
    return {}


def extract_config_prompts(file_path: Path) -> List[Dict]:
    """YAML/MDファイルからプロンプトを抽出"""
    prompts = []

    try:
        content = file_path.read_text(encoding='utf-8')
    except:
        return prompts

    suffix = file_path.suffix.lower()

    if suffix in ['.yaml', '.yml']:
        # YAML内のprompt/template/system_promptキーを抽出
        import yaml
        try:
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                prompts.extend(_extract_yaml_prompts(data, file_path.name))
        except:
            pass

    elif suffix == '.md':
        # Markdownファイル全体をプロンプトテンプレートとして収集
        if len(content) > 100:
            prompts.append({
                'type': 'markdown-template',
                'name': file_path.stem,
                'docstring': 'Markdownプロンプトテンプレート',
                'prompt': content,
                'file': str(file_path.name)
            })

    return prompts


def _extract_yaml_prompts(data: dict, filename: str, prefix: str = '') -> List[Dict]:
    """YAML辞書からプロンプト関連のキーを再帰的に抽出"""
    prompts = []
    prompt_keys = ['prompt', 'template', 'system_prompt', 'user_prompt', 'instruction']

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, str) and len(value) > 50:
            if any(pk in key.lower() for pk in prompt_keys):
                prompts.append({
                    'type': 'yaml-config',
                    'name': full_key,
                    'docstring': 'YAML設定ファイル',
                    'prompt': value,
                    'file': filename
                })
        elif isinstance(value, dict):
            prompts.extend(_extract_yaml_prompts(value, filename, full_key))

    return prompts


def collect_all_prompts(root_path: Path) -> Dict:
    """全プロンプトを収集"""
    result = {
        'prompts': [],
        'llm_calls': [],
        'stages': [],
        'config_files': [],
        'timestamp': datetime.now().isoformat()
    }

    for target_dir in TARGET_DIRS:
        dir_path = root_path / target_dir
        if not dir_path.exists():
            continue

        # Pythonファイル
        for py_file in sorted(dir_path.rglob('*.py')):
            if '__pycache__' in str(py_file):
                continue

            prompts = extract_prompts_from_file(py_file)
            result['prompts'].extend(prompts)

            calls = extract_llm_calls(py_file)
            result['llm_calls'].extend(calls)

            stage_info = extract_stage_info(py_file)
            if stage_info:
                result['stages'].append(stage_info)

        # YAML/MDファイル（configディレクトリ）
        for config_file in sorted(dir_path.rglob('*.yaml')) + sorted(dir_path.rglob('*.yml')) + sorted(dir_path.rglob('*.md')):
            prompts = extract_config_prompts(config_file)
            result['prompts'].extend(prompts)
            if prompts:
                result['config_files'].append(str(config_file.name))

    return result


def generate_markdown(data: Dict) -> str:
    """Markdown形式で出力"""
    md = []

    md.append("# パイプライン プロンプト全文ドキュメント\n")
    md.append(f"生成日時: {data['timestamp']}\n")
    md.append("---\n")

    # サマリー
    md.append("## サマリー\n")
    md.append(f"- プロンプト定義数: {len(data['prompts'])}\n")
    md.append(f"- LLM呼び出し箇所: {len(data['llm_calls'])}\n")
    md.append(f"- ステージ数: {len(data['stages'])}\n")
    md.append("\n---\n")

    # ステージ一覧
    md.append("## ステージ概要\n")
    for stage in data['stages']:
        md.append(f"### {stage['file']}\n")
        md.append("```\n")
        md.append(stage['docstring'][:2000])  # 長すぎる場合は切り詰め
        if len(stage['docstring']) > 2000:
            md.append("\n... (truncated)")
        md.append("\n```\n\n")

    md.append("---\n")

    # プロンプト全文（最重要セクション）
    md.append("## プロンプト全文\n")
    md.append("**以下がLLMに渡される実際の指示文です。ハルシネーションの原因追跡に使用してください。**\n\n")

    for i, prompt in enumerate(data['prompts'], 1):
        md.append(f"### {i}. {prompt['name']} ({prompt['file']})\n")
        md.append(f"- 種別: {prompt['type']}\n")
        if prompt['docstring']:
            md.append(f"- 説明: {prompt['docstring'][:200]}\n")
        md.append("\n**プロンプト全文:**\n")
        md.append("```\n")
        md.append(prompt['prompt'])
        md.append("\n```\n\n")

    md.append("---\n")

    # LLM呼び出し箇所
    md.append("## LLM呼び出し箇所\n")
    md.append("**どこでLLMが呼ばれ、どのプロンプトが使われているかの一覧**\n\n")

    for i, call in enumerate(data['llm_calls'], 1):
        md.append(f"### {i}. {call['file']}:{call['line']}\n")
        md.append(f"- 使用プロンプト: `{call['prompt_var']}`\n")
        md.append(f"- 使用モデル: `{call['model_var']}`\n")
        md.append("\n**コンテキスト:**\n")
        md.append("```python\n")
        md.append(call['context'])
        md.append("\n```\n\n")

    md.append("---\n")
    md.append("## 終わり\n")
    md.append("このドキュメントでプロンプト全文が確認できない場合は、collect_pipeline.py のバグです。\n")

    return ''.join(md)


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = project_root / f'PIPELINE_PROMPTS_{timestamp}.md'

    print(f"プロンプト収集中: {project_root}")
    print("=" * 50)

    # 収集
    data = collect_all_prompts(project_root)

    print(f"発見したプロンプト: {len(data['prompts'])}件")
    for p in data['prompts']:
        print(f"  - {p['name']} ({p['file']})")

    print(f"\nLLM呼び出し箇所: {len(data['llm_calls'])}件")

    # Markdown生成
    md_content = generate_markdown(data)

    # 出力
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print("=" * 50)
    print(f"完了: {output_file}")
    print(f"ファイルサイズ: {len(md_content):,} bytes")


if __name__ == '__main__':
    main()
