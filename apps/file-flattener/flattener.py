import os
import shutil
from typing import Dict, List, Tuple, Set

def get_unique_destination(filename: str, allocated_names: Set[str]) -> str:
    """
    ファイル名がすでに allocated_names に存在する場合、
    自動で連番 (_1, _2, ...) を付与して一意のファイル名を生成します。
    """
    if filename not in allocated_names:
        return filename
    
    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        new_name = f"{base}_{counter}{ext}"
        if new_name not in allocated_names:
            return new_name
        counter += 1

def scan_directory(root_dir: str) -> List[Dict[str, str]]:
    """
    指定された root_dir 配下のサブフォルダ内にあるすべてのファイルを走査し、
    最上位（root_dir 直下）に移動する際のマッピングを作成します。
    最上位に既に存在するファイルや、他の移動ファイルと名前が衝突する場合は自動で連番を付与します。
    """
    root_dir = os.path.abspath(root_dir)
    if not os.path.isdir(root_dir):
        raise ValueError(f"指定されたパスはディレクトリではありません: {root_dir}")

    # 最上位（root_dir直下）にあるファイル名を事前に把握
    allocated_names: Set[str] = set()
    for item in os.listdir(root_dir):
        item_path = os.path.join(root_dir, item)
        if os.path.isfile(item_path):
            allocated_names.add(item)

    move_mappings: List[Dict[str, str]] = []

    # サブフォルダを再帰的に走査
    for dirpath, _, filenames in os.walk(root_dir):
        # 最上位フォルダ直下はスキップ（既に最上位にあるため移動不要）
        if os.path.abspath(dirpath) == root_dir:
            continue

        for filename in filenames:
            src_path = os.path.join(dirpath, filename)
            # 一意な移動先ファイル名を決定
            dest_filename = get_unique_destination(filename, allocated_names)
            allocated_names.add(dest_filename)

            dest_path = os.path.join(root_dir, dest_filename)
            
            # 相対パスも生成してUIで表示しやすくする
            rel_src_path = os.path.relpath(src_path, root_dir)

            move_mappings.append({
                "src_path": src_path,
                "rel_src_path": rel_src_path,
                "dest_filename": dest_filename,
                "dest_path": dest_path,
                "original_filename": filename
            })

    return move_mappings

def clean_empty_directories(root_dir: str) -> List[str]:
    """
    root_dir 配下の空のサブフォルダをボトムアップで削除します。
    削除されたフォルダパスのリストを返します。
    """
    root_dir = os.path.abspath(root_dir)
    deleted_dirs: List[str] = []

    # topdown=False にすることで、深い階層から順に走査し、
    # 子フォルダが削除されて空になった親フォルダも安全に削除できるようにします。
    for dirpath, _, _ in os.walk(root_dir, topdown=False):
        if os.path.abspath(dirpath) == root_dir:
            continue

        # ディレクトリが空（または隠しファイル等も含めて中身が何もない）か確認
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                deleted_dirs.append(dirpath)
        except OSError:
            # 権限エラーやその他の理由で削除できない場合はスキップ
            pass

    return deleted_dirs

def execute_flatten(root_dir: str, move_mappings: List[Dict[str, str]], delete_empty_dirs: bool = True) -> Dict[str, any]:
    """
    スキャン結果（move_mappings）に基づき、実際にファイルを移動し、
    オプションで空フォルダを削除します。
    """
    root_dir = os.path.abspath(root_dir)
    success_count = 0
    errors: List[Dict[str, str]] = []
    moved_files: List[Dict[str, str]] = []

    for mapping in move_mappings:
        src = mapping["src_path"]
        dest = mapping["dest_path"]

        # 移動先のディレクトリが存在することを確認（基本は root_dir）
        if not os.path.exists(src):
            errors.append({
                "src_path": src,
                "error": "移動元ファイルが見つかりません。"
            })
            continue

        try:
            # 同名ファイルがすでに root_dir 直下に存在する場合の最終安全策（上書き防止）
            # 基本は scan_directory で解決されているはずだが、並行処理対策として
            if os.path.exists(dest):
                base, ext = os.path.splitext(dest)
                counter = 1
                while os.path.exists(dest):
                    dest = f"{base}_{counter}{ext}"
                    counter += 1
            
            shutil.move(src, dest)
            success_count += 1
            moved_files.append({
                "src": src,
                "dest": dest,
                "rel_src": mapping["rel_src_path"],
                "dest_filename": os.path.basename(dest)
            })
        except Exception as e:
            errors.append({
                "src_path": src,
                "error": str(e)
            })

    deleted_dirs: List[str] = []
    if delete_empty_dirs:
        deleted_dirs = clean_empty_directories(root_dir)

    return {
        "success_count": success_count,
        "errors": errors,
        "moved_files": moved_files,
        "deleted_dirs": [os.path.relpath(d, root_dir) for d in deleted_dirs]
    }
