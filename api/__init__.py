"""
Qanuni.AI — API Package
Flask app factory with blueprint registration.
"""

from flask import Flask, render_template
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from api.models import init_db
from api.auth import auth_bp
from api.chat import chat_bp
from api.conversations import conversations_bp

def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    import config
    app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = config.JWT_ACCESS_TOKEN_EXPIRES

    CORS(app, resources={r"/api/*": {"origins": config.ALLOWED_ORIGINS}})
    JWTManager(app)
    init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(conversations_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    return app
