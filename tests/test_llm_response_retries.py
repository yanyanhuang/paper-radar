import json
from datetime import datetime
from pathlib import Path

import yaml

from agents.analyzer_agent import AnalyzerAgent
from agents.base import LLMChatResponse
from agents.filter_agent import FilterAgent
from agents.summary_agent import SummaryAgent
from models import Paper, PaperAnalysis


class SequenceLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_with_metadata(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_paper() -> Paper:
    now = datetime(2026, 7, 13)
    return Paper(
        arxiv_id="2607.00001",
        title="A benchmark for autonomous language-model agents",
        summary="We evaluate planning and tool use in LLM agents.",
        authors=["Researcher"],
        published=now,
        updated=now,
        pdf_url="https://arxiv.org/pdf/2607.00001",
        categories=["cs.AI"],
        primary_category="cs.AI",
    )


def test_light_filter_retries_truncated_json_and_succeeds():
    valid = json.dumps(
        {
            "matched": True,
            "matched_keywords": ["LLM Agent"],
            "relevance": "high",
            "reason": "Agent benchmark",
        }
    )
    llm = SequenceLLM(
        [
            LLMChatResponse(content="{", finish_reason="length"),
            LLMChatResponse(content=valid, finish_reason="stop"),
        ]
    )
    agent = FilterAgent(
        llm,
        [{"name": "LLM Agent", "description": "Agent systems"}],
        max_attempts=3,
    )

    result = agent.filter_paper(make_paper())

    assert result.success is True
    assert result.matched is True
    assert result.matched_keywords == ["LLM Agent"]
    assert len(llm.calls) == 2


def test_light_filter_reports_operational_failure_instead_of_non_match():
    llm = SequenceLLM(
        [
            LLMChatResponse(content="", finish_reason="length"),
            LLMChatResponse(content="{", finish_reason="length"),
        ]
    )
    agent = FilterAgent(
        llm,
        [{"name": "LLM Agent", "description": "Agent systems"}],
        max_attempts=2,
    )

    matched = agent.filter_papers([make_paper()], max_workers=1)

    assert matched == []
    assert agent.last_failure_count == 1
    assert len(llm.calls) == 2


def test_heavy_analyzer_retries_invalid_json_and_uses_client_default_budget():
    valid = json.dumps(
        {
            "title": "A benchmark for autonomous language-model agents",
            "tldr": "A benchmark for agent planning and tool use.",
            "quality_score": 7,
        }
    )
    llm = SequenceLLM(
        [
            LLMChatResponse(content="{", finish_reason="length"),
            LLMChatResponse(content=valid, finish_reason="stop"),
        ]
    )
    agent = AnalyzerAgent(llm, max_attempts=3)

    result = agent.analyze_paper(make_paper(), ["LLM Agent"])

    assert result.success is True
    assert result.quality_score == 7
    assert len(llm.calls) == 2
    assert all("max_tokens" not in call for call in llm.calls)


def test_light_and_heavy_use_project_maximum_token_budget():
    config_path = Path(__file__).parents[1] / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["llm"]["light"]["max_tokens"] == 40000
    assert config["llm"]["heavy"]["max_tokens"] == 40000
    assert config["llm"]["summary"]["max_tokens"] == 40000


def test_summary_retries_length_terminated_response_and_uses_configured_budget():
    llm = SequenceLLM(
        [
            LLMChatResponse(
                content="### 今日概览\n这是一段被截断的总结",
                finish_reason="length",
                completion_tokens=2000,
            ),
            LLMChatResponse(
                content="### 今日概览\n完整总结。",
                finish_reason="stop",
                completion_tokens=2400,
            ),
        ]
    )
    analysis = PaperAnalysis(
        arxiv_id="2607.00001",
        pdf_url="https://arxiv.org/pdf/2607.00001",
        matched_keywords=["LLM Agent"],
        title="Agent benchmark",
        authors=["Researcher"],
        tldr="A benchmark for agents.",
    )
    agent = SummaryAgent(llm, max_attempts=3)

    summary = agent.generate_summary("LLM Agent", [analysis])

    assert summary == "### 今日概览\n完整总结。"
    assert len(llm.calls) == 2
    assert all("max_tokens" not in call for call in llm.calls)


def test_summary_does_not_publish_persistently_truncated_content():
    llm = SequenceLLM(
        [
            LLMChatResponse(content="半句", finish_reason="length"),
            LLMChatResponse(content="仍然是半句", finish_reason="length"),
        ]
    )
    analysis = PaperAnalysis(
        arxiv_id="2607.00001",
        pdf_url="https://arxiv.org/pdf/2607.00001",
        matched_keywords=["LLM Agent"],
        title="Agent benchmark",
        authors=["Researcher"],
        tldr="A benchmark for agents.",
    )
    agent = SummaryAgent(llm, max_attempts=2)

    summary = agent.generate_summary("LLM Agent", [analysis])

    assert summary.startswith("生成「LLM Agent」领域总结失败:")
    assert "半句" not in summary
    assert len(llm.calls) == 2
