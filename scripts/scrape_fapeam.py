#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import time
import unicodedata
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# pypdf é bem leve e funciona no GitHub Actions
from pypdf import PdfReader


BASE_LIST_URL = "https://www.fapeam.am.gov.br/editais/?aba=editais-abertos"
OUT_JSON = "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (GitHub Actions) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
}

# Regex de datas comuns
DATE_DDMMYYYY = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
DATE_DD_MM_YYYY = re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b")


def norm(s: str) -> str:
    """normaliza: minúsculo + remove acentos + limpa espaços"""
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s


def ddmmyyyy_to_iso(ddmmyyyy: str) -> str:
    """04/02/2026 -> 2026-02-04"""
    m = DATE_DDMMYYYY.search(ddmmyyyy or "")
    if not m:
        return ""
    dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
    return f"{yyyy}-{mm}-{dd}"


def find_first_date(text: str) -> str:
    """Retorna primeira data encontrada em ISO (yyyy-mm-dd) ou vazio."""
    if not text:
        return ""
    m = DATE_DDMMYYYY.search(text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m2 = DATE_DD_MM_YYYY.search(text)
    if m2:
        # dd-mm-yyyy
        return f"{m2.group(3)}-{m2.group(2)}-{m2.group(1)}"
    return ""


def http_get(url: str, timeout=60) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def http_get_bytes(url: str, timeout=60) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content


def is_edital_link(href: str) -> bool:
    if not href:
        return False
    href = href.strip()
    # tem que ser do domínio fapeam e do caminho /editais/edital...
    # evita as abas tipo ?aba=...
    if "aba=" in href:
        return False
    return "/editais/edital" in href


def extract_edital_links(list_html: str) -> list[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    links = []
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        abs_url = urljoin(BASE_LIST_URL, href)
        if is_edital_link(abs_url):
            links.append(abs_url)

    # remove duplicados preservando ordem
    seen = set()
    out = []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def extract_basic_from_edital_page(edital_url: str) -> dict:
    """
    Tenta extrair:
      - title
      - published_date (ISO yyyy-mm-dd) se achar
      - area / type (se achar)
      - pdf_url (se achar)
    """
    html = http_get(edital_url)
    soup = BeautifulSoup(html, "html.parser")

    # Título: tenta h1, depois title
    title = ""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)
    if not title:
        t = soup.find("title")
        if t:
            title = t.get_text(" ", strip=True)

    title = title or "(sem título)"

    # Data: tenta achar algo como 04/02/2026 em partes comuns do post
    text = soup.get_text(" ", strip=True)
    published_iso = find_first_date(text)

    # Área e tipo: nem sempre vem estruturado. Tentativa leve via keywords na página.
    # Se não achar, mantém default.
    area = "Geral"
    tipo = "Programa"

    # Heurística: procura palavras “Área:” e “Tipo:” no texto
    # (Se não existir, segue default)
    low = norm(text)
    m_area = re.search(r"\barea[:\-]\s*([a-z0-9çãõáéíóú \-]+)", low)
    if m_area:
        area_raw = m_area.group(1).strip()
        area_raw = re.split(r"\b(tipo|programa|edital)\b", area_raw)[0].strip()
        if area_raw:
            area = area_raw.title()

    m_tipo = re.search(r"\btipo[:\-]\s*([a-z0-9çãõáéíóú \-]+)", low)
    if m_tipo:
        tipo_raw = m_tipo.group(1).strip()
        tipo_raw = re.split(r"\b(area|programa|edital)\b", tipo_raw)[0].strip()
        if tipo_raw:
            tipo = tipo_raw.title()

    # PDF: pega o primeiro link .pdf
    pdf_url = ""
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if ".pdf" in href.lower():
            pdf_url = urljoin(edital_url, href)
            break

    return {
        "title": title,
        "date": published_iso,
        "area": area,
        "type": tipo,
        "pdf_url": pdf_url,
        "page_text": text,  # útil para fallback
    }


# Padrões (normalizados/sem acentos) para achar linhas “início” e “encerramento”
START_PATTERNS = [
    "inicio das submissoes",
    "inicio da submissao",
    "inicio do periodo de submissao",
    "inicio do periodo de submissao de propostas",
    "inicio do periodo de submissao das propostas",
    "inicio das submissoes das propostas",
    "inicio do periodo de submissao de propostas no sigfapeam",
]

END_PATTERNS = [
    "data limite para submissao",
    "data limite para submissao das propostas",
    "data limite para submissao das propostas online",
    "data limite para submissao eletronica das propostas",
    "final do periodo de submissao",
    "final do periodo de submissao de propostas",
    "encerramento das submissoes",
    "termino do periodo de submissao",
]


def pick_date_near_label(full_text: str, label_patterns: list[str]) -> str:
    """
    Estratégia robusta:
    - normaliza texto
    - encontra onde aparece um dos padrões
    - olha uma janela em volta e captura a primeira data
    """
    if not full_text:
        return ""

    raw = full_text
    n = norm(raw)

    # tenta cada padrão
    for pat in label_patterns:
        idx = n.find(pat)
        if idx == -1:
            continue

        # janela: 300 caracteres antes e 500 depois (no texto normalizado)
        start = max(0, idx - 300)
        end = min(len(raw), idx + 500)

        # usa raw (não normalizado) pra manter as datas no formato original
        snippet = raw[start:end]
        iso = find_first_date(snippet)
        if iso:
            return iso

    return ""


def extract_dates_from_pdf(pdf_url: str) -> tuple[str, str]:
    """
    Baixa o PDF, lê texto e tenta extrair:
      start_date, end_date (ISO)
    Se falhar, retorna ("", "")
    """
    if not pdf_url:
        return "", ""

    try:
        pdf_bytes = http_get_bytes(pdf_url, timeout=90)
        reader = PdfReader(io_bytes(pdf_bytes))
        text = []
        # lê só as 2 primeiras páginas (geralmente o cronograma está no começo)
        for i in range(min(2, len(reader.pages))):
            t = reader.pages[i].extract_text() or ""
            text.append(t)
        full = "\n".join(text)
        start_iso = pick_date_near_label(full, START_PATTERNS)
        end_iso = pick_date_near_label(full, END_PATTERNS)
        return start_iso, end_iso
    except Exception:
        return "", ""


def io_bytes(data: bytes):
    # helper sem importar io no topo (mantém simples)
    import io
    return io.BytesIO(data)


def main():
    # 1) pega a lista
    html = http_get(BASE_LIST_URL)
    edital_links = extract_edital_links(html)

    items = []
    for idx, url in enumerate(edital_links, start=1):
        try:
            info = extract_basic_from_edital_page(url)
            start_date, end_date = extract_dates_from_pdf(info.get("pdf_url", ""))

            # se não achou start/end no PDF, deixa vazio (como você quer)
            item = {
                "title": info.get("title", "(sem título)"),
                "url": url,
                "date": info.get("date", "") or "",        # data do edital/post
                "area": info.get("area", "Geral") or "Geral",
                "type": info.get("type", "Programa") or "Programa",
                "start_date": start_date or "",
                "end_date": end_date or "",
            }
            items.append(item)

            # politeness / evita sobrecarregar
            time.sleep(0.4)

        except Exception:
            # Se um edital falhar, não derruba tudo
            items.append({
                "title": "(falha ao ler edital)",
                "url": url,
                "date": "",
                "area": "Geral",
                "type": "Programa",
                "start_date": "",
                "end_date": "",
            })

    out = {
        "source": BASE_LIST_URL,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%d"),
        "items": items
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(items)} itens gravados em {OUT_JSON}")


if __name__ == "__main__":
    main()
