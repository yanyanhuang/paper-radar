"""Analyzer Agent for deep paper analysis using parsed Markdown."""

import base64
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Optional
from loguru import logger

from .base import BaseLLMClient
from models import Paper, PaperAnalysis, FilterResult


class AnalyzerAgent:
    """Stage 2: Heavy LLM agent for deep paper analysis."""

    ANALYSIS_PROMPT = """你是一位资深的AI研究员。请仔细阅读这篇学术论文的全文内容，并提供深度分析。

这篇论文被标记为与以下关键词相关: {matched_keywords}

请提供以下分析（使用{language}）：

1. **基本信息提取**
   - 完整标题
   - 作者列表（前5位主要作者）
   - 作者机构/单位

2. **核心内容分析**
   - TLDR（用1-2句简洁的话概括论文的核心问题、方法和贡献，让读者快速了解论文价值）
   - Motivation（研究动机：该问题为什么重要，现有方法有什么痛点或缺口，2-4句）
   - Background（研究背景：任务场景、领域上下文、与本文最相关的前置工作脉络，2-4句）
   - 主要贡献（3-5个要点）
   - 技术方法（简述核心方法论，100字以内）
   - 实验结果（关键数据和结论，100字以内）

3. **创新点与局限**
   - 主要创新点（2-3个）
   - 潜在局限性（1-2个）

4. **与关键词的关联分析**
   - 与匹配关键词的具体关联
   - 对该领域的贡献程度（high/medium/low）

5. **代码与数据集**
   - 如果论文提供了开源代码，提取代码仓库链接（GitHub、GitLab等）
   - 提取论文使用的数据集信息，包括：数据集名称、规模（样本数量、图像数量等）、是否公开

6. **论文质量评分**
   - 综合评分（1-10分），评分标准：
     * 9-10分：顶级工作，重大突破或创新，实验充分，影响力大
     * 7-8分：优秀工作，有明显创新点，实验扎实
     * 5-6分：良好工作，有一定贡献，但创新性或实验有不足
     * 3-4分：一般工作，贡献有限，存在明显问题
     * 1-2分：质量较差，缺乏创新或存在严重问题
   - 评分理由（一句话解释为什么给这个分数）

请严格以 JSON 格式返回，不要包含其他内容：
{{
    "title": "论文完整标题",
    "authors": ["作者1", "作者2", "作者3"],
    "affiliations": ["机构1", "机构2"],
    "tldr": "一句话总结：简洁概括论文解决什么问题、用什么方法、取得什么效果（1-2句话，不超过100字）",
    "motivation": "研究动机总结（2-4句）",
    "background": "研究背景总结（2-4句）",
    "contributions": ["贡献1", "贡献2", "贡献3"],
    "methodology": "技术方法简述",
    "experiments": "实验结果简述",
    "innovations": ["创新点1", "创新点2"],
    "limitations": ["局限1"],
    "keyword_relevance": {{
        "关键词名称": {{
            "relation": "具体关联说明",
            "contribution_level": "high或medium或low"
        }}
    }},
    "code_url": "代码仓库链接，如 https://github.com/xxx/xxx，若无则留空",
    "dataset_info": "数据集信息描述，如：使用ImageNet(1.2M张图像)、MIMIC-CXR(377K张胸部X光)等，包含规模信息；若未明确提及则写'未明确说明'",
    "quality_score": 7,
    "score_reason": "一句话解释评分理由，如：方法新颖但实验数据集较小，泛化性有待验证"
}}"""

    def __init__(
        self,
        llm_client: BaseLLMClient,
        language: str = "Chinese",
        requests_per_minute: int = 0,
        max_markdown_chars: int = 120000,
    ):
        """
        Initialize the analyzer agent.

        Args:
            llm_client: Heavy LLM client
            language: Output language
            requests_per_minute: Rate limit (0 means no limit)
            max_markdown_chars: Maximum parsed Markdown characters sent to LLM
        """
        self.llm = llm_client
        self.language = language
        self.requests_per_minute = requests_per_minute
        self.max_markdown_chars = max_markdown_chars
        self._last_request_time = 0

    def _wait_for_rate_limit(self):
        """Wait if necessary to respect rate limit."""
        if self.requests_per_minute <= 0:
            return

        min_interval = 60.0 / self.requests_per_minute
        elapsed = time.time() - self._last_request_time

        if elapsed < min_interval:
            wait_time = min_interval - elapsed
            logger.info(f"Rate limit: waiting {wait_time:.1f}s before next request...")
            time.sleep(wait_time)

        self._last_request_time = time.time()

    def _parse_response(self, response: str) -> Optional[dict]:
        """Parse LLM response to extract JSON."""
        # Try direct JSON parsing
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code block
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object (handling nested objects)
        # Find the outermost { }
        start = response.find("{")
        if start != -1:
            depth = 0
            end = start
            for i, char in enumerate(response[start:], start):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break

            if end > start:
                try:
                    return json.loads(response[start:end])
                except json.JSONDecodeError:
                    pass

        return None

    @staticmethod
    def _is_request_too_large_error(error: Optional[str]) -> bool:
        """Check whether an error message indicates request payload too large."""
        if not error:
            return False

        lowered = error.lower()
        return (
            "413" in lowered
            or "request entity too large" in lowered
            or "payload too large" in lowered
        )

    def analyze_paper(
        self,
        paper: Paper,
        matched_keywords: list[str],
        paper_markdown: Optional[str] = None,
    ) -> PaperAnalysis:
        """
        Analyze a single paper using MinerU Markdown, or abstract as fallback.

        Args:
            paper: Paper metadata
            matched_keywords: Keywords the paper matched
            paper_markdown: Parsed paper Markdown (None to use abstract)

        Returns:
            PaperAnalysis with analysis results
        """
        prompt = self.ANALYSIS_PROMPT.format(
            matched_keywords=", ".join(matched_keywords),
            language=self.language,
        )

        try:
            self._wait_for_rate_limit()

            if paper_markdown:
                context = self._build_markdown_context(paper, paper_markdown)
            else:
                logger.info("  ↓ MinerU Markdown unavailable, analyzing from abstract")
                context = self._build_abstract_context(paper)

            response = self.llm.chat(
                messages=[{"role": "user", "content": f"{prompt}\n\n{context}"}],
                max_tokens=4000,
            )

            result = self._parse_response(response)

            if result is None:
                logger.warning(f"Failed to parse analysis for {paper.arxiv_id}")
                logger.debug(f"Response: {response[:500]}")
                return PaperAnalysis(
                    arxiv_id=paper.arxiv_id,
                    pdf_url=paper.pdf_url,
                    matched_keywords=matched_keywords,
                    success=False,
                    error="Failed to parse LLM response",
                )

            return PaperAnalysis(
                arxiv_id=paper.arxiv_id,
                pdf_url=paper.pdf_url,
                matched_keywords=matched_keywords,
                title=result.get("title", paper.title),
                authors=result.get("authors", paper.authors[:5]),
                affiliations=result.get("affiliations", []),
                tldr=result.get("tldr", ""),
                motivation=result.get("motivation", ""),
                background=result.get("background", ""),
                contributions=result.get("contributions", []),
                methodology=result.get("methodology", ""),
                experiments=result.get("experiments", ""),
                innovations=result.get("innovations", []),
                limitations=result.get("limitations", []),
                keyword_relevance=result.get("keyword_relevance", {}),
                code_url=result.get("code_url", ""),
                dataset_info=result.get("dataset_info", ""),
                quality_score=result.get("quality_score", 5),
                score_reason=result.get("score_reason", ""),
                success=True,
            )

        except Exception as e:
            logger.error(f"Error analyzing paper {paper.arxiv_id}: {e}")
            return PaperAnalysis(
                arxiv_id=paper.arxiv_id,
                pdf_url=paper.pdf_url,
                matched_keywords=matched_keywords,
                success=False,
                error=str(e),
            )

    def analyze_papers(
        self,
        filter_results: list[FilterResult],
        pdf_handler,
        ezproxy_handler=None,
        today_date: str = None,
        mineru_client=None,
    ) -> list[PaperAnalysis]:
        """
        Analyze multiple papers.

        Args:
            filter_results: List of FilterResult from filter agent
            pdf_handler: PDFHandler instance for downloading arXiv PDFs
            ezproxy_handler: Optional EZproxyPDFHandler for paywalled journal PDFs
            today_date: Optional date string (YYYY-MM-DD) for organized PDF storage
            mineru_client: Optional MinerUClient for converting PDFs to Markdown

        Returns:
            List of PaperAnalysis results
        """
        analyses = []
        total = len(filter_results)

        for i, fr in enumerate(filter_results, 1):
            paper = fr.paper
            logger.info(f"[{i}/{total}] Analyzing: {paper.title[:60]}...")

            # Choose appropriate PDF handler based on paper source
            pdf_base64 = None
            selected_pdf_handler = pdf_handler
            storage_source = paper.primary_category
            if paper.is_journal:
                # Try direct download first (works for Nature and many OA journals)
                logger.debug(f"  Trying direct download for journal paper")
                storage_source = paper.primary_category
                pdf_base64 = pdf_handler.download_as_base64(
                    paper.pdf_url,
                    arxiv_id=paper.arxiv_id,
                    source=storage_source,
                    date=today_date,
                )
                # Fall back to EZproxy for paywalled journals (Science, etc.)
                if not pdf_base64 and ezproxy_handler:
                    logger.debug(f"  Direct failed, trying EZproxy")
                    selected_pdf_handler = ezproxy_handler
                    pdf_base64 = ezproxy_handler.download_as_base64(
                        paper.pdf_url,
                        paper_id=paper.arxiv_id,
                        require_auth=True,
                        source=storage_source,
                        date=today_date,
                    )
            else:
                # Use standard handler for arXiv and preprint papers
                is_arxiv_preprint = (
                    paper.source == "preprint" and ":" not in paper.arxiv_id
                )
                storage_source = "arxiv" if is_arxiv_preprint else paper.primary_category
                pdf_base64 = pdf_handler.download_as_base64(
                    paper.pdf_url,
                    arxiv_id=paper.arxiv_id,
                    source=storage_source,
                    date=today_date,
                )

            if not pdf_base64:
                if paper.summary:
                    logger.info(f"  ↓ PDF unavailable, analyzing from abstract")
                else:
                    logger.warning(f"  ✗ No PDF and no abstract available")
                    analyses.append(
                        PaperAnalysis(
                            arxiv_id=paper.arxiv_id,
                            pdf_url=paper.pdf_url,
                            matched_keywords=fr.matched_keywords,
                            paper=paper,
                            success=False,
                            error="Failed to download PDF and no abstract",
                        )
                    )
                    continue

            paper_markdown = None
            temp_pdf_path = None
            if pdf_base64 and mineru_client and getattr(mineru_client, "enabled", False):
                try:
                    pdf_path = self._get_downloaded_pdf_path(
                        selected_pdf_handler,
                        paper.arxiv_id,
                        storage_source,
                        today_date,
                    )
                    if not pdf_path:
                        temp_pdf_path = self._write_temp_pdf(pdf_base64, paper.arxiv_id)
                        pdf_path = temp_pdf_path

                    paper_markdown = mineru_client.parse_pdf_file(
                        pdf_path,
                        paper.arxiv_id,
                        source=storage_source,
                        date=today_date,
                    )
                except Exception as e:
                    logger.warning(f"  ! MinerU parsing failed for {paper.arxiv_id}: {e}")
                finally:
                    if temp_pdf_path and temp_pdf_path.exists():
                        temp_pdf_path.unlink(missing_ok=True)

            if not paper_markdown and not paper.summary:
                logger.warning(f"  ✗ MinerU Markdown unavailable and no abstract available")
                analyses.append(
                    PaperAnalysis(
                        arxiv_id=paper.arxiv_id,
                        pdf_url=paper.pdf_url,
                        matched_keywords=fr.matched_keywords,
                        paper=paper,
                        success=False,
                        error="MinerU Markdown unavailable and no abstract",
                    )
                )
                continue

            # Analyze with MinerU Markdown when available, otherwise abstract fallback.
            analysis = self.analyze_paper(
                paper,
                fr.matched_keywords,
                paper_markdown=paper_markdown,
            )

            # Store reference to original paper
            analysis.paper = paper

            if analysis.success:
                logger.info(f"  ✓ Analysis complete: {analysis.tldr[:50]}...")
            else:
                logger.warning(f"  ✗ Analysis failed: {analysis.error}")

            analyses.append(analysis)

        successful = sum(1 for a in analyses if a.success)
        logger.info(f"Analysis complete: {successful}/{total} papers analyzed successfully")

        return analyses

    def _build_markdown_context(self, paper: Paper, paper_markdown: str) -> str:
        """Build a text-only prompt context from parsed Markdown."""
        markdown = paper_markdown.strip()
        if len(markdown) > self.max_markdown_chars:
            logger.warning(
                f"  ! MinerU Markdown too long ({len(markdown)} chars), "
                f"truncating to {self.max_markdown_chars}"
            )
            markdown = (
                markdown[: self.max_markdown_chars]
                + "\n\n[Markdown truncated because it exceeded the configured limit.]"
            )

        return (
            "论文元数据:\n"
            f"Title: {paper.title}\n"
            f"Authors: {', '.join(paper.authors[:10])}\n"
            f"Abstract: {paper.summary}\n\n"
            "以下是由 MinerU 从论文 PDF 解析得到的 Markdown 全文。请优先依据全文内容分析；"
            "若 Markdown 中有解析噪声，请结合标题和摘要判断。\n\n"
            "<paper_markdown>\n"
            f"{markdown}\n"
            "</paper_markdown>"
        )

    @staticmethod
    def _build_abstract_context(paper: Paper) -> str:
        """Build a fallback prompt context from metadata and abstract."""
        return (
            "论文元数据:\n"
            f"Title: {paper.title}\n"
            f"Authors: {', '.join(paper.authors[:10])}\n"
            f"Abstract: {paper.summary}\n"
        )

    @staticmethod
    def _get_downloaded_pdf_path(pdf_handler, paper_id: str, source: str, date: Optional[str]) -> Optional[Path]:
        if not pdf_handler:
            return None
        pdf_path = pdf_handler.get_saved_pdf_path(paper_id, source, date)
        if pdf_path and Path(pdf_path).exists():
            return Path(pdf_path)
        return None

    @staticmethod
    def _write_temp_pdf(pdf_base64: str, hint: str) -> Path:
        safe_hint = re.sub(r"[^A-Za-z0-9_.-]+", "_", hint)[:64] or "paper"
        data = base64.standard_b64decode(pdf_base64)
        handle = tempfile.NamedTemporaryFile(
            prefix=f"paper-radar-{safe_hint}-",
            suffix=".pdf",
            delete=False,
        )
        try:
            handle.write(data)
            return Path(handle.name)
        finally:
            handle.close()
