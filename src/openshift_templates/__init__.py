"""Small OpenShift Template client; see README.md for usage and limitations."""

from ._client import OperationError, connect, create, delete, process

__all__ = ["OperationError", "connect", "create", "delete", "process"]
