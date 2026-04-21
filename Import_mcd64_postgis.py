

import os
import sys
import geopandas as gpd
from sqlalchemy import create_engine, text
from pathlib import Path
from dotenv import load_dotenv

# =============================================================
# CONFIGURATION — A ADAPTER
# =============================================================

load_dotenv(Path(__file__).parent/".env")
DB_HOST = os.environ.get("PGHOST","localhost")
DB_PORT = int(os.environ.get("PGPORT","5432"))
DB_USER = os.environ.get("PGUSER")
DB_PASS = os.environ.get("PGPASSWORD")
DB_NAME = os.environ.get("PGDATABASE")

# Mot de passe : laisse vide ici, le script le demandera,
# ou mets-le ici, ou utilise la variable d'environnement PGPASSWORD
DB_PASS = os.environ.get("PGPASSWORD", "")

# Table cible
SCHEMA = "biotique"
TABLE  = "mcd64_guyane_complet"

# Fichier GeoPackage a importer (produit par mcd64_guyane.py)
GPKG_PATH = Path("./outputs/mcd64/mcd64_guyane_postgis.gpkg")

# Mode :
#   "replace" = TRUNCATE la table avant import
#   "append"  = ajoute aux donnees existantes
MODE = "replace"


# =============================================================
# IMPORT
# =============================================================
def main():
    global DB_PASS

    # 1. Verif fichier
    if not GPKG_PATH.exists():
        print("[ERREUR] Fichier introuvable : {}".format(GPKG_PATH.resolve()))
        print("         Lance d'abord mcd64_guyane.py pour le generer.")
        sys.exit(1)

    # 2. Mot de passe interactif si vide
    if not DB_PASS:
        import getpass
        DB_PASS = getpass.getpass("Mot de passe pour {} : ".format(DB_USER))

    # 3. Lecture du GeoPackage
    print("Lecture de {} ...".format(GPKG_PATH))
    gdf = gpd.read_file(GPKG_PATH)
    print("  {:,} polygones, CRS = {}".format(len(gdf), gdf.crs))
    if gdf.crs is None or gdf.crs.to_epsg() != 2972:
        print("  Reprojection vers EPSG:2972 ...")
        gdf = gdf.to_crs("EPSG:2972")

    # 5. Ne garder que les colonnes de la table
    cols_cibles = {"BurnDate", "year", "month", "period", "area_ha", "geometry"}
    cols_presentes = set(gdf.columns)
    manquantes = cols_cibles - cols_presentes
    if manquantes:
        print("  [!] Colonnes manquantes dans le GPKG : {}".format(manquantes))
    gdf = gdf[[c for c in cols_cibles if c in cols_presentes]]

    # 6. Connexion
    url = "postgresql+psycopg2://{u}:{p}@{h}:{po}/{db}".format(
        u=DB_USER, p=DB_PASS, h=DB_HOST, po=DB_PORT, db=DB_NAME
    )
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar()
            print("Connecte : {}".format(version[:60]))
    except Exception as e:
        print("[ERREUR] Connexion impossible : {}".format(e))
        sys.exit(1)

    # 7. Mode replace : vider la table
    if MODE == "replace":
        print("TRUNCATE {}.{} ...".format(SCHEMA, TABLE))
        with engine.begin() as conn:
            conn.execute(text(
                "TRUNCATE {}.{} RESTART IDENTITY".format(SCHEMA, TABLE)
            ))

    # 8. Import via geopandas.to_postgis()
    # IMPORTANT : la colonne geometrique doit s'appeler "geom" dans la cible,
    # mais geopandas l'ecrit toujours sous le nom actuel ("geometry").
    # On renomme donc AVANT d'appeler to_postgis.
    gdf = gdf.rename_geometry("geom")

    print("Insertion dans {}.{} ...".format(SCHEMA, TABLE))
    gdf.to_postgis(
        name=TABLE,
        con=engine,
        schema=SCHEMA,
        if_exists="append",   # la table existe deja, on ajoute dedans
        index=False,
        chunksize=1000,       # envoi par paquets de 1000 lignes
    )

    # 9. Verification
    with engine.connect() as conn:
        stats = conn.execute(text("""
            SELECT COUNT(*) AS nb,
                   ROUND(SUM(area_ha)::numeric, 2) AS ha,
                   MIN(year) AS y1,
                   MAX(year) AS y2
            FROM {}.{}
        """.format(SCHEMA, TABLE))).fetchone()

    print("\n=== RESULTAT ===")
    print("  Lignes : {:,}".format(stats[0]))
    print("  Surface totale : {} ha".format(stats[1]))
    print("  Periode : {} - {}".format(stats[2], stats[3]))


if __name__ == "__main__":
    # Verification dependances
    missing = []
    for pkg, imp in [("psycopg2-binary", "psycopg2"),
                     ("sqlalchemy", "sqlalchemy"),
                     ("geoalchemy2", "geoalchemy2"),
                     ("geopandas", "geopandas")]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Packages manquants : " + ", ".join(missing))
        print("Installer avec : pip install " + " ".join(missing))
        sys.exit(1)
    main()