from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager
from flask_cors import CORS

bcrypt = Bcrypt()
jwt = JWTManager()

def init_extensions(app):

    bcrypt.init_app(app)
    jwt.init_app(app)

    CORS(
        app,
        origins=[
            "https://chatsaku.com",
            "https://www.chatsaku.com"
        ],
        supports_credentials=True
    )
