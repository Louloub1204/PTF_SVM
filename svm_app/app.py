"""SVM Outil — Streamlit app.

Replaces the legacy Excel "Outil_SVM.xlsx" workflow with:
  • A live dashboard for any of the 22 FCPs
  • Form-based transaction entry and editing
  • One-click BRVM price refresh (with manual CSV fallback)
  • Historical price archive identical in shape to the Excel "Cours" sheet

Run:
    streamlit run app.py
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import db
from portfolio import build_dashboard, previous_business_date

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SVM — Outil de gestion FCP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def _bootstrap() -> None:
    db.init_db()


_bootstrap()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fmt_xof(v: float, signed: bool = False) -> str:
    if v is None or pd.isna(v):
        return "-"
    if abs(v) < 0.5:
        return "-"
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{v:,.0f} FCFA".replace(",", " ")


def fmt_pct(v: float) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v*100:,.2f}%".replace(",", " ")


def style_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    arrow = df["variation"].apply(lambda v: "▲" if v > 0 else ("▼" if v < 0 else "•"))
    out = pd.DataFrame({
        "": arrow,
        "Symbole": df["ticker"],
        "Quantité": df["quantite"].map(lambda v: f"{v:,.0f}".replace(",", " ")),
        "CMP": df["cmp"].map(lambda v: f"{v:,.2f}".replace(",", " ")),
        "Coût total": df["cout_total"].map(fmt_xof),
        "Valorisation": df["valorisation"].map(fmt_xof),
        "+/- estim.": df["diff_estim"].map(lambda v: fmt_xof(v, signed=True)),
        "Poids": df["poids"].map(fmt_pct),
        "Cours veille": df["prev_close"].map(lambda v: f"{v:,.0f}".replace(",", " ")),
        "Cours jour": df["close"].map(lambda v: f"{v:,.0f}".replace(",", " ")),
        "Variation": df["variation"].map(lambda v: fmt_xof(v, signed=True)),
        "+/- value jour": df["plus_moins_value"].map(lambda v: fmt_xof(v, signed=True)),
    })
    return out


# ---------------------------------------------------------------------------
# Sidebar - FCP selection + global controls
# ---------------------------------------------------------------------------
fcps = db.get_fcps()

with st.sidebar:
    st.title("📊 SVM Outil")
    st.caption("Gestion FCP — BRVM")

    if not fcps:
        st.error("Aucun FCP en base. Initialisez les données dans Paramètres.")
        st.stop()

    fcp = st.selectbox("FCP actif", fcps, key="fcp_select")
    as_of = st.date_input("Date de valorisation", value=date.today())
    st.divider()

    page = st.radio(
        "Navigation",
        ["📈 Tableau de bord", "💼 Transactions", "🌐 Cours BRVM",
         "📚 Historique cours", "⚙️ Paramètres"],
        label_visibility="collapsed",
    )


# ---------------------------------------------------------------------------
# Page: Dashboard
# ---------------------------------------------------------------------------
if page == "📈 Tableau de bord":
    st.header(f"{fcp}")
    as_of_ts = pd.Timestamp(as_of)
    prev_date = previous_business_date(as_of_ts)
    st.caption(
        f"Valorisation au **{as_of_ts.strftime('%d/%m/%Y')}** — "
        f"Comparaison avec **{prev_date.strftime('%d/%m/%Y')}**"
    )

    tx_all = db.get_all_transactions_for_compute()
    prices = db.get_prices()
    divs = db.get_dividends(fcp)

    rows, totals = build_dashboard(tx_all, prices, fcp, as_of_ts, divs)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Coût total", fmt_xof(totals["cout_total"]))
    c2.metric("Valorisation", fmt_xof(totals["valorisation"]))
    c3.metric(
        "+/- value latente",
        fmt_xof(totals["diff_estim"], signed=True),
        delta=fmt_pct(
            totals["diff_estim"] / totals["cout_total"] if totals["cout_total"] else 0
        ),
    )
    c4.metric(
        "Variation du jour",
        fmt_xof(totals["variation_jour"], signed=True),
        delta=("▲" if totals["variation_jour"] > 0
               else "▼" if totals["variation_jour"] < 0 else "•"),
    )

    st.divider()

    if rows.empty:
        st.info("Aucune position pour ce FCP à cette date.")
    else:
        rows_active = rows[rows["quantite"] > 0]
        rows_zero = rows[rows["quantite"] == 0]

        st.subheader("Positions actives")
        st.dataframe(
            style_dashboard(rows_active),
            use_container_width=True,
            hide_index=True,
            height=min(600, 35 + 35 * len(rows_active)),
        )

        if not rows_zero.empty:
            with st.expander(f"Lignes soldées ({len(rows_zero)})"):
                st.dataframe(
                    style_dashboard(rows_zero),
                    use_container_width=True,
                    hide_index=True,
                )

        st.divider()
        col_a, col_b = st.columns([2, 3])
        with col_a:
            st.subheader("Répartition par titre")
            chart_data = (
                rows_active.set_index("ticker")["valorisation"]
                .sort_values(ascending=False)
            )
            st.bar_chart(chart_data)

        with col_b:
            st.subheader("Top mouvements du jour")
            top = rows_active.assign(abs_var=rows_active["plus_moins_value"].abs())
            top = top.nlargest(10, "abs_var")[["ticker", "plus_moins_value", "variation"]]
            top = top.rename(columns={
                "ticker": "Symbole",
                "plus_moins_value": "+/- value",
                "variation": "Variation unitaire",
            })
            st.dataframe(top, use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Exporter le tableau (CSV)",
            data=rows.to_csv(index=False).encode("utf-8"),
            file_name=f"{fcp.replace(' ', '_')}_{as_of_ts.date()}.csv",
            mime="text/csv",
        )


# ---------------------------------------------------------------------------
# Page: Transactions
# ---------------------------------------------------------------------------
elif page == "💼 Transactions":
    st.header(f"Transactions — {fcp}")

    with st.expander("➕ Nouvelle transaction", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            t_date = st.date_input("Date", value=date.today(), key="tx_date")
            t_sens = st.selectbox("Sens", ["ACHAT", "VENTE"], key="tx_sens")
        with col2:
            known_tickers = db.get_known_tickers()
            ticker_input = st.selectbox(
                "Titre (ou tapez)",
                options=[""] + known_tickers,
                index=0,
                key="tx_ticker_select",
            )
            ticker_manual = st.text_input(
                "...ou nouveau symbole",
                value="",
                key="tx_ticker_manual",
            ).strip().upper()
            t_ticker = ticker_manual or ticker_input
        with col3:
            t_qte = st.number_input("Quantité", min_value=0.0, step=1.0, key="tx_qte")
            t_prix = st.number_input("Prix unitaire (FCFA)", min_value=0.0, step=1.0, key="tx_prix")
            t_frais = st.number_input("Frais (FCFA)", min_value=0.0, step=1.0,
                                       key="tx_frais", value=0.0)

        valeur_preview = t_qte * t_prix + (t_frais if t_sens == "ACHAT" else -t_frais)
        st.caption(f"Valeur calculée : **{fmt_xof(valeur_preview)}**")

        if st.button("💾 Enregistrer", type="primary"):
            if not t_ticker:
                st.error("Symbole requis.")
            elif t_qte <= 0 or t_prix <= 0:
                st.error("Quantité et prix doivent être > 0.")
            else:
                tx_id = db.add_transaction(
                    str(t_date), fcp, t_ticker, t_sens, t_qte, t_prix, t_frais
                )
                st.success(f"Transaction #{tx_id} enregistrée.")
                st.rerun()

    st.divider()
    st.subheader("Historique")
    tx = db.get_transactions(fcp)
    if tx.empty:
        st.info("Aucune transaction pour ce FCP.")
    else:
        col_l, col_r = st.columns([3, 1])
        col_l.metric("Total transactions", f"{len(tx)}")
        col_r.download_button(
            "⬇️ Exporter (CSV)",
            data=tx.to_csv(index=False).encode("utf-8"),
            file_name=f"transactions_{fcp.replace(' ', '_')}.csv",
            mime="text/csv",
        )

        st.dataframe(
            tx[["id", "date", "ticker", "sens", "quantite", "prix", "valeur", "frais", "cost_in", "cost_out"]],
            use_container_width=True,
            hide_index=True,
        )

        with st.expander("🗑️ Supprimer une transaction"):
            tx_id_del = st.number_input("ID à supprimer", min_value=0, step=1, key="tx_del_id")
            if st.button("Supprimer"):
                if tx_id_del > 0:
                    db.delete_transaction(int(tx_id_del))
                    st.success(f"Transaction #{tx_id_del} supprimée.")
                    st.rerun()


# ---------------------------------------------------------------------------
# Page: BRVM live quotes
# ---------------------------------------------------------------------------
elif page == "🌐 Cours BRVM":
    st.header("Cours BRVM — Mise à jour automatique")
    st.caption("Source officielle : brvm.org/fr/cours-actions/0")

    col1, col2 = st.columns([1, 3])
    with col1:
        do_fetch = st.button("🔄 Rafraîchir maintenant", type="primary")
    with col2:
        st.caption(
            "Astuce : les cours BRVM sont diffusés après la clôture (~ 17h GMT). "
            "Une mise à jour quotidienne suffit."
        )

    if do_fetch:
        try:
            from scraper import fetch_with_session_date
            with st.spinner("Récupération depuis BRVM..."):
                quotes_df, sess = fetch_with_session_date(timeout=25)
            for _, row in quotes_df.iterrows():
                db.upsert_quote_today(row.to_dict())
            close_rows = quotes_df[["ticker", "close"]].dropna().copy()
            close_rows["date"] = sess.isoformat()
            close_rows = close_rows.rename(columns={"close": "price"})[["date", "ticker", "price"]]
            n_prices = db.upsert_prices(close_rows, source="brvm")
            st.success(
                f"✅ {len(quotes_df)} cours récupérés (séance du {sess.strftime('%d/%m/%Y')}). "
                f"{n_prices} cours archivés."
            )
        except Exception as e:
            st.error(f"Échec du rafraîchissement : {e}")
            st.info("Si le scraper échoue, utilisez l'import CSV ci-dessous.")

    st.divider()

    quotes = db.get_quotes_today()
    if quotes.empty:
        st.info("Pas encore de cours en mémoire — cliquez sur Rafraîchir.")
    else:
        st.subheader(f"Snapshot ({len(quotes)} titres)")
        if "fetched_at" in quotes.columns and not quotes["fetched_at"].isna().all():
            st.caption(f"Dernier rafraîchissement : {quotes['fetched_at'].max()}")
        display = quotes.copy()
        for c in ["volume", "prev_close", "open", "close"]:
            if c in display.columns:
                display[c] = display[c].map(
                    lambda v: f"{v:,.0f}".replace(",", " ") if pd.notna(v) else "-"
                )
        if "variation_pct" in display.columns:
            display["variation_pct"] = display["variation_pct"].map(
                lambda v: f"{v:+.2f}%" if pd.notna(v) else "-"
            )
        st.dataframe(
            display.drop(columns=["fetched_at"], errors="ignore"),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    with st.expander("📥 Import manuel CSV (fallback si scraping bloqué)"):
        st.caption("Format attendu : ticker,name,volume,prev_close,open,close,variation_pct")
        f = st.file_uploader("Fichier CSV", type=["csv"], key="quotes_csv")
        if f is not None:
            df = pd.read_csv(f)
            session_d = st.date_input("Date de séance", value=date.today(), key="manual_sess")
            if st.button("Importer ce CSV"):
                for _, r in df.iterrows():
                    db.upsert_quote_today(r.to_dict())
                close_rows = df[["ticker", "close"]].dropna().copy()
                close_rows["date"] = session_d.isoformat()
                close_rows = close_rows.rename(columns={"close": "price"})
                close_rows = close_rows[["date", "ticker", "price"]]
                n = db.upsert_prices(close_rows, source="manual_csv")
                st.success(f"Importé : {len(df)} cours, {n} archivés.")
                st.rerun()


# ---------------------------------------------------------------------------
# Page: Price history
# ---------------------------------------------------------------------------
elif page == "📚 Historique cours":
    st.header("Historique des cours")
    prices = db.get_prices()
    if prices.empty:
        st.info("Base vide.")
    else:
        prices["date"] = pd.to_datetime(prices["date"])
        tickers = sorted(prices["ticker"].unique())
        sel = st.multiselect(
            "Sélectionnez 1 à 5 titres", tickers,
            default=tickers[:1], max_selections=5,
        )
        if sel:
            sub = prices[prices["ticker"].isin(sel)]
            pivot = sub.pivot_table(index="date", columns="ticker", values="price")
            st.line_chart(pivot)

            with st.expander("Voir les données brutes"):
                st.dataframe(
                    sub.sort_values(["ticker", "date"]),
                    use_container_width=True,
                    hide_index=True,
                )


# ---------------------------------------------------------------------------
# Page: Settings
# ---------------------------------------------------------------------------
elif page == "⚙️ Paramètres":
    st.header("Paramètres")

    st.subheader("État de la base")
    tx = db.get_all_transactions_for_compute()
    prices = db.get_prices()
    quotes = db.get_quotes_today()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("FCPs", len(db.get_fcps()))
    c2.metric("Transactions", f"{len(tx):,}".replace(",", " "))
    c3.metric("Cours archivés", f"{len(prices):,}".replace(",", " "))
    c4.metric("Cours du jour", f"{len(quotes)}")

    st.divider()
    st.subheader(f"Dividendes — {fcp}")
    st.caption("Montant par action ajouté à la valorisation (équivalent col N dans Excel).")
    divs = db.get_dividends(fcp)
    held_tickers = sorted(set(tx.loc[tx["fcp"] == fcp, "ticker"].dropna().tolist()))
    if not held_tickers:
        st.info("Aucun titre détenu pour ce FCP.")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            div_ticker = st.selectbox("Titre", held_tickers, key="div_ticker")
        with col2:
            current = divs.get(div_ticker, 0.0)
            div_amount = st.number_input(
                "Dividende (FCFA / action)", value=float(current), step=1.0, key="div_amount"
            )
        with col3:
            st.write("")
            st.write("")
            if st.button("💾 Enregistrer dividende"):
                db.set_dividend(fcp, div_ticker, float(div_amount))
                st.success("Dividende enregistré.")
                st.rerun()

        if divs:
            st.write("**Dividendes actifs :**")
            st.dataframe(
                pd.DataFrame(list(divs.items()), columns=["Symbole", "Montant"]),
                hide_index=True, use_container_width=True,
            )

    st.divider()
    st.subheader("Maintenance")
    st.warning("⚠️ Les actions ci-dessous sont irréversibles.")
    if st.button("🔄 Réinitialiser depuis les fichiers seed"):
        from db import DB_PATH
        if DB_PATH.exists():
            DB_PATH.unlink()
        db.init_db(force=True)
        st.success("Base réinitialisée. Rechargez la page.")
        st.rerun()
