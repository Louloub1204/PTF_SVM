# Outil SVM — Application Streamlit

Application web qui automatise le fichier Excel **Outil_SVM.xlsx** pour la
gestion des 22 Fonds Communs de Placement (FCP) cotés sur la BRVM.

## Ce que fait l'application

| Module | Fonction |
| --- | --- |
| 📈 **Tableau de bord** | Reproduit la feuille `FCP PLACEMENT CROISSANCE` pour n'importe quel FCP : quantités, CMP, valorisation, ± value, poids, variation du jour, top mouvements. Recalcul instantané. |
| 💼 **Transactions** | Saisie ACHAT / VENTE par formulaire. Plus besoin d'aller chercher la ligne 6000 de l'onglet `Transactions`. Suppression possible par ID. Export CSV. |
| 🌐 **Cours BRVM** | Bouton « Rafraîchir » qui scrape la page officielle `brvm.org/fr/cours-actions/0` et archive automatiquement le cours de clôture du jour. Fallback CSV si le site change. |
| 📚 **Historique cours** | Tracé interactif de 1 à 5 titres sur tout l'historique (équivalent feuille `Cours`). |
| ⚙️ **Paramètres** | Statut de la base, gestion des dividendes par titre, réinitialisation. |

## Architecture

```
svm_app/
├── app.py            # UI Streamlit (5 pages)
├── portfolio.py      # Moteur de calcul (remplace les formules Excel)
├── db.py             # Couche SQLite
├── scraper.py        # Scraper BRVM
├── seed_data/        # Données initiales extraites du fichier Excel
│   ├── fcps.json         # 22 FCPs
│   ├── transactions.csv  # 6 100 transactions historiques
│   ├── cours.csv         # 149 296 cours historiques (114 titres, depuis 2014)
│   └── table4.csv        # Snapshot du jour
└── svm.db            # Base SQLite (créée au premier lancement)
```

Au premier lancement, `svm.db` est seedé automatiquement depuis `seed_data/`.

## Installation locale

```bash
cd svm_app
python -m venv .venv
source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

L'application ouvre `http://localhost:8501`.

## Déploiement en ligne

### Option 1 — Streamlit Community Cloud (gratuit)

1. Pousser le dossier `svm_app/` (avec `seed_data/`) sur un dépôt GitHub privé.
2. Aller sur [share.streamlit.io](https://share.streamlit.io), connecter le dépôt.
3. App file = `app.py`. Déploiement automatique en 2 minutes.

> ⚠️ La base `svm.db` n'est pas persistée sur Streamlit Cloud entre redéploiements.
> Pour un usage en production, voir Option 2.

### Option 2 — VPS / Render / Fly.io (production)

Container Docker simple :

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Monter un volume sur `/app/svm.db` pour persister la base.

## Correspondance avec l'Excel

| Excel | App |
| --- | --- |
| Feuille `Transactions` | Table SQLite `transactions` |
| Feuille `Cours` | Table SQLite `prices` (long format) |
| Feuille `Table 4` | Table SQLite `quotes_today` (rafraîchie via scraper) |
| Feuille `LISTE` | Table SQLite `fcps` |
| Feuilles `FCP …` | Calculées à la demande par `portfolio.build_dashboard()` |
| Formules `SUMIFS` / `XLOOKUP` | Pandas vectorisé (millisecondes) |

La formule Excel `=IF(WEEKDAY(J2,2)=1, J2-3, J2-1)` (jour ouvré précédent) est
reproduite dans `portfolio.previous_business_date()`.

## Maintenance

- **Ajouter un FCP** : modifier `seed_data/fcps.json`, puis Paramètres → Réinitialiser.
- **Importer de nouvelles transactions en masse** : ajouter au CSV seed et
  réinitialiser, ou utiliser l'API `db.add_transaction()` dans un script.
- **Le scraper BRVM échoue** : page `🌐 Cours BRVM` → import CSV manuel.
