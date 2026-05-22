import hashlib, secrets
def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
def generate_api_key(prefix="asgl"):
    raw = f"{prefix}_{secrets.token_urlsafe(32)}"
    return raw, hash_api_key(raw)
