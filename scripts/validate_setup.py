#!/usr/bin/env python3
"""
Simple validation script to check if setup is correct before running the main script.
"""

import sys
import re
import json
from pathlib import Path
from urllib.parse import unquote
import urllib.request
import urllib.error

# Add the scripts directory to the path so we can import gamecache modules
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

# Now import after path is set
from gamecache.config import parse_config_file  # noqa: E402
from gamecache.http_client import make_http_request  # noqa: E402


def _http_request(method, url, timeout=10, headers=None):
    """HTTP request helper that returns (status, headers, body_bytes).

    Uses urllib directly so we can do HEAD requests and read error bodies.
    """
    req = urllib.request.Request(url, method=method)
    req.add_header('User-Agent', 'GameCache/1.0')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b''
        return e.code, dict(e.headers), body


def _decode_snippet(body, limit=300):
    if not body:
        return ''
    try:
        return body[:limit].decode('utf-8', errors='replace')
    except Exception:
        return str(body[:limit])


def _normalize_github_repo(raw_value):
    """Return (normalized_repo, warnings).

    Accepts:
      - owner/repo
      - https://github.com/owner/repo
      - github.com/owner/repo
    """
    value = str(raw_value).strip()
    warnings = []

    value = value.rstrip('/')

    m = re.match(r'^(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/]+)$', value, re.IGNORECASE)
    if m:
        owner, repo = m.group(1), m.group(2)
        warnings.append("github_repo should be just 'owner/repo' (not a full URL)")
        return f"{owner}/{repo}", warnings

    return value, warnings


def _is_valid_github_owner(owner):
    # Reasonably strict (not perfect): GitHub user/org names are 1-39 chars,
    # alphanumeric or single hyphens between segments.
    return re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner) is not None


def _is_valid_github_repo_name(repo):
    # Repo names can include dots/underscores. Avoid path-ish patterns.
    if repo in {'.', '..'}:
        return False
    if '..' in repo:
        return False
    if '%' in repo:
        return False
    return re.fullmatch(r'[A-Za-z0-9._-]+', repo) is not None


def _validate_github_user(owner):
    """Validate that the GitHub user/organization exists.
    
    Returns True if the user exists, False otherwise.
    """
    print(f"🔍 Checking GitHub user '{owner}' exists...")
    api_url = f"https://api.github.com/users/{owner}"
    req_headers = {'Accept': 'application/vnd.github+json'}
    status, resp_headers, body = _http_request('GET', api_url, timeout=10, headers=req_headers)
    
    if status == 200:
        print(f"✅ GitHub user '{owner}' exists")
        return True
    elif status == 404:
        print(f"❌ GitHub user '{owner}' not found (404)")
        print("   Check that the username is spelled correctly")
        return False
    elif status == 403:
        msg = ''
        try:
            msg = json.loads(body.decode('utf-8', errors='ignore')).get('message', '')
        except Exception:
            msg = _decode_snippet(body)
        print(f"⚠️  GitHub API returned 403 when checking user (rate limit or access restriction)")
        if msg:
            print(f"   Details: {msg}")
        # Don't fail validation on rate limit, let repo check handle it
        return True
    else:
        print(f"⚠️  GitHub API returned HTTP {status} when checking user")
        snippet = _decode_snippet(body)
        if snippet:
            print(f"   Details: {snippet}")
        # Don't fail on other errors, let repo check handle it
        return True


def validate_github_repo(repo_value):
    """Validate github_repo format and sanity-check proxy/repo reachability."""
    normalized, warnings = _normalize_github_repo(repo_value)
    for w in warnings:
        print(f"⚠️  {w}")
        print(f"   Suggested value: {normalized}")

    if '/' not in normalized or normalized.count('/') != 1:
        print("❌ github_repo must be in the form 'OWNER/REPO'")
        print("   Example: EmilStenstrom/gamecache")
        return False

    owner, repo = normalized.split('/', 1)

    if not _is_valid_github_owner(owner) or not _is_valid_github_repo_name(repo):
        print("❌ github_repo contains invalid characters")
        print("   It must look like: OWNER/REPO")
        print("   Repo names may include '.', '_' and '-' (e.g. doszek-games.github.io)")
        print(f"   Current value: {normalized}")
        return False

    print(f"✅ GitHub repo format looks good: {normalized}")

    # First, validate that the GitHub user exists.
    # This makes an additional API call but provides clearer error messages
    # by distinguishing username typos from repository issues.
    if not _validate_github_user(owner):
        return False

    # Check repo exists (helps catch typos and private repos).
    print("🔍 Checking GitHub repo exists...")
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    req_headers = {'Accept': 'application/vnd.github+json'}
    status, headers, body = _http_request('GET', api_url, timeout=10, headers=req_headers)

    if status == 200:
        print("✅ GitHub repo is reachable")
    elif status == 404:
        print(f"❌ GitHub repo '{repo}' not found in user '{owner}' account (404)")
        print("   Check that:")
        print("   - The repository name is spelled correctly")
        print("   - The repository is public (not private)")
        print("   - The repository exists in your GitHub account")
        return False
    elif status == 403:
        msg = ''
        try:
            msg = json.loads(body.decode('utf-8', errors='ignore')).get('message', '')
        except Exception:
            msg = _decode_snippet(body)
        print(f"⚠️  GitHub API returned 403 (rate limit or access restriction)")
        if msg:
            print(f"   Details: {msg}")
    else:
        print(f"⚠️  GitHub API returned HTTP {status}")
        snippet = _decode_snippet(body)
        if snippet:
            print(f"   Details: {snippet}")

    # Check the exact URL the website will fetch in production.
    print("🔍 Checking CORS proxy download endpoint...")
    proxy_url = f"https://cors-proxy.mybgg.workers.dev/{owner}/{repo}"
    status, _, body = _http_request('HEAD', proxy_url, timeout=20)
    if status == 200:
        print("✅ CORS proxy can access your latest database release")
        return True
    if status == 404:
        print("❌ CORS proxy returned 404 (database asset not found)")
        print("   Run: python scripts/download_and_index.py --cache_bgg")
        print("   Then ensure a GitHub Release exists with asset 'gamecache.sqlite.gz'")
        return False
    if status == 400:
        # HEAD responses often have empty body; try GET for details.
        status2, _, body2 = _http_request('GET', proxy_url, timeout=20)
        detail = _decode_snippet(body2)
        print("❌ CORS proxy rejected your github_repo (HTTP 400)")
        if detail:
            print(f"   Details: {detail}")
        print("   github_repo must be OWNER/REPO (no extra path segments)")
        return False

    print(f"⚠️  CORS proxy returned HTTP {status}")
    return True

def validate_config():
    """Validate the config.ini file"""
    config_path = Path("config.ini")

    if not config_path.exists():
        print("❌ config.ini not found!")
        print("   Make sure you're running this from the GameCache directory")
        return False

    try:
        config = parse_config_file("config.ini")
    except FileNotFoundError:
        print("❌ config.ini not found!")
        return False
    except ValueError as e:
        print("❌ config.ini has invalid syntax!")
        print(f"   Error: {e}")
        return False
    except Exception as e:
        print("❌ Error reading config.ini!")
        print(f"   Error: {e}")
        return False

    # Check required fields
    required_fields = ["title", "bgg_username", "github_repo"]

    for field in required_fields:
        if field not in config:
            print(f"❌ Missing field '{field}' in config.ini")
            return False

        value = config[field]
        if not value or "YOUR_" in str(value).upper():
            print(f"❌ Please replace placeholder: {field}")
            print(f"   Current value: {value}")
            return False

    print("✅ config.ini looks good!")

    # Convert flat config to nested structure for compatibility with other functions
    nested_config = {
        "project": {"title": config["title"]},
        "boardgamegeek": {"user_name": config["bgg_username"]},
        "github": {"repo": config["github_repo"]}
    }
    return True, nested_config

def validate_bgg_user(username):
    """Check if BGG username exists and has a public collection"""
    print(f"🔍 Checking BGG user '{username}'...")
    safe_username = unquote(username)

    try:
        # Check user exists
        url = "https://boardgamegeek.com/xmlapi2/user"
        response = make_http_request(url, params={"name": safe_username}, timeout=10)

        # Check collection exists and is public
        url = "https://boardgamegeek.com/xmlapi2/collection"
        response = make_http_request(url, params={"username": safe_username, "own": 1}, timeout=10)

        # Basic check for collection content
        if b"<item " in response:
            print(f"✅ BGG user '{username}' found with accessible collection!")
        else:
            print(f"⚠️  BGG user '{username}' found but collection appears empty")
            print("   Make sure you have games marked as 'owned' in your BGG collection")

        return True

    except Exception as e:
        print(f"❌ Error checking BGG user: {e}")
        print("   Check your internet connection and BGG username")
        return False

def validate_python_deps():
    """Check if required Python packages are installed"""
    print("🔍 Checking Python dependencies...")

    # Read requirements from requirements.in file
    requirements_path = Path("scripts/requirements.in")
    try:
        with open(requirements_path) as f:
            required_packages = []
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith('#'):
                    # Handle package names with version specifiers
                    package_name = line.split('==')[0].split('>=')[0].split('<=')[0].split('~=')[0]
                    required_packages.append(package_name.strip())
    except Exception as e:
        print(f"❌ Error reading requirements.in: {e}")
        print("   Make sure you run this from the GameCache directory")
        return False

    missing = []
    for package in required_packages:
        try:
            # Handle package names that import differently than their pip name
            import_name = package

            # Special cases for packages that import differently
            if package == "pillow":
                import_name = "PIL"
            elif package == "pynacl":
                import_name = "nacl"
            elif "-" in package:
                import_name = package.replace("-", "_")
            elif "." in package:
                import_name = package.replace(".", "_")

            __import__(import_name)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"❌ Missing Python packages: {', '.join(missing)}")
        print("   Run: pip install -r scripts/requirements.txt")
        return False

    print("✅ All Python dependencies are installed!")
    return True

def main():
    print("🧪 Validating GameCache setup...\n")

    all_good = True

    # Validate config
    result = validate_config()
    if isinstance(result, tuple):
        config_valid, config = result
        all_good &= config_valid
    else:
        all_good = False
        return

    print()

    # Validate GitHub repo and proxy endpoint (helps diagnose browser 'NetworkError')
    if config_valid:
        all_good &= validate_github_repo(config["github"]["repo"])
        print()

    # Validate Python dependencies
    all_good &= validate_python_deps()
    print()

    # Validate BGG user
    if config_valid:
        bgg_username = config["boardgamegeek"]["user_name"]
        all_good &= validate_bgg_user(bgg_username)

    print("\n" + "=" * 50)

    if all_good:
        print("🎉 Setup validation passed!")
        print("You're ready to run: python scripts/download_and_index.py --cache_bgg")
    else:
        print("❌ Setup validation failed!")
        print("Please fix the issues above before running the main script.")
        sys.exit(1)


if __name__ == "__main__":
    main()
