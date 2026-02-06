#!/usr/bin/env python3
"""
PaperRadar - Main Entry Point

Automated paper analysis based on keywords using dual LLM architecture.
Supports arXiv and academic journals (Nature, NEJM, etc.)
"""

import sys
import argparse
from datetime import datetime
from pathlib import Path
from loguru import logger

from config_loader import load_config, get_llm_config
from fetcher import ArxivFetcher
from journal_fetcher import JournalFetcher
from paper_history import PaperHistory
from pdf_handler import PDFHandler, EZproxyPDFHandler
from agents import BaseLLMClient, FilterAgent, AnalyzerAgent, SummaryAgent
from reporter import Reporter
from models import DailyReport, PaperAnalysis


def setup_logging(debug: bool = False):
    """Configure logging."""
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    level = "DEBUG" if debug else "INFO"
    logger.add(sys.stderr, format=log_format, level=level, colorize=True)

    # Also log to file
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"paper-radar-{datetime.now().strftime('%Y-%m-%d')}.log"
    logger.add(log_file, format=log_format, level="DEBUG", rotation="1 day")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="arXiv Keyword Daily")
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to config file",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Debug mode (fetch fewer papers, more verbose logging)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run (skip external delivery)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode (fetch top 100 arXiv RSS papers only, full pipeline)",
    )
    args = parser.parse_args()

    setup_logging(args.debug)

    logger.info("=" * 60)
    logger.info(f"arXiv Keyword Daily - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # Load configuration
    try:
        config = load_config(args.config)
        logger.info(f"Loaded config from: {args.config}")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    keywords = config.get("keywords", [])
    keyword_names = [kw["name"] for kw in keywords]
    logger.info(f"Keywords: {keyword_names}")

    # ========== Stage 0: Fetch papers ==========
    logger.info("")
    logger.info("[Stage 0] Fetching papers...")

    papers = []
    today_date = datetime.now().strftime("%Y-%m-%d")

    # Initialize paper history for deduplication
    paper_history = PaperHistory("./cache/paper_history.json")
    history_stats = paper_history.get_stats()
    logger.info(f"Paper history: {history_stats['total_papers']} papers tracked")

    # Fetch from arXiv
    arxiv_config = config.get("arxiv", {})
    if arxiv_config.get("enabled", True):
        logger.info("[Stage 0.1] Fetching from arXiv...")
        if args.test:
            arxiv_config = {**arxiv_config, "max_papers_per_day": 100}
        fetcher = ArxivFetcher(arxiv_config)
        arxiv_papers = fetcher.get_today_papers(debug=args.debug)
        logger.info(f"  arXiv: {len(arxiv_papers)} papers")
        papers.extend(arxiv_papers)

    # Fetch from journals (with deduplication)
    journals_config = config.get("journals", {})
    if journals_config.get("enabled", False) and not args.test:
        logger.info("[Stage 0.2] Fetching from journals...")
        journal_fetcher = JournalFetcher(
            {
                "journals": journals_config.get("sources", []),
                "max_papers_per_journal": journals_config.get("max_papers_per_journal", 30),
            },
            paper_history=paper_history,
        )
        journal_papers = journal_fetcher.get_papers(debug=args.debug)
        logger.info(f"  Journals: {len(journal_papers)} new papers")
        papers.extend(journal_papers)

    logger.info(f"Total papers fetched: {len(papers)}")

    if not papers:
        logger.warning("No new papers found. Exiting.")
        return

    # ========== Stage 1: Filter with Light LLM ==========
    logger.info("")
    logger.info("[Stage 1] Filtering papers with Light LLM...")

    light_llm_config = get_llm_config(config, "light")
    if not light_llm_config.get("api_key"):
        logger.error("Light LLM API key not configured")
        sys.exit(1)

    light_llm = BaseLLMClient(**light_llm_config)
    filter_agent = FilterAgent(light_llm, keywords)

    filter_workers = config.get("runtime", {}).get("concurrent_filtering", 5)
    try:
        filter_workers = int(filter_workers)
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid runtime.concurrent_filtering={filter_workers}, fallback to 5"
        )
        filter_workers = 5
    if filter_workers < 1:
        logger.warning(
            f"runtime.concurrent_filtering={filter_workers} is less than 1, fallback to 1"
        )
        filter_workers = 1

    logger.info(f"Light LLM filtering concurrency: {filter_workers}")
    filter_results = filter_agent.filter_papers(
        papers,
        max_workers=filter_workers,
    )

    logger.info(f"Matched {len(filter_results)} papers")

    if not filter_results:
        logger.warning("No papers matched any keywords. Exiting.")
        # Still generate empty report
        report = DailyReport(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_papers=len(papers),
            matched_papers=0,
            analyzed_papers=0,
            summaries={kw: "今日该领域暂无相关论文更新。" for kw in keyword_names},
            analyses_by_keyword={kw: [] for kw in keyword_names},
            keywords=keyword_names,
        )
        reporter = Reporter(config)
        if args.dry_run:
            reporter.save_markdown(report)
            reporter.save_json(report)
        else:
            results = reporter.generate_and_send(report)
            logger.info(f"Report delivery results: {results}")
        return

    # ========== Stage 2: Analyze with Heavy LLM ==========
    logger.info("")
    logger.info("[Stage 2] Analyzing papers with Heavy Vision LLM...")

    heavy_llm_config = get_llm_config(config, "heavy")
    if not heavy_llm_config.get("api_key"):
        logger.error("Heavy LLM API key not configured")
        sys.exit(1)

    heavy_llm = BaseLLMClient(**heavy_llm_config)

    # Get rate limit from config
    rate_limit = config.get("llm", {}).get("heavy", {}).get("rate_limit", {})
    requests_per_minute = rate_limit.get("requests_per_minute", 0)

    analyzer_agent = AnalyzerAgent(
        heavy_llm,
        config.get("output", {}).get("language", "Chinese"),
        requests_per_minute=requests_per_minute,
    )

    # Create PDF handlers
    # Standard handler for arXiv papers
    pdf_handler = PDFHandler(
        timeout=config.get("runtime", {}).get("pdf_timeout", 120),
        cache_dir="./cache/pdfs",
    )

    # EZproxy handler for paywalled journal papers (Nature, etc.)
    ezproxy_config = config.get("ezproxy", {})
    ezproxy_handler = None
    if ezproxy_config.get("enabled", False):
        logger.info("EZproxy authentication enabled for journal PDFs")
        ezproxy_handler = EZproxyPDFHandler(
            timeout=config.get("runtime", {}).get("pdf_timeout", 120),
            cache_dir="./cache/pdfs",
            headless=ezproxy_config.get("headless", True),
        )

    analyses = analyzer_agent.analyze_papers(
        filter_results,
        pdf_handler,
        ezproxy_handler=ezproxy_handler,
        today_date=today_date,
    )

    successful_analyses = [a for a in analyses if a.success]
    logger.info(f"Successfully analyzed {len(successful_analyses)} papers")

    # Save successfully analyzed non-arXiv external papers to history
    external_papers_saved = 0
    for analysis in successful_analyses:
        paper = analysis.paper
        # External sources use "key:identifier" IDs; native arXiv IDs do not contain ":"
        if paper and ":" in paper.arxiv_id:
            paper_history.add_paper(
                paper_id=paper.arxiv_id,
                title=paper.title,
                source=paper.primary_category,
                keywords=analysis.matched_keywords,
                pdf_path=pdf_handler.get_saved_pdf_path(
                    paper.arxiv_id, paper.primary_category, today_date
                ),
            )
            external_papers_saved += 1

    if external_papers_saved > 0:
        logger.info(f"Saved {external_papers_saved} external-source papers to history")

    # Group analyses by keyword
    analyses_by_keyword: dict[str, list[PaperAnalysis]] = {kw: [] for kw in keyword_names}

    for analysis in analyses:
        for keyword in analysis.matched_keywords:
            if keyword in analyses_by_keyword:
                analyses_by_keyword[keyword].append(analysis)

    # ========== Stage 3: Generate summaries ==========
    logger.info("")
    logger.info("[Stage 3] Generating field summaries...")

    summary_llm_config = get_llm_config(config, "summary")
    summary_llm = BaseLLMClient(**summary_llm_config)
    summary_agent = SummaryAgent(summary_llm, config.get("output", {}).get("language", "Chinese"))

    # Only generate summaries for keywords with papers
    keywords_with_papers = {
        kw: analyses_by_keyword[kw]
        for kw in keyword_names
        if analyses_by_keyword.get(kw)
    }

    summaries = summary_agent.generate_all_summaries(keywords_with_papers)

    # Add empty summaries for keywords without papers
    for kw in keyword_names:
        if kw not in summaries:
            summaries[kw] = "今日该领域暂无相关论文更新。"

    # ========== Stage 4: Generate and send report ==========
    logger.info("")
    logger.info("[Stage 4] Generating and sending report...")

    report = DailyReport(
        date=datetime.now().strftime("%Y-%m-%d"),
        total_papers=len(papers),
        matched_papers=len(filter_results),
        analyzed_papers=len(successful_analyses),
        summaries=summaries,
        analyses_by_keyword=analyses_by_keyword,
        keywords=keyword_names,
    )

    reporter = Reporter(config)

    if args.dry_run:
        logger.info("Dry run mode - saving reports only")
        reporter.save_markdown(report)
        reporter.save_json(report)
    else:
        results = reporter.generate_and_send(report)
        logger.info(f"Report delivery results: {results}")

    logger.info("")
    logger.info("=" * 60)
    logger.info("Done!")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
