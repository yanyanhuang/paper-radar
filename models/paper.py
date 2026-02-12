"""Paper data models."""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Paper:
    """Represents an arXiv paper or journal article."""

    arxiv_id: str  # For journals: "journal_key:doi" format
    title: str
    summary: str
    authors: list[str]
    published: datetime
    updated: datetime
    pdf_url: str
    categories: list[str]
    primary_category: str

    # Fields populated during processing
    matched_keywords: list[str] = field(default_factory=list)
    relevance: str = ""
    match_reason: str = ""

    # Source type: "preprint" or "journal"
    source: str = "preprint"

    @property
    def abstract_url(self) -> str:
        """Get the abstract page URL."""
        if self.source == "preprint" and ":" not in self.arxiv_id:
            return f"https://arxiv.org/abs/{self.arxiv_id}"
        else:
            # For non-arXiv sources, pdf_url is usually the article page
            return self.pdf_url

    @property
    def is_journal(self) -> bool:
        """Check if this paper is from a journal source."""
        return self.source == "journal"

    @property
    def is_preprint(self) -> bool:
        """Check if this paper is from a preprint source."""
        return self.source in {"preprint", "arxiv"}

    @property
    def journal_name(self) -> str:
        """Get journal name if from journal source."""
        if self.is_journal and self.categories:
            return self.categories[0]
        return ""

    def __repr__(self) -> str:
        source_tag = f"[{self.source}]" if self.source != "preprint" else ""
        return f"Paper{source_tag}(id={self.arxiv_id}, title={self.title[:50]}...)"


@dataclass
class PaperAnalysis:
    """Represents the deep analysis result of a paper."""

    arxiv_id: str
    pdf_url: str
    matched_keywords: list[str]

    # Basic info extracted from PDF
    title: str = ""
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)

    # Analysis content
    tldr: str = ""
    motivation: str = ""
    background: str = ""
    contributions: list[str] = field(default_factory=list)
    methodology: str = ""
    experiments: str = ""
    innovations: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    # Keyword relevance analysis
    keyword_relevance: dict = field(default_factory=dict)

    # Code and dataset resources
    code_url: str = ""  # GitHub or other code repository URL
    dataset_info: str = ""  # Dataset name and scale description

    # Quality score (1-10)
    quality_score: int = 5  # Default score
    score_reason: str = ""  # One-sentence explanation for the score

    # Paper numbering (assigned during report generation)
    paper_number: int = 0

    # Reference to original paper (for source info)
    paper: Optional["Paper"] = None

    # Processing status
    success: bool = True
    error: str = ""


@dataclass
class FilterResult:
    """Result from the filter agent."""

    paper: Paper
    matched: bool
    matched_keywords: list[str] = field(default_factory=list)
    relevance: str = "low"
    reason: str = ""


@dataclass
class DailyReport:
    """Represents the daily report data."""

    date: str
    total_papers: int
    matched_papers: int
    analyzed_papers: int

    # Summaries grouped by keyword
    summaries: dict[str, str] = field(default_factory=dict)

    # Analyses grouped by keyword
    analyses_by_keyword: dict[str, list[PaperAnalysis]] = field(default_factory=dict)

    # All keywords
    keywords: list[str] = field(default_factory=list)
