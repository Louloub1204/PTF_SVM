"""SQLite-backed storage for FCPs, transactions, and historical prices.

A single file `svm.db` next to the app. On first run, it is seeded from
the CSVs/JSON in `seed_data/`.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "svm.db"
SEED_DIR = APP_DIR / "seed_data"


SCHEMA = """
CREATE TABLE IF NOT EXISTS fcps (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    fcp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    sens TEXT NOT NULL CHECK(sens IN ('ACHAT','VENTE')),
    quantite REAL NOT NULL,
    prix REAL DEFAULT 0,
    valeur REAL DEFAULT 0,        -- qty * prix (no fees)
    frais REAL DEFAULT 0,
    cost_in REAL DEFAULT 0,       -- ACHAT: qty*prix + frais ; VENTE: 0
    cost_out REAL DEFAULT 0,      -- VENTE: qty * CMP_at_sale ; ACHAT: 0
    cmp_at_tx REAL DEFAULT 0,     -- snapshot of CMP at time of sale (VENTE) for traceability
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tx_fcp_date ON transactions(fcp, date);
CREATE INDEX IF NOT EXISTS idx_tx_ticker ON transactions(ticker);

CREATE TABLE IF NOT EXISTS prices (
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    price REAL NOT NULL,
    source TEXT DEFAULT 'seed',
    PRIMARY KEY (date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices(ticker, date);

CREATE TABLE IF NOT EXISTS quotes_today (
    ticker TEXT PRIMARY KEY,
    name TEXT,
    volume REAL,
    prev_close REAL,
    open REAL,
    close REAL,
    variation_pct REAL,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS dividends (
    fcp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    amount REAL NOT NULL,
    date TEXT,
    PRIMARY KEY (fcp, ticker)
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db(force: bool = False) -> None:
    """Create schema. If empty, seed from CSV/JSON files."""
    with conn() as c:
        c.executescript(SCHEMA)

    if force or _is_empty():
        seed_from_files()


def _is_empty() -> bool:
    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return n == 0


def seed_from_files() -> None:
    if not SEED_DIR.exists():
        return

    fcps_file = SEED_DIR / "fcps.json"
    if fcps_file.exists():
        names = json.loads(fcps_file.read_text(encoding="utf-8"))
        with conn() as c:
            c.executemany(
                "INSERT OR IGNORE INTO fcps(name) VALUES (?)",
                [(n,) for n in names],
            )

    tx_file = SEED_DIR / "transactions.csv"
    if tx_file.exists():
        df = pd.read_csv(tx_file)
        with conn() as c:
            df.to_sql("transactions", c, if_exists="append", index=False)

    cours_file = SEED_DIR / "cours.csv"
    if cours_file.exists():
        df = pd.read_csv(cours_file)
        # The Excel "Cours" sheet has rare duplicates on (date, ticker); keep the last one.
        df = df.drop_duplicates(subset=["date", "ticker"], keep="last")
        df["source"] = "seed"
        with conn() as c:
            rows = list(
                df[["date", "ticker", "price", "source"]].itertuples(index=False, name=None)
            )
            c.executemany(
                "INSERT OR REPLACE INTO prices(date, ticker, price, source) VALUES (?, ?, ?, ?)",
                rows,
            )

    t4_file = SEED_DIR / "table4.csv"
    if t4_file.exists():
        df = pd.read_csv(t4_file)
        df["fetched_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
        with conn() as c:
            df.to_sql("quotes_today", c, if_exists="replace", index=False)


# --- Reads ---

def get_fcps() -> list[str]:
    with conn() as c:
        return [r["name"] for r in c.execute("SELECT name FROM fcps ORDER BY name")]


def get_transactions(fcp: str | None = None) -> pd.DataFrame:
    with conn() as c:
        if fcp:
            return pd.read_sql_query(
                "SELECT * FROM transactions WHERE fcp = ? ORDER BY date DESC, id DESC",
                c, params=(fcp,),
            )
        return pd.read_sql_query(
            "SELECT * FROM transactions ORDER BY date DESC, id DESC", c
        )


def get_all_transactions_for_compute() -> pd.DataFrame:
    """Slim version for the engine."""
    with conn() as c:
        return pd.read_sql_query(
            "SELECT date, fcp, ticker, sens, quantite, prix, valeur, frais, "
            "       cost_in, cost_out, cmp_at_tx "
            "FROM transactions",
            c,
        )


def get_prices() -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query("SELECT date, ticker, price FROM prices", c)


def get_quotes_today() -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query("SELECT * FROM quotes_today", c)


def get_dividends(fcp: str) -> dict[str, float]:
    with conn() as c:
        rows = c.execute(
            "SELECT ticker, amount FROM dividends WHERE fcp = ?", (fcp,)
        ).fetchall()
    return {r["ticker"]: r["amount"] for r in rows}


def get_known_tickers() -> list[str]:
    with conn() as c:
        rows = c.execute("SELECT DISTINCT ticker FROM prices ORDER BY ticker").fetchall()
    return [r["ticker"] for r in rows]


# --- Writes ---

def add_transaction(
    date: str, fcp: str, ticker: str, sens: str,
    quantite: float, prix: float, frais: float = 0,
) -> int:
    """Insert a transaction.

    For ACHAT: cost_in = qty*prix + frais.
    For VENTE: cost_out = qty * (current weighted-average CMP for that FCP/ticker
               computed from prior transactions). This mirrors how the source
               Excel filled column G ("COÛT TOTAL" for sells) before computing
               net cost basis.
    """
    valeur = quantite * prix
    cost_in = valeur + frais if sens == "ACHAT" else 0.0
    cost_out = 0.0
    cmp_at_tx = 0.0

    if sens == "VENTE":
        # Compute running CMP from all prior ACHAT/VENTE for this fcp+ticker on or before `date`.
        with conn() as c:
            row = c.execute(
                """SELECT
                       SUM(CASE WHEN sens='ACHAT' THEN quantite ELSE -quantite END) AS qty,
                       SUM(cost_in - cost_out) AS cost
                   FROM transactions
                   WHERE fcp = ? AND ticker = ? AND date <= ?""",
                (fcp, ticker, date),
            ).fetchone()
        prior_qty = (row["qty"] or 0) if row else 0
        prior_cost = (row["cost"] or 0) if row else 0
        cmp_at_tx = prior_cost / prior_qty if prior_qty else 0.0
        cost_out = quantite * cmp_at_tx

    with conn() as c:
        cur = c.execute(
            """INSERT INTO transactions
               (date, fcp, ticker, sens, quantite, prix, valeur, frais,
                cost_in, cost_out, cmp_at_tx)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (date, fcp, ticker, sens, quantite, prix, valeur, frais,
             cost_in, cost_out, cmp_at_tx),
        )
        return cur.lastrowid


def delete_transaction(tx_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def upsert_prices(df: pd.DataFrame, source: str = "manual") -> int:
    """df: columns date, ticker, price."""
    if df.empty:
        return 0
    df = df.copy()
    df["source"] = source
    rows = list(df[["date", "ticker", "price", "source"]].itertuples(index=False, name=None))
    with conn() as c:
        c.executemany(
            "INSERT OR REPLACE INTO prices(date, ticker, price, source) VALUES (?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def upsert_quote_today(quote: dict) -> None:
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO quotes_today
               (ticker, name, volume, prev_close, open, close, variation_pct, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                quote.get("ticker"),
                quote.get("name"),
                quote.get("volume"),
                quote.get("prev_close"),
                quote.get("open"),
                quote.get("close"),
                quote.get("variation_pct"),
                quote.get("fetched_at") or pd.Timestamp.now().isoformat(timespec="seconds"),
            ),
        )


def set_dividend(fcp: str, ticker: str, amount: float, date: str | None = None) -> None:
    with conn() as c:
        if amount == 0:
            c.execute("DELETE FROM dividends WHERE fcp = ? AND ticker = ?", (fcp, ticker))
        else:
            c.execute(
                "INSERT OR REPLACE INTO dividends(fcp, ticker, amount, date) VALUES (?, ?, ?, ?)",
                (fcp, ticker, amount, date),
            )
