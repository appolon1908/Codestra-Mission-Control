from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .control_sync import (
    CheckpointEnvelope,
    ControlSurfaceAdapter,
    Surface,
    SurfaceObservation,
)

JsonRequester = Callable[[str, str, dict[str, str], dict[str, Any] | None], dict[str, Any]]


def request_json(
    url: str,
    method: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _checkpoint_text(checkpoint: CheckpointEnvelope) -> str:
    return (
        f"Mission Control checkpoint: mission={checkpoint.mission_id} "
        f"status={checkpoint.status} head_sha={checkpoint.head_sha or 'none'} "
        f"request_complete={str(checkpoint.request_complete).lower()}"
    )


@dataclass(frozen=True)
class SurfaceBindings:
    local_repo: str | None = None
    linear_issue_id: str | None = None
    notion_page_id: str | None = None
    github_repo: str | None = None
    github_pr_number: int | None = None


class LocalCheckpointAdapter(ControlSurfaceAdapter):
    surface = Surface.LOCAL

    def __init__(self, repo_path: str | Path) -> None:
        self.repo = Path(repo_path)

    @staticmethod
    def _git_binary() -> str:
        discovered = shutil.which("git")
        if discovered:
            return discovered
        candidate = Path.home() / "AppData/Local/Programs/Git/cmd/git.exe"
        if candidate.is_file():
            return str(candidate)
        return "git"

    def _git(self, *args: str) -> str:
        return subprocess.check_output(
            [
                self._git_binary(),
                "-c",
                f"safe.directory={self.repo}",
                "-C",
                str(self.repo),
                *args,
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

    def read_state(self, mission_id: str) -> SurfaceObservation:
        try:
            head = self._git("rev-parse", "HEAD")
            return SurfaceObservation(Surface.LOCAL, True, "OK", head)
        except Exception as exc:  # noqa: BLE001
            return SurfaceObservation(
                Surface.LOCAL,
                False,
                "ERROR",
                error=type(exc).__name__,
            )

    def publish_checkpoint(self, checkpoint: CheckpointEnvelope) -> SurfaceObservation:
        channel = self.repo / ".codestra-mission"
        channel.mkdir(parents=True, exist_ok=True)
        target = channel / "checkpoint.json"
        temp = channel / "checkpoint.json.tmp"
        payload = {
            "mission_id": checkpoint.mission_id,
            "status": checkpoint.status,
            "head_sha": checkpoint.head_sha,
            "request_complete": checkpoint.request_complete,
        }
        temp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temp.replace(target)
        return self.read_state(checkpoint.mission_id)


class GitHubCheckpointAdapter(ControlSurfaceAdapter):
    surface = Surface.GITHUB

    def __init__(
        self,
        repo: str,
        pr_number: int,
        *,
        token: str | None = None,
        requester: JsonRequester = request_json,
    ) -> None:
        self.repo = repo
        self.pr_number = pr_number
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.requester = requester

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def read_state(self, mission_id: str) -> SurfaceObservation:
        try:
            payload = self.requester(
                f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}",
                "GET",
                self._headers(),
                None,
            )
            head = (payload.get("head") or {}).get("sha")
            state = str(payload.get("state") or "UNKNOWN").upper()
            return SurfaceObservation(Surface.GITHUB, True, state, head)
        except Exception as exc:  # noqa: BLE001
            return SurfaceObservation(
                Surface.GITHUB,
                False,
                "ERROR",
                error=type(exc).__name__,
            )

    def publish_checkpoint(self, checkpoint: CheckpointEnvelope) -> SurfaceObservation:
        state = self.read_state(checkpoint.mission_id)
        if not state.available:
            return state
        try:
            self.requester(
                (f"https://api.github.com/repos/{self.repo}/issues/{self.pr_number}/comments"),
                "POST",
                self._headers(),
                {"body": _checkpoint_text(checkpoint)},
            )
            return state
        except Exception as exc:  # noqa: BLE001
            return SurfaceObservation(
                Surface.GITHUB,
                False,
                "ERROR",
                state.head_sha,
                type(exc).__name__,
            )


class LinearCheckpointAdapter(ControlSurfaceAdapter):
    surface = Surface.LINEAR

    def __init__(
        self,
        issue_id: str,
        *,
        token: str | None = None,
        requester: JsonRequester = request_json,
    ) -> None:
        self.issue_id = issue_id
        self.token = token or os.environ.get("LINEAR_API_TOKEN", "")
        self.requester = requester

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = self.token
        return headers

    def read_state(self, mission_id: str) -> SurfaceObservation:
        body = {
            "query": ("query($id:String!){issue(id:$id){id identifier state{name}}}"),
            "variables": {"id": self.issue_id},
        }
        try:
            payload = self.requester(
                "https://api.linear.app/graphql",
                "POST",
                self._headers(),
                body,
            )
            issue = (payload.get("data") or {}).get("issue") or {}
            state = (issue.get("state") or {}).get("name") or "UNKNOWN"
            return SurfaceObservation(Surface.LINEAR, True, state)
        except Exception as exc:  # noqa: BLE001
            return SurfaceObservation(
                Surface.LINEAR,
                False,
                "ERROR",
                error=type(exc).__name__,
            )

    def publish_checkpoint(self, checkpoint: CheckpointEnvelope) -> SurfaceObservation:
        body = {
            "query": ("mutation($input:CommentCreateInput!){commentCreate(input:$input){success}}"),
            "variables": {
                "input": {
                    "issueId": self.issue_id,
                    "body": _checkpoint_text(checkpoint),
                }
            },
        }
        try:
            payload = self.requester(
                "https://api.linear.app/graphql",
                "POST",
                self._headers(),
                body,
            )
            success = bool(((payload.get("data") or {}).get("commentCreate") or {}).get("success"))
            if not success:
                return SurfaceObservation(Surface.LINEAR, False, "ERROR", checkpoint.head_sha)
            return SurfaceObservation(
                Surface.LINEAR,
                True,
                checkpoint.status,
                checkpoint.head_sha,
            )
        except Exception as exc:  # noqa: BLE001
            return SurfaceObservation(
                Surface.LINEAR,
                False,
                "ERROR",
                checkpoint.head_sha,
                type(exc).__name__,
            )


class NotionCheckpointAdapter(ControlSurfaceAdapter):
    surface = Surface.NOTION

    def __init__(
        self,
        page_id: str,
        *,
        token: str | None = None,
        requester: JsonRequester = request_json,
    ) -> None:
        self.page_id = page_id
        self.token = token or os.environ.get("NOTION_API_TOKEN", "")
        self.requester = requester

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def read_state(self, mission_id: str) -> SurfaceObservation:
        try:
            self.requester(
                f"https://api.notion.com/v1/pages/{self.page_id}",
                "GET",
                self._headers(),
                None,
            )
            return SurfaceObservation(Surface.NOTION, True, "AVAILABLE")
        except Exception as exc:  # noqa: BLE001
            return SurfaceObservation(
                Surface.NOTION,
                False,
                "ERROR",
                error=type(exc).__name__,
            )

    def publish_checkpoint(self, checkpoint: CheckpointEnvelope) -> SurfaceObservation:
        body = {
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": _checkpoint_text(checkpoint)},
                            }
                        ]
                    },
                }
            ]
        }
        try:
            self.requester(
                f"https://api.notion.com/v1/blocks/{self.page_id}/children",
                "PATCH",
                self._headers(),
                body,
            )
            return SurfaceObservation(
                Surface.NOTION,
                True,
                checkpoint.status,
                checkpoint.head_sha,
            )
        except Exception as exc:  # noqa: BLE001
            return SurfaceObservation(
                Surface.NOTION,
                False,
                "ERROR",
                checkpoint.head_sha,
                type(exc).__name__,
            )
