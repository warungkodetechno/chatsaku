from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager
from flask_cors import CORS

bcrypt = Bcrypt()
jwt = JWTManager()
@jwt.invalid_token_loader
def invalid_token(reason):

    print("JWT INVALID:", reason)

    return jsonify({
        "success":False,
        "message":reason
    }),401


@jwt.unauthorized_loader
def missing_token(reason):

    print("JWT MISSING:", reason)

    return jsonify({
        "success":False,
        "message":reason
    }),401


@jwt.expired_token_loader
def expired_token(jwt_header, jwt_payload):

    print("JWT EXPIRED")

    return jsonify({
        "success":False,
        "message":"Token expired"
    }),401

cors = CORS()

def init_extensions(app):

    bcrypt.init_app(app)
    jwt.init_app(app)

    cors.init_app(
        app,
        resources={
            r"/*": {
                "origins": [
                    "https://chatsaku.com",
                    "https://www.chatsaku.com",
                    "http://localhost:5500",
                    "http://127.0.0.1:5500"
                ]
            }
        },
        supports_credentials=True
    )
