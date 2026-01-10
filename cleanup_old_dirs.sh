#!/bin/bash
# 旧ディレクトリ削除スクリプト（移行完了後に実行）

set -e

PROJECT_ROOT=~/document-management-system
cd $PROJECT_ROOT

echo "=========================================="
echo "旧ディレクトリの削除"
echo "=========================================="
echo ""
echo "⚠️  このスクリプトは以下のディレクトリを削除します:"
echo ""
echo "  - A_common"
echo "  - B_ingestion"
echo "  - C_ai_common"
echo "  - G_processor"
echo "  - G_cloud_run"
echo "  - G_unified_pipeline"
echo "  - K_kakeibo"
echo "  - I_frontend"
echo "  - L_product_classification"
echo "  - netsuper_search_app"
echo ""
echo "また、以下のルートスクリプトも削除します:"
echo "  - process_*.py"
echo "  - reset_*.py"
echo "  - *email*.py"
echo "  - その他ユーティリティスクリプト"
echo ""
echo "新しい構造でサービスが正常に動作することを確認してから実行してください。"
echo ""

read -p "本当に削除しますか？ (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "キャンセルしました。"
    exit 0
fi

echo ""
echo "削除を開始します..."

# カラー定義
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

warn() {
    echo -e "${YELLOW}[削除]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

# 旧ディレクトリを削除
warn "旧ディレクトリを削除中..."

rm -rf A_common && echo "  ✓ A_common"
rm -rf B_ingestion && echo "  ✓ B_ingestion"
rm -rf C_ai_common && echo "  ✓ C_ai_common"
rm -rf G_processor && echo "  ✓ G_processor"
rm -rf G_cloud_run && echo "  ✓ G_cloud_run"
rm -rf G_unified_pipeline && echo "  ✓ G_unified_pipeline"
rm -rf K_kakeibo && echo "  ✓ K_kakeibo"
rm -rf I_frontend && echo "  ✓ I_frontend"
rm -rf L_product_classification && echo "  ✓ L_product_classification"
rm -rf netsuper_search_app && echo "  ✓ netsuper_search_app"

success "旧ディレクトリを削除しました"

# ルートスクリプトを削除
warn "ルートスクリプトを削除中..."

rm -f process_*.py && echo "  ✓ process_*.py"
rm -f reset_*.py && echo "  ✓ reset_*.py"
rm -f *email*.py && echo "  ✓ *email*.py"
rm -f delete_gmail*.py && echo "  ✓ delete_gmail*.py"
rm -f bulk_delete*.py && echo "  ✓ bulk_delete*.py"
rm -f reimport_*.py && echo "  ✓ reimport_*.py"
rm -f sync_*.py && echo "  ✓ sync_*.py"
rm -f daily_*.py && echo "  ✓ daily_*.py"
rm -f retry_*.py && echo "  ✓ retry_*.py"
rm -f verify_*.py && echo "  ✓ verify_*.py"
rm -f fix_*.py && echo "  ✓ fix_*.py"
rm -f check_*.py && echo "  ✓ check_*.py"
rm -f rakuten_seiyu_scraper_playwright.py && echo "  ✓ rakuten_seiyu_scraper_playwright.py"

success "ルートスクリプトを削除しました"

# 旧Dockerfileを削除
warn "旧Dockerfileを削除中..."

rm -f Dockerfile.processor && echo "  ✓ Dockerfile.processor"
rm -f cloudbuild.yaml && echo "  ✓ cloudbuild.yaml（旧）"

success "旧Dockerfileを削除しました"

# Gitコミット
echo ""
echo "変更をコミット中..."

git add -A
git commit -m "refactor: remove old directory structure" || echo "変更なしまたはコミット済み"

success "削除完了しました"

echo ""
echo "=========================================="
echo -e "${GREEN}クリーンアップ完了！${NC}"
echo "=========================================="
echo ""
echo "新しいディレクトリ構造:"
tree -L 2 -d | head -30 || ls -la
echo ""
echo "次のステップ:"
echo "1. 変更をmainブランチにマージ:"
echo "   git checkout main"
echo "   git merge refactoring/restructure"
echo "2. リモートにプッシュ:"
echo "   git push origin main"
echo ""
