import json
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen

SOURCE_URL = "https://www.fapeam.am.gov.br/editais/"

def fetch_html(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (GitHubActions) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
        },
    )
    with urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def extract_links(html: str):
    # Pega links que parecem ser de editais dentro do domínio da FAPEAM
    # Ajustável depois, se a estrutura mudar.
    hrefs = re.findall(r'href=["\'](https?://www\.fapeam\.am\.gov\.br/editais/[^"\']+)["\']', html, flags=re.I)

    # Remove duplicados mantendo ordem
    seen = set()
    out = []
    for h in hrefs:
        h = h.strip()
        if h not in seen:
            seen.add(h)
            out.append(h)

    items = [{"title": None, "url": u} for u in out]
    return items

def main():
    html = fetch_html(SOURCE_URL)
    items = extract_links(html)

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE_URL,
        "items": items,
        "count": len(items),
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
