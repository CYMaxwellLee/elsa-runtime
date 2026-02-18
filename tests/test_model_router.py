"""Mock API routing tests."""

from elsa_runtime.routing.model_router import ModelRouter


def test_model_router_instantiation():
    router = ModelRouter()
    assert router is not None
