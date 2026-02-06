"""Journal paper fetcher module for Nature, NEJM, etc."""

import re
import feedparser
import requests
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger

from models import Paper
from paper_history import PaperHistory


# Known journal RSS feeds
JOURNAL_RSS_FEEDS = {
    # Preprint servers
    "biorxiv": "https://connect.biorxiv.org/biorxiv_xml.php?subject=all",
    "medrxiv": "https://connect.medrxiv.org/medrxiv_xml.php?subject=all",
    # Nature journals
    "nature": "https://www.nature.com/nature.rss",
    "nature_medicine": "https://www.nature.com/nm.rss",
    "nature_communications": "https://www.nature.com/ncomms.rss",
    "nature_methods": "https://www.nature.com/nmeth.rss",
    "nature_biotechnology": "https://www.nature.com/nbt.rss",
    "nature_machine_intelligence": "https://www.nature.com/natmachintell.rss",
    "nature_biomedical_engineering": "https://www.nature.com/natbiomedeng.rss",
    "nature_cancer": "https://www.nature.com/natcancer.rss",
    "nature_computational_science": "https://www.nature.com/natcomputsci.rss",
    # Medical journals
    "nejm": "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss",
    # Note: NEJM AI RSS feed has been discontinued
    "lancet": "https://www.thelancet.com/rssfeed/lancet_current.xml",
    "lancet_digital_health": "https://www.thelancet.com/rssfeed/landig_current.xml",
    "lancet_oncology": "https://www.thelancet.com/rssfeed/lanonc_current.xml",
    # Cell Press journals
    "cell": "https://www.cell.com/cell/current.rss",
    "cancer_cell": "https://www.cell.com/cancer-cell/current.rss",
    "cell_reports_medicine": "https://www.cell.com/cell-reports-medicine/current.rss",
    # Science journals
    "science": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science",
    "science_advances": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=sciadv",
    # Other
    "pnas": "https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas",
}

PREPRINT_KEYS = {"biorxiv", "medrxiv"}


class JournalFetcher:
    """Fetches papers from academic journals via RSS."""

    def __init__(self, config: dict, paper_history: Optional[PaperHistory] = None):
        """
        Initialize the journal fetcher.

        Args:
            config: Journal configuration dict containing:
                - journals: List of journal configs with name, rss_url, enabled
                - max_papers_per_journal: Maximum papers per journal
            paper_history: Optional PaperHistory instance for deduplication
        """
        self.journals = config.get("journals", [])
        self.max_papers = config.get("max_papers_per_journal", 50)
        self.paper_history = paper_history
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })

    def get_enabled_journals(self) -> list[dict]:
        """Get list of enabled journals."""
        return [j for j in self.journals if j.get("enabled", True)]

    def get_papers(self, debug: bool = False) -> list[Paper]:
        """
        Fetch papers from all enabled journals.

        Args:
            debug: If True, fetch without date filtering

        Returns:
            List of Paper objects (only new papers not previously processed)
        """
        all_papers = []
        enabled_journals = self.get_enabled_journals()

        if not enabled_journals:
            logger.info("No journals enabled")
            return []

        logger.info(f"Fetching papers from {len(enabled_journals)} journals")

        total_skipped = 0
        for journal in enabled_journals:
            try:
                papers = self._fetch_journal(journal, debug)

                # Filter out already processed papers if history is available
                if self.paper_history:
                    new_papers = []
                    for paper in papers:
                        if self.paper_history.is_new_paper(paper.arxiv_id):
                            new_papers.append(paper)
                        else:
                            total_skipped += 1
                    papers = new_papers

                all_papers.extend(papers)
                logger.info(f"  {journal['name']}: {len(papers)} new papers")
            except Exception as e:
                logger.error(f"Error fetching {journal['name']}: {e}")
                continue

        if total_skipped > 0:
            logger.info(f"Skipped {total_skipped} already processed papers")

        logger.info(f"Total new external papers: {len(all_papers)}")
        return all_papers

    def _resolve_source_type(self, journal: dict) -> str:
        """Resolve normalized source type for a configured feed."""
        key = str(journal.get("key", "")).lower()
        if key in PREPRINT_KEYS:
            return "preprint"
        return "journal"

    def _fetch_journal(self, journal: dict, debug: bool = False) -> list[Paper]:
        """Fetch papers from a single journal."""
        name = journal["name"]
        rss_url = journal.get("rss_url") or JOURNAL_RSS_FEEDS.get(journal.get("key", ""))

        if not rss_url:
            logger.warning(f"No RSS URL for journal: {name}")
            return []

        logger.debug(f"Fetching RSS: {rss_url}")

        # Parse RSS feed
        feed = feedparser.parse(rss_url)

        if not feed.entries:
            logger.warning(f"No entries in RSS feed for {name}")
            return []

        papers = []
        cutoff_date = datetime.now().date() - timedelta(days=7)  # Last 7 days for journals

        for entry in feed.entries[: self.max_papers]:
            try:
                paper = self._parse_entry(entry, journal)
                if paper:
                    # Date filtering (skip in debug mode)
                    if debug or paper.published.date() >= cutoff_date:
                        papers.append(paper)
            except Exception as e:
                logger.debug(f"Error parsing entry: {e}")
                continue

        return papers

    def _parse_entry(self, entry, journal: dict) -> Optional[Paper]:
        """Parse RSS entry into Paper object."""
        # Extract basic info
        title = entry.get("title", "").strip()
        if not title:
            return None

        # Clean title
        title = re.sub(r"\s+", " ", title)

        # Get link first to check if it's a research article
        link = entry.get("link", "")

        # Skip non-research articles (news, commentary, etc.)
        if not self._is_research_article(entry, link):
            logger.debug(f"Skipping non-research article: {title[:50]}...")
            return None

        # Get summary/abstract
        summary = ""
        if hasattr(entry, "summary"):
            summary = entry.summary
        elif hasattr(entry, "description"):
            summary = entry.description

        # Clean HTML from summary
        summary = re.sub(r"<[^>]+>", "", summary)
        summary = re.sub(r"\s+", " ", summary).strip()

        # Get authors
        authors = []
        if hasattr(entry, "authors"):
            for author in entry.authors:
                if isinstance(author, dict):
                    authors.append(author.get("name", ""))
                else:
                    authors.append(str(author))
        elif hasattr(entry, "author"):
            authors = [entry.author]

        # Get publication date
        published = datetime.now()
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            published = datetime(*entry.published_parsed[:6])
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            published = datetime(*entry.updated_parsed[:6])

        # Get DOI
        doi = ""
        if hasattr(entry, "dc_identifier"):
            doi = entry.dc_identifier
        elif hasattr(entry, "prism_doi"):
            doi = entry.prism_doi

        # Generate paper ID
        paper_id = self._generate_paper_id(journal, entry, doi)

        # Get PDF URL (if available)
        pdf_url = self._extract_pdf_url(entry, link)

        return Paper(
            arxiv_id=paper_id,  # Using arxiv_id field for compatibility
            title=title,
            summary=summary,
            authors=authors,
            published=published,
            updated=published,
            pdf_url=pdf_url,
            categories=[journal["name"]],
            primary_category=journal["name"],
            source=self._resolve_source_type(journal),
        )

    def _generate_paper_id(self, journal: dict, entry, doi: str) -> str:
        """Generate a unique paper ID."""
        journal_key = journal.get("key", journal["name"].lower().replace(" ", "_"))

        if doi:
            # Use DOI as ID
            doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
            return f"{journal_key}:{doi_clean}"

        # Use entry ID or link hash
        entry_id = entry.get("id", entry.get("link", ""))
        if entry_id:
            # Extract meaningful part
            if "/doi/" in entry_id:
                return f"{journal_key}:{entry_id.split('/doi/')[-1]}"
            else:
                # Use last part of URL
                return f"{journal_key}:{entry_id.split('/')[-1]}"

        # Fallback: use title hash
        import hashlib
        title_hash = hashlib.md5(entry.get("title", "").encode()).hexdigest()[:8]
        return f"{journal_key}:{title_hash}"

    def _extract_pdf_url(self, entry, link: str) -> str:
        """Try to extract PDF URL from entry."""
        # Check for PDF link in entry
        if hasattr(entry, "links"):
            for link_info in entry.links:
                if isinstance(link_info, dict):
                    href = link_info.get("href", "")
                    link_type = link_info.get("type", "")
                    if "pdf" in href.lower() or "application/pdf" in link_type:
                        return href

        # bioRxiv/medRxiv: construct PDF URL
        # https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1 -> .full.pdf
        if "biorxiv.org" in link or "medrxiv.org" in link:
            if "/content/" in link:
                # Remove any trailing version and add .full.pdf
                base_url = link.rstrip("/")
                if not base_url.endswith(".pdf"):
                    return base_url + ".full.pdf"

        # Nature journals: construct PDF URL
        if "nature.com" in link:
            # https://www.nature.com/articles/s41586-024-xxxx -> .pdf
            if "/articles/" in link:
                return link + ".pdf"

        # Lancet journals: construct PDF URL from PII
        if "thelancet.com" in link:
            # Extract PII from URL like:
            # https://www.thelancet.com/journals/landig/article/PIIS2589-7500(25)00120-7/fulltext
            # -> https://www.thelancet.com/action/showPdf?pii=S2589-7500(25)00120-7
            pii_match = re.search(r"/article/PII(S[\d\-\(\)]+)/", link)
            if pii_match:
                pii = pii_match.group(1)
                return f"https://www.thelancet.com/action/showPdf?pii={pii}"

        # Cell Press journals: construct PDF URL from PII
        # https://www.cell.com/cell/fulltext/S0092-8674(24)00001-1 -> pdf URL
        if "cell.com" in link:
            pii_match = re.search(r"/fulltext/(S[\d\-\(\)]+)", link)
            if pii_match:
                pii = pii_match.group(1)
                # Extract journal path (e.g., "cell", "cancer-cell")
                journal_match = re.search(r"cell\.com/([^/]+)/", link)
                if journal_match:
                    journal_path = journal_match.group(1)
                    return f"https://www.cell.com/{journal_path}/pdf/{pii}.pdf"

        # Science journals: construct PDF URL
        # https://www.science.org/doi/10.1126/science.xxx -> /doi/pdf/...
        if "science.org/doi/" in link:
            return link.replace("/doi/", "/doi/pdf/")

        # For most journals, PDF is not directly available via RSS
        # Return the article page URL
        return link

    def _is_research_article(self, entry, link: str) -> bool:
        """
        Check if entry is a research article (not news, commentary, etc.).

        Nature article ID patterns:
        - s41591-xxx: Research articles (have PDF)
        - d41591-xxx: News/commentary (no PDF)
        """
        # Check Nature article patterns
        if "nature.com/articles/" in link:
            # Extract article ID
            article_id = link.split("/articles/")[-1].split(".")[0]
            # Research articles start with 's', news with 'd'
            if article_id.startswith("d"):
                return False
        return True


def test_journal_fetcher():
    """Test the journal fetcher."""
    config = {
        "journals": [
            {"name": "Nature Medicine", "key": "nature_medicine", "enabled": True},
            {"name": "Nature Communications", "key": "nature_communications", "enabled": True},
            {"name": "NEJM", "key": "nejm", "enabled": True},
        ],
        "max_papers_per_journal": 5,
    }

    fetcher = JournalFetcher(config)
    papers = fetcher.get_papers(debug=True)

    print(f"\nFetched {len(papers)} papers:")
    for p in papers[:10]:
        print(f"  [{p.primary_category}] {p.title[:60]}...")
        print(f"    ID: {p.arxiv_id}")
        print(f"    URL: {p.pdf_url}")
        print()


if __name__ == "__main__":
    test_journal_fetcher()
