#!/bin/bash
# import文更新スクリプト - 旧パスから新パスに一括置換

set -e

PROJECT_ROOT=~/document-management-system
cd $PROJECT_ROOT

echo "=========================================="
echo "import文の一括更新"
echo "=========================================="

# カラー定義
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

# Pythonファイルを再帰的に検索して置換
update_imports() {
    local old_path=$1
    local new_path=$2
    local description=$3

    step "更新中: $description ($old_path → $new_path)"

    # services/, shared/, scripts/ 配下の全Pythonファイルを対象
    find services shared scripts -name "*.py" -type f 2>/dev/null | while read file; do
        if grep -q "from $old_path" "$file" 2>/dev/null || grep -q "import $old_path" "$file" 2>/dev/null; then
            # from A_common → from shared.common
            sed -i "s/from $old_path/from $new_path/g" "$file"
            # import A_common → import shared.common
            sed -i "s/import $old_path/import $new_path/g" "$file"
            echo "  更新: $file"
        fi
    done

    success "$description の import文を更新しました"
}

# =============================================================================
# import文の置換実行
# =============================================================================

# 共通モジュール
update_imports "A_common" "shared.common" "A_common"
update_imports "C_ai_common" "shared.ai" "C_ai_common"
update_imports "G_unified_pipeline" "shared.pipeline" "G_unified_pipeline"
update_imports "K_kakeibo" "shared.kakeibo" "K_kakeibo"

# サービス間参照（あれば）
update_imports "B_ingestion" "services.data_ingestion" "B_ingestion"

# =============================================================================
# Dockerfile内のCOPY文も更新
# =============================================================================
step "Dockerfile内のパス更新"

find services -name "Dockerfile" -type f | while read file; do
    # COPY A_common → COPY shared/common
    sed -i 's|COPY A_common/|COPY shared/common/|g' "$file"
    sed -i 's|COPY C_ai_common/|COPY shared/ai/|g' "$file"
    sed -i 's|COPY G_unified_pipeline/|COPY shared/pipeline/|g' "$file"
    sed -i 's|COPY K_kakeibo/|COPY shared/kakeibo/|g' "$file"

    # COPY process_queued_documents.py → COPY scripts/processing/process_queued_documents.py
    # （ただし、ルートに残す場合はスキップ）

    echo "  更新: $file"
done

success "Dockerfileのパスを更新しました"

# =============================================================================
# コミット
# =============================================================================
step "変更をコミット"

git add -A
git commit -m "refactor: update import paths to new structure" || echo "変更なしまたはコミット済み"

success "import文の更新が完了しました"

echo ""
echo "=========================================="
echo -e "${GREEN}import文の更新完了！${NC}"
echo "=========================================="
echo ""
echo "次のステップ:"
echo "1. サービスをテスト:"
echo "   cd services/doc-processor && python app.py"
echo "2. 問題なければ、旧ディレクトリを削除:"
echo "   ./cleanup_old_dirs.sh"
echo ""
