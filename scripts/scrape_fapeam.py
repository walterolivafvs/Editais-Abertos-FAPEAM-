#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import time
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

DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")

# padrões (normalizados, sem acentos)
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
    "data limite para submissao eletronica",
    "data limite para submissao eletronica das propostas",
    "final do periodo de submissao",
    "final do periodo de submissao de propostas",
    "encerramento das submissoes",
]

CRONO_ANCHORS = [
    "cronograma",
    "periodo",
    "submissao",  # ajuda quando “cronograma” não aparece limpo
]


def norm_text(s: str) -> str:
    """lower + remove acentos + normaliza espaços"""
    if not s:
        return ""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def ddmmyyyy_to_iso(d: str) -> str | None:
    try:
        dt = datetime.strptime(d, "%d/%m/%Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def find_pdf_url_in_edital_page(edital_url: str) -> str | None:
    """Procura o primeiro link .pdf na página do edital"""
    html = fetch_html(edital_url)
    soup = BeautifulSoup(html, "html.parser")

    # tenta achar links .pdf
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if href and ".pdf" in href.lower():
            return urljoin(edital_url, href)

    return None


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrai texto do PDF inteiro (tudo junto)."""
    reader = PdfReader(pdf_bytes)
    parts = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        parts.append(txt)
    return "\n".join(parts)


def find_dates_near_patterns(full_text: str) -> tuple[str | None, str | None]:
    """
    Retorna (start_iso, end_iso) procurando datas perto de padrões.
    Estratégia:
    - normaliza texto
    - recorta janela depois de 'cronograma' se existir
    - procura por padrões de início/fim e pega a primeira data próxima
    """
    raw = full_text or ""
    n = norm_text(raw)

    # tenta reduzir para uma janela após CRONOGRAMA (melhora precisão)
    idx = n.find("cronograma")
    if idx != -1:
        window = n[idx: idx + 4000]  # janela razoável após “cronograma”
        raw_window = raw[ max(0, idx-200): ]  # raw não tem o mesmo índice perfeito; mas ajuda pouco
    else:
        window = n[:8000]  # fallback

    # helper: acha data próxima de uma ocorrência
    def date_after(pattern: str) -> str | None:
        pos = window.find(pattern)
        if pos == -1:
            return None

        # pega um trecho depois do padrão e busca data dd/mm/yyyy
        snippet = window[pos: pos + 400]
        m = DATE_RE.search(snippet)
        if m:
            return ddmmyyyy_to_iso(m.group(1))
        return None

    # tenta start
    start_iso = None
    for p in START_PATTERNS:
        start_iso = date_after(p)
        if start_iso:
            break

    # tenta end
    end_iso = None
    for p in END_PATTERNS:
        end_iso = date_after(p)
        if end_iso:
            break

    return start_iso, end_iso


def parse_editais_abertos() -> list[dict]:
    """
    Busca a página "Editais Abertos" e extrai:
    - title
    - url (página do edital)
    - area (quando disponível)
    - type (quando disponível)
    - date (quando disponível; se não, null)
    """
    html = fetch_html(BASE_LIST_URL)
    soup = BeautifulSoup(html, "html.parser")

    items = []

    # A FAPEAM costuma listar cards/posts. Vamos pegar links de /editais/...
    # Ajuste: pega todos os links que parecem ser editais e deduplica.
    links = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/editais/" in href:
            # evita pegar a própria listagem
            if "aba=editais-abertos" in href:
                continue
            links.append(urljoin(BASE_LIST_URL, href))

    # dedupe preservando ordem
    seen = set()
    uniq_links = []
    for u in links:
        if u not in seen:
            seen.add(u)
            uniq_links.append(u)

    for edital_url in uniq_links:
        try:
            edital_html = fetch_html(edital_url)
            s2 = BeautifulSoup(edital_html, "html.parser")

            # título: h1
            title = (s2.select_one("h1") or {}).get_text(strip=True) if s2.select_one("h1") else "Edital"
            title = title.replace("\u00a0", " ").strip()

            # tentativa simples de extrair data/área/tipo do próprio texto (se existir)
            page_text = s2.get_text(" ", strip=True)
            page_norm = norm_text(page_text)

            # data do edital (não é a data limite — é a “data do post” se existir no HTML)
            # fallback: tenta achar dd/mm/yyyy no topo
            mdate = DATE_RE.search(page_text[:2000])
            date_iso = ddmmyyyy_to_iso(mdate.group(1)) if mdate else None

            # heurísticas de tipo
            tipo = "Edital"
            if "programa" in page_norm:
                tipo = "Programa"
            if "chamada" in page_norm:
                tipo = "Chamada"

            # área (bem simples: tenta inferir por palavras comuns)
            area = "Geral"
            if "inovacao" in page_norm:
                area = "Inovação"
            if "pesquisa" in page_norm:
                area = "Pesquisa"
            if "eventos" in page_norm or "evento" in page_norm:
                area = "Eventos"
            if "interior" in page_norm:
                area = "Interiorização"
            if "gestao" in page_norm:
                area = "Gestão"

            items.append({
                "title": title,
                "url": edital_url,
                "area": area,
                "type": tipo,
                "date": date_iso,        # data “do item” (pode ficar null)
                "start_date": None,      # do cronograma (PDF)
                "end_date": None         # do cronograma (PDF)
            })

            time.sleep(0.3)  # reduz risco de bloquear
        except Exception as e:
            # não quebra tudo por 1 edital
            items.append({
                "title": "Edital (erro ao ler)",
                "url": edital_url,
                "area": "Geral",
                "type": "Edital",
                "date": None,
                "start_date": None,
                "end_date": None,
                "error": str(e)
            })

    return items


def enrich_with_pdf_dates(items: list[dict]) -> list[dict]:
    """Para cada edital, tenta baixar o PDF e extrair start_date/end_date."""
    session = requests.Session()
    session.headers.update(HEADERS)

    for it in items:
        url = it.get("url")
        if not url or url == "#":
            continue

        try:
            pdf_url = find_pdf_url_in_edital_page(url)
            if not pdf_url:
                continue

            r = session.get(pdf_url, timeout=60)
            r.raise_for_status()

            text = extract_text_from_pdf(r.content)
            start_iso, end_iso = find_dates_near_patterns(text)

            it["pdf_url"] = pdf_url
            it["start_date"] = start_iso
            it["end_date"] = end_iso

            time.sleep(0.3)
        except Exception:
            # deixa sem datas se falhar
            continue

    return items


def main():
    items = parse_editais_abertos()
    items = enrich_with_pdf_dates(items)

    out = {
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "source": BASE_LIST_URL,
        "items": items
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"OK: {OUT_JSON} atualizado com {len(items)} editais.")


if __name__ == "__main__":
    main()
