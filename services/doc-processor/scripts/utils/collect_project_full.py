"""
完全復元用プロジェクト収集ツール
- プロジェクトを1字1句まで再現可能
- バイナリ以外の全ファイルを収集
- 機密キーは伏字化
"""
import os
import re
import base64
from pathlib import Path
from datetime import datetime

# 除外するディレクトリ（復元不要なもののみ）
EXCLUDE_DIRS = {
    '.git', '__pycache__', 'node_modules', 'venv', '.venv',
    '.pytest_cache', '.mypy_cache', 'htmlcov', '.tox',
    '_runtime', 'cache', 'temp', 'tmp', '.devcontainer'
}

# 除外するファイル（生成物・バイナリのみ）
EXCLUDE_FILES = {
    '.DS_Store', 'Thumbs.db', '*.pyc', '*.pyo', '*.so',
    '*.dll', '*.exe', '*.bin', '*.pkl', '*.model',
    '*.jpg', '*.jpeg', '*.png', '*.gif', '*.ico', '*.svg',
    '*.pdf', '*.doc', '*.docx', '*.xls', '*.xlsx',
    '*.zip', '*.tar', '*.gz', '*.rar', '*.7z',
    '*.mp3', '*.mp4', '*.wav', '*.avi', '*.mov',
    '*.woff', '*.woff2', '*.ttf', '*.eot',
    'PROJECT_SNAPSHOT_*.md', 'PROJECT_FULL_*.md', 'PIPELINE_*.md',
    'collect_project_full.py', 'collect_pipeline.py'
}

# 機密キーのパターン
SECRET_PATTERNS = [
    (r'(SUPABASE_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(SUPABASE_SERVICE_ROLE_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(OPENAI_API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(ANTHROPIC_API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(GOOGLE_API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
    (r'(GEMINI_API_KEY\s*=\s*["\']?)([^"\'"\s]+)', r'\1***REDACTED***'),
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
    return name in EXCLUDE_DIRS or name.startswith('.')


def should_exclude_file(name):
    if name in EXCLUDE_FILES:
        return True
    for pattern in EXCLUDE_FILES:
        if '*' in pattern:
            parts = pattern.split('*')
            if len(parts) == 2:
                prefix, suffix = parts
                if name.startswith(prefix) and name.endswith(suffix):
                    return True
    return False


def is_text_file(path):
    """テキストファイルかどうか判定"""
    try:
        with open(path, 'rb') as f:
            chunk = f.read(8192)
            if b'\x00' in chunk:
                return False
        return True
    except:
        return False


def redact_secrets(content):
    for pattern, replacement in SECRET_PATTERNS:
        content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
    return content


def collect_all_files(root_path):
    """全ファイルを収集"""
    files = []
    root = Path(root_path)

    for path in sorted(root.rglob('*')):
        if any(part in EXCLUDE_DIRS or part.startswith('.') for part in path.parts):
            continue
        if path.is_file() and not should_exclude_file(path.name):
            if is_text_file(path):
                try:
                    rel_path = path.relative_to(root)
                    content = path.read_text(encoding='utf-8', errors='replace')
                    content = redact_secrets(content)
                    files.append({
                        'path': str(rel_path).replace('\\', '/'),
                        'content': content
                    })
                except Exception as e:
                    pass
    return files


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = project_root / f'PROJECT_FULL_{timestamp}.md'

    print(f"完全収集中: {project_root}")
    files = collect_all_files(project_root)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# Project Full Snapshot (Reproducible)\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Files: {len(files)}\n\n")
        f.write("---\n\n")

        for file_info in files:
            path = file_info['path']
            content = file_info['content']
            ext = Path(path).suffix.lstrip('.') or 'text'

            f.write(f"## FILE: {path}\n\n")
            f.write(f"```{ext}\n")
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')
            f.write("```\n\n")

    print(f"完了: {len(files)} ファイル")
    print(f"出力: {output_file}")


if __name__ == '__main__':
    main()
