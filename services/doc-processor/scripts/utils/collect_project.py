"""
プロジェクト構造・コード収集ツール
- ディレクトリ構造をツリー表示
- 全コードを収集
- 機密キーを自動で伏字化
"""
import os
import re
from pathlib import Path
from datetime import datetime

# 除外するディレクトリ
EXCLUDE_DIRS = {
    '.git', '__pycache__', 'node_modules', 'venv', '.venv',
    'dist', 'build', '.pytest_cache', '.mypy_cache',
    'htmlcov', '.tox', 'eggs', '*.egg-info', '.local',
    '_runtime', '.devcontainer', 'cache', 'temp', 'tmp'
}

# 除外するファイル
EXCLUDE_FILES = {
    '.DS_Store', 'Thumbs.db', '*.pyc', '*.pyo', '*.so',
    '*.dll', '*.exe', '*.bin', '*.pkl', '*.model',
    '*.jpg', '*.jpeg', '*.png', '*.gif', '*.ico', '*.svg',
    '*.pdf', '*.doc', '*.docx', '*.xls', '*.xlsx',
    '*.zip', '*.tar', '*.gz', '*.rar', '*.7z',
    '*.mp3', '*.mp4', '*.wav', '*.avi', '*.mov',
    '*.woff', '*.woff2', '*.ttf', '*.eot',
    'package-lock.json', 'yarn.lock', '*.lock',
    'PROJECT_SNAPSHOT_*.md',  # 過去のスナップショットを除外（自己参照防止）
}

# コードファイルの拡張子
CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.scss',
    '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg',
    '.md', '.txt', '.sh', '.bash', '.ps1', '.bat',
    '.sql', '.dockerfile', '.env.example'
}

# 機密キーのパターン
SECRET_PATTERNS = [
    (r'(SUPABASE_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(SUPABASE_SERVICE_ROLE_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(OPENAI_API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(ANTHROPIC_API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(GOOGLE_API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(SECRET_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(PASSWORD\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(TOKEN\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(PRIVATE_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'("private_key"\s*:\s*")([^"]+)', r'\1***REDACTED***'),
    (r'("client_secret"\s*:\s*")([^"]+)', r'\1***REDACTED***'),
    (r'(eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)', '***JWT_REDACTED***'),
    (r'(sk-[a-zA-Z0-9]{20,})', '***OPENAI_KEY_REDACTED***'),
    (r'(ghp_[a-zA-Z0-9]{36})', '***GITHUB_TOKEN_REDACTED***'),
]


def should_exclude_dir(name):
    """ディレクトリを除外すべきか"""
    return name in EXCLUDE_DIRS or name.startswith('.')


def should_exclude_file(name):
    """ファイルを除外すべきか"""
    if name in EXCLUDE_FILES:
        return True
    for pattern in EXCLUDE_FILES:
        if '*' in pattern:
            # ワイルドカードパターンをシンプルに処理
            # 例: '*.pyc' -> 末尾一致, 'PROJECT_SNAPSHOT_*.md' -> 前後一致
            parts = pattern.split('*')
            if len(parts) == 2:
                prefix, suffix = parts
                if name.startswith(prefix) and name.endswith(suffix):
                    return True
    return False


def is_code_file(name):
    """コードファイルかどうか"""
    ext = Path(name).suffix.lower()
    return ext in CODE_EXTENSIONS or name in {'.env', '.env.example', 'Dockerfile', 'Makefile'}


def redact_secrets(content):
    """機密情報を伏字化"""
    for pattern, replacement in SECRET_PATTERNS:
        content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
    return content


def get_tree(root_path, prefix=""):
    """ディレクトリツリーを生成"""
    lines = []
    root = Path(root_path)

    items = sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    dirs = [i for i in items if i.is_dir() and not should_exclude_dir(i.name)]
    files = [i for i in items if i.is_file() and not should_exclude_file(i.name)]

    all_items = dirs + files

    for i, item in enumerate(all_items):
        is_last = i == len(all_items) - 1
        connector = "└── " if is_last else "├── "

        if item.is_dir():
            lines.append(f"{prefix}{connector}{item.name}/")
            extension = "    " if is_last else "│   "
            lines.extend(get_tree(item, prefix + extension))
        else:
            lines.append(f"{prefix}{connector}{item.name}")

    return lines


def collect_code(root_path):
    """全コードを収集"""
    files_content = []
    root = Path(root_path)

    for path in sorted(root.rglob('*')):
        # 除外ディレクトリ内のファイルはスキップ
        if any(part in EXCLUDE_DIRS or part.startswith('.') for part in path.parts):
            continue

        if path.is_file() and not should_exclude_file(path.name):
            if is_code_file(path.name) or path.name == '.env':
                try:
                    rel_path = path.relative_to(root)
                    content = path.read_text(encoding='utf-8', errors='ignore')
                    content = redact_secrets(content)
                    files_content.append({
                        'path': str(rel_path),
                        'content': content
                    })
                except Exception as e:
                    files_content.append({
                        'path': str(rel_path),
                        'content': f'# Error reading file: {e}'
                    })

    return files_content


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = project_root / f'PROJECT_SNAPSHOT_{timestamp}.md'

    print(f"プロジェクト収集中: {project_root}")

    # ツリー生成
    print("ディレクトリ構造を収集中...")
    tree_lines = get_tree(project_root)

    # コード収集
    print("コードファイルを収集中...")
    code_files = collect_code(project_root)

    # 出力
    print(f"出力ファイル生成中: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# Project Snapshot\n\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        f.write("## Directory Structure\n\n")
        f.write("```\n")
        f.write(f"{project_root.name}/\n")
        f.write('\n'.join(tree_lines))
        f.write("\n```\n\n")

        f.write("## Source Files\n\n")
        for file_info in code_files:
            ext = Path(file_info['path']).suffix or 'text'
            f.write(f"### {file_info['path']}\n\n")
            f.write(f"```{ext.lstrip('.')}\n")
            f.write(file_info['content'])
            if not file_info['content'].endswith('\n'):
                f.write('\n')
            f.write("```\n\n")

    print(f"\n完了! {len(code_files)} ファイルを収集しました")
    print(f"出力: {output_file}")


if __name__ == '__main__':
    main()
