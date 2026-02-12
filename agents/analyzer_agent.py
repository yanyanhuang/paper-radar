"""Analyzer Agent for deep paper analysis using multimodal LLM."""

import json
import re
import time
from typing import Optional
from loguru import logger

from .base import BaseLLMClient
from models import Paper, PaperAnalysis, FilterResult


class AnalyzerAgent:
    """Stage 2: Heavy multimodal LLM agent for deep PDF analysis."""

    ANALYSIS_PROMPT = """你是一位资深的AI研究员。请仔细阅读这篇学术论文的完整PDF，并提供深度分析。

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

    def __init__(self, llm_client: BaseLLMClient, language: str = "Chinese", requests_per_minute: int = 0):
        """
        Initialize the analyzer agent.

        Args:
            llm_client: Heavy multimodal LLM client (e.g., Gemini)
            language: Output language
            requests_per_minute: Rate limit (0 means no limit)
        """
        self.llm = llm_client
        self.language = language
        self.requests_per_minute = requests_per_minute
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
        pdf_base64: str,
    ) -> PaperAnalysis:
        """
        Analyze a single paper using its PDF.

        Args:
            paper: Paper metadata
            matched_keywords: Keywords the paper matched
            pdf_base64: Base64 encoded PDF content

        Returns:
            PaperAnalysis with analysis results
        """
        prompt = self.ANALYSIS_PROMPT.format(
            matched_keywords=", ".join(matched_keywords),
            language=self.language,
        )

        try:
            # Apply rate limiting before making request
            self._wait_for_rate_limit()

            response = self.llm.chat_with_pdf(
                prompt=prompt,
                pdf_base64=pdf_base64,
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
    ) -> list[PaperAnalysis]:
        """
        Analyze multiple papers.

        Args:
            filter_results: List of FilterResult from filter agent
            pdf_handler: PDFHandler instance for downloading arXiv PDFs
            ezproxy_handler: Optional EZproxyPDFHandler for paywalled journal PDFs
            today_date: Optional date string (YYYY-MM-DD) for organized PDF storage

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
            if paper.is_journal and ezproxy_handler:
                # Use EZproxy for journal papers (Nature, etc.)
                logger.debug(f"  Using EZproxy handler for journal paper")
                selected_pdf_handler = ezproxy_handler
                pdf_base64 = ezproxy_handler.download_as_base64(
                    paper.pdf_url,
                    paper_id=paper.arxiv_id,
                    require_auth=True,
                    source=paper.primary_category,
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
                logger.warning(f"  ✗ Failed to download PDF")
                analyses.append(
                    PaperAnalysis(
                        arxiv_id=paper.arxiv_id,
                        pdf_url=paper.pdf_url,
                        matched_keywords=fr.matched_keywords,
                        paper=paper,
                        success=False,
                        error="Failed to download PDF",
                    )
                )
                continue

            # Analyze
            analysis = self.analyze_paper(
                paper,
                fr.matched_keywords,
                pdf_base64,
            )

            # Retry once with compressed PDF only when payload is too large (413)
            if (
                not analysis.success
                and self._is_request_too_large_error(analysis.error)
                and selected_pdf_handler is not None
            ):
                logger.warning(
                    f"  413 detected for {paper.arxiv_id}, compressing PDF and retrying once..."
                )
                compressed_pdf = selected_pdf_handler.compress_base64_for_retry(
                    pdf_base64,
                    hint=paper.arxiv_id,
                )
                if compressed_pdf:
                    analysis = self.analyze_paper(
                        paper,
                        fr.matched_keywords,
                        compressed_pdf,
                    )
                else:
                    logger.warning(
                        f"  Compression unavailable/failed for {paper.arxiv_id}, skip retry"
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
