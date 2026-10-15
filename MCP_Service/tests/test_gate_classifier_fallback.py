import unittest
from types import SimpleNamespace
from unittest import mock

import httpx
from google.genai import errors as genai_errors

import config
from domain.exceptions import UpstreamComponent, UpstreamFailureKind, UpstreamServiceError
from domain.write_gate_policy import WriteGateDecision
from infrastructure.gemini_gate_classifier import GeminiGateClassifier

_PRIMARY = "primary-model"
_FALLBACK = "fallback-model"
_ADD_RESPONSE = SimpleNamespace(text='{"decision": "ADD"}')


def _api_error(code: int) -> genai_errors.APIError:
    error_class = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return error_class(code, {"error": {"code": code, "message": "x", "status": "X"}})


class _FakeModels:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self._outcomes = outcomes
        self.requested_models: list[str] = []

    async def generate_content(self, *, model, contents, config):
        self.requested_models.append(model)
        outcome = self._outcomes[model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _classifier(outcomes: dict[str, object]) -> tuple[GeminiGateClassifier, _FakeModels]:
    models = _FakeModels(outcomes)
    classifier = object.__new__(GeminiGateClassifier)
    classifier._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return classifier, models


_MEMORY = SimpleNamespace(id=None, title="t", premise="p", conclusion="c")


async def _classify(classifier: GeminiGateClassifier, *, fallback: str | None = _FALLBACK):
    with mock.patch.object(config, "GATE_CLASSIFIER_MODEL", _PRIMARY), mock.patch.object(
        config, "GATE_CLASSIFIER_FALLBACK_MODEL", fallback
    ):
        return await classifier.classify(_MEMORY, ())


class GateClassifierFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_overloaded_primary_model_is_bypassed_by_the_fallback_model(self):
        # 2026-10-14 的 503 事故：主模型過載時，寫入不該因此整個失敗。
        classifier, models = _classifier({_PRIMARY: _api_error(503), _FALLBACK: _ADD_RESPONSE})

        verdict = await _classify(classifier)

        self.assertEqual(WriteGateDecision.ADD, verdict.decision)
        self.assertEqual([_PRIMARY, _FALLBACK], models.requested_models)

    async def test_healthy_primary_never_touches_the_fallback(self):
        classifier, models = _classifier({_PRIMARY: _ADD_RESPONSE})

        await _classify(classifier)

        self.assertEqual([_PRIMARY], models.requested_models)

    async def test_both_models_failing_reports_both_so_the_caller_knows_it_is_not_a_single_model_issue(self):
        classifier, _ = _classifier({_PRIMARY: _api_error(503), _FALLBACK: _api_error(429)})

        with self.assertRaises(UpstreamServiceError) as context:
            await _classify(classifier)

        self.assertEqual(UpstreamComponent.GATE_CLASSIFIER, context.exception.component)
        self.assertEqual(UpstreamFailureKind.QUOTA_EXCEEDED, context.exception.kind)
        self.assertIn(_PRIMARY, context.exception.detail)
        self.assertIn(_FALLBACK, context.exception.detail)

    async def test_auth_failure_does_not_fall_back_because_another_model_cannot_fix_it(self):
        classifier, models = _classifier({_PRIMARY: _api_error(403), _FALLBACK: _ADD_RESPONSE})

        with self.assertRaises(UpstreamServiceError) as context:
            await _classify(classifier)

        self.assertEqual(UpstreamFailureKind.AUTHENTICATION, context.exception.kind)
        self.assertEqual([_PRIMARY], models.requested_models)

    async def test_without_a_configured_fallback_the_primary_failure_is_reported(self):
        classifier, models = _classifier({_PRIMARY: _api_error(503)})

        with self.assertRaises(UpstreamServiceError) as context:
            await _classify(classifier, fallback=None)

        self.assertEqual(UpstreamFailureKind.OVERLOADED, context.exception.kind)
        self.assertEqual([_PRIMARY], models.requested_models)

    async def test_network_timeout_is_treated_as_unavailable_and_falls_back(self):
        classifier, models = _classifier({_PRIMARY: httpx.ReadTimeout("slow"), _FALLBACK: _ADD_RESPONSE})

        await _classify(classifier)

        self.assertEqual([_PRIMARY, _FALLBACK], models.requested_models)

    async def test_unparseable_verdict_is_reported_as_bad_response_not_a_raw_python_error(self):
        for text in ("not json", None, '{"decision": "MAYBE"}', "{}"):
            with self.subTest(text=text):
                classifier, models = _classifier({_PRIMARY: SimpleNamespace(text=text)})

                with self.assertRaises(UpstreamServiceError) as context:
                    await _classify(classifier)

                self.assertEqual(UpstreamFailureKind.BAD_RESPONSE, context.exception.kind)
                self.assertEqual([_PRIMARY], models.requested_models)


if __name__ == "__main__":
    unittest.main()
