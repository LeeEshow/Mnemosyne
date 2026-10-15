import functools
import inspect

import httpx
from google.api_core import exceptions as api_exceptions
from google.auth import exceptions as auth_exceptions
from google.genai import errors as genai_errors

from domain.exceptions import UpstreamComponent, UpstreamFailureKind, UpstreamServiceError

_HTTP_NOT_FOUND = 404
_HTTP_SERVER_ERROR_FLOOR = 500
_STATUS_KINDS = {
    401: UpstreamFailureKind.AUTHENTICATION,
    403: UpstreamFailureKind.AUTHENTICATION,
    429: UpstreamFailureKind.QUOTA_EXCEEDED,
    503: UpstreamFailureKind.OVERLOADED,
}

GenaiFailure = (genai_errors.APIError, httpx.TransportError)


def _kind_from_status(status: int) -> UpstreamFailureKind:
    if status in _STATUS_KINDS:
        return _STATUS_KINDS[status]
    if status >= _HTTP_SERVER_ERROR_FLOOR:
        return UpstreamFailureKind.UNAVAILABLE
    return UpstreamFailureKind.CONFIGURATION


def translate_genai_error(component: UpstreamComponent, error: Exception) -> UpstreamServiceError:
    if isinstance(error, httpx.TransportError):
        return UpstreamServiceError(component, UpstreamFailureKind.UNAVAILABLE, type(error).__name__)
    status = error.code
    kind = UpstreamFailureKind.MODEL_UNAVAILABLE if status == _HTTP_NOT_FOUND else _kind_from_status(status)
    return UpstreamServiceError(component, kind, f"HTTP {status}")


def translate_firestore_error(error: Exception) -> UpstreamServiceError:
    component = UpstreamComponent.MEMORY_STORE
    if isinstance(error, auth_exceptions.GoogleAuthError):
        return UpstreamServiceError(component, UpstreamFailureKind.AUTHENTICATION, type(error).__name__)
    status = getattr(error, "code", None)
    if not isinstance(status, int):
        return UpstreamServiceError(component, UpstreamFailureKind.UNAVAILABLE, type(error).__name__)
    return UpstreamServiceError(component, _kind_from_status(status), f"HTTP {status} {type(error).__name__}")


def translating_firestore_errors(repository_class):
    for name, method in list(vars(repository_class).items()):
        if name.startswith("_") or not inspect.iscoroutinefunction(method):
            continue
        setattr(repository_class, name, _wrap_firestore_method(method))
    return repository_class


def _wrap_firestore_method(method):
    @functools.wraps(method)
    async def wrapper(*args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except (api_exceptions.GoogleAPIError, auth_exceptions.GoogleAuthError) as error:
            raise translate_firestore_error(error) from error

    return wrapper
