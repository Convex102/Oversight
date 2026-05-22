"""ASGL-C exceptions."""

class ASGLCError(Exception):
    """Base error for ASGL-C."""

class SchemaInferenceError(ASGLCError):
    """Raised when schema inference fails."""

class RegistryError(ASGLCError):
    """Raised when registry operations fail."""

class CanonicalError(ASGLCError):
    """Raised when canonicalization fails."""
