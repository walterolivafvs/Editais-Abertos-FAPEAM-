#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


BASE_LIST_URL = "https://www.fapeam.am.gov.br/editais/?aba=editais-abertos"
OUT_JSON = "data.json"
SEED_FILE = "seed_url.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (GitHub Actions) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
}

# --- Regex úteis ---
RE_VIGENCIA_RANGE = re.compile(
    r"(vig[eê]ncia)\s*:\s*(\d{2}/\d{2}/\d{4})\s*(a|à|-|até)\s*(\d{2}/\d{2}/\d{4})",
    flags=re.IGNORECASE,
)
RE_ANY_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = s.strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s)
    return s.lower().strip()

def fetch(url: str, timeout: int = 40) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

def load_seed_urls() -> list:
    p = Path(SEED_FILE)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict) and "urls" in data and isinstance(data["urls"], list):
        urls = [u for u in data["urls"] if isinstance(u, str) and u.startswith("http")]
        return urls
    if isinstance(data, list):
        urls = [u for u in data if isinstance(u, str) and u.startswith("http")]
        return urls
    return []

def guess_type_from_title(title: str) -> str:
    t = normalize_text(title)
    # ajustes simples (você pode refinar depois)
    if "programa" in t:
        return "Programa"
    if "chamada" in t:
        return "Chamada"
    if "bolsa" in t:
        return "Bolsa"
    return "Edital"

def extract_title_from_page(soup: BeautifulSoup) -> str:
    # tenta H1
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)

    # tenta title da página
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)

    # tenta og:title
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og.get("content").strip()

    return "Edital (título não detectado)"

def extract_vigencia_from_text(text: str):
    """
    Retorna (start_date, end_date) no formato dd/mm/yyyy ou (None, None).
    """
    if not text:
        return None, None

    m = RE_VIGENCIA_RANGE.search(text)
    if m:
        return m.group(2), m.group(4)

    # fallback: se achar duas datas próximas, pode ser um intervalo
    dates = RE_ANY_DATE.findall(text)
    if len(dates) >= 2:
        # tenta usar as duas primeiras
        return dates[0], dates[1]

    return None, None

def extract_area_guess(url: str, title: str) -> str:
    # Por enquanto, deixe "Geral".
    # Dá para evoluir: inferir por palavras-chave no título (educação, interior, etc.)
    return "Geral"

def parse_edital_page(url: str) -> dict:
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    title = extract_title_from_page(soup)

    # texto completo para buscar "Vigência: ..."
    page_text = soup.get_text(separator=" ", strip=True)
    start_date, end_date = extract_vigencia_from_text(page_text)

    item_type = guess_type_from_title(title)
    area = extract_area_guess(url, title)

    # Campo "date" (data de publicação/atualização): se não tiver, deixa vazio
    # (o site vai agrupar em "Sem data" e isso é ok)
    date_field = ""

    return {
        "title": title,
        "url": url,
        "area": area,
        "type": item_type,
        "date": date_field,
        "start_date": start_date,  # dd/mm/yyyy ou None
        "end_date": end_date,      # dd/mm/yyyy ou None
    }

def extract_links_from_list_page() -> list:
    """
    Fallback: tenta extrair links da página de lista.
    Nem sempre funciona (muitos sites carregam por JS).
    """
    try:
        html = fetch(BASE_LIST_URL)
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("https://www.fapeam.am.gov.br/editais/edital"):
            links.append(href)

    # remove duplicados mantendo ordem
    seen = set()
    out = []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def main():
    urls = load_seed_urls()

    if not urls:
        # fallback (quando seed não existe)
        urls = extract_links_from_list_page()

    if not urls:
        print("ERRO: Não há URLs para processar. Crie seed_url.json com os links dos editais.")
        sys.exit(1)

    items = []
    for u in urls:
        try:
            item = parse_edital_page(u)
            items.append(item)
            print(f"OK: {item['title'][:80]}...")
        except Exception as e:
            # não trava tudo: cria item mínimo
            print(f"FALHOU: {u} -> {e}")
            items.append({
                "title": "Edital (falha ao coletar)",
                "url": u,
                "area": "Geral",
                "type": "Edital",
                "date": "",
                "start_date": None,
                "end_date": None,
            })

    out = {
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "source": BASE_LIST_URL,
        "items": items
    }

    Path(OUT_JSON).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGerado {OUT_JSON} com {len(items)} itens.")

if __name__ == "__main__":
    main()
