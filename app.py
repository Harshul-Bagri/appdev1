import os
import secrets
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request, session, url_for

import admin
import api
import auth
import examiner
import student
from common import pretty_date, pretty_status
from db import close_db, init_db
from demo import ensure_admin, seed_demo


def secret_key(directory):
    path = directory / "session.key"
    try:
        with path.open("x", encoding="utf-8") as key_file:
            key_file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return path.read_text(encoding="utf-8").strip()


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(24)
    return session["csrf_token"]


def check_csrf():
    if request.method != "POST":
        return

    saved_token = session.get("csrf_token", "")
    submitted_token = request.form.get("csrf_token", "")
    if not saved_token or not secrets.compare_digest(saved_token.encode(), submitted_token.encode()):
        abort(400, "This form expired. Go back, refresh the page and try again.")


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=Path(app.instance_path) / "portal.sqlite",
        SECRET_KEY=os.environ.get("PORTAL_SECRET_KEY"),
        ADMIN_EMAIL=os.environ.get("PORTAL_ADMIN_EMAIL", "admin@example.test").strip().lower(),
        ADMIN_PASSWORD=os.environ.get("PORTAL_ADMIN_PASSWORD", "DemoPass123"),
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        instance = Path(app.instance_path)
        instance.mkdir(exist_ok=True)
        app.config["SECRET_KEY"] = secret_key(instance)

    app.teardown_appcontext(close_db)
    with app.app_context():
        Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
        init_db()
        ensure_admin()

    app.before_request(auth.load_user)
    app.before_request(check_csrf)

    @app.get("/")
    def home():
        endpoint = f"{g.user['role']}.dashboard" if g.user else "auth.login"
        return redirect(url_for(endpoint))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    def show_error(error):
        return render_template("error.html", error=error), error.code

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.filters.update(date=pretty_date, status=pretty_status)
    for blueprint in (auth.bp, admin.bp, examiner.bp, student.bp, api.bp):
        app.register_blueprint(blueprint)
    app.cli.add_command(seed_demo)
    return app


if __name__ == "__main__":
    create_app().run()
