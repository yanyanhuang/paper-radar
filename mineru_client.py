"""MinerU online API client for converting papers to Markdown."""

from __future__ import annotations

import hashlib
import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from zipfile import BadZipFile, ZipFile

import requests
from loguru import logger


class MinerUError(RuntimeError):
    """Raised when MinerU parsing cannot produce Markdown."""


class MinerUClient:
    """Convert local PDFs to Markdown through MinerU's online APIs."""

    PENDING_STATES = {"waiting-file", "uploading", "pending", "running", "converting"}

    def __init__(self, config: Optional[dict[str, Any]] = None):
        config = config or {}
        self.enabled = self._to_bool(config.get("enabled", False))
        self.api_base = (config.get("api_base") or "https://mineru.net").rstrip("/")
        self.api_key = (config.get("api_key") or "").strip()
        self.mode = str(config.get("mode") or "precise").strip().lower()
        self.allow_agent_fallback = self._to_bool(config.get("allow_agent_fallback", False))
        self.model_version = str(config.get("model_version") or "vlm").strip()
        self.language = str(config.get("language") or "ch").strip()
        self.is_ocr = self._to_bool(config.get("is_ocr", False))
        self.enable_table = self._to_bool(config.get("enable_table", True))
        self.enable_formula = self._to_bool(config.get("enable_formula", True))
        self.page_ranges = str(config.get("page_ranges") or "").strip()
        self.cache_dir = Path(config.get("cache_dir") or "./cache/mineru")
        self.timeout = int(config.get("timeout", 180))
        self.poll_interval = max(1, int(config.get("poll_interval", 5)))
        self.max_wait_seconds = max(30, int(config.get("max_wait_seconds", 900)))
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def active_mode(self) -> str:
        """Return the API mode to use for this run."""
        if self.mode == "auto":
            if self.api_key:
                return "precise"
            if self.allow_agent_fallback:
                return "agent"
            return "precise"
        return self.mode

    @property
    def is_ready(self) -> bool:
        """Whether the configured MinerU mode has enough credentials to run."""
        if not self.enabled:
            return False
        if self.active_mode == "precise" and not self.api_key:
            return False
        return self.active_mode in {"precise", "agent"}

    def parse_pdf_file(
        self,
        pdf_path: str | Path,
        paper_id: str,
        source: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """Parse a local PDF and return cached or freshly generated Markdown."""
        if not self.enabled:
            return None

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise MinerUError(f"PDF file not found: {pdf_path}")

        markdown_path = self._markdown_cache_path(paper_id, source, date)
        if markdown_path.exists():
            logger.info(f"  ↓ Loading MinerU Markdown from cache: {markdown_path}")
            return markdown_path.read_text(encoding="utf-8")

        data_id = self._safe_component(paper_id, max_len=128)
        mode = self.active_mode
        logger.info(f"  ↓ Parsing PDF with MinerU ({mode}/{self.model_version})")

        if mode == "precise":
            if not self.api_key:
                if self.allow_agent_fallback:
                    logger.warning("  ! MINERU_API_KEY missing, falling back to Agent API")
                    markdown, metadata = self._parse_with_agent_api(pdf_path)
                else:
                    raise MinerUError("MINERU_API_KEY is not configured")
            else:
                markdown, metadata = self._parse_with_precise_api(pdf_path, data_id)
        elif mode == "agent":
            markdown, metadata = self._parse_with_agent_api(pdf_path)
        else:
            raise MinerUError(f"Unsupported MinerU mode: {mode}")

        if not markdown or not markdown.strip():
            raise MinerUError("MinerU returned empty Markdown")

        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown, encoding="utf-8")
        metadata_path = markdown_path.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(
                {
                    "paper_id": paper_id,
                    "source": source,
                    "date": date,
                    "mode": mode,
                    **metadata,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(f"  ↓ Cached MinerU Markdown: {markdown_path}")
        return markdown

    def _parse_with_precise_api(self, pdf_path: Path, data_id: str) -> tuple[str, dict[str, Any]]:
        self._check_file_size(pdf_path, max_mb=200, mode="precise")
        file_entry: dict[str, Any] = {
            "name": pdf_path.name,
            "data_id": data_id,
            "is_ocr": self.is_ocr,
        }
        if self.page_ranges:
            file_entry["page_ranges"] = self.page_ranges

        payload: dict[str, Any] = {
            "files": [file_entry],
            "model_version": self.model_version,
            "language": self.language,
            "enable_table": self.enable_table,
            "enable_formula": self.enable_formula,
        }

        data = self._post_json(
            "/api/v4/file-urls/batch",
            payload,
            headers=self._auth_headers(),
        )
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or []
        if not batch_id or not file_urls:
            raise MinerUError(f"MinerU did not return batch upload URLs: {data}")

        self._upload_file(file_urls[0], pdf_path)
        result = self._poll_precise_batch(batch_id, data_id, pdf_path.name)
        full_zip_url = result.get("full_zip_url")
        if not full_zip_url:
            raise MinerUError(f"MinerU result missing full_zip_url: {result}")

        markdown = self._download_zip_markdown(full_zip_url)
        return markdown, {
            "batch_id": batch_id,
            "data_id": data_id,
            "state": result.get("state"),
            "file_name": result.get("file_name"),
        }

    def _parse_with_agent_api(self, pdf_path: Path) -> tuple[str, dict[str, Any]]:
        self._check_file_size(pdf_path, max_mb=10, mode="agent")
        payload: dict[str, Any] = {
            "file_name": pdf_path.name,
            "language": self.language,
            "is_ocr": self.is_ocr,
            "enable_table": self.enable_table,
            "enable_formula": self.enable_formula,
        }
        if self.page_ranges:
            payload["page_range"] = self.page_ranges

        data = self._post_json("/api/v1/agent/parse/file", payload)
        task_id = data.get("task_id")
        file_url = data.get("file_url")
        if not task_id or not file_url:
            raise MinerUError(f"MinerU Agent did not return upload URL: {data}")

        self._upload_file(file_url, pdf_path)
        result = self._poll_agent_task(task_id)
        markdown_url = result.get("markdown_url")
        if not markdown_url:
            raise MinerUError(f"MinerU Agent result missing markdown_url: {result}")

        markdown = self._download_text(markdown_url)
        return markdown, {"task_id": task_id, "state": result.get("state")}

    def _poll_precise_batch(self, batch_id: str, data_id: str, file_name: str) -> dict[str, Any]:
        deadline = time.time() + self.max_wait_seconds
        poll_url = f"/api/v4/extract-results/batch/{batch_id}"

        while time.time() < deadline:
            data = self._get_json(poll_url, headers=self._auth_headers())
            results = data.get("extract_result") or data.get("extract_results") or []
            if isinstance(results, dict):
                results = [results]

            result = self._find_result(results, data_id, file_name)
            if result:
                state = str(result.get("state") or "").lower()
                if state == "done":
                    return result
                if state == "failed":
                    raise MinerUError(result.get("err_msg") or "MinerU precise parsing failed")
                if state:
                    self._log_progress(result)

            time.sleep(self.poll_interval)

        raise MinerUError(f"Timed out waiting for MinerU batch {batch_id}")

    def _poll_agent_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.max_wait_seconds
        poll_url = f"/api/v1/agent/parse/{task_id}"

        while time.time() < deadline:
            data = self._get_json(poll_url)
            state = str(data.get("state") or "").lower()
            if state == "done":
                return data
            if state == "failed":
                raise MinerUError(data.get("err_msg") or "MinerU Agent parsing failed")
            if state in self.PENDING_STATES:
                self._log_progress(data)
            time.sleep(self.poll_interval)

        raise MinerUError(f"Timed out waiting for MinerU Agent task {task_id}")

    def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        response = requests.post(
            self._url(path),
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        return self._decode_mineru_response(response)

    def _get_json(
        self,
        path: str,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        response = requests.get(
            self._url(path),
            headers=headers,
            timeout=self.timeout,
        )
        return self._decode_mineru_response(response)

    @staticmethod
    def _decode_mineru_response(response: requests.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise MinerUError(f"MinerU HTTP error: {exc}") from exc

        if body.get("code") != 0:
            raise MinerUError(f"MinerU API error {body.get('code')}: {body.get('msg')}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise MinerUError(f"MinerU response missing data: {body}")
        return data

    def _upload_file(self, upload_url: str, pdf_path: Path) -> None:
        with pdf_path.open("rb") as handle:
            response = requests.put(upload_url, data=handle, timeout=self.timeout)
        if response.status_code not in {200, 201, 204}:
            raise MinerUError(f"MinerU upload failed with HTTP {response.status_code}")

    def _download_zip_markdown(self, zip_url: str) -> str:
        response = requests.get(zip_url, timeout=self.timeout)
        response.raise_for_status()
        try:
            with ZipFile(BytesIO(response.content)) as archive:
                return self._extract_markdown_from_zip(archive)
        except BadZipFile as exc:
            raise MinerUError("MinerU result is not a valid zip file") from exc

    @staticmethod
    def _extract_markdown_from_zip(archive: ZipFile) -> str:
        names = [name for name in archive.namelist() if name.lower().endswith(".md")]
        if not names:
            raise MinerUError("MinerU zip result does not contain Markdown")

        def score(name: str) -> tuple[int, int]:
            lowered = name.lower()
            if lowered.endswith("/full.md") or lowered == "full.md":
                return (0, len(name))
            return (1, len(name))

        md_name = sorted(names, key=score)[0]
        data = archive.read(md_name)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")

    def _download_text(self, url: str) -> str:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text

    def _markdown_cache_path(
        self,
        paper_id: str,
        source: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Path:
        safe_id = self._safe_component(paper_id)
        parts = [self.cache_dir]
        if date:
            parts.append(Path(self._safe_component(date)))
        if source:
            parts.append(Path(self._safe_component(source).lower()))
        parts.append(Path(safe_id))
        return Path(*parts) / "full.md"

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.api_base}/{path.lstrip('/')}"

    @staticmethod
    def _find_result(
        results: list[dict[str, Any]],
        data_id: str,
        file_name: str,
    ) -> Optional[dict[str, Any]]:
        if not results:
            return None
        for result in results:
            if result.get("data_id") == data_id or result.get("file_name") == file_name:
                return result
        return results[0]

    @staticmethod
    def _log_progress(result: dict[str, Any]) -> None:
        state = result.get("state") or "unknown"
        progress = result.get("extract_progress") or {}
        total = progress.get("total_pages")
        done = progress.get("extracted_pages")
        if total and done is not None:
            logger.info(f"  ↓ MinerU state={state}, pages={done}/{total}")
        else:
            logger.info(f"  ↓ MinerU state={state}")

    @staticmethod
    def _check_file_size(pdf_path: Path, max_mb: int, mode: str) -> None:
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if size_mb > max_mb:
            raise MinerUError(
                f"PDF is {size_mb:.1f} MB, above MinerU {mode} limit of {max_mb} MB"
            )

    @staticmethod
    def _safe_component(value: str, max_len: int = 96) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._-")
        if not safe:
            safe = "paper"
        if len(safe) > max_len:
            digest = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:12]
            safe = f"{safe[: max_len - 13]}_{digest}"
        return safe

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() not in {"", "0", "false", "no", "off"}
