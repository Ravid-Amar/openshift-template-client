"""Small OpenShift Template client; see README.md for usage and limitations."""

from ._client import (
    OperationError,
    connect,
    create,
    delete,
    get,
    list_template_parameters,
    list_templates,
    process,
)

__all__ = [
    "OperationError",
    "connect",
    "create",
    "delete",
    "get",
    "list_template_parameters",
    "list_templates",
    "process",
]
