"""adapters.atlassian 시험 (mock 서버): Cloud/DC base path·인증, pagination 3 방식, 429 Retry-After, 409, 401, redirect, write off preview, doctor."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from test_adapters_mockserver import MockServer, Recorded, Scripted

from corp_dl_agent.adapters.atlassian import (
    PRODUCTS,
    AtlassianClient,
    BambooClient,
    BitbucketClient,
    ConfluenceClient,
    EventLedger,
    JiraClient,
    client_for,
    integrations_doctor,
    preview_payload,
)
from corp_dl_agent.common import Status
from corp_dl_agent.config import load_config
from corp_dl_agent.errors import AgentError

TOKEN_ENV = "CORP_DL_AGENT_TEST_ATL_TOKEN"
PAT = "atl-pat-secret-0123456789abcdef"
BASIC = "user@example.internal:api-token-secret-9876543210"


def make_cfg(
    tmp_path: Path,
    port: int,
    product: str,
    *,
    deployment: str = "datacenter",
    write: bool = False,
    **extra: str,
) -> Any:
    ov = {
        "profile": "corp-gateway",
        "gateway.enabled": "true",
        "gateway.base_url": f"http://127.0.0.1:{port}",
        "gateway.model_id": "synthetic-model-id",
        "gateway.secret_ref": f"env:{TOKEN_ENV}",
        "gateway.approved_origins": f"[http://127.0.0.1:{port}]",
        "integrations.write": "true" if write else "false",
        "integrations.approved_origins": f"[http://127.0.0.1:{port}]",
        f"integrations.{product}.enabled": "true",
        f"integrations.{product}.base_url": f"http://127.0.0.1:{port}",
        f"integrations.{product}.deployment": deployment,
        f"integrations.{product}.secret_ref": f"env:{TOKEN_ENV}",
        "paths.data_root": str(tmp_path),
    }
    ov.update(extra)
    return load_config(overrides=ov)


@pytest.fixture
def srv() -> Any:
    with MockServer() as s:
        yield s


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, PAT)


def no_sleep(_: float) -> None:
    return None


# --------------------------------------------------------------------------- base path / 인증


def test_base_paths_cloud_vs_datacenter(tmp_path: Path, srv: MockServer) -> None:
    dc = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"))
    cloud = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira", deployment="cloud"))
    assert dc.url("/issue/X-1").endswith("/rest/api/2/issue/X-1") and cloud.url("/issue/X-1").endswith(
        "/rest/api/3/issue/X-1"
    )
    assert (
        ConfluenceClient.from_app_config(make_cfg(tmp_path, srv.port, "confluence")).base_path()
        == "/rest/api"
    )
    assert (
        ConfluenceClient.from_app_config(
            make_cfg(tmp_path, srv.port, "confluence", deployment="cloud")
        ).base_path()
        == "/wiki/rest/api"
    )
    assert (
        BitbucketClient.from_app_config(make_cfg(tmp_path, srv.port, "bitbucket")).base_path()
        == "/rest/api/1.0"
    )
    assert (
        BitbucketClient.from_app_config(
            make_cfg(tmp_path, srv.port, "bitbucket", deployment="cloud")
        ).base_path()
        == "/2.0"
    )
    assert (
        BambooClient.from_app_config(make_cfg(tmp_path, srv.port, "bamboo")).base_path() == "/rest/api/latest"
    )
    with pytest.raises(AgentError) as ei:
        BambooClient.from_app_config(make_cfg(tmp_path, srv.port, "bamboo", deployment="cloud")).base_path()
    assert ei.value.code == "E_NOT_SUPPORTED"
    assert dc.auth_scheme == "bearer" and cloud.auth_scheme == "basic"


def test_bearer_and_basic_auth_headers(
    tmp_path: Path, srv: MockServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    srv.enqueue(200, {"key": "SYN-1", "fields": {"summary": "합성"}})
    dc = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=no_sleep)
    assert dc.get_issue("SYN-1")["key"] == "SYN-1"
    assert srv.requests[0].headers["authorization"] == f"Bearer {PAT}"
    assert srv.requests[0].path == "/rest/api/2/issue/SYN-1"
    monkeypatch.setenv(TOKEN_ENV, BASIC)
    srv.enqueue(200, {"key": "SYN-2"})
    cloud = JiraClient.from_app_config(
        make_cfg(tmp_path, srv.port, "jira", deployment="cloud"), sleep=no_sleep
    )
    cloud.get_issue("SYN-2")
    expected = "Basic " + base64.b64encode(BASIC.encode()).decode()
    assert srv.requests[1].headers["authorization"] == expected
    assert srv.requests[1].path == "/rest/api/3/issue/SYN-2"
    # cloud Basic 인증인데 secret 에 ':' 가 없으면 설정 오류 (값 노출 없음)
    monkeypatch.setenv(TOKEN_ENV, "tokenwithoutcolon-abcdefgh")
    cloud2 = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira", deployment="cloud"))
    with pytest.raises(AgentError) as ei:
        cloud2.headers()
    assert ei.value.code == "E_BLOCKED_CONFIG" and "tokenwithoutcolon" not in str(ei.value.to_dict())


def test_missing_config_blocks_without_fallback(tmp_path: Path, srv: MockServer) -> None:
    cfg = load_config(overrides={"paths.data_root": str(tmp_path)})  # 아무것도 설정 안 됨
    client = JiraClient.from_app_config(cfg)
    with pytest.raises(AgentError) as ei:
        client.get_issue("X-1")
    assert ei.value.code == "E_BLOCKED_CONFIG" and "base_url" in ei.value.details["missing"]
    assert srv.requests == []


def test_offline_profile_makes_no_requests(tmp_path: Path, srv: MockServer) -> None:
    cfg = load_config(
        overrides={
            "paths.data_root": str(tmp_path),
            "integrations.jira.enabled": "true",
            "integrations.jira.base_url": f"http://127.0.0.1:{srv.port}",
            "integrations.jira.secret_ref": f"env:{TOKEN_ENV}",
            "integrations.approved_origins": f"[http://127.0.0.1:{srv.port}]",
        }
    )
    client = JiraClient.from_app_config(cfg)
    with pytest.raises(AgentError) as ei:
        client.get_issue("X-1")
    assert ei.value.code == "E_NETWORK_BLOCKED" and srv.requests == []


def test_request_outside_approved_origin_rejected(tmp_path: Path, srv: MockServer) -> None:
    cfg = make_cfg(tmp_path, srv.port, "jira", **{"integrations.approved_origins": "[https://jira.internal]"})
    client = JiraClient.from_app_config(cfg)
    with pytest.raises(AgentError) as ei:
        client.get_issue("X-1")
    assert ei.value.code == "E_ORIGIN_NOT_ALLOWED" and srv.requests == []


# --------------------------------------------------------------------------- pagination 3 방식


def test_jira_dc_pagination_start_at_max_results(tmp_path: Path, srv: MockServer) -> None:
    def responder(req: Recorded) -> Scripted:
        start = int(req.query.get("startAt", ["0"])[0])
        issues = [{"key": f"SYN-{i}"} for i in range(start, min(start + 2, 5))]
        return Scripted(200, {"startAt": start, "maxResults": 2, "total": 5, "issues": issues})

    srv.set_responder(responder)
    client = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=no_sleep)
    result = client.search_issues("project = SYN", fields=("summary",))
    assert [i["key"] for i in result.items] == [f"SYN-{i}" for i in range(5)]
    assert result.pages == 3 and result.total == 5 and result.truncated is False
    assert [r.query["startAt"][0] for r in srv.requests] == ["0", "2", "4"]
    assert all(r.path == "/rest/api/2/search" for r in srv.requests)
    assert srv.requests[0].query["jql"] == ["project = SYN"] and srv.requests[0].query["maxResults"] == ["50"]


def test_jira_cloud_pagination_cursor_token(
    tmp_path: Path, srv: MockServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, BASIC)

    def responder(req: Recorded) -> Scripted:
        token = req.query.get("nextPageToken", [None])[0]
        if token is None:
            return Scripted(
                200, {"issues": [{"key": "A-1"}, {"key": "A-2"}], "nextPageToken": "tok-2", "isLast": False}
            )
        assert token == "tok-2"
        return Scripted(200, {"issues": [{"key": "A-3"}], "isLast": True})

    srv.set_responder(responder)
    client = JiraClient.from_app_config(
        make_cfg(tmp_path, srv.port, "jira", deployment="cloud"), sleep=no_sleep
    )
    result = client.search_issues("project = A")
    assert [i["key"] for i in result.items] == ["A-1", "A-2", "A-3"] and result.pages == 2
    assert all(r.path == "/rest/api/3/search/jql" for r in srv.requests)
    assert "nextPageToken" not in srv.requests[0].query and srv.requests[1].query["nextPageToken"] == [
        "tok-2"
    ]


def test_bitbucket_dc_pagination_is_last_page(tmp_path: Path, srv: MockServer) -> None:
    def responder(req: Recorded) -> Scripted:
        start = int(req.query.get("start", ["0"])[0])
        if start == 0:
            return Scripted(
                200,
                {
                    "values": [{"id": 1}, {"id": 2}],
                    "size": 2,
                    "limit": 2,
                    "isLastPage": False,
                    "nextPageStart": 2,
                    "start": 0,
                },
            )
        return Scripted(200, {"values": [{"id": 3}], "size": 1, "limit": 2, "isLastPage": True, "start": 2})

    srv.set_responder(responder)
    cfg = make_cfg(
        tmp_path,
        srv.port,
        "bitbucket",
        **{"integrations.bitbucket.project_key": "SYN", "integrations.bitbucket.repo_slug": "core"},
    )
    client = BitbucketClient.from_app_config(cfg, sleep=no_sleep)
    result = client.list_pull_requests()
    assert [v["id"] for v in result.items] == [1, 2, 3] and result.pages == 2
    assert srv.requests[0].path == "/rest/api/1.0/projects/SYN/repos/core/pull-requests"
    assert srv.requests[1].query["start"] == ["2"] and srv.requests[0].query["state"] == ["OPEN"]


def test_bitbucket_cloud_pagination_next_url(
    tmp_path: Path, srv: MockServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, BASIC)

    def responder(req: Recorded) -> Scripted:
        page = req.query.get("page", ["1"])[0]
        if page == "1":
            return Scripted(
                200,
                {
                    "values": [{"id": 10}],
                    "next": f"{srv.base_url}/2.0/repositories/ws/core/pullrequests?page=2&state=OPEN",
                },
            )
        return Scripted(200, {"values": [{"id": 11}]})

    srv.set_responder(responder)
    cfg = make_cfg(tmp_path, srv.port, "bitbucket", deployment="cloud")
    client = BitbucketClient.from_app_config(cfg, sleep=no_sleep)
    result = client.list_pull_requests("ws", "core")
    assert [v["id"] for v in result.items] == [10, 11] and result.pages == 2
    assert srv.requests[0].path == "/2.0/repositories/ws/core/pullrequests" and srv.requests[1].query[
        "page"
    ] == ["2"]


def test_pagination_next_url_to_other_origin_is_rejected(
    tmp_path: Path, srv: MockServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, BASIC)
    srv.enqueue(
        200,
        {
            "values": [{"id": 1}],
            "next": "https://api.bitbucket.org/2.0/repositories/ws/core/pullrequests?page=2",
        },
    )
    client = BitbucketClient.from_app_config(
        make_cfg(tmp_path, srv.port, "bitbucket", deployment="cloud"), sleep=no_sleep
    )
    with pytest.raises(AgentError) as ei:
        client.list_pull_requests("ws", "core")
    assert ei.value.code == "E_ORIGIN_NOT_ALLOWED" and len(srv.requests) == 1


def test_confluence_dc_pagination_links_next_relative(tmp_path: Path, srv: MockServer) -> None:
    def responder(req: Recorded) -> Scripted:
        start = int(req.query.get("start", ["0"])[0])
        if start == 0:
            return Scripted(
                200,
                {
                    "results": [{"id": "1"}, {"id": "2"}],
                    "start": 0,
                    "limit": 2,
                    "size": 2,
                    "_links": {"next": "/rest/api/content?spaceKey=SYN&type=page&start=2&limit=2"},
                },
            )
        return Scripted(200, {"results": [{"id": "3"}], "start": 2, "limit": 2, "size": 1, "_links": {}})

    srv.set_responder(responder)
    cfg = make_cfg(tmp_path, srv.port, "confluence", **{"integrations.confluence.space_key": "SYN"})
    client = ConfluenceClient.from_app_config(cfg, sleep=no_sleep)
    result = client.list_pages()
    assert [p["id"] for p in result.items] == ["1", "2", "3"] and result.pages == 2
    assert srv.requests[0].path == "/rest/api/content" and srv.requests[1].query["start"] == ["2"]


def test_bamboo_nested_plans_pagination(tmp_path: Path, srv: MockServer) -> None:
    def responder(req: Recorded) -> Scripted:
        start = int(req.query.get("start-index", ["0"])[0])
        if start == 0:
            return Scripted(
                200,
                {
                    "plans": {
                        "size": 3,
                        "start-index": 0,
                        "max-result": 2,
                        "plan": [{"key": "P-A"}, {"key": "P-B"}],
                    }
                },
            )
        return Scripted(
            200, {"plans": {"size": 3, "start-index": 2, "max-result": 2, "plan": [{"key": "P-C"}]}}
        )

    srv.set_responder(responder)
    client = BambooClient.from_app_config(make_cfg(tmp_path, srv.port, "bamboo"), sleep=no_sleep)
    result = client.list_plans()
    assert [p["key"] for p in result.items] == ["P-A", "P-B", "P-C"] and result.pages == 2
    assert srv.requests[0].path == "/rest/api/latest/plan" and srv.requests[1].query["start-index"] == ["2"]


def test_collect_truncates_at_max_items_and_max_pages(tmp_path: Path, srv: MockServer) -> None:
    srv.set_responder(
        lambda req: Scripted(200, {"values": [{"id": 1}, {"id": 2}], "isLastPage": False, "nextPageStart": 2})
    )
    cfg = make_cfg(
        tmp_path,
        srv.port,
        "bitbucket",
        **{"integrations.bitbucket.project_key": "SYN", "integrations.bitbucket.repo_slug": "core"},
    )
    client = BitbucketClient.from_app_config(cfg, sleep=no_sleep)
    result = client.collect("/projects/SYN/repos/core/pull-requests", BitbucketClient.PR_DC, max_items=3)
    assert len(result.items) == 3 and result.truncated is True
    result2 = client.collect(
        "/projects/SYN/repos/core/pull-requests", BitbucketClient.PR_DC, max_items=1000, max_pages=4
    )
    assert result2.pages == 4 and result2.truncated is True and len(result2.items) == 8


# --------------------------------------------------------------------------- rate limit / conflict / auth / redirect


def test_rate_limit_retry_after_twice_then_success(tmp_path: Path, srv: MockServer) -> None:
    srv.enqueue(429, {"message": "rate"}, {"Retry-After": "3"})
    srv.enqueue(429, {"message": "rate"}, {"Retry-After": "0"})
    srv.enqueue(200, {"key": "SYN-9"})
    sleeps: list[float] = []
    client = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=sleeps.append)
    assert client.get_issue("SYN-9")["key"] == "SYN-9"
    assert len(srv.requests) == 3 and sleeps == [3.0, 0.0]
    assert [o["status"] for o in client.outcomes] == [429, 429, 200]


def test_rate_limit_exhausted_after_two_retries(tmp_path: Path, srv: MockServer) -> None:
    for _ in range(3):
        srv.enqueue(429, {"message": "rate"}, {"Retry-After": "1"})
    srv.enqueue(200, {"key": "never"})
    client = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=no_sleep)
    with pytest.raises(AgentError) as ei:
        client.get_issue("SYN-9")
    assert (
        ei.value.code == "E_GATEWAY_RESPONSE"
        and "429" in ei.value.message
        and ei.value.details["attempts"] == 3
    )
    assert len(srv.requests) == 3 and len(srv.queue) == 1


def test_conflict_409_reported_not_retried(tmp_path: Path, srv: MockServer) -> None:
    srv.enqueue(409, {"errorMessages": ["version conflict"], "echo": f"Bearer {PAT}"})
    srv.enqueue(200, {})
    cfg = make_cfg(
        tmp_path, srv.port, "confluence", write=True, **{"integrations.confluence.space_key": "SYN"}
    )
    client = ConfluenceClient.from_app_config(cfg, sleep=no_sleep)
    with pytest.raises(AgentError) as ei:
        client.create_page({"title": "합성 페이지", "body_storage": "<p>x</p>"}, confirm=True)
    err = ei.value
    assert (
        err.code == "E_INTEGRATION_CONFLICT"
        and err.details["status"] == 409
        and err.details["method"] == "POST"
    )
    assert "version conflict" in err.details["body_preview"] and PAT not in json.dumps(err.to_dict())
    assert len(srv.requests) == 1 and len(srv.queue) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_not_retried(tmp_path: Path, srv: MockServer, status: int) -> None:
    srv.enqueue(status, {"message": "denied", "echo": PAT})
    srv.enqueue(200, {})
    client = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=no_sleep)
    with pytest.raises(AgentError) as ei:
        client.get_issue("SYN-1")
    assert ei.value.code == "E_GATEWAY_AUTH" and ei.value.details["status"] == status
    assert len(srv.requests) == 1 and len(srv.queue) == 1
    assert PAT not in json.dumps(ei.value.to_dict()) + json.dumps(client.outcomes)


def test_redirect_rejected(tmp_path: Path, srv: MockServer) -> None:
    srv.enqueue(301, None, {"Location": "https://jira.example.com/rest/api/2/issue/SYN-1"})
    client = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=no_sleep)
    with pytest.raises(AgentError) as ei:
        client.get_issue("SYN-1")
    assert ei.value.code == "E_ORIGIN_NOT_ALLOWED" and len(srv.requests) == 1


def test_transient_5xx_then_success(tmp_path: Path, srv: MockServer) -> None:
    srv.enqueue(503, {"message": "maintenance"})
    srv.enqueue(200, {"key": "SYN-1"})
    client = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=no_sleep)
    assert client.get_issue("SYN-1")["key"] == "SYN-1" and len(srv.requests) == 2


def test_non_json_body_and_unexpected_status(tmp_path: Path, srv: MockServer) -> None:
    srv.enqueue(200, b"<html>login page</html>")
    client = JiraClient.from_app_config(make_cfg(tmp_path, srv.port, "jira"), sleep=no_sleep)
    with pytest.raises(AgentError) as ei:
        client.get_issue("SYN-1")
    assert ei.value.code == "E_GATEWAY_RESPONSE" and "login page" in ei.value.details["body_preview"]
    srv.enqueue(204, None)
    with pytest.raises(AgentError) as ei2:
        client.get_issue("SYN-1")
    assert ei2.value.code == "E_GATEWAY_RESPONSE" and ei2.value.details["status"] == 204


# --------------------------------------------------------------------------- write 정책 (기본 off)


def test_write_disabled_returns_preview_only(tmp_path: Path, srv: MockServer) -> None:
    cfg = make_cfg(tmp_path, srv.port, "jira", **{"integrations.jira.project_key": "SYN"})
    client = JiraClient.from_app_config(cfg)
    preview = client.preview_issue({"summary": "합성 이슈", "description": "d", "labels": ["synthetic"]})
    assert (
        preview["write_enabled"] is False and preview["would_write"] is True and preview["performed"] is False
    )
    assert preview["method"] == "POST" and preview["url"].endswith("/rest/api/2/issue")
    assert preview["body"]["fields"]["project"] == {"key": "SYN"} and preview["body"]["fields"]["labels"] == [
        "synthetic"
    ]
    with pytest.raises(AgentError) as ei:
        client.create_issue({"summary": "합성 이슈"}, confirm=True)
    assert (
        ei.value.code == "E_INTEGRATION_WRITE_DISABLED" and ei.value.details["preview"]["performed"] is False
    )
    assert srv.requests == [] and client.requests_made == 0


def test_write_enabled_requires_explicit_confirm(tmp_path: Path, srv: MockServer) -> None:
    cfg = make_cfg(tmp_path, srv.port, "jira", write=True, **{"integrations.jira.project_key": "SYN"})
    client = JiraClient.from_app_config(cfg, sleep=no_sleep)
    with pytest.raises(AgentError) as ei:
        client.create_issue({"summary": "합성 이슈"})
    assert ei.value.code == "E_INTEGRATION_WRITE_DISABLED" and "confirm" in ei.value.message
    assert srv.requests == []
    srv.enqueue(201, {"id": "10001", "key": "SYN-42"})
    result = client.create_issue({"summary": "합성 이슈"}, confirm=True)
    assert result["performed"] is True and result["status"] == 201 and result["response"]["key"] == "SYN-42"
    assert srv.requests[0].method == "POST" and srv.requests[0].path == "/rest/api/2/issue"
    assert srv.requests[0].body == {
        "fields": {"project": {"key": "SYN"}, "summary": "합성 이슈", "issuetype": {"name": "Task"}}
    }


def test_previews_for_all_products(tmp_path: Path, srv: MockServer) -> None:
    cfg = load_config(
        overrides={"paths.data_root": str(tmp_path)}
    )  # offline, 설정 없음 -> preview 는 그래도 가능
    jira = preview_payload("jira", cfg, {"summary": "s", "project_key": "SYN"})
    assert jira["action"] == "create_issue" and "<base_url 미설정>" in jira["url"]
    conf = preview_payload("confluence", cfg, {"title": "t", "space_key": "SYN", "parent_id": "7"})
    assert conf["body"]["ancestors"] == [{"id": "7"}] and conf["path"] == "/content"
    bb = preview_payload(
        "bitbucket",
        cfg,
        {
            "title": "t",
            "source_branch": "feat",
            "target_branch": "main",
            "project": "SYN",
            "repo_slug": "core",
        },
    )
    assert (
        bb["body"]["fromRef"]["id"] == "refs/heads/feat"
        and bb["path"] == "/projects/SYN/repos/core/pull-requests"
    )
    bam = preview_payload("bamboo", cfg, {"plan_key": "SYN-PLAN", "variables": {"rev": "R1"}})
    assert bam["path"].startswith("/queue/SYN-PLAN?") and "bamboo.variable.rev=R1" in bam["path"]
    for p in (jira, conf, bb, bam):
        assert p["performed"] is False and p["write_enabled"] is False
    with pytest.raises(AgentError) as ei:
        preview_payload("jira", cfg, {"description": "no summary"})
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError) as ei2:
        preview_payload("trello", cfg, {})
    assert ei2.value.code == "E_USAGE"
    assert srv.requests == []


def test_bitbucket_cloud_pr_preview_shape(tmp_path: Path, srv: MockServer) -> None:
    cfg = make_cfg(tmp_path, srv.port, "bitbucket", deployment="cloud")
    bb = BitbucketClient.from_app_config(cfg).preview_pr(
        {"title": "t", "source_branch": "a", "target_branch": "b", "project": "ws", "repo_slug": "r"}
    )
    assert bb["body"]["source"] == {"branch": {"name": "a"}} and bb["url"].endswith(
        "/2.0/repositories/ws/r/pullrequests"
    )


# --------------------------------------------------------------------------- doctor


def test_doctor_not_run_when_unconfigured(tmp_path: Path) -> None:
    cfg = load_config(overrides={"paths.data_root": str(tmp_path)})
    for name in PRODUCTS:
        rec = client_for(name, cfg).doctor()
        assert rec.status == Status.NOT_RUN and "설정 없음" in rec.reason
    report = integrations_doctor(cfg)
    assert report["live_verified"] is False and report["write_enabled"] is False
    assert set(report["products"]) == set(PRODUCTS)


def test_doctor_blocked_reasons(tmp_path: Path, srv: MockServer) -> None:
    missing = JiraClient(
        load_config(
            overrides={"integrations.jira.enabled": "true", "paths.data_root": str(tmp_path)}
        ).integrations.jira
    )
    rec = missing.doctor()
    assert rec.status == Status.BLOCKED and "base_url" in rec.reason
    offline = load_config(
        overrides={
            "paths.data_root": str(tmp_path),
            "integrations.jira.enabled": "true",
            "integrations.jira.base_url": f"http://127.0.0.1:{srv.port}",
            "integrations.jira.secret_ref": f"env:{TOKEN_ENV}",
            "integrations.approved_origins": f"[http://127.0.0.1:{srv.port}]",
        }
    )
    rec2 = JiraClient.from_app_config(offline).doctor()
    assert rec2.status == Status.BLOCKED and "프로파일" in rec2.reason
    not_approved = make_cfg(
        tmp_path, srv.port, "jira", **{"integrations.approved_origins": "[https://other.internal]"}
    )
    rec3 = JiraClient.from_app_config(not_approved).doctor()
    assert rec3.status == Status.BLOCKED and "approved_origins" in rec3.reason
    bamboo_cloud = BambooClient.from_app_config(
        make_cfg(tmp_path, srv.port, "bamboo", deployment="cloud")
    ).doctor()
    assert bamboo_cloud.status == Status.BLOCKED
    assert srv.requests == []


def test_doctor_configured_is_not_run_without_probe_and_mock_tested_with_probe(
    tmp_path: Path, srv: MockServer
) -> None:
    cfg = make_cfg(tmp_path, srv.port, "jira")
    rec = JiraClient.from_app_config(cfg).doctor()
    assert rec.status == Status.NOT_RUN and "live" in rec.reason and "secret_ref=***" in rec.evidence
    assert PAT not in json.dumps(rec.model_dump(mode="json")) and srv.requests == []
    srv.enqueue(200, {"name": "synthetic-user"})
    rec2 = JiraClient.from_app_config(cfg, sleep=no_sleep).doctor(probe=True)
    assert rec2.status == Status.MOCK_TESTED and srv.requests[0].path == "/rest/api/2/myself"
    srv.enqueue(401, {"message": "denied"})
    rec3 = JiraClient.from_app_config(cfg, sleep=no_sleep).doctor(probe=True)
    assert rec3.status == Status.FAIL and "E_GATEWAY_AUTH" in rec3.reason
    report = integrations_doctor(cfg)
    assert (
        report["products"]["jira"]["status"] == "NOT_RUN"
        and report["products"]["bamboo"]["status"] == "NOT_RUN"
    )
    assert report["live_verified"] is False and report["network_allowed"] is True


def test_bitbucket_probe_endpoint_by_deployment(tmp_path: Path, srv: MockServer) -> None:
    assert (
        BitbucketClient.from_app_config(make_cfg(tmp_path, srv.port, "bitbucket")).probe_endpoint()
        == "/application-properties"
    )
    assert (
        BitbucketClient.from_app_config(
            make_cfg(tmp_path, srv.port, "bitbucket", deployment="cloud")
        ).probe_endpoint()
        == "/user"
    )


# --------------------------------------------------------------------------- 중복 event / 기타


def test_event_ledger_detects_duplicates(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "events.json")
    ev = {"id": "evt-1", "type": "jira:issue_updated", "issue": "SYN-1"}
    key = EventLedger.event_key("jira", ev)
    assert ledger.record(key) is True and ledger.record(key) is False and ledger.is_duplicate(key)
    reloaded = EventLedger(tmp_path / "events.json")
    assert reloaded.is_duplicate(key) and len(reloaded) == 1
    anon = {"type": "x", "payload": {"a": 1}}
    k1 = EventLedger.event_key("bitbucket", anon)
    assert k1 == EventLedger.event_key("bitbucket", {"payload": {"a": 1}, "type": "x"}) and k1.startswith(
        "bitbucket:sha256:"
    )


def test_client_close_is_safe_and_generic_client_type(tmp_path: Path, srv: MockServer) -> None:
    client = client_for("jira", make_cfg(tmp_path, srv.port, "jira"))
    assert isinstance(client, AtlassianClient) and isinstance(client, JiraClient)
    with client:
        pass
    client.close()
