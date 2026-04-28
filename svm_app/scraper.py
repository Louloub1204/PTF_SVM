"""BRVM web-scraper.

Pulls the official daily quotes table from www.brvm.org/fr/cours-actions/0
and writes them to the local DB. Two outputs per call:
  - quotes_today (live snapshot table)
  - prices       (one row per (session_date, ticker) for the historical chart)

The scrape is best-effort: if the structure changes, the user can fall back
to a manual CSV upload (see app.py).
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL = "https://www.brvm.org/fr/cours-actions/0"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _to_float(s: str) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace("\xa0", "").replace(" ", "").replace("%", "")
    s = s.replace(",", ".")
    if s in ("", "-", "ND"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_brvm_quotes(timeout: int = 20) -> pd.DataFrame:
    """Return a DataFrame with one row per ticker for today's session.

    Columns: ticker, name, volume, prev_close, open, close, variation_pct
    """
    resp = requests.get(URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table")
    if table is None:
        raise RuntimeError("BRVM page returned no quotes table.")

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    if not headers:
        first_tr = table.find("tr")
        if first_tr:
            headers = [c.get_text(strip=True) for c in first_tr.find_all(["td", "th"])]

    rows: list[dict] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(cells) < 5:
            continue
        rows.append(cells)

    if not rows:
        raise RuntimeError("BRVM page returned no quote rows.")

    df = pd.DataFrame(rows)

    # Map columns by position based on the canonical BRVM "Cours des actions" layout:
    # 0 Symbole | 1 Nom | 2 Volume | 3 Cours veille | 4 Cours ouverture |
    # 5 Cours clôture | 6 Variation (%) | 7 Valeur transigée
    n = df.shape[1]
    if n < 7:
        raise RuntimeError(f"Unexpected BRVM table layout: {n} columns.")

    out = pd.DataFrame({
        "ticker": df.iloc[:, 0].astype(str).str.strip(),
        "name": df.iloc[:, 1].astype(str).str.strip(),
        "volume": df.iloc[:, 2].apply(_to_float),
        "prev_close": df.iloc[:, 3].apply(_to_float),
        "open": df.iloc[:, 4].apply(_to_float),
        "close": df.iloc[:, 5].apply(_to_float),
        "variation_pct": df.iloc[:, 6].apply(_to_float),
    })
    out = out[out["ticker"].str.match(r"^[A-Z0-9]{2,6}$", na=False)]
    out["fetched_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
    return out.reset_index(drop=True)


def parse_session_date(html: str) -> date | None:
    """BRVM page prints the session date as 'Séance du JJ/MM/AAAA' in the header."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", html)
    if not m:
        return None
    d, mo, y = m.groups()
    try:
        return date(int(y), int(mo), int(d))
    except ValueError:
        return None


def fetch_with_session_date(timeout: int = 20) -> tuple[pd.DataFrame, date]:
    resp = requests.get(URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    sess = parse_session_date(resp.text) or date.today()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("BRVM page returned no quotes table.")

    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(cells) >= 7:
            rows.append(cells)
    if not rows:
        raise RuntimeError("BRVM page returned no quote rows.")

    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "ticker": df.iloc[:, 0].astype(str).str.strip(),
        "name": df.iloc[:, 1].astype(str).str.strip(),
        "volume": df.iloc[:, 2].apply(_to_float),
        "prev_close": df.iloc[:, 3].apply(_to_float),
        "open": df.iloc[:, 4].apply(_to_float),
        "close": df.iloc[:, 5].apply(_to_float),
        "variation_pct": df.iloc[:, 6].apply(_to_float),
    })
    out = out[out["ticker"].str.match(r"^[A-Z0-9]{2,6}$", na=False)].reset_index(drop=True)
    out["fetched_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
    return out, sess
