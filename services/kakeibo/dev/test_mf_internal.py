import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
import os
from flask import Flask, request, jsonify

# app.pyがあるディレクトリをパスに追加
sys.path.append(os.getcwd())

from app import app, import_mf_csv

def run_test():
    with app.test_request_context(method='POST'):
        try:
            response = import_mf_csv()
            print("Response:", response.get_data(as_text=True))
        except Exception as e:
            print("Error occurred during import_mf_csv:")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    run_test()
