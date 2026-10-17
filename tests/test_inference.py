"""Structured provider output cannot invent IDs, windows, or fresh video verification."""

import json
import os
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from highlight_agent.briefs import STREET_PEOPLE_BRIEF, STREET_TRAFFIC_BRIEF
from highlight_agent.config import Settings
from highlight_agent.inference import InferenceClient, InferenceError
from highlight_agent.models import Moment


class FakeCompletions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(
            {**kwargs, "messages": [message.copy() for message in kwargs["messages"]]}
        )
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))])


def client(responses):
    completions = FakeCompletions(responses)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return InferenceClient(Settings(wandb_model="test-model"), client=fake), completions


def test_inference_endpoint_is_independent_of_weave_server(monkeypatch):
    monkeypatch.setenv("WANDB_BASE_URL", "https://api.wandb.ai")
    monkeypatch.setenv("WANDB_INFERENCE_BASE_URL", "https://inference.example/v1")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    inference = InferenceClient(Settings.from_env())
    try:
        assert str(inference.client.base_url) == "https://inference.example/v1/"
        assert os.environ["WANDB_BASE_URL"] == "https://api.wandb.ai"
    finally:
        inference.client.close()


def candidate():
    return Moment(
        id="known",
        video_id="source",
        start_seconds=10,
        end_seconds=14,
        caption="Car lands upright and continues driving.",
        score=2,
    )


def score(identifier="known", *, match=True, value=8):
    return {
        "id": identifier,
        "match": match,
        "score": value,
        "reason": "Upright car continues driving.",
    }


@pytest.mark.parametrize(
    ("brief", "query", "guidance"),
    [
        (STREET_PEOPLE_BRIEF, "pedestrians walking and cyclists riding", "people on foot"),
        (STREET_TRAFFIC_BRIEF, "buses and cars crossing an intersection", "buses and motor traffic"),
    ],
)
def test_street_search_briefs_use_observable_city_activity(brief, query, guidance):
    inference, completions = client(
        [json.dumps({"query": query, "alternate_query": "street scene with road users"})]
    )

    assert inference.expand(brief).query == query

    messages = completions.calls[0]["messages"]
    assert json.loads(messages[1]["content"]) == {"brief": brief}
    system = messages[0]["content"]
    assert guidance in system
    assert "unseen behavior" in system
    assert "clean stunt landings" not in system
    assert "Do not infer collisions, risk, or legality" in system


@pytest.mark.parametrize(
    ("brief", "caption", "guidance"),
    [
        (
            STREET_PEOPLE_BRIEF,
            "Traffic crosses the intersection as several pedestrians walk and cyclists pass.",
            "people on foot",
        ),
        (
            STREET_TRAFFIC_BRIEF,
            "A bus drives down the street alongside cars and trucks.",
            "buses and motor traffic",
        ),
    ],
)
def test_street_rankings_allow_driving_and_preserve_caption_evidence(brief, caption, guidance):
    entry = {**score(), "reason": "Street activity matches the requested scene."}
    inference, completions = client([json.dumps({"scores": [entry]})])
    original = candidate().model_copy(update={"caption": caption})

    ranked = inference.rank(brief, [original])

    assert len(ranked) == 1
    assert ranked[0].caption == caption
    assert ranked[0].start_seconds == original.start_seconds
    assert ranked[0].evidence_type == "caption_ranked"
    messages = completions.calls[0]["messages"]
    payload = json.loads(messages[1]["content"])
    assert payload == {"brief": brief, "candidates": [{"id": "known", "caption": caption}]}
    system = messages[0]["content"]
    assert guidance in system
    assert "Do not reject ordinary driving or quiet scenes" in system
    assert "Do not infer collisions, risk, or legality" in system
    assert "do not claim fresh verification" in system
    assert "clean stunt landings" not in system


@pytest.mark.parametrize(
    ("brief", "guidance"),
    [
        ("Make me look like a pro", "lands upright and continues driving"),
        ("clean landings", "A landing followed by a crash does not qualify"),
        ("Roast my driving", "visible impacts, rollovers, or failed landings"),
    ],
)
def test_explicit_gameplay_briefs_retain_their_outcome_criteria(brief, guidance):
    inference, completions = client(
        [
            json.dumps({"query": "requested driving outcome", "alternate_query": "driving action"}),
            json.dumps({"scores": [score()]}),
        ]
    )

    inference.expand(brief)
    inference.rank(brief, [candidate()])

    assert all(guidance in call["messages"][0]["content"] for call in completions.calls)


def test_other_footage_briefs_do_not_assume_a_gameplay_source():
    inference, completions = client(
        [
            json.dumps({"query": "bird taking flight", "alternate_query": "bird spreading wings"}),
            json.dumps({"scores": [score()]}),
        ]
    )

    inference.expand("Show a bird taking flight")
    inference.rank("Show a bird taking flight", [candidate()])

    for call in completions.calls:
        system = call["messages"][0]["content"]
        assert "Do not assume the footage is gameplay" in system
        assert "lands upright and continues driving" not in system
        assert "gameplay highlights editor" not in system


def test_malformed_json_gets_exactly_one_repair():
    inference, completions = client(
        [
            "not JSON",
            json.dumps({"query": "car lands upright", "alternate_query": "clean stunt landing"}),
        ]
    )
    result = inference.expand("Make me look like a pro")
    assert result.query == "car lands upright"
    assert len(completions.calls) == 2
    assert completions.calls[1]["messages"][-1]["content"].startswith("Repair your JSON")


@pytest.mark.parametrize("bad_scores", [[score("invented")], [score(), score()]])
def test_unknown_or_duplicate_ids_are_repaired_before_an_edit_is_returned(bad_scores):
    inference, completions = client(
        [json.dumps({"scores": bad_scores}), json.dumps({"scores": [score()]})]
    )
    original = candidate()
    ranked = inference.rank("clean landings", [original])
    assert len(completions.calls) == 2 and len(ranked) == 1
    assert ranked[0].id == original.id
    assert (ranked[0].start_seconds, ranked[0].end_seconds) == (10, 14)
    assert ranked[0].evidence_type == "caption_ranked"
    assert ranked[0].score == 8 and original.score == 2
    request = completions.calls[0]
    supplied = json.loads(request["messages"][1]["content"])["candidates"]
    assert supplied == [{"id": "known", "caption": original.caption}]


def test_repeated_invalid_data_fails_after_two_calls_without_returning_candidates():
    inference, completions = client([json.dumps({"scores": [score("invented")]}), "malformed"])
    with pytest.raises(InferenceError, match="twice"):
        inference.rank("crashes", [candidate()])
    assert len(completions.calls) == 2


@pytest.mark.parametrize("entry", [score(match=False), score(value=5.9)])
def test_nonmatches_and_insufficient_scores_are_excluded(entry):
    inference, _ = client([json.dumps({"scores": [entry]})])
    assert inference.rank("clean landings", [candidate()]) == []


@pytest.mark.parametrize(
    "provider_error",
    [
        APIConnectionError(
            request=httpx.Request("POST", "https://inference.example"),
            message="private-key in internal network error",
        ),
        APIStatusError(
            "private-key in remote body",
            response=httpx.Response(
                401, request=httpx.Request("POST", "https://inference.example")
            ),
            body={"detail": "private-key"},
        ),
    ],
)
def test_provider_errors_are_actionable_not_retried_as_json_and_hide_secrets(provider_error):
    inference, completions = client([provider_error])
    with pytest.raises(InferenceError, match="W&B inference request failed") as error:
        inference.expand("crashes")
    assert "private-key" not in str(error.value)
    assert len(completions.calls) == 1


def test_extra_timestamp_in_model_rank_output_is_rejected():
    invented = {**score(), "start_seconds": 999}
    inference, completions = client(
        [json.dumps({"scores": [invented]}), json.dumps({"scores": [score()]})]
    )
    ranked = inference.rank("clean landings", [candidate()])
    assert len(completions.calls) == 2
    assert ranked[0].start_seconds == 10
