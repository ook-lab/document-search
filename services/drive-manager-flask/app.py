import os
import json
import sys
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from loguru import logger
from dotenv import load_dotenv

_service_dir = Path(__file__).resolve().parent
project_root = _service_dir.parent.parent
load_dotenv(_service_dir / ".env")
load_dotenv(project_root / ".env", override=False)
if str(_service_dir) not in sys.path:
    sys.path.insert(0, str(_service_dir))

from google_drive_connector import GoogleDriveConnector

app = Flask(__name__)
app.secret_key = "drive-manager-safety-first"

# Driveコネクタ
try:
    drive = GoogleDriveConnector()
except Exception as e:
    drive = None

def get_system_folders():
    """ .env にある重要なフォルダ ID のリストを返す """
    return {v: k for k, v in os.environ.items() if "_FOLDER_ID" in k}

@app.route("/")
def index():
    # ユーザー指定がなければ、普通のマイドライブ(root)をデフォルトにする
    selected_folder_id = request.args.get("folder_id", "root")
    
    # システムフォルダとの一致チェック
    system_folders = get_system_folders()
    is_system_folder = selected_folder_id in system_folders
    system_folder_name = system_folders.get(selected_folder_id) if is_system_folder else None

    files = []
    folder_name = "マイドライブ (root)"
    if drive and selected_folder_id:
        try:
            # フォルダ自体の情報を取得（名前の確認）
            f_meta = drive.service.files().get(
                fileId=selected_folder_id, fields='name', supportsAllDrives=True
            ).execute()
            folder_name = f_meta.get('name', 'Unknown')

            query = f"'{selected_folder_id}' in parents and trashed=false"
            results = drive.service.files().list(
                q=query, spaces='drive',
                fields='files(id, name, mimeType, size, webViewLink)',
                supportsAllDrives=True, includeItemsFromAllDrives=True, corpora='allDrives'
            ).execute()
            files = results.get('files', [])
        except Exception as e:
            flash(f"フォルダが見つかりません。IDが正しいか確認してください。(Error: {e})", "error")
    
    return render_template(
        "index.html", 
        files=files,
        folder_name=folder_name, 
        selected_folder_id=selected_folder_id,
        is_system_folder=is_system_folder,
        system_folder_name=system_folder_name
    )

@app.route("/execute", methods=["POST"])
def execute():
    # 実行ロジック（そのまま）
    if not drive:
        flash("Driveコネクタなし", "error")
        return redirect(url_for("index"))

    instruction_str = request.form.get("instruction", "").strip()
    selected_folder_id = request.form.get("current_folder_id")
    
    if not instruction_str:
        flash("指示が入力されていません", "error")
        return redirect(url_for("index", folder_id=selected_folder_id))

    # システムフォルダへの実行を制限（または警告）
    system_folders = get_system_folders()
    if selected_folder_id in system_folders:
        flash(f"警告: システムフォルダ ({system_folders[selected_folder_id]}) に対する操作はできません。", "error")
        return redirect(url_for("index", folder_id=selected_folder_id))

    try:
        # JSON部分だけを抽出する高度な処理
        import re
        json_match = re.search(r'\[\s*{.*}\s*\]', instruction_str, re.DOTALL)
        if json_match:
            clean_json = json_match.group(0)
            # コメントの削除 (// ...)
            clean_json = re.sub(r'//.*', '', clean_json)
            # 末尾のカンマの削除 (, } -> } / , ] -> ])
            clean_json = re.sub(r',\s*([\]}])', r'\1', clean_json)
            actions = json.loads(clean_json)
        else:
            # マッチしなければ直接トライ
            actions = json.loads(instruction_str)
        # 実行処理
        results = []
        newly_created_folders = {} # 名前 -> ID の記録

        # ID マッピング (既存フォルダ) - 全件取得
        all_item_cache = {} # 名前 -> ID
        # (中略: pagination部分は維持)
        page_token = None
        while True:
            q_str = f"'{selected_folder_id}' in parents and trashed=false"
            res_list = drive.service.files().list(
                q=q_str, fields='nextPageToken, files(id, name)',
                supportsAllDrives=True, includeItemsFromAllDrives=True,
                pageToken=page_token
            ).execute()
            for f in res_list.get('files', []):
                # 同名がある場合はリストにする or 最初のものを優先
                if f['name'] not in files_cache:
                    files_cache[f['name']] = f['id']
            page_token = res_list.get('nextPageToken')
            if not page_token: break

        for action in actions:
            op = action.get("action")
            file_id = action.get("file_id") or files_cache.get(action.get("name"))
            
            try:
                if op == "create_folder":
                    parent = action.get("parent_id") or selected_folder_id
                    new_id = drive.create_folder(action["name"], parent)
                    if new_id:
                        # あなたの権限をコピーして、見えるようにする
                        try:
                            # 親の権限リストを取得
                            p_perms = drive.service.permissions().list(fileId=parent, fields='permissions(emailAddress, role, type)').execute()
                            for perm in p_perms.get('permissions', []):
                                if perm.get('type') == 'user' and 'emailAddress' in perm:
                                    drive.service.permissions().create(
                                        fileId=new_id,
                                        body={'role': perm['role'], 'type': 'user', 'emailAddress': perm['emailAddress']},
                                        supportsAllDrives=True
                                    ).execute()
                        except Exception as pe:
                            logger.error(f"Permission copy failed: {pe}")
                        
                        action["status"] = f"SUCCESS (ID: {new_id})"
                        action["new_id"] = new_id
                        newly_created_folders[action["name"]] = new_id
                    else:
                        action["status"] = "FAILED"
                
                elif op == "move":
                    target_name = action.get("new_folder_id") or action.get("target_folder_id")
                    
                    # 移動先の「ID」を特定する (1.新規作成リスト内 -> 2.既存リスト内)
                    target_id = newly_created_folders.get(target_name) or all_item_cache.get(target_name) or target_name
                    
                    if not file_id:
                        action["status"] = "FAILED: 元ファイル名が見つかりません"
                    else:
                        try:
                            # 移動実行
                            res = drive.move_file(file_id, target_id)
                            action["status"] = "SUCCESS" if res else "FAILED (Move error)"
                        except Exception as e:
                            action["status"] = f"FAILED: {str(e)}"
                
                elif op == "rename":
                    if not file_id:
                        action["status"] = "FAILED: 元ファイルが見つかりません"
                    else:
                        res = drive.rename_file(file_id, action["new_name"])
                        action["status"] = "SUCCESS" if res else "FAILED"
                else: 
                    action["status"] = "UNKNOWN"
            except Exception as e:
                action["status"] = f"ERROR: {str(e)}"
            results.append(action)
        return render_template("results.html", results=results)
    except Exception as e:
        flash(f"エラー: {e}", "error")
    return redirect(url_for("index", folder_id=selected_folder_id))

@app.route("/rescue", methods=["POST"])
def rescue():
    if not drive: return redirect(url_for("index"))
    
    selected_folder_id = request.form.get("current_folder_id")
    if not selected_folder_id:
        flash("救出先のフォルダを指定してください", "error")
        return redirect(url_for("index"))

    try:
        # 親フォルダの権限を事前に取得（救出用）
        p_perms = drive.service.permissions().list(fileId=selected_folder_id, fields='permissions(emailAddress, role, type)').execute()
        target_users = [p for p in p_perms.get('permissions', []) if p.get('type') == 'user' and 'emailAddress' in p]

        # サービスアカウントが所有している全ファイルを取得
        results = drive.service.files().list(
            q="'me' in owners and trashed=false",
            fields='files(id, name, mimeType)',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        orphans = results.get('files', [])
        
        rescued_count = 0
        for item in orphans:
            try:
                # 権限付与
                for user in target_users:
                    try:
                        drive.service.permissions().create(
                            fileId=item['id'],
                            body={'role': user['role'], 'type': 'user', 'emailAddress': user['emailAddress']},
                            supportsAllDrives=True
                        ).execute()
                    except: pass
                
                # 移動実行 (最上位の親を移動すれば子はついてくるため、エラー無視して回す)
                drive.move_file(item['id'], selected_folder_id)
                rescued_count += 1
            except: pass
            
        flash(f"合計 {rescued_count} 件のアイテムを救出し、権限を付与しました！", "success")
    except Exception as e:
        flash(f"救出失敗: {e}", "error")

    return redirect(url_for("index", folder_id=selected_folder_id))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
