import json
import logging
import re
import time
from html import unescape
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse, urlunparse

from .http_client import make_http_request

logger = logging.getLogger(__name__)

RULEBOOK_KEYWORDS = (
    "rule",
    "rules",
    "rulebook",
    "manual",
    "instruction",
    "instructions",
    "livret",
    "regle",
    "regles",
)

THIRD_PARTY_HOSTS = (
    "1jour-1jeu.com",
)

SOURCE_PRIORITY = {
    "publisher": 1,
    "third_party": 2,
    "official_link": 3,
    "bgg_files": 10,
    "bgg_search": 11,
}


class RulebookDiscovery:
    def __init__(self, timeout=20, delay_seconds=0.5):
        self.timeout = timeout
        self.delay_seconds = delay_seconds

    def discover_urls(self, game_id, game_name, publishers):
        findings = []

        for publisher in publishers[:3]:
            findings.extend(self._discover_from_publisher_search(game_name, publisher))

        findings.extend(self._discover_from_third_party(game_name))
        findings.extend(self._discover_from_bgg_official_links(game_id))

        # Keep BGG-hosted file sources as fallback candidates.
        findings.extend(self._discover_from_bgg_files(game_id))
        findings.extend(self._discover_from_bgg_filepage_search(game_name))

        deduped = self._dedupe_findings(findings)
        return self._prioritize_findings(deduped)

    def _http_get_text(self, url):
        try:
            response_data, _ = make_http_request(url, timeout=self.timeout)
            time.sleep(self.delay_seconds)
            return response_data.decode("utf-8", errors="ignore")
        except Exception as exc:
            logger.debug("Rulebook request failed for %s: %s", url, exc)
            return ""

    def _discover_from_bgg_files(self, game_id):
        findings = self._discover_from_bgg_files_api(game_id)
        if findings:
            return findings

        # Fallback for cases where API is unavailable.
        url = f"https://boardgamegeek.com/boardgame/{game_id}/files"
        html = self._http_get_text(url)
        if not html:
            return findings

        for href, anchor_text in self._extract_anchor_links(html, base_url="https://boardgamegeek.com"):
            text = f"{href} {anchor_text}".lower()
            if "/filepage/" in href and any(keyword in text for keyword in RULEBOOK_KEYWORDS):
                findings.append({"source": "bgg_files", "url": href})
                continue

            if self._is_probable_rulebook_url(href, anchor_text):
                findings.append({"source": "bgg_files", "url": href})

        return findings

    def _discover_from_bgg_files_api(self, game_id):
        api_url = f"https://api.geekdo.com/api/file?objectid={game_id}&objecttype=thing&sort=hot&pageid=1"
        text = self._http_get_text(api_url)
        if not text:
            return []

        try:
            payload = json.loads(text)
        except Exception as exc:
            logger.debug("Failed to parse BGG file API payload for %s: %s", game_id, exc)
            return []

        files = payload.get("files", [])
        findings = []
        for file_entry in files:
            filepage_id = file_entry.get("filepageid")
            if not filepage_id:
                continue

            title = file_entry.get("title", "") or ""
            filename = file_entry.get("filename", "") or ""
            slug = file_entry.get("href", "") or ""
            description = file_entry.get("description", "") or ""
            if isinstance(description, dict):
                description = description.get("rendered", "") or ""

            searchable = f"{title} {filename} {description}"
            if slug:
                if str(slug).startswith("http"):
                    file_url = str(slug)
                elif str(slug).startswith("/"):
                    file_url = f"https://boardgamegeek.com{slug}"
                else:
                    file_url = f"https://boardgamegeek.com/filepage/{filepage_id}/{slug}"
            else:
                file_url = f"https://boardgamegeek.com/filepage/{filepage_id}"

            if self._is_probable_rulebook_url(file_url, searchable):
                findings.append({"source": "bgg_files", "url": file_url})

        return findings

    def _discover_from_publisher_search(self, game_name, publisher):
        query = f'"{game_name}" "{publisher}" (rulebook OR rules OR manual OR pdf)'
        findings = []
        fallback_findings = []

        for result_url in self._duckduckgo_search(query):
            if not self._is_probable_rulebook_url(result_url, result_url):
                continue

            if self._publisher_looks_relevant(result_url, publisher):
                findings.append({"source": "publisher", "url": result_url})
            else:
                fallback_findings.append({"source": "publisher", "url": result_url})

        # If the strict publisher-domain heuristic filters everything out,
        # keep probable rulebook URLs rather than returning nothing.
        if not findings:
            findings.extend(fallback_findings[:3])

        return findings

    def _discover_from_bgg_filepage_search(self, game_name):
        query = f'site:boardgamegeek.com/filepage "{game_name}" (rules OR rulebook OR manual OR instructions OR pdf)'
        findings = []

        for result_url in self._duckduckgo_search(query):
            if "boardgamegeek.com/filepage" not in result_url.lower():
                continue
            if self._is_probable_rulebook_url(result_url, result_url):
                findings.append({"source": "bgg_search", "url": result_url})

        return findings

    def _discover_from_third_party(self, game_name):
        query = f'site:1jour-1jeu.com "{game_name}" (regles OR regle OR rules OR rulebook)'
        findings = []
        for result_url in self._duckduckgo_search(query):
            host = urlparse(result_url).netloc.lower()
            if any(third_party_host in host for third_party_host in THIRD_PARTY_HOSTS):
                findings.append({"source": "third_party", "url": result_url})

        return findings

    def _discover_from_bgg_official_links(self, game_id):
        url = f"https://boardgamegeek.com/boardgame/{game_id}"
        html = self._http_get_text(url)
        if not html:
            return []

        findings = []
        for href, anchor_text in self._extract_anchor_links(html, base_url="https://boardgamegeek.com"):
            parsed = urlparse(href)
            host = parsed.netloc.lower()
            if not host or "boardgamegeek.com" in host:
                continue

            if self._is_probable_rulebook_url(href, anchor_text):
                findings.append({"source": "official_link", "url": href})

        return findings

    def _duckduckgo_search(self, query):
        search_url = f"https://duckduckgo.com/html/?q={quote(query)}"
        html = self._http_get_text(search_url)
        if not html:
            return []

        urls = []
        for href, _ in self._extract_anchor_links(html):
            resolved = self._resolve_ddg_redirect(href)
            if not resolved:
                continue

            parsed = urlparse(resolved)
            if parsed.scheme not in ("http", "https"):
                continue

            if parsed.netloc.endswith("duckduckgo.com"):
                continue

            urls.append(self._normalize_url(resolved))

        # Keep ordering but avoid duplicate work.
        deduped = []
        seen = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
            if len(deduped) >= 8:
                break

        return deduped

    def _extract_anchor_links(self, html, base_url=None):
        links = []
        pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
        for match in pattern.finditer(html):
            href = unescape(match.group(1).strip())
            anchor_text = re.sub(r"<[^>]+>", "", unescape(match.group(2))).strip()
            if not href:
                continue
            if base_url:
                href = urljoin(base_url, href)
            links.append((href, anchor_text))
        return links

    def _resolve_ddg_redirect(self, url):
        parsed = urlparse(url)
        if "duckduckgo.com" not in parsed.netloc:
            return url

        query = parse_qs(parsed.query)
        uddg = query.get("uddg")
        if uddg:
            return unquote(uddg[0])

        return None

    def _normalize_url(self, url):
        parsed = urlparse(url)
        cleaned = parsed._replace(fragment="")
        normalized = urlunparse(cleaned)
        return normalized.rstrip("/")

    def _is_probable_rulebook_url(self, url, anchor_text):
        lowercase = f"{url} {anchor_text}".lower()

        if any(keyword in lowercase for keyword in RULEBOOK_KEYWORDS):
            return True

        # Direct document links can still be useful if title text is generic.
        return re.search(r"\.(pdf|docx?|rtf)(\?|$)", lowercase) is not None

    def _publisher_looks_relevant(self, url, publisher_name):
        if not publisher_name:
            return False

        host = urlparse(url).netloc.lower()
        slug = re.sub(r"[^a-z0-9]", "", publisher_name.lower())
        if len(slug) < 4:
            return False

        return slug in re.sub(r"[^a-z0-9]", "", host) or slug in re.sub(r"[^a-z0-9]", "", url.lower())

    def _dedupe_findings(self, findings):
        deduped = []
        seen = set()
        for finding in findings:
            url = self._normalize_url(finding["url"])
            if not url or url in seen:
                continue
            seen.add(url)
            deduped.append({"source": finding["source"], "url": url})
        return deduped

    def _prioritize_findings(self, findings):
        return sorted(
            findings,
            key=lambda item: SOURCE_PRIORITY.get(item.get("source"), 99),
        )


def parse_publishers_json(publishers_json):
    if not publishers_json:
        return []

    try:
        publishers = json.loads(publishers_json)
    except Exception:
        return []

    names = []
    for publisher in publishers:
        name = (publisher or {}).get("name") if isinstance(publisher, dict) else None
        if name:
            names.append(name)

    return names
