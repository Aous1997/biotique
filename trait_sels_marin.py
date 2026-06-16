"""
=============================================================
 Vent AROME Outre-Mer Guyane -> biotique.sels_marins_meteo
 Source : MeteoFetch (Meteo-France, sans cle API)

 OBJECTIF :
   1. Telecharger les dernieres previsions vent 10m AROME-OM
   2. Filtrer les points en mer (boite englobante maritime)
   3. Inserer directement dans biotique.sels_marins_meteo

 Pre-requis :
   pip install meteofetch xarray pandas numpy python-dotenv \\
               psycopg2-binary sqlalchemy

 Fichier .env (a cote du script) :
   PGHOST=localhost
   PGPORT=15432
   PGUSER=icare
   PGDATABASE=icare_3.2.3
   PGPASSWORD=ton_mot_de_passe
=============================================================
"""

import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# Chargement du .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    print("[OK] Fichier .env charge")
except ImportError:
    print("[!] python-dotenv non installe. Lance : pip install python-dotenv")

# =============================================================
# PARAMETRES
# =============================================================

DOSSIER_SORTIE = Path("./donnees_sels_marins")
DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)

# Metadonnees
NUMREG       = "03"
ANNEE_REF    = str(datetime.now().year)
HAUTEUR      = 10.0
SOURCE       = "Meteo-France AROME Outre-Mer Guyane (via MeteoFetch)"
VERSION      = datetime.now().strftime("%Y-%m-%d")
COMMENTAIRES = ("Vent a 10m issu du modele AROME Outre-Mer (resolution ~2.5 km). "
                "Filtre sur la zone maritime au large de la Guyane. "
                "Coordonnees WGS84.")

# Boite englobante MARITIME (Guyane)
LAT_MIN, LAT_MAX =  4.5,   8.0
LON_MIN, LON_MAX = -54.0, -50.5

# Sous-echantillonnage temporel (1 = toutes les heures)
PAS_TEMPOREL_HEURES = 1

# Table cible
SCHEMA = "biotique"
TABLE  = "sels_marins_meteo"

# Mode d'insertion
MODE_IMPORT = "replace"

# Connexion (lue depuis .env)
DB_HOST = os.environ.get("PGHOST",     "localhost")
DB_PORT = int(os.environ.get("PGPORT", "15432"))
DB_USER = os.environ.get("PGUSER",     "icare")
DB_NAME = os.environ.get("PGDATABASE", "icare_3.2.3")
DB_PASS = os.environ.get("PGPASSWORD", "")


# =============================================================
# 1. TELECHARGEMENT VIA METEOFETCH
# =============================================================
def telecharger_arome_guyane():
    """Telecharge les dernieres previsions AROME Outre-Mer Guyane."""
    try:
        from meteofetch import AromeOutreMerGuyane
    except ImportError:
        raise SystemExit(
            "meteofetch manquant. Lance : pip install meteofetch"
        )

    print("\nTelechargement AROME Outre-Mer Guyane...")
    print("(les dernieres previsions disponibles, jusqu'a 48h d'echeance)")

    datasets = AromeOutreMerGuyane.get_latest_forecast(
        paquet="SP1",
        variables=["u10", "v10"]
    )

    print("  OK  variables disponibles : {}".format(list(datasets.data_vars)))
    print("  OK  echeances : {} pas de temps".format(
        datasets.dims.get("time", "?")))
    return datasets


# =============================================================
# 2. CONVERSION EN DATAFRAME
# =============================================================
def netcdf_vers_dataframe(ds):
    """Convertit le Dataset xarray en DataFrame long."""
    print("\nConversion en DataFrame...")
    df = ds[["u10", "v10"]].to_dataframe().reset_index()

    rename_map = {"u10": "u", "v10": "v"}
    for nom_lat in ("latitude", "lat"):
        if nom_lat in df.columns:
            rename_map[nom_lat] = "latitude"
            break
    for nom_lon in ("longitude", "lon"):
        if nom_lon in df.columns:
            rename_map[nom_lon] = "longitude"
            break
    for nom_t in ("time", "valid_time"):
        if nom_t in df.columns:
            rename_map[nom_t] = "date"
            break

    df = df.rename(columns=rename_map)
    df = df[["latitude", "longitude", "date", "u", "v"]].dropna(subset=["u", "v"])

    # Normalisation longitudes 0-360 -> -180/+180 si besoin
    df.loc[df["longitude"] > 180, "longitude"] -= 360

    print("  OK  {:,} mesures extraites".format(len(df)))
    return df


# =============================================================
# 3. FILTRAGE GEOGRAPHIQUE + TEMPOREL
# =============================================================
def filtrer(df, lat_min, lat_max, lon_min, lon_max, pas_horaire):
    """Filtre boite englobante + sous-echantillonnage temporel."""
    n_avant = len(df)
    df = df[
        (df["latitude"] >= lat_min) & (df["latitude"] <= lat_max) &
        (df["longitude"] >= lon_min) & (df["longitude"] <= lon_max)
    ].copy()
    print("  Filtre geo  : {:,} -> {:,} points".format(n_avant, len(df)))

    if pas_horaire > 1:
        n_avant = len(df)
        df["heure"] = pd.to_datetime(df["date"]).dt.hour
        df = df[df["heure"] % pas_horaire == 0].drop(columns=["heure"])
        print("  Echant. {}h : {:,} -> {:,} points".format(
            pas_horaire, n_avant, len(df)))

    return df.reset_index(drop=True)


# =============================================================
# 4. FORMATAGE SCHEMA biotique.sels_marins_meteo
# =============================================================
def formater_table_cible(df):
    """Reformate au schema exact de la table cible."""
    df = df.reset_index(drop=True)

    out = pd.DataFrame({
        "gid":          (df.index + 1).astype("int32"),
        "numreg":       NUMREG,
        "latitude":     df["latitude"].astype(float),
        "longitude":    df["longitude"].astype(float),
        "u_m_par_sec":  df["u"].astype(float),
        "v_m_par_sec":  df["v"].astype(float),
        "hauteur_m":    HAUTEUR,
        "date":         pd.to_datetime(df["date"]),
        "annee_ref":    ANNEE_REF,
        "source":       SOURCE,
        "version":      VERSION,
        "commentaires": COMMENTAIRES,
    })

    out = out.dropna(subset=["u_m_par_sec", "v_m_par_sec", "date"])
    out = out.reset_index(drop=True)
    out["gid"] = (out.index + 1).astype("int32")
    return out


# =============================================================
# 5. INSERTION EN BASE
# =============================================================
def verifier_table(engine, schema, table):
    from sqlalchemy import text
    sql = text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :table
        )
    """)
    with engine.connect() as conn:
        return conn.execute(sql, {"schema": schema, "table": table}).scalar()


def inserer_en_base(df):
    """Insere dans biotique.sels_marins_meteo."""
    if MODE_IMPORT == "skip":
        print("\n[SKIP] MODE_IMPORT='skip' : pas d'insertion en base.")
        return

    if not DB_PASS:
        print("\n[ERREUR] Mot de passe manquant (PGPASSWORD).")
        return

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("\n[ERREUR] Lance : pip install sqlalchemy psycopg2-binary")
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

    if not verifier_table(engine, SCHEMA, TABLE):
        print("[ERREUR] La table {}.{} n'existe pas.".format(SCHEMA, TABLE))
        return

    if MODE_IMPORT == "replace":
        print("DELETE pour numreg='{}' annee_ref='{}' ...".format(
            NUMREG, ANNEE_REF))
        with engine.begin() as conn:
            res = conn.execute(text(
                "DELETE FROM {}.{} "
                "WHERE numreg = :reg AND annee_ref = :an".format(SCHEMA, TABLE)
            ), {"reg": NUMREG, "an": ANNEE_REF})
            print("  {} lignes supprimees".format(res.rowcount))

    # Decalage des gid pour eviter les conflits
    with engine.connect() as conn:
        max_gid = conn.execute(text(
            "SELECT COALESCE(MAX(gid), 0) FROM {}.{}".format(SCHEMA, TABLE)
        )).scalar()

    if max_gid > 0:
        print("  Decalage gid de +{} (table non vide)".format(max_gid))
        df = df.copy()
        df["gid"] = df["gid"] + max_gid

    print("Insertion de {:,} lignes ...".format(len(df)))
    try:
        df.to_sql(
            name=TABLE,
            con=engine,
            schema=SCHEMA,
            if_exists="append",
            index=False,
            chunksize=1000,
        )
    except Exception as e:
        print("[ERREUR] Insertion echouee : {}".format(e))
        return

    with engine.connect() as conn:
        stats = conn.execute(text("""
            SELECT COUNT(*) AS nb,
                   MIN(date) AS dmin,
                   MAX(date) AS dmax
            FROM {}.{}
            WHERE numreg = :reg AND annee_ref = :an
        """.format(SCHEMA, TABLE)),
        {"reg": NUMREG, "an": ANNEE_REF}).fetchone()

    print("\n=== RESULTAT BASE ===")
    print("  Lignes pour reg {} / an {} : {:,}".format(NUMREG, ANNEE_REF, stats[0]))
    print("  Periode                     : {} -> {}".format(stats[1], stats[2]))


# =============================================================
# EXECUTION
# =============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  Vent AROME-OM Guyane -> biotique.sels_marins_meteo")
    print("  Region    : {}".format(NUMREG))
    print("  Annee_ref : {}".format(ANNEE_REF))
    print("=" * 60)

    # 1. Telechargement
    ds = telecharger_arome_guyane()

    # 2. Conversion
    df = netcdf_vers_dataframe(ds)

    # 3. Filtrage
    df = filtrer(df, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, PAS_TEMPOREL_HEURES)

    if df.empty:
        print("\n[ERREUR] Aucune mesure apres filtrage.")
        sys.exit(1)

    # 4. Formatage
    out = formater_table_cible(df)
    print("\n[OK] {:,} mesures pretes pour insertion.".format(len(out)))

    # 5. Export CSV (archivage)
    f_csv = DOSSIER_SORTIE / "sels_marins_meteo_{}_{}.csv".format(NUMREG, ANNEE_REF)
    out.to_csv(f_csv, sep=";", index=False, encoding="utf-8",
               date_format="%Y-%m-%d %H:%M:%S")
    print("[FICHIER] CSV : {}".format(f_csv))

    # 6. Apercu + statistiques
    print("\nApercu :")
    print(out.head(5).to_string(index=False))

    vitesse = (out['u_m_par_sec']**2 + out['v_m_par_sec']**2)**0.5
    direction = (np.degrees(np.arctan2(-out['u_m_par_sec'], -out['v_m_par_sec'])) + 360) % 360
    print("\nStatistiques vent :")
    print("   Periode         : {} -> {}".format(out['date'].min(), out['date'].max()))
    print("   Vitesse moyenne : {:.2f} m/s".format(vitesse.mean()))
    print("   Vitesse max     : {:.2f} m/s".format(vitesse.max()))
    print("   Direction moy.  : {:.0f} deg".format(direction.mean()))
    print("   Points uniques  : {} (lat) x {} (lon)".format(
        out['latitude'].nunique(), out['longitude'].nunique()))

    # 7. Insertion en base
    inserer_en_base(out)

    print("\n" + "=" * 60)
    print("  TERMINE")
    print("=" * 60)