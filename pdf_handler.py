"""PDF download and base64 encoding handler."""

import base64
import os
import pickle
import shutil
import subprocess
import tempfile
import time
import httpx
import requests
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlsplit, urlunsplit
from loguru import logger


class PDFHandler:
    """Handles PDF download and base64 encoding."""

    CLOUDFLARE_DOMAINS = {"biorxiv.org", "medrxiv.org"}

    def __init__(
        self,
        timeout: int = 120,
        cache_dir: Optional[str] = None,
        compression_timeout: int = 120,
        compression_profile: str = "/ebook",
        browser_fallback: bool = False,
    ):
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.compression_timeout = compression_timeout
        self.compression_profile = compression_profile
        self.browser_fallback = browser_fallback
        self._browser_driver = None
        self._browser_download_dir = None
        self._xvfb_proc = None

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def download_as_base64(
        self,
        pdf_url: str,
        arxiv_id: Optional[str] = None,
        source: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """
        Download PDF and return as base64 encoded string.

        Args:
            pdf_url: URL to the PDF file
            arxiv_id: Optional arxiv ID for caching
            source: Optional source name for organized storage (e.g., "Nature Medicine")
            date: Optional date string (YYYY-MM-DD) for organized storage

        Returns:
            Base64 encoded PDF content, or None if download fails
        """
        request_url = self._normalize_pdf_url(pdf_url)
        if request_url != pdf_url:
            logger.debug(f"Normalized PDF URL: {pdf_url} -> {request_url}")

        # Determine cache path based on source and date
        cache_path = self._get_cache_path(arxiv_id, source, date)

        # Check cache first
        if cache_path and cache_path.exists():
            logger.debug(f"Loading PDF from cache: {cache_path}")
            return self._file_to_base64(cache_path)

        # Download PDF
        logger.debug(f"Downloading PDF from: {request_url}")

        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(
                    request_url,
                    headers=self._build_download_headers(request_url),
                )
                response.raise_for_status()

                pdf_content = response.content

                # Verify it's a PDF
                if not pdf_content.startswith(b"%PDF"):
                    logger.error(f"Downloaded content is not a valid PDF: {request_url}")
                    return None

                # Cache if enabled - use organized path
                if cache_path:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(pdf_content)
                    logger.debug(f"Cached PDF to: {cache_path}")

                # Encode to base64
                return base64.standard_b64encode(pdf_content).decode("utf-8")

        except httpx.TimeoutException:
            logger.error(f"Timeout downloading PDF: {request_url}")
            return None
        except httpx.HTTPStatusError as e:
            if (
                e.response.status_code == 403
                and self.browser_fallback
                and self._is_cloudflare_site(request_url)
            ):
                logger.info(f"Cloudflare 403, trying browser fallback: {request_url}")
                return self._download_via_browser(request_url, cache_path)
            logger.error(f"HTTP error downloading PDF: {e}")
            return None
        except Exception as e:
            logger.error(f"Error downloading PDF: {e}")
            return None

    def _is_cloudflare_site(self, url: str) -> bool:
        lowered = url.lower()
        return any(d in lowered for d in self.CLOUDFLARE_DOMAINS)

    def _start_xvfb(self):
        """Start Xvfb virtual display if not already running."""
        if self._xvfb_proc and self._xvfb_proc.poll() is None:
            return True
        xvfb_bin = shutil.which("Xvfb")
        if not xvfb_bin:
            return False
        try:
            self._xvfb_proc = subprocess.Popen(
                [xvfb_bin, ":99", "-screen", "0", "1920x1080x24", "-nolisten", "tcp", "-ac"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            os.environ["DISPLAY"] = ":99"
            time.sleep(0.5)
            logger.debug("Xvfb started on :99")
            return True
        except Exception as e:
            logger.warning(f"Failed to start Xvfb: {e}")
            return False

    def _get_or_create_browser(self):
        """Create undetected Chrome with Xvfb to bypass Cloudflare."""
        if self._browser_driver:
            try:
                self._browser_driver.title
                return self._browser_driver
            except Exception:
                self._cleanup_browser()

        try:
            import undetected_chromedriver as uc
        except ImportError:
            logger.warning("undetected-chromedriver not installed, browser fallback unavailable")
            return None

        self._browser_download_dir = tempfile.mkdtemp(prefix="paper-radar-dl-")
        use_xvfb = self._start_xvfb()

        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.set_capability("prefs", {
            "download.default_directory": self._browser_download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
        })

        chrome_bin = os.getenv("CHROME_BIN")
        if chrome_bin and Path(chrome_bin).exists():
            options.binary_location = chrome_bin

        try:
            driver = uc.Chrome(
                options=options,
                headless=not use_xvfb,
                use_subprocess=True,
            )
            driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": self._browser_download_dir,
            })
            self._browser_driver = driver
            mode = "Xvfb non-headless" if use_xvfb else "headless"
            logger.info(f"Undetected Chrome created ({mode}) for Cloudflare bypass")
            return driver
        except Exception as e:
            logger.error(f"Failed to create browser for PDF download: {e}")
            return None

    def _wait_for_cloudflare(self, driver, timeout: int = 30) -> bool:
        """Wait for Cloudflare challenge to resolve."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                title = driver.title.lower()
                if "just a moment" not in title and "attention" not in title:
                    return True
            except Exception:
                pass
            time.sleep(1)
        logger.warning("Cloudflare challenge did not resolve in time")
        return False

    def _download_via_browser(self, url: str, cache_path: Optional[Path]) -> Optional[str]:
        """Download PDF using headless Chrome (bypasses Cloudflare)."""
        driver = self._get_or_create_browser()
        if not driver:
            return None

        download_dir = Path(self._browser_download_dir)
        try:
            for f in download_dir.iterdir():
                f.unlink(missing_ok=True)

            driver.get(url)

            # Phase 1: wait for Cloudflare challenge to pass
            if not self._wait_for_cloudflare(driver, timeout=45):
                logger.error(f"Cloudflare challenge not resolved: {url}")
                return None
            logger.debug(f"Cloudflare passed, waiting for PDF download: {url}")

            # Phase 2: wait for PDF file to appear in download dir
            deadline = time.time() + 60
            while time.time() < deadline:
                time.sleep(2)
                completed = [
                    f for f in download_dir.iterdir()
                    if f.suffix != ".crdownload" and f.stat().st_size > 0
                ]
                if completed:
                    pdf_content = completed[0].read_bytes()
                    if not pdf_content.startswith(b"%PDF"):
                        logger.error(f"Browser download is not a valid PDF: {url}")
                        return None

                    if cache_path:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_bytes(pdf_content)

                    size_mb = len(pdf_content) / 1024 / 1024
                    logger.info(f"Browser downloaded PDF: {size_mb:.2f} MB from {url}")
                    return base64.standard_b64encode(pdf_content).decode("utf-8")

            # Phase 3: download didn't appear — try extracting via CDP
            logger.debug(f"No file downloaded, trying CDP fetch: {url}")
            try:
                current_url = driver.current_url
                resp = driver.execute_cdp_cmd("Network.enable", {})
                resource = driver.execute_cdp_cmd(
                    "Page.captureSnapshot", {}
                )
            except Exception:
                pass

            # Final fallback: use cookies from browser session with httpx
            try:
                cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
                ua = driver.execute_script("return navigator.userAgent")
                if cookies:
                    logger.debug(f"Trying cookie-based download with {len(cookies)} cookies")
                    with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                        response = client.get(url, headers={
                            "User-Agent": ua,
                            "Accept": "application/pdf,*/*",
                        }, cookies=cookies)
                        response.raise_for_status()
                        if response.content.startswith(b"%PDF"):
                            size_mb = len(response.content) / 1024 / 1024
                            logger.info(f"Cookie-based download succeeded: {size_mb:.2f} MB from {url}")
                            if cache_path:
                                cache_path.parent.mkdir(parents=True, exist_ok=True)
                                cache_path.write_bytes(response.content)
                            return base64.standard_b64encode(response.content).decode("utf-8")
            except Exception as e:
                logger.debug(f"Cookie-based download failed: {e}")

            logger.error(f"Browser download timed out: {url}")
            return None
        except Exception as e:
            logger.error(f"Browser download error: {e}")
            return None

    def _cleanup_browser(self):
        if self._browser_driver:
            try:
                self._browser_driver.quit()
            except Exception:
                pass
            self._browser_driver = None
        if self._browser_download_dir:
            shutil.rmtree(self._browser_download_dir, ignore_errors=True)
            self._browser_download_dir = None
        if self._xvfb_proc and self._xvfb_proc.poll() is None:
            self._xvfb_proc.terminate()
            self._xvfb_proc = None

    def close(self):
        """Clean up resources including browser."""
        self._cleanup_browser()

    def __del__(self):
        self._cleanup_browser()

    @staticmethod
    def _build_download_headers(url: str) -> dict:
        """Build browser-like headers for PDF download requests."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        lowered = url.lower()
        if "biorxiv.org" in lowered or "medrxiv.org" in lowered:
            parsed = urlsplit(url)
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        return headers

    @staticmethod
    def _normalize_pdf_url(pdf_url: str) -> str:
        """
        Normalize PDF URL before downloading.

        Fixes bioRxiv/medRxiv feed links such as:
        - .../content/10.xxxv1?rss=1.full.pdf
        - .../content/10.xxxv1?rss=1
        """
        lowered = pdf_url.lower()
        if "biorxiv.org" not in lowered and "medrxiv.org" not in lowered:
            return pdf_url

        parsed = urlsplit(pdf_url)
        path = parsed.path.rstrip("/")
        if not path or "/content/" not in path:
            return pdf_url

        for suffix in (".abstract", ".short"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break

        # Handle malformed "?rss=1.full.pdf" created by appending to query URL
        if not path.endswith(".pdf"):
            path = f"{path}.full.pdf"

        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _file_to_base64(self, file_path: Path) -> Optional[str]:
        """Read file and return as base64."""
        try:
            content = file_path.read_bytes()
            return base64.standard_b64encode(content).decode("utf-8")
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return None

    def _get_cache_path(
        self,
        paper_id: Optional[str],
        source: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Get cache path for a PDF, optionally organized by source and date.

        Directory structure:
        - With source and date: cache_dir/{date}/{source}/{paper_id}.pdf
        - With source only: cache_dir/{source}/{paper_id}.pdf
        - With date only: cache_dir/{date}/{paper_id}.pdf
        - Basic: cache_dir/{paper_id}.pdf

        Args:
            paper_id: Unique paper identifier
            source: Source name (e.g., "Nature Medicine", "arxiv")
            date: Date string (YYYY-MM-DD)

        Returns:
            Path object for cache location, or None if caching disabled
        """
        if not self.cache_dir or not paper_id:
            return None

        # Sanitize paper_id for use as filename
        safe_id = paper_id.replace("/", "_").replace(":", "_")

        # Sanitize source name for directory
        safe_source = None
        if source:
            safe_source = source.replace(" ", "_").replace("/", "_").lower()

        # Build path based on available info
        if date and safe_source:
            # Full organization: date/source/paper.pdf
            cache_path = self.cache_dir / date / safe_source / f"{safe_id}.pdf"
        elif date:
            # Date only: date/paper.pdf
            cache_path = self.cache_dir / date / f"{safe_id}.pdf"
        elif safe_source:
            # Source only: source/paper.pdf
            cache_path = self.cache_dir / safe_source / f"{safe_id}.pdf"
        else:
            # Basic: paper.pdf
            cache_path = self.cache_dir / f"{safe_id}.pdf"

        return cache_path

    def get_saved_pdf_path(
        self,
        paper_id: str,
        source: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get the path where a PDF is/would be saved.

        Args:
            paper_id: Unique paper identifier
            source: Source name
            date: Date string

        Returns:
            Path string if cache is enabled, None otherwise
        """
        cache_path = self._get_cache_path(paper_id, source, date)
        return str(cache_path) if cache_path else None

    def get_pdf_size_mb(self, pdf_base64: str) -> float:
        """Get the size of base64 encoded PDF in MB."""
        # Base64 encoding increases size by ~33%
        original_size = len(pdf_base64) * 3 / 4
        return original_size / (1024 * 1024)

    def compress_base64_for_retry(
        self,
        pdf_base64: str,
        hint: Optional[str] = None,
    ) -> Optional[str]:
        """
        Compress a base64 PDF for retry scenarios (e.g., request entity too large).

        Args:
            pdf_base64: Original base64 encoded PDF content
            hint: Optional paper identifier for logging

        Returns:
            Compressed PDF as base64, or None when compression is unavailable/failed
        """
        gs_bin = shutil.which("gs")
        if not gs_bin:
            logger.warning("Ghostscript (gs) not found, cannot compress PDF for retry")
            return None

        try:
            original_bytes = base64.standard_b64decode(pdf_base64)
        except Exception as e:
            logger.warning(f"Failed to decode PDF base64 for compression: {e}")
            return None

        if not original_bytes.startswith(b"%PDF"):
            logger.warning("Decoded content is not a valid PDF, skip compression retry")
            return None

        original_size_mb = len(original_bytes) / 1024 / 1024
        target_name = hint or "unknown-paper"
        logger.info(
            f"Compressing PDF for retry ({target_name}): {original_size_mb:.2f} MB"
        )

        with tempfile.TemporaryDirectory(prefix="paper-radar-pdf-compress-") as tmpdir:
            input_pdf = Path(tmpdir) / "input.pdf"
            output_pdf = Path(tmpdir) / "output.pdf"
            input_pdf.write_bytes(original_bytes)

            cmd = [
                gs_bin,
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                f"-dPDFSETTINGS={self.compression_profile}",
                "-dNOPAUSE",
                "-dQUIET",
                "-dBATCH",
                "-dDetectDuplicateImages=true",
                "-dDownsampleColorImages=true",
                "-dColorImageDownsampleType=/Bicubic",
                "-dColorImageResolution=150",
                "-dDownsampleGrayImages=true",
                "-dGrayImageDownsampleType=/Bicubic",
                "-dGrayImageResolution=150",
                "-dDownsampleMonoImages=true",
                "-dMonoImageDownsampleType=/Subsample",
                "-dMonoImageResolution=300",
                f"-sOutputFile={output_pdf}",
                str(input_pdf),
            ]

            try:
                subprocess.run(
                    cmd,
                    check=True,
                    timeout=self.compression_timeout,
                    capture_output=True,
                    text=True,
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"PDF compression timed out after {self.compression_timeout}s"
                )
                return None
            except subprocess.CalledProcessError as e:
                stderr_tail = (e.stderr or "").strip()[-500:]
                logger.warning(f"PDF compression failed: {stderr_tail or e}")
                return None

            if not output_pdf.exists():
                logger.warning("Compressed PDF file not generated")
                return None

            compressed_bytes = output_pdf.read_bytes()
            if not compressed_bytes.startswith(b"%PDF"):
                logger.warning("Compressed output is not a valid PDF")
                return None

            compressed_size_mb = len(compressed_bytes) / 1024 / 1024
            ratio = (compressed_size_mb / original_size_mb * 100) if original_size_mb else 100
            logger.info(
                f"PDF compression done ({target_name}): "
                f"{original_size_mb:.2f} MB -> {compressed_size_mb:.2f} MB ({ratio:.1f}%)"
            )

            if len(compressed_bytes) >= len(original_bytes):
                logger.warning(
                    f"Compressed PDF is not smaller for {target_name}, skip retry compression"
                )
                return None

            return base64.standard_b64encode(compressed_bytes).decode("utf-8")

    def clear_cache(self):
        """Clear the PDF cache directory."""
        if self.cache_dir and self.cache_dir.exists():
            for pdf_file in self.cache_dir.glob("*.pdf"):
                pdf_file.unlink()
            logger.info("PDF cache cleared")


class EZproxyPDFHandler(PDFHandler):
    """
    PDF Handler with EZproxy authentication for accessing paywalled content.

    This handler uses Selenium to authenticate with HKU Library's EZproxy,
    then uses the authenticated session to download PDFs from Nature and
    other journals that require institutional access.
    """

    EZPROXY_BASE = "https://eproxy.lib.hku.hk/login?url="

    def __init__(
        self,
        timeout: int = 120,
        cache_dir: Optional[str] = None,
        cookies_file: Optional[str] = None,
        headless: bool = True,
        compression_timeout: int = 120,
        compression_profile: str = "/ebook",
    ):
        """
        Initialize the EZproxy PDF handler.

        Args:
            timeout: Request timeout in seconds
            cache_dir: Optional directory to cache downloaded PDFs
            cookies_file: Path to store/load EZproxy cookies
            headless: Run browser in headless mode (default True)
            compression_timeout: PDF compression timeout in seconds
            compression_profile: Ghostscript PDFSETTINGS profile
        """
        super().__init__(
            timeout=timeout,
            cache_dir=cache_dir,
            compression_timeout=compression_timeout,
            compression_profile=compression_profile,
        )
        self.headless = headless

        # Set up cookies file path
        if cookies_file:
            self.cookies_file = Path(cookies_file)
        elif self.cache_dir:
            self.cookies_file = self.cache_dir / "ezproxy_cookies.pkl"
        else:
            self.cookies_file = Path("./cache/ezproxy_cookies.pkl")

        self.cookies_file.parent.mkdir(parents=True, exist_ok=True)

        # Load credentials from environment
        self.hku_uid = os.getenv("HKU_LIBRARY_UID", "")
        self.hku_pin = os.getenv("HKU_LIBRARY_PIN", "")

        # Session for authenticated requests
        self._session: Optional[requests.Session] = None
        self._driver = None
        self._authenticated = False

        # Clear cached cookies on startup to force fresh login each run
        self._clear_cached_cookies()

    def _convert_to_ezproxy_url(self, url: str) -> str:
        """
        Convert a regular URL to EZproxy proxied URL format.

        Example:
            https://www.nature.com/articles/xxx -> https://www-nature-com.eproxy.lib.hku.hk/articles/xxx
        """
        parsed = urlparse(url)
        # Convert hostname: www.nature.com -> www-nature-com.eproxy.lib.hku.hk
        proxied_host = parsed.netloc.replace(".", "-") + ".eproxy.lib.hku.hk"
        proxied_url = f"https://{proxied_host}{parsed.path}"
        if parsed.query:
            proxied_url += f"?{parsed.query}"
        return proxied_url

    def _create_driver(self):
        """Create and configure Chrome/Chromium WebDriver."""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options
        except ImportError:
            logger.error("Selenium not installed. Run: pip install selenium webdriver-manager")
            raise

        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        # Common options for Docker/headless environments
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")

        # User agent
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Check for Chromium in Docker environment
        chrome_bin = os.getenv("CHROME_BIN")
        chromedriver_path = os.getenv("CHROMEDRIVER_PATH")

        if chrome_bin and Path(chrome_bin).exists():
            # Use system Chromium (Docker environment)
            logger.debug(f"Using system Chromium: {chrome_bin}")
            options.binary_location = chrome_bin

            if chromedriver_path and Path(chromedriver_path).exists():
                service = Service(chromedriver_path)
            else:
                service = Service()
        else:
            # Use webdriver-manager to download Chrome driver (local development)
            try:
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())
            except Exception as e:
                logger.warning(f"webdriver-manager failed: {e}, trying default")
                service = Service()

        driver = webdriver.Chrome(service=service, options=options)

        # Remove webdriver property
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        return driver

    def _clear_cached_cookies(self):
        """Clear cached cookies file to force fresh login on next authentication."""
        if self.cookies_file.exists():
            try:
                self.cookies_file.unlink()
                logger.info("Cleared cached EZproxy cookies - will perform fresh login")
            except Exception as e:
                logger.warning(f"Failed to clear cached cookies: {e}")

    def _save_cookies(self):
        """Save cookies to file for later reuse."""
        if self._driver:
            cookies = self._driver.get_cookies()
            with open(self.cookies_file, "wb") as f:
                pickle.dump(cookies, f)
            logger.debug(f"Saved {len(cookies)} cookies to {self.cookies_file}")

    def _load_cookies_to_session(self) -> bool:
        """Load cookies from file into requests session."""
        if not self.cookies_file.exists():
            return False

        try:
            with open(self.cookies_file, "rb") as f:
                cookies = pickle.load(f)

            self._session = requests.Session()
            for cookie in cookies:
                self._session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain", ""),
                    path=cookie.get("path", "/"),
                )

            logger.debug(f"Loaded {len(cookies)} cookies from cache")
            return True
        except Exception as e:
            logger.warning(f"Failed to load cookies: {e}")
            return False

    def _perform_login(self, target_url: str) -> bool:
        """
        Perform EZproxy login using Selenium.

        Args:
            target_url: The URL we want to access after login

        Returns:
            True if login successful
        """
        if not self.hku_uid or not self.hku_pin:
            logger.error("HKU_LIBRARY_UID or HKU_LIBRARY_PIN not set in environment")
            return False

        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
        except ImportError:
            logger.error("Selenium not installed")
            return False

        logger.info(f"Performing EZproxy login for UID: {self.hku_uid[:3]}***")

        try:
            self._driver = self._create_driver()

            # Navigate to EZproxy login
            login_url = self.EZPROXY_BASE + target_url
            logger.debug(f"Opening: {login_url}")
            self._driver.get(login_url)
            time.sleep(3)

            # Check if already authenticated (redirected to proxied URL)
            current_url = self._driver.current_url or ""
            if "eproxy.lib.hku.hk" in current_url and "login" not in current_url.lower():
                logger.info("Already authenticated via cached session")
                self._save_cookies()
                return True

            # Wait for login form to be fully loaded and interactable
            wait = WebDriverWait(self._driver, 15)

            # Wait for userid field to be clickable
            logger.debug("Waiting for login form...")
            username_field = wait.until(
                EC.element_to_be_clickable((By.NAME, "userid"))
            )

            # Scroll to element to ensure visibility
            self._driver.execute_script(
                "arguments[0].scrollIntoView(true);", username_field
            )
            time.sleep(0.5)

            # Clear and fill username
            username_field.clear()
            username_field.send_keys(self.hku_uid)
            logger.debug("Username entered")
            time.sleep(0.3)

            # Wait for password field to be clickable
            password_field = wait.until(
                EC.element_to_be_clickable((By.NAME, "password"))
            )
            password_field.clear()
            password_field.send_keys(self.hku_pin)
            logger.debug("Password entered")
            time.sleep(0.3)

            # Find and click submit button
            submit_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit']"))
            )
            submit_btn.click()
            logger.debug("Login form submitted, waiting for redirect...")

            # Wait for redirect to authenticated URL
            for i in range(30):
                time.sleep(1)
                current_url = self._driver.current_url or ""
                # Check for successful authentication
                if "eproxy.lib.hku.hk" in current_url and "login" not in current_url.lower():
                    logger.info(f"Login successful after {i+1}s")
                    self._save_cookies()
                    self._load_cookies_to_session()
                    self._authenticated = True
                    return True
                # Also check if redirected to target site via proxy
                if "-eproxy-lib-hku-hk" in current_url or ".eproxy.lib.hku.hk" in current_url:
                    logger.info(f"Login successful (proxied URL) after {i+1}s")
                    self._save_cookies()
                    self._load_cookies_to_session()
                    self._authenticated = True
                    return True

            logger.error("Login timeout - did not redirect to authenticated URL")
            logger.debug(f"Final URL: {self._driver.current_url}")
            return False

        except Exception as e:
            logger.error(f"Login error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False
        finally:
            if self._driver:
                self._driver.quit()
                self._driver = None

    def ensure_authenticated(self, test_url: str = "https://www.nature.com") -> bool:
        """
        Ensure we have a valid authenticated session.

        Args:
            test_url: URL to test authentication against

        Returns:
            True if authenticated
        """
        if self._authenticated and self._session:
            # Test if session is still valid
            try:
                proxied_url = self._convert_to_ezproxy_url(test_url)
                response = self._session.get(proxied_url, timeout=10)
                if response.status_code == 200:
                    return True
            except Exception:
                pass

        # Try to load cookies from file
        if self._load_cookies_to_session():
            try:
                proxied_url = self._convert_to_ezproxy_url(test_url)
                response = self._session.get(proxied_url, timeout=10)
                if response.status_code == 200:
                    self._authenticated = True
                    logger.info("Using cached authentication")
                    return True
            except Exception:
                pass

        # Need to login
        logger.info("Cached session invalid, performing fresh login...")
        return self._perform_login(test_url)

    def download_as_base64(
        self,
        pdf_url: str,
        paper_id: Optional[str] = None,
        require_auth: bool = True,
        source: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """
        Download PDF and return as base64 encoded string.

        This method handles both authenticated (paywalled) and public PDFs.

        Args:
            pdf_url: URL to the PDF file
            paper_id: Optional paper ID for caching (replaces arxiv_id)
            require_auth: If True, use EZproxy authentication
            source: Optional source name for organized storage (e.g., "Nature Medicine")
            date: Optional date string (YYYY-MM-DD) for organized storage

        Returns:
            Base64 encoded PDF content, or None if download fails
        """
        # Get organized cache path
        cache_path = self._get_cache_path(paper_id, source, date)

        # Check cache first
        if cache_path and cache_path.exists():
            logger.debug(f"Loading PDF from cache: {cache_path}")
            return self._file_to_base64(cache_path)

        # If authentication not required, use parent class method
        if not require_auth:
            return super().download_as_base64(pdf_url, paper_id, source, date)

        # Ensure we're authenticated
        if not self.ensure_authenticated():
            logger.error("Failed to authenticate with EZproxy")
            return None

        # Convert URL to EZproxy format and download
        proxied_url = self._convert_to_ezproxy_url(pdf_url)
        logger.debug(f"Downloading authenticated PDF from: {proxied_url}")

        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/pdf,*/*",
            }

            response = self._session.get(
                proxied_url,
                headers=headers,
                allow_redirects=True,
                timeout=self.timeout,
            )
            response.raise_for_status()

            pdf_content = response.content

            # Verify it's a PDF
            if not pdf_content.startswith(b"%PDF"):
                logger.error(f"Downloaded content is not a valid PDF: {proxied_url}")
                # Save for debugging
                if self.cache_dir:
                    debug_path = self.cache_dir / "download_error.html"
                    debug_path.write_bytes(pdf_content[:5000])
                    logger.debug(f"Saved error response to: {debug_path}")
                return None

            # Cache if enabled - use organized path
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(pdf_content)
                logger.debug(f"Cached PDF to: {cache_path}")

            logger.info(f"Downloaded PDF: {len(pdf_content) / 1024 / 1024:.2f} MB")

            # Encode to base64
            return base64.standard_b64encode(pdf_content).decode("utf-8")

        except requests.exceptions.Timeout:
            logger.error(f"Timeout downloading PDF: {proxied_url}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error downloading PDF: {e}")
            return None
        except Exception as e:
            logger.error(f"Error downloading PDF: {e}")
            return None

    def download_nature_pdf(
        self,
        article_url: str,
        paper_id: Optional[str] = None,
        source: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """
        Download a Nature journal PDF.

        This is a convenience method specifically for Nature articles.
        Nature PDFs are at: article_url + ".pdf"

        Args:
            article_url: URL to the Nature article page
            paper_id: Optional paper ID for caching
            source: Optional source name for organized storage
            date: Optional date string for organized storage

        Returns:
            Base64 encoded PDF content, or None if download fails
        """
        # Nature PDF URLs are just article URL + .pdf
        if not article_url.endswith(".pdf"):
            pdf_url = article_url + ".pdf"
        else:
            pdf_url = article_url

        return self.download_as_base64(pdf_url, paper_id, require_auth=True, source=source, date=date)

    def close(self):
        """Clean up resources."""
        if self._driver:
            self._driver.quit()
            self._driver = None
        if self._session:
            self._session.close()
            self._session = None
