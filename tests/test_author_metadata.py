import json
from datetime import datetime

from agents.analyzer_agent import AnalyzerAgent
from agents.base import LLMChatResponse
from models import Paper, PaperAnalysis
from reporter import Reporter


AUTHORS = [
    "Zelin Qiu",
    "Xi Wang",
    "Zhuoyao Xie",
    "Juan Zhou",
    "Yu Wang",
    "Lingjie Yang",
    "Xinrui Jiang",
    "Juyoung Bae",
    "Moo Hyun Son",
    "Qiang Ye",
    "Dexuan Chen",
    "Rui Zhang",
    "Tao Li",
    "Neeraj Ramesh Mahboobani",
    "Varut Vardhanabhuti",
    "Xiaohui Duan",
    "Yinghua Zhao",
    "Hao Chen",
]


class StaticLLM:
    def chat_with_metadata(self, **kwargs):
        return LLMChatResponse(
            content=json.dumps(
                {
                    "title": "Large-scale multi-sequence pretraining",
                    "authors": AUTHORS[:5],
                    "tldr": "MRI foundation model.",
                }
            ),
            finish_reason="stop",
        )


def make_paper() -> Paper:
    now = datetime(2026, 7, 14)
    return Paper(
        arxiv_id="nature_biomedical_engineering:doi:10.1038/s41551-026-01740-5",
        title="Large-scale multi-sequence pretraining",
        summary="A large-scale MRI foundation model.",
        authors=AUTHORS,
        published=now,
        updated=now,
        pdf_url="https://www.nature.com/articles/s41551-026-01740-5.pdf",
        categories=["Nature Biomedical Engineering"],
        primary_category="Nature Biomedical Engineering",
        source="journal",
    )


def test_analyzer_preserves_complete_metadata_authors():
    analysis = AnalyzerAgent(StaticLLM()).analyze_paper(
        make_paper(),
        ["Medical Image Analysis"],
    )

    assert analysis.authors == AUTHORS


def test_analyzer_uses_extracted_authors_when_metadata_is_empty():
    paper = make_paper()
    paper.authors = []

    analysis = AnalyzerAgent(StaticLLM()).analyze_paper(
        paper,
        ["Medical Image Analysis"],
    )

    assert analysis.authors == AUTHORS[:5]


def test_reporter_prefers_complete_metadata_for_legacy_analysis():
    paper = make_paper()
    analysis = PaperAnalysis(
        arxiv_id=paper.arxiv_id,
        pdf_url=paper.pdf_url,
        matched_keywords=["Medical Image Analysis"],
        title=paper.title,
        authors=AUTHORS[:5],
        paper=paper,
    )

    payload = Reporter({})._analysis_to_dict(analysis)

    assert payload["authors"] == AUTHORS
