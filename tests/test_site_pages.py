"""The pages added for the announcement: evidence, the agent install guide, llms.txt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import main

STATIC = Path(main.__file__).resolve().parent.parent / "static"
REPO = "https://github.com/fstandhartinger/auto-model-router"


@pytest.mark.parametrize("path", ["/evidence", "/claims"])
def test_evidence_pages_serve_the_app_shell_with_their_own_head(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert 'id="page-evidence"' in resp.text
    assert f"/{path.strip('/')}\"" in resp.text  # canonical URL is this page's


def test_evidence_is_in_the_nav_and_the_repo_link_is_visible(client):
    page = client.get("/").text
    assert 'href="/evidence" data-link data-nav="evidence"' in page
    # Top bar and hero both carry the GitHub link with its label.
    assert page.count(f'href="{REPO}" rel="noopener"') >= 3  # top bar, hero, footer
    assert "Open source · GitHub" in page
    assert 'class="gh-mark"' in page


def test_home_tells_agents_how_to_install(client):
    page = client.get("/").text
    assert "For AI agents: how to install this router" in page
    assert "Read https://whichmodel.app.mintapis.com/agents.md and install the auto router for me." in page
    assert 'href="/agents.md"' in page and 'href="/install.md"' in page


@pytest.mark.parametrize("path", ["/agents.md", "/install.md"])
def test_agent_guide_is_markdown(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.text == (STATIC / "agents.md").read_text(encoding="utf-8")
    assert "auto-model-router" in resp.text


def test_agent_guide_covers_supported_harnesses_and_key_names(client):
    guide = client.get("/agents.md").text
    for harness in ("Claude Code", "Codex", "OpenCode", "OpenClaw", "Hermes Agent",
                    "GitHub Copilot", "Cursor"):
        assert harness in guide
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "TENSORX_API_KEY", "TYPESAFE_API_KEY"):
        assert key in guide
    assert "Do not run `env`, `set`" in guide
    assert "`printenv NAME`" in guide
    assert "Windows 11 PowerShell" in guide


def test_llms_txt_links_everything_an_agent_needs(client):
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    body = resp.text
    for needle in ("/agents.md", "/install.md", "/install.sh", "/evidence", REPO):
        assert needle in body, needle
    assert ("/install.ps1" in body) == (STATIC / "install.ps1").is_file()
    assert "A/B study has not run" in body


def test_install_ps1_is_served_when_present_and_404_otherwise(client):
    resp = client.get("/install.ps1")
    if (STATIC / "install.ps1").is_file():
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
    else:
        assert resp.status_code == 404


def test_install_ps1_route_serves_the_file(client, tmp_path, monkeypatch):
    fake = tmp_path / "static"
    fake.mkdir()
    (fake / "install.ps1").write_text("Write-Host 'hi'\n")
    monkeypatch.setattr(main, "STATIC", fake)
    resp = client.get("/install.ps1")
    assert resp.status_code == 200 and "Write-Host" in resp.text
    assert "/install.ps1" in client.get("/llms.txt").text


def test_missing_study_file_is_a_real_404_not_the_app_shell(client):
    if (STATIC / "data" / "ab-study.json").is_file():
        pytest.skip("the real study file is present")
    resp = client.get("/data/ab-study.json")
    assert resp.status_code == 404


def test_sample_study_matches_the_schema_and_says_it_is_a_sample(client):
    resp = client.get("/data/ab-study.sample.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample"] is True
    assert "SAMPLE" in data["method"]["summary"]
    for key in ("generated_at", "method", "tasks", "summary", "what_if"):
        assert key in data
    assert data["method"]["limitations"]
    for task in data["tasks"]:
        for arm in ("A", "B"):
            run = task[arm]
            for key in ("model", "score", "cost_list_usd", "tokens", "screenshot"):
                assert key in run, (task["id"], arm, key)
        assert isinstance(task["B"]["routes"], list)
    total_a = sum(t["A"]["cost_list_usd"] for t in data["tasks"])
    assert abs(total_a - data["summary"]["A"]["total_cost_list_usd"]) < 0.01


def test_public_evidence_does_not_fall_back_to_invented_sample_data(client):
    source = (STATIC / "assets" / "app.js").read_text(encoding="utf-8")
    page = client.get("/evidence").text
    assert 'loadJson("/data/ab-study.sample.json")' not in source
    assert "analysis of real coding-agent traffic" in page
    evidence = (STATIC / "assets" / "evidence.js").read_text(encoding="utf-8")
    assert "Matched A/B study: not run" not in evidence
    assert "Analyzed on real traffic" in evidence
    # The measured study replaced the "not run" wording; the claim now states the measured ratio.
    assert "Not what the measurement shows" in page
    assert "replay simulation" in page


def test_real_study_file_if_present_is_valid_json_with_tasks():
    real = STATIC / "data" / "ab-study.json"
    if not real.is_file():
        pytest.skip("the real study file has not been dropped in yet")
    data = json.loads(real.read_text(encoding="utf-8"))
    assert data["tasks"] and data["summary"]
    assert data["sample"] is False and data["status"] == "measured"
    total_a = sum(t["A"]["cost_list_usd"] for t in data["tasks"])
    total_b = sum(t["B"]["cost_list_usd"] for t in data["tasks"])
    assert abs(total_a - data["summary"]["A"]["total_cost_list_usd"]) < 0.01
    assert abs(total_b - data["summary"]["B"]["total_cost_list_usd"]) < 0.01
    for task in data["tasks"]:
        shot = task["A"].get("screenshot")
        if shot:
            assert (STATIC / shot.lstrip("/")).is_file(), shot


def test_claims_link_to_files_and_the_anthropic_pages(client):
    page = client.get("/evidence").text
    for path in ("auto_router/plan_auth.py", "auto_router/switch.py", "auto_router/delegate.py",
                 "auto_router/jev.py", "auto_router/cache_index.py", "auto_router/economics.py",
                 "auto_router/pricing.py", "auto_router/bench.py", "auto_router/verify.py",
                 "scripts/route-run"):
        assert f"{REPO}/blob/main/{path}" in page, path
    assert "https://code.claude.com/docs/en/llm-gateway" in page
    assert "https://code.claude.com/docs/en/legal-and-compliance" in page
    assert "your own login on your own machine" in page


def test_model_list_labels_the_bonsai_assumption(client):
    page = client.get("/how").text
    for name in ("Claude Opus 5.5", "Claude Sonnet 5", "GPT-6 Luna", "GPT-6 Astra",
                 "GLM-5.3 Flash", "Bonsai 2 (local)", "Local Jev-class model"):
        assert name in page, name
    assert "<em>assumed</em> ≈ Qwen3.8-27B" in page
