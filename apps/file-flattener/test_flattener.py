import os
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from flattener import scan_directory, execute_flatten

class TestFileFlattener(unittest.TestCase):
    def setUp(self):
        # テスト用の一時ディレクトリを作成
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

    def tearDown(self):
        # テスト後、一時ディレクトリをクリーンアップ
        self.temp_dir.cleanup()

    def test_scan_and_flatten(self):
        # 1. テスト用のフォルダ階層とファイルを構築
        # - root/
        #   - c.txt (直下ファイル)
        #   - sub1/
        #     - a.txt
        #     - b.txt
        #   - sub2/
        #     - a.txt (sub1/a.txt と名前が重複)
        #     - sub3/
        #       - b.txt (sub1/b.txt と名前が重複)
        
        # フォルダ作成
        sub1 = os.path.join(self.root, "sub1")
        sub2 = os.path.join(self.root, "sub2")
        sub3 = os.path.join(sub2, "sub3")
        os.makedirs(sub1, exist_ok=True)
        os.makedirs(sub2, exist_ok=True)
        os.makedirs(sub3, exist_ok=True)

        # ファイル作成
        c_path = os.path.join(self.root, "c.txt")
        sub1_a = os.path.join(sub1, "a.txt")
        sub1_b = os.path.join(sub1, "b.txt")
        sub2_a = os.path.join(sub2, "a.txt")
        sub3_b = os.path.join(sub3, "b.txt")

        with open(c_path, "w") as f: f.write("content of c")
        with open(sub1_a, "w") as f: f.write("content of sub1/a")
        with open(sub1_b, "w") as f: f.write("content of sub1/b")
        with open(sub2_a, "w") as f: f.write("content of sub2/a")
        with open(sub3_b, "w") as f: f.write("content of sub3/b")

        # 2. スキャン (Dry Run) のテスト
        mappings = scan_directory(self.root)

        # 最上位以外のファイル（sub1/a, sub1/b, sub2/a, sub3/b）の4件がスキャン対象
        self.assertEqual(len(mappings), 4)

        # 移動先の名前を取得
        dest_names = [m["dest_filename"] for m in mappings]
        
        # 元のファイル名が a.txt, b.txt であるため、重複回避が機能しているか
        # 期待値: a.txt と a_1.txt, b.txt と b_1.txt
        self.assertIn("a.txt", dest_names)
        self.assertIn("a_1.txt", dest_names)
        self.assertIn("b.txt", dest_names)
        self.assertIn("b_1.txt", dest_names)

        # c.txt は最上位なのでスキャン対象外であること
        src_paths = [m["src_path"] for m in mappings]
        self.assertNotIn(c_path, src_paths)

        # 3. フラット化実行のテスト
        result = execute_flatten(self.root, mappings, delete_empty_dirs=True)

        # 移動成功数
        self.assertEqual(result["success_count"], 4)
        self.assertEqual(len(result["errors"]), 0)

        # 最上位ディレクトリ直下に期待するファイルがすべて存在することを確認
        expected_files = ["c.txt", "a.txt", "a_1.txt", "b.txt", "b_1.txt"]
        for filename in expected_files:
            file_path = os.path.join(self.root, filename)
            self.assertTrue(os.path.exists(file_path), f"{filename} が最上位に存在しません。")

        # ファイルの中身が正しく保持されていること
        # a.txt と a_1.txt の内容を確認
        a_contents = []
        with open(os.path.join(self.root, "a.txt"), "r") as f: a_contents.append(f.read())
        with open(os.path.join(self.root, "a_1.txt"), "r") as f: a_contents.append(f.read())
        self.assertIn("content of sub1/a", a_contents)
        self.assertIn("content of sub2/a", a_contents)

        # 空フォルダが削除されていること
        self.assertFalse(os.path.exists(sub1))
        self.assertFalse(os.path.exists(sub2))
        self.assertFalse(os.path.exists(sub3))

if __name__ == "__main__":
    unittest.main()
