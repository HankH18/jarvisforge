"""Acceptance #4: capability questions are answered solely from
capabilities.yaml. Combines self_manifest() with synthesize_grounded to
prove the answer prompt for a capability question is built strictly from
manifest data, and that editing the manifest changes what reaches the model.
"""

from __future__ import annotations

import json

import yaml
from fakes import RecordingModelClient

from jarvis.llm.synthesis import synthesize_grounded
from jarvis.tools.self_manifest import self_manifest


def test_capability_answer_prompt_contains_only_the_manifest_data(tmp_path):
    manifest_path = tmp_path / "capabilities.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "version": "1.0.0",
                "capabilities": ["hold a real-time voice conversation"],
                "limitations": ["cannot answer github questions yet"],
            }
        )
    )
    result = self_manifest(path=manifest_path)
    client = RecordingModelClient(reply="Jarvis can hold a real-time voice conversation.")

    synthesize_grounded("What can you do?", [result], [], model="stub-model", client=client)

    # the exact manifest data reaches the model request, verbatim as JSON --
    # nothing added, nothing summarized by our code.
    expected_blob = json.dumps({"source": "self:manifest", "data": result["data"]})
    assert expected_blob in client.last_user


def test_removing_a_capability_changes_what_reaches_the_model(tmp_path):
    manifest_path = tmp_path / "capabilities.yaml"
    full = {
        "version": "1.0.0",
        "capabilities": ["hold a real-time voice conversation", "answer weather questions"],
        "limitations": [],
    }
    manifest_path.write_text(yaml.safe_dump(full))
    client_before = RecordingModelClient(reply="ok")
    synthesize_grounded(
        "What can you do?",
        [self_manifest(path=manifest_path)],
        [],
        model="stub-model",
        client=client_before,
    )
    assert "answer weather questions" in client_before.last_user

    reduced = dict(full, capabilities=["hold a real-time voice conversation"])
    manifest_path.write_text(yaml.safe_dump(reduced))
    client_after = RecordingModelClient(reply="ok")
    synthesize_grounded(
        "What can you do?",
        [self_manifest(path=manifest_path)],
        [],
        model="stub-model",
        client=client_after,
    )

    assert "answer weather questions" not in client_after.last_user
    assert client_before.last_user != client_after.last_user
