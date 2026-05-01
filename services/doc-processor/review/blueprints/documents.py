"""
Documents Blueprint
ドキュメントレビュー画面用ルート
"""
from flask import Blueprint, render_template, redirect, url_for, session

from services.auth_service import login_required

documents_bp = Blueprint('documents', __name__)


@documents_bp.route('/')
@documents_bp.route('/review')
@login_required
def review_page():
    """ドキュメントレビュー画面"""
    return render_template('documents/review.html')
