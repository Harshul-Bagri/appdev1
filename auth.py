import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from common import FormError, email_value, field
from db import get_db, row

bp = Blueprint("auth", __name__)


def load_user():
    user_id = session.get("user_id")
    g.user = None
    if user_id is None:
        return

    g.user = row("SELECT * FROM users WHERE id = ? AND status = 'active'", (user_id,))
    if g.user is None:
        session.clear()


def check_role(role):
    if g.user is None:
        return redirect(url_for("auth.login"))
    if g.user["role"] != role:
        abort(403)


def create_account(form, role, status):
    name = field(form, "name", "Full name")
    email = email_value(form)
    password = form.get("password", "")
    if not 8 <= len(password) <= 128 or not password.strip():
        raise FormError("Use a password with 8 to 128 characters.")

    roll_number = None
    department = None
    if role == "student":
        roll_number = field(form, "roll_no", "Roll number", 40).upper()
    else:
        department = field(form, "department", "Department")

    get_db().execute(
        """
        INSERT INTO users (name, email, password_hash, role, roll_no, department, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, email, generate_password_hash(password), role, roll_number, department, status),
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = row("SELECT * FROM users WHERE email = ?", (email,))
        if not user or len(password) > 128 or not check_password_hash(user["password_hash"], password):
            flash("Check your email and password.", "error")
        elif user["status"] == "pending":
            flash("Your examiner account is pending approval.", "error")
        elif user["status"] == "inactive":
            flash("Your account is inactive.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
    return render_template("login.html")


@bp.route("/register/<role>", methods=["GET", "POST"])
def register(role):
    if role not in ("student", "examiner"):
        abort(404)
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        try:
            status = "pending" if role == "examiner" else "active"
            create_account(request.form, role, status)
        except FormError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("That email or roll number is already registered.", "error")
        else:
            flash("Request sent for admin approval." if role == "examiner" else "Account created. You can sign in.")
            return redirect(url_for("auth.login"))
    return render_template("register.html", role=role)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
