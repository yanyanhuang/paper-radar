"""Summary Agent for generating field progress summaries."""

from loguru import logger

from .base import BaseLLMClient
from models import PaperAnalysis


class SummaryAgent:
    """Stage 3: Agent for generating field progress summaries."""

    SUMMARY_PROMPT = """你是一位AI领域的资深研究顾问。请基于以下今日arXiv新论文的分析结果，撰写「{keyword}」领域的每日研究进展总结。

## 今日该领域相关论文分析:

{papers_analysis}

---

请撰写一份简洁有力的领域进展总结（使用{language}，300-500字）：

1. **今日概览**: 简述今日该领域发表的论文数量和整体趋势

2. **重点突破**: 最值得关注的1-2项研究及其意义（请使用论文编号引用，如"论文1"、"论文3"）

3. **技术趋势**: 观察到的技术方向或方法论趋势

4. **值得跟进**: 建议深入阅读的论文及原因（请使用论文编号引用）

请直接输出总结内容，使用 Markdown 格式，不需要 JSON，也不要把整段总结包裹在 Markdown 代码围栏中。在引用论文时，请使用论文编号（如"论文1"、"论文2"），方便读者与下方论文列表对照。"""

    def __init__(
        self,
        llm_client: BaseLLMClient,
        language: str = "Chinese",
        max_attempts: int = 3,
    ):
        """
        Initialize the summary agent.

        Args:
            llm_client: LLM client (can reuse light or heavy)
            language: Output language
            max_attempts: Maximum attempts for empty or truncated responses
        """
        self.llm = llm_client
        self.language = language
        try:
            self.max_attempts = max(1, int(max_attempts))
        except (TypeError, ValueError):
            self.max_attempts = 3

    def _format_paper_analysis(self, analysis: PaperAnalysis) -> str:
        """Format a single paper analysis for the prompt."""
        authors_str = ", ".join(analysis.authors[:3])
        if len(analysis.authors) > 3:
            authors_str += " et al."

        affiliations_str = ", ".join(analysis.affiliations[:2]) if analysis.affiliations else "未提取"

        contributions_str = "\n".join(f"  - {c}" for c in analysis.contributions[:3])

        innovations_str = "; ".join(analysis.innovations[:2]) if analysis.innovations else "未提取"

        return f"""### {analysis.title}
- **arXiv ID**: {analysis.arxiv_id}
- **作者**: {authors_str}
- **机构**: {affiliations_str}
- **TLDR**: {analysis.tldr}
- **主要贡献**:
{contributions_str}
- **创新点**: {innovations_str}
- **方法**: {analysis.methodology[:200] if analysis.methodology else '未提取'}
"""

    def _format_papers_analysis(self, analyses: list[PaperAnalysis]) -> str:
        """Format multiple paper analyses for the prompt."""
        parts = []
        for i, analysis in enumerate(analyses, 1):
            if analysis.success:
                parts.append(f"## 论文 {i}\n{self._format_paper_analysis(analysis)}")
        return "\n".join(parts)

    def generate_summary(self, keyword: str, analyses: list[PaperAnalysis]) -> str:
        """
        Generate a summary for a specific keyword field.

        Args:
            keyword: The keyword/field name
            analyses: List of paper analyses for this keyword

        Returns:
            Summary text in Markdown format
        """
        # Filter to only successful analyses
        successful_analyses = [a for a in analyses if a.success]

        if not successful_analyses:
            return f"今日「{keyword}」领域暂无相关论文更新。"

        papers_analysis = self._format_papers_analysis(successful_analyses)

        prompt = self.SUMMARY_PROMPT.format(
            keyword=keyword,
            papers_analysis=papers_analysis,
            language=self.language,
        )

        messages = [{"role": "user", "content": prompt}]
        last_error = "Unknown summary generation error"

        for attempt in range(1, self.max_attempts + 1):
            try:
                # Use the Summary LLM's configured token budget. Reasoning models may
                # spend a large part of this budget before emitting visible content.
                llm_response = self.llm.chat_with_metadata(
                    messages=messages,
                    temperature=0.5,
                )
                summary = llm_response.content.strip()
                finish_reason = (llm_response.finish_reason or "").lower()
                response_incomplete = finish_reason in {
                    "length",
                    "max_tokens",
                    "max_output_tokens",
                    "content_filter",
                }

                if summary and not response_incomplete:
                    return summary

                last_error = (
                    "Incomplete LLM response "
                    f"(finish_reason={finish_reason or 'unknown'}, "
                    f"content_chars={len(summary)}, "
                    f"completion_tokens={llm_response.completion_tokens or 'unknown'})"
                )
                logger.warning(
                    f"Summary response invalid for {keyword} "
                    f"(attempt {attempt}/{self.max_attempts}): {last_error}"
                )
            except Exception as e:
                last_error = f"LLM request error: {e}"
                logger.warning(
                    f"Error generating summary for {keyword} "
                    f"(attempt {attempt}/{self.max_attempts}): {e}"
                )

        logger.error(
            f"Summary generation failed for {keyword} after "
            f"{self.max_attempts} attempt(s): {last_error}"
        )
        return f"生成「{keyword}」领域总结失败: {last_error}"

    def generate_all_summaries(
        self,
        analyses_by_keyword: dict[str, list[PaperAnalysis]],
    ) -> dict[str, str]:
        """
        Generate summaries for all keyword fields.

        Args:
            analyses_by_keyword: Dict mapping keyword names to paper analyses

        Returns:
            Dict mapping keyword names to summary texts
        """
        summaries = {}

        for keyword, analyses in analyses_by_keyword.items():
            logger.info(f"Generating summary for: {keyword} ({len(analyses)} papers)")
            summaries[keyword] = self.generate_summary(keyword, analyses)

        return summaries
