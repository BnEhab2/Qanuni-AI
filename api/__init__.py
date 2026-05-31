"""
Qanuni.AI — API Package
========================
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
    # Since api is a subdirectory, we set template and static folders relative to this file
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    
    # Configure app from config.py constants
    import config
    app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = config.JWT_ACCESS_TOKEN_EXPIRES
    
    # Enable CORS
    CORS(app)
    
    # Enable JWT Management
    JWTManager(app)
    
    # Initialize SQLite Database
    init_db()
    
    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(conversations_bp)
    
    # Base route to render frontend
    @app.route("/")
    def index():
        return render_template("index.html")
        
    return app
