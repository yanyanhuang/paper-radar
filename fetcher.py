"""arXiv paper fetcher module."""

import time
import arxiv
import feedparser
from datetime import datetime, timedelta
from loguru import logger

from models import Paper


class ArxivFetcher:
    """Fetches papers from arXiv."""

    def __init__(self, config: dict):
        """
        Initialize the fetcher.

        Args:
            config: arXiv configuration dict containing:
                - categories: arXiv categories to query (e.g., "cs.AI+cs.CV")
                - max_papers_per_day: Maximum papers to fetch per day
                - retry_interval_minutes: Wait time between retries (default: 30)
                - max_retry_hours: Maximum total wait time (default: 5)
        """
        self.categories = config.get("categories", "cs.AI+cs.CV+cs.CL+cs.LG")
        self.max_papers = config.get("max_papers_per_day", 200)
        self.retry_interval_minutes = config.get("retry_interval_minutes", 30)
        self.max_retry_hours = config.get("max_retry_hours", 5)
        self.client = arxiv.Client(num_retries=10, delay_seconds=5)

    def get_today_papers(self, debug: bool = False) -> list[Paper]:
        """
        Fetch today's new papers from arXiv with retry logic.

        If no papers are found, retries every 30 minutes for up to 5 hours.
        This handles cases where arXiv RSS hasn't updated yet (e.g., weekends,
        holidays, or timing issues).

        Args:
            debug: If True, fetch recent papers regardless of date (for testing)

        Returns:
            List of Paper objects
        """
        max_retries = (self.max_retry_hours * 60) // self.retry_interval_minutes
        retry_count = 0

        while retry_count <= max_retries:
            papers = self._fetch_from_rss()

            if papers:
                return papers if not debug else papers[:10]

            # No papers found
            if retry_count < max_retries:
                wait_minutes = self.retry_interval_minutes
                total_waited = retry_count * self.retry_interval_minutes
                remaining_time = self.max_retry_hours * 60 - total_waited - wait_minutes

                logger.warning(
                    f"No papers in arXiv RSS feed. "
                    f"Retry {retry_count + 1}/{max_retries} in {wait_minutes} minutes. "
                    f"(Max wait: {remaining_time} more minutes)"
                )
                time.sleep(wait_minutes * 60)
                retry_count += 1
            else:
                logger.warning(
                    f"No papers found after {self.max_retry_hours} hours of retrying. "
                    f"Proceeding with journal papers only."
                )
                break

        return []

    def _fetch_from_rss(self) -> list[Paper]:
        """
        Fetch papers from arXiv RSS feed.

        Returns:
            List of Paper objects, or empty list if no papers found
        """
        logger.info(f"Fetching papers from arXiv RSS for categories: {self.categories}")

        # Get paper IDs from RSS feed
        rss_url = f"https://rss.arxiv.org/atom/{self.categories}"
        logger.debug(f"RSS URL: {rss_url}")

        feed = feedparser.parse(rss_url)

        if not feed.entries:
            logger.debug("No entries found in RSS feed")
            return []

        # Filter for new papers only (not updates/replacements)
        new_paper_ids = []
        for entry in feed.entries:
            announce_type = getattr(entry, "arxiv_announce_type", "new")
            if announce_type == "new":
                # Extract arxiv ID from the entry URL
                entry_id = entry.id
                if "/abs/" in entry_id:
                    paper_id = entry_id.split("/abs/")[-1]
                elif ":" in entry_id:
                    paper_id = entry_id.split(":")[-1]
                else:
                    paper_id = entry_id.split("/")[-1]
                # Remove version suffix if present
                if "v" in paper_id and paper_id[-1].isdigit():
                    paper_id = paper_id.rsplit("v", 1)[0]
                new_paper_ids.append(paper_id)

        logger.info(f"Found {len(new_paper_ids)} new papers in RSS feed")

        if not new_paper_ids:
            return []

        # Limit the number of papers (0 means no limit)
        if self.max_papers > 0 and len(new_paper_ids) > self.max_papers:
            logger.info(f"Limiting to {self.max_papers} papers")
            new_paper_ids = new_paper_ids[: self.max_papers]

        # Fetch full paper details in batches
        papers = []
        batch_size = 50

        for i in range(0, len(new_paper_ids), batch_size):
            batch_ids = new_paper_ids[i : i + batch_size]
            logger.debug(f"Fetching batch {i // batch_size + 1}: {len(batch_ids)} papers")

            search = arxiv.Search(id_list=batch_ids)

            try:
                for result in self.client.results(search):
                    paper = self._convert_to_paper(result)
                    papers.append(paper)
            except Exception as e:
                logger.error(f"Error fetching batch: {e}")
                continue

        logger.info(f"Returning {len(papers)} papers from RSS feed")
        return papers

    def _convert_to_paper(self, result: arxiv.Result) -> Paper:
        """Convert arxiv.Result to Paper model."""
        # Extract arxiv ID without version
        arxiv_id = result.entry_id.split("/")[-1]
        if "v" in arxiv_id:
            arxiv_id = arxiv_id.rsplit("v", 1)[0]

        # Get categories - handle both old and new arxiv library versions
        categories = []
        if hasattr(result, "categories"):
            for cat in result.categories:
                if hasattr(cat, "term"):
                    categories.append(cat.term)
                elif isinstance(cat, str):
                    categories.append(cat)

        return Paper(
            arxiv_id=arxiv_id,
            title=result.title.replace("\n", " ").strip(),
            summary=result.summary.replace("\n", " ").strip(),
            authors=[author.name for author in result.authors],
            published=result.published,
            updated=result.updated,
            pdf_url=result.pdf_url,
            categories=categories,
            primary_category=result.primary_category if hasattr(result, "primary_category") else (categories[0] if categories else ""),
            source="preprint",
        )

    def search_papers(self, query: str, max_results: int = 10) -> list[Paper]:
        """
        Search for papers by query string.

        Args:
            query: Search query
            max_results: Maximum number of results

        Returns:
            List of Paper objects
        """
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )

        papers = []
        for result in self.client.results(search):
            papers.append(self._convert_to_paper(result))

        return papers
