"""
=============================================================
 Traitement meteo -> table biotique.biotique_meteo
 Source : CSV bruts produits par telecharger_meteo.py

 OBJECTIF :
   1. Lire les CSV bruts de ./donnees_meteo_guyane/brut/
   2. Calculer T_kelvin, PAR, agregation par mediane
   3. Generer biotique_meteo.csv
   4. Inserer directement dans PostgreSQL (via .env)

 Pre-requis :
   pip install pandas python-dotenv psycopg2-binary sqlalchemy

 Fichier .env (a creer a cote du script) :
   PGHOST=localhost
   PGPORT=15432
   PGUSER=icare
   PGDATABASE=icare_3.2.3
   PGPASSWORD=ton_mot_de_passe

 Formules :
   - T_kelvin = T_celsius + 273.15
   - PAR (umol/m^2/s) = GLO (J/cm^2) x 0.454 x (10000/3600) x 2.02
 Agregation inter-stations : MEDIANE (robuste aux valeurs extremes)
=============================================================
"""

import os
import pandas as pd
from pathlib import Path
from datetime import datetime

# Chargement du .env a cote du script
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    print("[OK] Fichier .env charge")
except ImportError:
    print("[!] python-dotenv non installe. Lance : pip install python-dotenv")

# =============================================================
# PARAMETRES
# =============================================================

DOSSIER_BRUT   = Path("./donnees_meteo_guyane/brut")
DOSSIER_SORTIE = Path("./donnees_meteo_guyane")

# Metadonnees a ecrire dans la table
NUMREG       = "03"
NUMDEP       = "973"
SOURCE       = "Meteo-France (data.gouv.fr)"
VERSION      = datetime.now().strftime("%Y-%m-%d")
COMMENTAIRES = ("Heures en fuseau local (UTC-3). "
                "PAR=GLO*0.454*(10000/3600)*2.02. "
                "Agregation inter-stations par mediane.")

# Coefficient PAR (~ 2.5474)
COEF_PAR = 0.454 * (10000 / 3600) * 2.02

# Table cible
SCHEMA = "biotique"
TABLE  = "biotique_meteo"

# Mode d'insertion :
#   "replace" = DELETE du departement avant INSERT (re-import propre)
#   "append"  = ajoute aux donnees existantes (peut creer des doublons)
#   "skip"    = n'insere pas en base (produit seulement les CSV)
MODE_IMPORT = "skip"

# Parametres base (lus depuis .env avec valeurs par defaut)
DB_HOST = os.environ.get("PGHOST",     "localhost")
DB_PORT = int(os.environ.get("PGPORT", "15432"))
DB_USER = os.environ.get("PGUSER",     "icare")
DB_NAME = os.environ.get("PGDATABASE", "icare_3.2.3")
DB_PASS = os.environ.get("PGPASSWORD", "")


# =============================================================
# 1. LECTURE DES CSV BRUTS
# =============================================================
def lire_csv_bruts(dossier):
    """Lit tous les CSV du dossier brut."""
    csv_files = sorted(dossier.glob("*.csv"))
    if not csv_files:
        raise SystemExit(
            "Aucun CSV trouve dans {}.\n"
            "Lance d'abord : python telecharger_meteo.py".format(dossier)
        )

    dfs = []
    print("\nLecture des CSV bruts :")
    for f in csv_files:
        df = pd.read_csv(f, sep=";", low_memory=False)
        cols_cles = [c for c in ("T", "GLO", "NUM_POSTE", "AAAAMMJJHH")
                     if c in df.columns]
        print("  {}  ({:,} lignes, {} cols, cles : {})".format(
            f.name, len(df), len(df.columns), cols_cles))
        dfs.append(df)
    return dfs


# =============================================================
# 2. FUSION
# =============================================================
def fusionner(dfs):
    """Concatene puis dedoublonne sur (NUM_POSTE, AAAAMMJJHH)."""
    df_all = pd.concat(dfs, ignore_index=True, sort=False)
    if "NUM_POSTE" in df_all.columns and "AAAAMMJJHH" in df_all.columns:
        df_all = (
            df_all.sort_values(["NUM_POSTE", "AAAAMMJJHH"])
            .groupby(["NUM_POSTE", "AAAAMMJJHH"], as_index=False)
            .first()
        )
    return df_all


# =============================================================
# 3. CALCUL DES COLONNES
# =============================================================
def calculer_colonnes_biotique(df):
    """Transforme le DataFrame fusionne en colonnes pour biotique_meteo."""
    for col in ("NUM_POSTE", "AAAAMMJJHH"):
        if col not in df.columns:
            raise SystemExit("Colonne '{}' absente des donnees.".format(col))

    date_series = pd.to_datetime(
        df["AAAAMMJJHH"].astype(str), format="%Y%m%d%H", errors="coerce"
    )
    t_celsius = pd.to_numeric(df.get("T"), errors="coerce")
    t_kelvin  = t_celsius + 273.15
    rg        = pd.to_numeric(df.get("GLO"), errors="coerce")
    par       = rg * COEF_PAR

    out = pd.DataFrame({
        "num_poste":          df["NUM_POSTE"].values,
        "numreg":             NUMREG,
        "numdep":             NUMDEP,
        "date":               date_series,
        "t_ambiante_celsius": t_celsius,
        "t_ambiante_kelvin":  t_kelvin,
        "rg":                 rg,
        "unite_rg":           "J/cm2",
        "par":                par,
        "unite_par":          "umol/m2/s",
        "source":             SOURCE,
        "version":            VERSION,
        "commentaires":       COMMENTAIRES,
    })

    out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return out


# =============================================================
# 4. EXPORTS CSV
# =============================================================
def exporter_csv(out, dossier):
    """Produit 2 CSV : par station + agrege departement."""

    # Par station
    f_station = dossier / "biotique_meteo_par_station.csv"
    out.to_csv(f_station, sep=";", index=False, encoding="utf-8",
               date_format="%Y-%m-%d %H:%M:%S")
    print("[FICHIER] Par station   : {}  ({:,} lignes)".format(
        f_station, len(out)))

    # Agrege departement par MEDIANE
    cols_agg = ["t_ambiante_celsius", "t_ambiante_kelvin", "rg", "par"]
    out_dep = out.groupby("date", as_index=False)[cols_agg].median()

    out_dep["numreg"]       = NUMREG
    out_dep["numdep"]       = NUMDEP
    out_dep["unite_rg"]     = "J/cm2"
    out_dep["unite_par"]    = "umol/m2/s"
    out_dep["source"]       = SOURCE
    out_dep["version"]      = VERSION
    out_dep["commentaires"] = COMMENTAIRES

    ordre = [
        "numreg", "numdep", "date",
        "t_ambiante_celsius", "t_ambiante_kelvin",
        "rg", "unite_rg",
        "par", "unite_par",
        "source", "version", "commentaires",
    ]
    out_dep = out_dep[ordre]

    f_table = dossier / "biotique_meteo.csv"
    out_dep.to_csv(f_table, sep=";", index=False, encoding="utf-8",
                   date_format="%Y-%m-%d %H:%M:%S")
    print("[FICHIER] Pour INSERT   : {}  ({:,} lignes)".format(
        f_table, len(out_dep)))

    return out_dep


# =============================================================
# 5. INSERTION EN BASE POSTGRESQL
# =============================================================
def inserer_en_base(out_dep):
    """Insere directement le DataFrame dans la table PostgreSQL."""
    if MODE_IMPORT == "skip":
        print("\n[SKIP] MODE_IMPORT='skip' : pas d'insertion en base.")
        return

    if not DB_PASS:
        print("\n[ERREUR] Mot de passe manquant (variable PGPASSWORD).")
        print("         Cree un fichier .env avec PGPASSWORD=... a cote du script.")
        return

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("\n[ERREUR] sqlalchemy manquant.")
        print("         Lance : pip install sqlalchemy psycopg2-binary")
        return

    url = "postgresql+psycopg2://{u}:{p}@{h}:{po}/{db}".format(
        u=DB_USER, p=DB_PASS, h=DB_HOST, po=DB_PORT, db=DB_NAME
    )

    print("\n" + "=" * 60)
    print("  Insertion en base")
    print("  Connexion : {}@{}:{}/{}".format(DB_USER, DB_HOST, DB_PORT, DB_NAME))
    print("  Table     : {}.{}".format(SCHEMA, TABLE))
    print("  Mode      : {}".format(MODE_IMPORT))
    print("=" * 60)

    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar()
            print("Connecte : {}".format(version[:60]))
    except Exception as e:
        print("[ERREUR] Connexion impossible : {}".format(e))
        return

    # Mode replace : supprime les donnees existantes du departement
    if MODE_IMPORT == "replace":
        print("DELETE des donnees existantes pour numdep='{}' ...".format(NUMDEP))
        with engine.begin() as conn:
            res = conn.execute(text(
                "DELETE FROM {}.{} WHERE numdep = :dep".format(SCHEMA, TABLE)
            ), {"dep": NUMDEP})
            print("  {} lignes supprimees".format(res.rowcount))

    # Insertion via pandas.to_sql
    print("Insertion de {:,} lignes ...".format(len(out_dep)))
    out_dep.to_sql(
        name=TABLE,
        con=engine,
        schema=SCHEMA,
        if_exists="append",   # la table existe deja
        index=False,
        chunksize=1000,
    )

    # Verification
    with engine.connect() as conn:
        stats = conn.execute(text("""
            SELECT COUNT(*) AS nb,
                   MIN(date) AS dmin,
                   MAX(date) AS dmax
            FROM {}.{}
            WHERE numdep = :dep
        """.format(SCHEMA, TABLE)), {"dep": NUMDEP}).fetchone()

    print("\n=== RESULTAT BASE ===")
    print("  Lignes pour dep {} : {:,}".format(NUMDEP, stats[0]))
    print("  Periode            : {} -> {}".format(stats[1], stats[2]))


# =============================================================
# EXECUTION
# =============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  Traitement meteo Guyane -> biotique.biotique_meteo")
    print("=" * 60)

    dfs = lire_csv_bruts(DOSSIER_BRUT)
    df = fusionner(dfs)
    print("\n[OK] {:,} lignes apres fusion.\n".format(len(df)))

    out = calculer_colonnes_biotique(df)
    out_dep = exporter_csv(out, DOSSIER_SORTIE)

    print("\nApercu (moyenne departementale) :")
    print(out_dep.head(10).to_string(index=False))

    print("\nStatistiques :")
    print("   Periode    : {} -> {}".format(out_dep['date'].min(), out_dep['date'].max()))
    print("   T mediane  : {:.2f} C".format(out_dep['t_ambiante_celsius'].median()))
    print("   RG median  : {:.2f} J/cm2".format(out_dep['rg'].median()))
    print("   PAR median : {:.2f} umol/m2/s".format(out_dep['par'].median()))
    print("   Coef PAR   : {:.4f}".format(COEF_PAR))

    inserer_en_base(out_dep)

    print("\n" + "=" * 60)
    print("  TERMINE")
    print("=" * 60)