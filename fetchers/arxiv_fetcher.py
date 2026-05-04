import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from config import LOOKBACK_HOURS

ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "stat.ML"]

# ArXiv has a moderation delay: papers submitted yesterday appear today.
# Use a 48h window to avoid missing papers that just cleared moderation.
ARXIV_LOOKBACK_HOURS = max(LOOKBACK_HOURS, 48)


def fetch_arxiv_papers() -> list[dict]:
    """Fetch recent AI/ML papers from ArXiv via the API (cs.AI, cs.LG, cs.CL, stat.ML)."""
    search_query = " OR ".join([f"cat:{c}" for c in ARXIV_CATEGORIES])
    params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": 60,
    }

    try:
        resp = requests.get(ARXIV_API_URL, params=params, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ArXiv] Request error: {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"[ArXiv] XML parse error: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=ARXIV_LOOKBACK_HOURS)
    articles = []

    for entry in root.findall("atom:entry", ns):
        published_str = entry.findtext("atom:published", "", ns)
        try:
            published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        if published < cutoff:
            break  # Results are sorted by date desc; can stop early

        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        url = (entry.findtext("atom:id", "", ns) or "").strip()
        abstract = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
        authors = [
            a.findtext("atom:name", "", ns)
            for a in entry.findall("atom:author", ns)
        ]
        author_str = ", ".join(a for a in authors[:3] if a)
        if len(authors) > 3:
            author_str += " et al."

        categories = [
            c.get("term", "")
            for c in entry.findall("atom:category", ns)
        ]
        primary_cat = categories[0] if categories else ""

        articles.append({
            "title": title,
            "url": url,
            "summary": f"[{primary_cat}] {author_str}. {abstract[:400]}",
            "source": "ArXiv",
            "published": published.isoformat(),
        })

    print(f"[ArXiv] Found {len(articles)} papers in last {ARXIV_LOOKBACK_HOURS}h")
    return articles
