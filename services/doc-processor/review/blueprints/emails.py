"""
Emails Blueprint
メール受信トレイ画面用ルート
"""
from flask import Blueprint, render_template

from services.auth_service import login_required

emails_bp = Blueprint('emails', __name__)


@emails_bp.route('/')
@emails_bp.route('/inbox')
@login_required
def inbox_page():
    """メール受信トレイ画面"""
    return render_template('emails/inbox.html')
