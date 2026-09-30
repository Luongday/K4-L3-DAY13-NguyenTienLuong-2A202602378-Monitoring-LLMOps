from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import agent as agent_module
from app.incidents import STATE


class RecordedObservation:
    def __init__(self, name: str, as_type: str, kwargs: dict) -> None:
        self.name = name
        self.as_type = as_type
        self.kwargs = kwargs
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class ManagedPrompt:
    version = 2

    def compile(self, **variables: str) -> str:
        return f"Feature={variables['feature']}\nDocs={variables['docs']}\nQuestion={variables['message']}"


class Client:
    def get_prompt(self, name: str, **kwargs):
        return ManagedPrompt()

    def update_current_span(self, **kwargs) -> None:
        return None


@pytest.fixture
def observations(monkeypatch) -> list[RecordedObservation]:
    recorded: list[RecordedObservation] = []

    @contextmanager
    def record(name: str, *, as_type: str, **kwargs):
        obs = RecordedObservation(name, as_type, kwargs)
        recorded.append(obs)
        yield obs

    @contextmanager
    def no_attributes(**kwargs):
        yield

    monkeypatch.setattr(agent_module, "observation", record)
    monkeypatch.setattr(agent_module, "propagate_attributes", no_attributes)
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: Client())
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: True)
    return recorded


def _run(message: str) -> agent_module.AgentResult:
    return agent_module.LabAgent.run.__wrapped__(
        agent_module.LabAgent(),
        user_id="student-01",
        feature="qa",
        session_id="session-01",
        message=message,
        correlation_id="req-12345678",
    )


def test_run_creates_retriever_then_generation_child_observations(observations) -> None:
    result = _run("Explain monitoring")

    assert [(o.name, o.as_type) for o in observations] == [
        ("retrieval", "retriever"),
        ("llm-generation", "generation"),
    ]
    retrieval, generation = observations
    assert retrieval.updates[-1]["output"] == {"doc_count": 1}
    assert generation.kwargs["model"] == "claude-sonnet-4-5"
    assert isinstance(generation.kwargs["prompt"], ManagedPrompt)

    final = generation.updates[-1]
    assert final["usage_details"] == {"input": result.tokens_in, "output": result.tokens_out}
    assert final["cost_details"]["total"] == result.cost_usd
    assert final["cost_details"]["total"] == pytest.approx(
        final["cost_details"]["input"] + final["cost_details"]["output"], abs=1e-6
    )
    assert final["completion_start_time"] is not None
    assert final["metadata"]["prompt_version"] == "2"


def test_child_observations_carry_no_user_text_or_pii(observations) -> None:
    _run("My email is student@vinuni.edu.vn and phone 0987654321")

    for obs in observations:
        assert "input" not in obs.kwargs
        assert all("output" not in update or update["output"] == {"doc_count": 1} for update in obs.updates)
    captured = repr([(o.kwargs, o.updates) for o in observations])
    assert "student@vinuni.edu.vn" not in captured
    assert "0987654321" not in captured
    assert "Explain" not in captured and "email" not in captured


def test_retrieval_failure_marks_retriever_observation_as_error(observations) -> None:
    STATE["tool_fail"] = True
    try:
        with pytest.raises(RuntimeError):
            _run("Explain monitoring")
    finally:
        STATE["tool_fail"] = False

    assert [o.as_type for o in observations] == ["retriever"]
    assert observations[0].updates[-1]["level"] == "ERROR"
    assert "Vector store timeout" in observations[0].updates[-1]["status_message"]
