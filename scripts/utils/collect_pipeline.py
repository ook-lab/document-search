"""
パイプライン検証用 軽量収集ツール
- shared/pipeline/ のみ
- shared/ai/ のみ
- パイプラインの変更検証に特化
"""
import re
from pathlib import Path
from datetime import datetime

# 収集対象ディレクトリ（パイプライン関連のみ）
TARGET_DIRS = [
    'shared/pipeline',
    'shared/ai',
]

# 追加で収集する重要ファイル
TARGET_FILES = [
    'shared/__init__.py',
    'shared/config.py',
    'requirements.txt',
    'pyproject.toml',
]

# 除外するファイル
EXCLUDE_FILES = {
    'PROJECT_SNAPSHOT_*.md', 'PROJECT_FULL_*.md', 'PIPELINE_*.md',
    'collect_project_full.py', 'collect_pipeline.py'
}

SECRET_PATTERNS = [
    (r'(API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(SECRET_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(sk-[a-zA-Z0-9]{20,})', '***REDACTED***'),
]


def redact_secrets(content):
    for pattern, replacement in SECRET_PATTERNS:
        content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
    return content


def should_exclude_file(name):
    for pattern in EXCLUDE_FILES:
        if '*' in pattern:
            parts = pattern.split('*')
            if len(parts) == 2 and name.startswith(parts[0]) and name.endswith(parts[1]):
                return True
        elif name == pattern:
            return True
    return False


def collect_pipeline_files(root_path):
    """パイプライン関連ファイルのみ収集"""
    files = []
    root = Path(root_path)

    # ターゲットディレクトリ内の全.pyファイル
    for target_dir in TARGET_DIRS:
        dir_path = root / target_dir
        if dir_path.exists():
            for path in sorted(dir_path.rglob('*.py')):
                if '__pycache__' not in str(path) and not should_exclude_file(path.name):
                    try:
                        rel_path = path.relative_to(root)
                        content = path.read_text(encoding='utf-8')
                        content = redact_secrets(content)
                        files.append({
                            'path': str(rel_path).replace('\\', '/'),
                            'content': content
                        })
                    except:
                        pass

    # 追加の重要ファイル
    for target_file in TARGET_FILES:
        path = root / target_file
        if path.exists():
            try:
                content = path.read_text(encoding='utf-8')
                files.append({
                    'path': target_file,
                    'content': content
                })
            except:
                pass

    return files


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = project_root / f'PIPELINE_{timestamp}.md'

    print(f"パイプライン収集中: {project_root}")
    files = collect_pipeline_files(project_root)

    total_lines = 0
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# Pipeline Snapshot (Lightweight)\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Files: {len(files)}\n\n")

        # ファイル一覧
        f.write("## Files\n\n")
        for file_info in files:
            lines = file_info['content'].count('\n') + 1
            total_lines += lines
            f.write(f"- {file_info['path']} ({lines} lines)\n")
        f.write(f"\nTotal: {total_lines} lines\n\n")
        f.write("---\n\n")

        # ファイル内容
        for file_info in files:
            path = file_info['path']
            content = file_info['content']

            f.write(f"## {path}\n\n")
            f.write("```python\n")
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')
            f.write("```\n\n")

    print(f"完了: {len(files)} ファイル, {total_lines} 行")
    print(f"出力: {output_file}")


if __name__ == '__main__':
    main()
