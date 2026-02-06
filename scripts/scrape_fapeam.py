#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Página “Editais Abertos” (aba)
SOURCE_URL = "https://www.fapeam.am.gov.br/editais/?aba=editais-abertos"
OUT_JSON = "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (GitHub Actions) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
}

# captura datas no formato 29/01/2026
DATE_BR_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")

# links que parecem ser editais individuais
# exemplos: /editais/edital-n-o-0092026-.../
EDITAL_PATH_HINTS = (
    "/editais/edital",
    "/editais/edital-no-",
    "/editais/edital-n-o-",
    "/editais/edital-n%C2%BA",
)

# links que NÃO são editais (abas do arquivo)
EXCLUDE_HINTS = (
    "?aba=",
    "/editais/?aba=",
)

def fetch_html(url: str, timeout: int = 60) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text

def is_valid_edital_url(href: str) -> bool:
    if not href:
        return False
    h = href.strip()
    for bad in EXCLUDE_HINTS:
        if bad in h:
            return False
    return any(good in h for good in EDITAL_PATH_HINTS)

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def normalize_url(base: str, href: str) -> str:
    return urljoin(base, href)

def pick_best_title(anchor: BeautifulSoup) -> str:
    """
    Tenta montar um título confiável:
    - usa o texto do próprio link
    - se for curto ou vazio, tenta subir no DOM e capturar um heading próximo
    """
    t = clean_text(anchor.get_text(" ", strip=True))

    if len(t) >= 12:
        return t

    # tenta achar heading próximo
    parent = anchor.find_parent()
    if parent:
        for tag in ["h1", "h2", "h3", "h4", "strong"]:
            h = parent.find(tag)
            if h:
                ht = clean_text(h.get_text(" ", strip=True))
                if len(ht) >= 12:
                    return ht

    # fallback: texto do link mesmo
    return t if t else "Edital (título não detectado)"

def extract_date_near(anchor: BeautifulSoup) -> str | None:
    """
    Procura uma data dd/mm/aaaa perto do link (no bloco/cartão do edital).
    Se não achar, retorna None (e o frontend pode mostrar “Sem data”).
    """
    # tenta no container mais próximo
    container = anchor.find_parent()
    text_to_search = ""
    if container:
        text_to_search = clean_text(container.get_text(" ", strip=True))

    m = DATE_BR_RE.search(text_to_search)
    if m:
        return m.group(1)

    # fallback: tenta na página inteira (às vezes a data fica fora do bloco)
    soup = anchor.find_parent("body")
    if soup:
        m2 = DATE_BR_RE.search(clean_text(soup.get_text(" ", strip=True)))
        if m2:
            return m2.group(1)

    return None

def same_domain(url: str, domain: str) -> bool:
    try:
        return urlparse(url).netloc.endswith(domain)
    except Exception:
        return False

def scrape_editais_from_page(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    anchors = soup.find_all("a", href=True)
    items_by_url: dict[str, dict] = {}

    for a in anchors:
        href = a.get("href", "")
        if not is_valid_edital_url(href):
            continue

        full = normalize_url(base_url, href)

        # garante que é do domínio da FAPEAM
        if not same_domain(full, "fapeam.am.gov.br"):
            continue

        # remove fragmentos
        full = full.split("#")[0].strip()

        title = pick_best_title(a)
        date_br = extract_date_near(a)  # pode ser None

        # Campos "area" e "type": neste passo, mantemos estável (sem forçar)
        item = {
            "title": title,
            "url": full,
            "area": "Geral",
            "type": "Edital",
            "date": date_br or "",          # mantém compatível com seu frontend (“Sem data”)
            "start_date": None,             # por enquanto: NULL
            "end_date": None                # por enquanto: NULL
        }

        items_by_url[full] = item

    # ordena: primeiro os que têm data (mais recente), depois sem data
    def sort_key(it: dict):
        d = it.get("date") or ""
        if not d:
            return (1, datetime.min)
        try:
            dt = datetime.strptime(d, "%d/%m/%Y")
            return (0, dt)
        except Exception:
            return (1, datetime.min)

    items = sorted(items_by_url.values(), key=sort_key, reverse=True)
    return items

def main():
    html = fetch_html(SOURCE_URL)

    items = scrape_editais_from_page(html, SOURCE_URL)

    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "source": SOURCE_URL,
        "items": items
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[OK] Gerado {OUT_JSON} com {len(items)} item(ns).")

if __name__ == "__main__":
    main()
