#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import time
import unicodedata
from datetime import datetime
from typing import Optional, Tuple, List, Dict

import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================
SEED_FILE = "seed_url.json"
OUT_JSON = "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (GitHub Actions) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
}

TIMEOUT = 60
SLEEP_BETWEEN = 1.2  # segundos entre requests


# =========================
# HELPERS
# =========================
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")

def now_iso_date() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def normalize_text(s: str) -> str:
    """
    Normaliza: lowercase, remove acentos, normaliza espaços.
    """
    if s is None:
        return ""
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s

def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def soup_text(soup: BeautifulSoup) -> str:
    """
    Texto do HTML (limpo) para busca por padrões.
    """
    text = soup.get_text(separator=" ", strip=True)
    return text

def find_first_date_near(text: str, keyword_patterns: List[str]) -> Optional[str]:
    """
    Procura uma data dd/mm/aaaa "próxima" de uma keyword (tolerante).
    Estratégia:
      - normaliza texto
      - para cada padrão, encontra ocorrência e pega um trecho após
      - procura primeira data nesse trecho
    """
    norm = normalize_text(text)

    for pat in keyword_patterns:
        # procurar o padrão (normalizado)
        m = re.search(re.escape(pat), norm)
        if not m:
            continue
        # pega um trecho após o match
        start = m.end()
        snippet = norm[start:start + 250]  # janela curta
        d = DATE_RE.search(snippet)
        if d:
            return d.group(1)

    return None

def extract_title(soup: BeautifulSoup) -> str:
    """
    Tenta título por meta og:title, depois h1, depois <title>.
    """
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        t = og["content"].strip()
        if t:
            return t

    h1 = soup.find("h1")
    if h1:
        t = h1.get_text(strip=True)
        if t:
            return t

    if soup.title and soup.title.string:
        t = soup.title.string.strip()
        if t:
            return t

    return "Edital (título não detectado)"

def extract_card_date_if_any(soup: BeautifulSoup) -> str:
    """
    Muitas páginas de edital exibem a data do post/publicação em algum ponto.
    Aqui tentamos achar a primeira dd/mm/aaaa em regiões comuns.
    Se não achar, retorna "" (para manter seu layout atual, que usa date como string).
    """
    # tenta em elementos comuns
    candidates = []
    for selector in ["time", ".date", ".post-date", ".entry-date", ".elementor-post-date"]:
        for el in soup.select(selector):
            candidates.append(el.get_text(" ", strip=True))

    # também tenta em texto geral, mas com cautela: pega a primeira data do documento
    joined = " | ".join(candidates)
    m = DATE_RE.search(joined)
    if m:
        return m.group(1)

    full = soup_text(soup)
    m2 = DATE_RE.search(full)
    if m2:
        return m2.group(1)

    return ""

def extract_start_end_dates(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrai:
      - start_date: início da submissão
      - end_date: data limite para submissão
    Aceita variações:
      - início das submissões / início da submissão / início do período de submissão...
      - data limite para submissão das propostas (online/on-line)...
      - final do período de submissão...
    Se não achar, retorna None.
    """
    text = soup_text(soup)

    # padrões normalizados (sem acento), para buscar no texto normalizado
    START_PATTERNS = [
        "inicio das submissoes",
        "inicio da submissao",
        "inicio do periodo de submissao",
        "inicio do periodo de submissoes",
        "inicio do periodo de submissao de propostas",
        "inicio do periodo de submissoes de propostas",
        "inicio das submissoes das propostas",
        "inicio de submissao das propostas",
        "inicio do periodo de submissao de propostas no sigfapeam",
        "inicio das submissoes das propostas no sigfapeam",
        "inicio do periodo de submissao no sigfapeam",
        "inicio das submissoes no sigfapeam",
        "inicio do periodo de submissao de propostas no sistema",
        "inicio do periodo de submissao no sistema",
    ]

    END_PATTERNS = [
        "data limite para submissao",
        "data limite para submissao das propostas",
        "data limite para submissao das propostas online",
        "data limite para submissao das propostas on line",
        "data limite para submissao eletronica",
        "data limite para submissao eletronica das propostas",
        "data limite para submissao eletronica das propostas no sigfapeam",
        "data limite para submissao das propostas no sigfapeam",
        "data limite para submissao no sigfapeam",
        "final do periodo de submissao",
        "final do periodo de submissao de propostas",
        "encerramento das submissoes",
        "termino do periodo de submissao",
    ]

    start_date = find_first_date_near(text, START_PATTERNS)
    end_date = find_first_date_near(text, END_PATTERNS)

    return (start_date, end_date)


# =========================
# MAIN
# =========================
def load_seed_urls() -> List[str]:
    with open(SEED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("seed_url.json deve conter uma LISTA de URLs (JSON array).")

    urls = []
    for u in data:
        if isinstance(u, str) and u.strip():
            urls.append(u.strip())
    return urls

def main():
    urls = load_seed_urls()

    items: List[Dict] = []
    for idx, url in enumerate(urls, start=1):
        try:
            html = fetch_html(url)
            soup = BeautifulSoup(html, "html.parser")

            title = extract_title(soup)
            date_str = extract_card_date_if_any(soup)

            start_date, end_date = extract_start_end_dates(soup)

            item = {
                "title": title,
                "url": url,
                "area": "Geral",
                "type": "Edital",
                "date": date_str or "",
                "start_date": start_date if start_date else None,
                "end_date": end_date if end_date else None,
            }
            items.append(item)

            print(f"[OK] {idx}/{len(urls)} - {title}")

        except Exception as e:
            print(f"[ERRO] {idx}/{len(urls)} - {url} -> {e}")
            # mantém o item mesmo se falhar, para não sumir do site
            items.append({
                "title": "Edital (título não detectado)",
                "url": url,
                "area": "Geral",
                "type": "Edital",
                "date": "",
                "start_date": None,
                "end_date": None
            })

        time.sleep(SLEEP_BETWEEN)

    out = {
        "updated_at": now_iso_date(),
        "source": "https://www.fapeam.am.gov.br/editais/?aba=editais-abertos",
        "items": items
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\nPronto. Gerado: {OUT_JSON} com {len(items)} itens.")

if __name__ == "__main__":
    main()
