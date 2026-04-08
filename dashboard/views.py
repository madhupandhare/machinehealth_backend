"""
dashboard/views.py
Single-page dashboard — all data loaded client-side via /api/* endpoints.
This means it reads DynamoDB directly, so data shows even when fog node
is in a separate process or running remotely.
"""
import sys
sys.path.insert(0, ".")
from flask import Blueprint, redirect, render_template, url_for

dashboard_bp = Blueprint("dashboard", __name__)

@dashboard_bp.route("/")
def index():
    return redirect(url_for("dashboard.main"))

@dashboard_bp.route("/dashboard")
def main():
    return render_template("dashboard.html")
