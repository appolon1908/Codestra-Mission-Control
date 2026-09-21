from mission_control.control_sync import CheckpointEnvelope, Surface
from mission_control.surface_adapters import (
    GitHubCheckpointAdapter,
    LinearCheckpointAdapter,
    NotionCheckpointAdapter,
)


def test_github_adapter_reads_exact_pr_head_and_posts_comment():
    calls = []

    def requester(url, method, headers, body):
        calls.append((url, method, body))
        if method == "GET":
            return {"state": "open", "head": {"sha": "abc123"}}
        return {"id": 1}

    adapter = GitHubCheckpointAdapter(
        "owner/repo",
        7,
        token="x",
        requester=requester,
    )
    cp = CheckpointEnvelope("PAS-185", "IMPLEMENTED", "abc123")
    observation = adapter.publish_checkpoint(cp)
    assert observation.surface == Surface.GITHUB
    assert observation.head_sha == "abc123"
    assert calls[-1][1] == "POST"
    assert "head_sha=abc123" in calls[-1][2]["body"]


def test_linear_adapter_posts_checkpoint_comment():
    calls = []

    def requester(url, method, headers, body):
        calls.append((url, method, body))
        return {"data": {"commentCreate": {"success": True}}}

    adapter = LinearCheckpointAdapter("issue-id", token="x", requester=requester)
    observation = adapter.publish_checkpoint(
        CheckpointEnvelope("PAS-185", "TESTED", "abc123")
    )
    assert observation.available is True
    assert observation.head_sha == "abc123"
    assert calls[0][0] == "https://api.linear.app/graphql"


def test_notion_adapter_appends_checkpoint_block():
    calls = []

    def requester(url, method, headers, body):
        calls.append((url, method, body))
        return {"object": "list"}

    adapter = NotionCheckpointAdapter("page-id", token="x", requester=requester)
    observation = adapter.publish_checkpoint(
        CheckpointEnvelope("PAS-185", "REVIEW", "abc123")
    )
    assert observation.available is True
    assert calls[0][1] == "PATCH"
    content = calls[0][2]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert "mission=PAS-185" in content
