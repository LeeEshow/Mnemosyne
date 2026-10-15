import unittest

from google.api_core import exceptions as api_exceptions
from google.genai import errors as genai_errors

from domain.exceptions import MemoryNotFoundError, UpstreamComponent, UpstreamFailureKind, UpstreamServiceError
from infrastructure.upstream_errors import translate_genai_error, translating_firestore_errors


def _genai_error(code: int) -> genai_errors.APIError:
    error_class = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return error_class(code, {"error": {"code": code, "message": "x", "status": "X"}})


class GenaiTranslationTest(unittest.TestCase):
    def test_http_status_maps_to_the_failure_kind_that_decides_the_retry_advice(self):
        expected = {
            503: UpstreamFailureKind.OVERLOADED,
            429: UpstreamFailureKind.QUOTA_EXCEEDED,
            404: UpstreamFailureKind.MODEL_UNAVAILABLE,
            401: UpstreamFailureKind.AUTHENTICATION,
            403: UpstreamFailureKind.AUTHENTICATION,
            500: UpstreamFailureKind.UNAVAILABLE,
            400: UpstreamFailureKind.CONFIGURATION,
        }
        for status, kind in expected.items():
            with self.subTest(status=status):
                translated = translate_genai_error(UpstreamComponent.EMBEDDING, _genai_error(status))
                self.assertEqual(kind, translated.kind)
                self.assertEqual(UpstreamComponent.EMBEDDING, translated.component)

    def test_only_transient_failures_are_advertised_as_retryable(self):
        self.assertTrue(UpstreamFailureKind.OVERLOADED.retryable)
        self.assertTrue(UpstreamFailureKind.QUOTA_EXCEEDED.retryable)
        self.assertFalse(UpstreamFailureKind.MODEL_UNAVAILABLE.retryable)
        self.assertFalse(UpstreamFailureKind.AUTHENTICATION.retryable)


@translating_firestore_errors
class _FakeRepository:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def read(self):
        raise self._error

    def helper_that_is_not_async(self):
        return "unchanged"


class FirestoreTranslationTest(unittest.IsolatedAsyncioTestCase):
    async def test_firestore_outage_becomes_a_retryable_upstream_error(self):
        with self.assertRaises(UpstreamServiceError) as context:
            await _FakeRepository(api_exceptions.ServiceUnavailable("down")).read()

        self.assertEqual(UpstreamComponent.MEMORY_STORE, context.exception.component)
        self.assertTrue(context.exception.kind.retryable)

    async def test_missing_index_is_reported_as_configuration_not_as_a_retryable_outage(self):
        with self.assertRaises(UpstreamServiceError) as context:
            await _FakeRepository(api_exceptions.FailedPrecondition("index required")).read()

        self.assertEqual(UpstreamFailureKind.CONFIGURATION, context.exception.kind)

    async def test_domain_errors_raised_inside_the_repository_are_not_swallowed(self):
        with self.assertRaises(MemoryNotFoundError):
            await _FakeRepository(MemoryNotFoundError("x")).read()

    def test_non_async_methods_are_left_untouched(self):
        self.assertEqual("unchanged", _FakeRepository(RuntimeError()).helper_that_is_not_async())


if __name__ == "__main__":
    unittest.main()
