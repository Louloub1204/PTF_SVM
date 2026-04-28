"""Portfolio computation engine.

Replicates the Excel formulas from FCP PLACEMENT CROISSANCE:
- C: QUANTITES = sum(ACHAT.qte) - sum(VENTE.qte) up to date D
- E: COUT TOTAL = sum(ACHAT.valeur) - sum(VENTE.prix*qte) up to date D
- D: CMP = E/C
- I: prev close, J: close on D, K: variation, L: K*C (+/- value)
- F: valorisation = C * J
- G: diff estim = F - E
- H: poids = F / total F
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class PortfolioRow:
    ticker: str
    quantite: float
    cmp: float
    cout_total: float
    valorisation: float
    diff_estim: float
    poids: float
    prev_close: float
    close: float
    variation: float
    plus_moins_value: float
    dividende: float = 0.0


def compute_positions(
    transactions: pd.DataFrame,
    fcp: str,
    as_of_date: pd.Timestamp,
) -> pd.DataFrame:
    """Net quantity and remaining cost basis per ticker for a given FCP up to a date.

    The cost basis follows the convention used in the source Excel:
      - ACHAT adds `cost_in` (= qty*prix + frais) to the cost basis
      - VENTE removes `cost_out` (= qty * CMP_at_sale) from the cost basis

    This means the running cost is `Σ cost_in − Σ cost_out` and CMP is
    `cost / quantite`, which stays stable across pure VENTEs of held shares.
    """
    if transactions.empty:
        return pd.DataFrame(columns=["ticker", "quantite", "cout_total", "cmp"])

    df = transactions.copy()
    df["date"] = pd.to_datetime(df["date"])
    mask = (df["fcp"] == fcp) & (df["date"] <= pd.Timestamp(as_of_date))
    df = df.loc[mask]

    if df.empty:
        return pd.DataFrame(columns=["ticker", "quantite", "cout_total", "cmp"])

    df["signed_qty"] = df.apply(
        lambda r: r["quantite"] if r["sens"] == "ACHAT" else -r["quantite"],
        axis=1,
    )
    grouped = df.groupby("ticker").agg(
        quantite=("signed_qty", "sum"),
        cost_in=("cost_in", "sum"),
        cost_out=("cost_out", "sum"),
    )
    grouped["cout_total"] = grouped["cost_in"] - grouped["cost_out"]
    grouped["cmp"] = grouped.apply(
        lambda r: r["cout_total"] / r["quantite"] if r["quantite"] else 0.0,
        axis=1,
    )
    return grouped[["quantite", "cout_total", "cmp"]].reset_index()


def get_price_on(cours: pd.DataFrame, ticker: str, target_date: pd.Timestamp) -> float | None:
    """Last known price for `ticker` on or before `target_date`. None if absent."""
    if cours.empty:
        return None
    df = cours[cours["ticker"] == ticker].copy()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] <= pd.Timestamp(target_date)].sort_values("date")
    if df.empty:
        return None
    return float(df.iloc[-1]["price"])


def previous_business_date(d: pd.Timestamp) -> pd.Timestamp:
    """Excel: =IF(WEEKDAY(J2,2)=1, J2-3, J2-1) — Monday goes to Friday."""
    d = pd.Timestamp(d)
    return d - pd.Timedelta(days=3 if d.weekday() == 0 else 1)


def build_dashboard(
    transactions: pd.DataFrame,
    cours: pd.DataFrame,
    fcp: str,
    as_of_date: pd.Timestamp,
    dividends: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Return (rows DataFrame, totals dict) for one FCP on `as_of_date`."""
    dividends = dividends or {}
    positions = compute_positions(transactions, fcp, as_of_date)
    if positions.empty:
        return pd.DataFrame(), {
            "cout_total": 0,
            "valorisation": 0,
            "diff_estim": 0,
            "variation_jour": 0,
            "as_of": as_of_date,
            "prev_date": previous_business_date(as_of_date),
        }

    prev_date = previous_business_date(as_of_date)
    rows: list[PortfolioRow] = []

    for _, p in positions.iterrows():
        ticker = p["ticker"]
        qte = float(p["quantite"])
        cout = float(p["cout_total"])
        cmp_ = float(p["cmp"])

        prev = get_price_on(cours, ticker, prev_date) or 0.0
        close = get_price_on(cours, ticker, as_of_date) or 0.0
        div = dividends.get(ticker, 0.0)
        close_eff = close + div  # mirrors Excel "+N3" dividend addition

        valorisation = qte * close_eff
        variation_unit = close_eff - prev
        plus_moins = variation_unit * qte
        diff_estim = valorisation - cout

        rows.append(
            PortfolioRow(
                ticker=ticker,
                quantite=qte,
                cmp=cmp_,
                cout_total=cout,
                valorisation=valorisation,
                diff_estim=diff_estim,
                poids=0.0,  # filled after total
                prev_close=prev,
                close=close_eff,
                variation=variation_unit,
                plus_moins_value=plus_moins,
                dividende=div,
            )
        )

    df = pd.DataFrame([r.__dict__ for r in rows])
    total_valo = df["valorisation"].sum()
    df["poids"] = df["valorisation"] / total_valo if total_valo else 0.0
    df = df.sort_values("valorisation", ascending=False).reset_index(drop=True)

    totals = {
        "cout_total": float(df["cout_total"].sum()),
        "valorisation": float(total_valo),
        "diff_estim": float(df["diff_estim"].sum()),
        "variation_jour": float(df["plus_moins_value"].sum()),
        "as_of": pd.Timestamp(as_of_date),
        "prev_date": prev_date,
    }
    return df, totals
