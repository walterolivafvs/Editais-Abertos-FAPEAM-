#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import unicodedata
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE_LIST_URL = "https://www.fapeam.am.gov.br/editais/?aba=editais-abertos"
OUT_JSON = "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (GitHub Actions) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
}

DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
PDF_RE = re.compile(r"\.pdf($|\?)", re.I)

# Padrões (normalizados, sem acento)
START_PATTERNS = [
    "inicio das submissoes",
    "inicio da submissao",
    "inicio do periodo de submissao",
    "inicio do periodo de submissao de propostas",
    "inicio das submissoes das propostas",
    "inicio do periodo de submissao de propostas no sigfapeam",
    "inicio das submissoes das propostas no sigfapeam",
]

END_PATTERNS = [
    "data limite para submissao",
    "data limite para submissao das propostas",
    "data limite para submissao das propostas online",
    "data limite para submissao eletronica",
    "data limite para submissao eletronica das propostas",
    "final do periodo de submissao",
    "final do periodo de submissao de propostas",
]

def norm(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\s+", " ", text)
    return text

def br_to_iso(br_date: str) -> str:
    m = DATE_RE.search(br_date or "")
    if not m:
        return ""
    dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
    return f"{yyyy}-{mm}-{dd}"

def first_date_in_text(text: str) -> str:
    m = DATE_RE.search(text or "")
    return br_to_iso(m.group(0)) if m else ""

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=40)
    r.raise_for_status()
    return r.text

def fetch_bytes(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.content

def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 6) -> str:
    # Lê as primeiras páginas, onde normalmente está o “CRONOGRAMA”
    reader = PdfReader(io_bytes(pdf_bytes))
    pages = min(len(reader.pages), max_pages)
    out = []
    for i in range(pages):
        try:
            out.append(reader.pages[i].extract_text() or "")
        except Exception:
            pass
    return "\n".join(out)

def io_bytes(b: bytes):
    import io
    return io.BytesIO(b)

def find_pdf_link(edit_url: str) -> str:
    html = fetch(edit_url)
    soup = BeautifulSoup(html, "html.parser")

    # tenta achar links .pdf explícitos
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if href and PDF_RE.search(href):
            return urljoin(edit_url, href)

    # fallback: às vezes o PDF está embutido em botões/arquivos
    # procura por qualquer href contendo ".pdf"
    pdf_any = soup.find("a", href=re.compile(r"\.pdf", re.I))
    if pdf_any and pdf_any.get("href"):
        return urljoin(edit_url, pdf_any["href"])

    return ""

def extract_start_end_from_text(text: str):
    """
    Estratégia robusta:
    - Normaliza o texto
    - Procura por linhas contendo padrões de início/fim
    - Pega a primeira data dd/mm/aaaa encontrada perto dessa linha
    """
    if not text:
        return ("", "")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    nlines = [norm(l) for l in lines]

    start_iso = ""
    end_iso = ""

    def scan_for(patterns):
        for i, ln in enumerate(nlines):
            if any(p in ln for p in patterns):
                # tenta pegar data na mesma linha
                d = first_date_in_text(lines[i])
                if d:
                    return d
                # tenta nas 2 linhas seguintes (muito comum em tabelas)
                for j in range(i+1, min(i+4, len(lines))):
                    d2 = first_date_in_text(lines[j])
                    if d2:
                        return d2
        return ""

    start_iso = scan_for(START_PATTERNS)
    end_iso = scan_for(END_PATTERNS)

    return (start_iso, end_iso)

def parse_list_page():
    html = fetch(BASE_LIST_URL)
    soup = BeautifulSoup(html, "html.parser")

    items = []

    # A página pode mudar; então a gente coleta links que pareçam editais
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue
        if "/editais/" in href and "fapeam.am.gov.br" in href:
            title = a.get_text(" ", strip=True)
            # filtra títulos muito curtos/menus
            if title and len(title) >= 8 and "editais" not in norm(title):
                items.append((title, href))

    # remove duplicados por URL
    seen = set()
    unique = []
    for title, url in items:
        if url in seen:
            continue
        seen.add(url)
        unique.append((title, url))

    return unique

def classify_area_type(title: str):
    # heurística simples; você pode refinar depois
    t = norm(title)
    tipo = "Edital"
    if "programa" in t:
        tipo = "Programa"
    elif "chamada" in t:
        tipo = "Chamada"

    area = "Geral"
    if "evento" in t:
        area = "Eventos"
    elif "popularizacao" in t or "pop" in t:
        area = "Popularização"
    elif "interior" in t or "calha" in t:
        area = "Interiorização"
    elif "pesquisa" in t:
        area = "Pesquisa"
    elif "inovacao" in t or "tecnologia" in t:
        area = "Inovação"

    return area, tipo

def extract_publish_date_from_page(edit_url: str) -> str:
    # tenta achar uma data de publicação na página do edital
    html = fetch(edit_url)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = DATE_RE.search(text)
    if m:
        return br_to_iso(m.group(0))
    return ""

def main():
    raw_list = parse_list_page()

    out_items = []
    for title, url in raw_list:
        area, tipo = classify_area_type(title)
        pub_date = extract_publish_date_from_page(url)

        # pegar pdf e extrair datas de submissão
        start_date = ""
        end_date = ""
        try:
            pdf_url = find_pdf_link(url)
            if pdf_url:
                pdf_bytes = fetch_bytes(pdf_url)
                # texto primeiras páginas
                pdf_text = extract_pdf_text(pdf_bytes, max_pages=7)
                start_date, end_date = extract_start_end_from_text(pdf_text)
        except Exception:
            pass

        out_items.append({
            "title": title,
            "url": url,
            "area": area,
            "type": tipo,
            "date": pub_date,
            "start_date": start_date,
            "end_date": end_date
        })

    data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "source": BASE_LIST_URL,
        "items": out_items
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(out_items)} editais -> {OUT_JSON}")

if __name__ == "__main__":
    main()
