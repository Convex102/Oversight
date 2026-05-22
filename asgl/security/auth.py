from fastapi import Header
from asgl.config import API_KEYS
from asgl.utils.errors import AuthError

def require_api_key(x_api_key: str = Header(default="")):
    if API_KEYS and x_api_key not in API_KEYS:
        raise AuthError("Invalid API key")
    return x_api_key
