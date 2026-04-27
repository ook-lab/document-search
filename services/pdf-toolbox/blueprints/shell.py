from flask import Blueprint, render_template

shell_bp = Blueprint('shell', __name__)

@shell_bp.route('/')
def index():
    return render_template('shell.html')
