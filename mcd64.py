"""
=============================================================
  MCD64A1 — Surface Brûlée Guyane Française
  Téléchargement SFTP + Extraction + Clip + Fusion
  Source : sftp://fuoco.geog.umd.edu (login public)
=============================================================
Lancement :
    pip install paramiko geopandas shapely pandas tqdm requests
    python mcd64_guyane.py

Configuration :
    Modifier YEAR_START et YEAR_END ci-dessous
    Modifier MONTHS pour filtrer certains mois (None = tous)

Sorties dans ./outputs/mcd64/ :
    mcd64_guyane_YYYY_MM.shp     (fichiers mensuels decoupes en EPSG:4326)
    mcd64_guyane_complet.gpkg    (fusion EPSG:4326, pour QGIS)
    mcd64_guyane_complet.geojson (pour dashboards web)
    mcd64_guyane_postgis.gpkg    (EPSG:2972, MultiPolygon, pret pour ogr2ogr)
    import_postgis.bat           (Windows : lance ogr2ogr)
    import_postgis.sh            (Linux/Mac)
    mcd64_guyane_resume_annuel.csv

Table cible :
    CREATE TABLE biotique.mcd64_guyane_complet (
        id serial4 NOT NULL,
        geom public.geometry(multipolygon, 2972) NULL,
        "BurnDate" int2 NULL,
        "year" int2 NULL,
        "month" int2 NULL,
        "period" varchar NULL,
        area_ha numeric NULL,
        CONSTRAINT mcd64_guyane_complet_pkey PRIMARY KEY (id)
    );
=============================================================
"""

import os, tarfile, shutil, time, math
import paramiko
import requests
import pandas as pd
import geopandas as gpd
from pathlib import Path
from tqdm import tqdm
from shapely.geometry import MultiPolygon, Polygon

# ─────────────────────────────────────────────
# CONFIGURATION — À MODIFIER
# ─────────────────────────────────────────────

YEAR_START = 2010        # Année de début
YEAR_END   = 2023        # Année de fin (incluse)

# Mois à télécharger (1=Jan ... 12=Dec)
# None = tous les mois
# Exemple saison sèche : MONTHS = [7, 8, 9, 10, 11]
MONTHS = None            # None = tous les mois

# ─────────────────────────────────────────────
# PARAMÈTRES SFTP (identifiants publics du guide MCD64)
# ─────────────────────────────────────────────
SFTP_HOST   = "fuoco.geog.umd.edu"
SFTP_USER   = "fire"
SFTP_PASS   = "burnt"
SFTP_BASE   = "/data/MODIS/C6/MCD64A1/SHP/Win05"

# URL contour Guyane (IGN via france-geojson, open data)
CONTOUR_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson"
    "/master/regions/guyane/departements-guyane.geojson"
)

# Schéma et table cibles pour la sortie SQL
SQL_SCHEMA = "biotique"
SQL_TABLE  = "mcd64_guyane_complet"
SQL_SRID   = 2972   # CRS de la colonne geom

# Dossiers
DATA_DIR = Path("./data/mcd64_raw")     # fichiers bruts téléchargés
EXTRACT_DIR = Path("./data/mcd64_shp") # shapefiles extraits
OUT_DIR  = Path("./outputs/mcd64")
for d in [DATA_DIR, EXTRACT_DIR, OUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────

DOY_TO_MONTH = {
    1:1, 32:2, 60:3, 61:3,
    91:4, 92:4, 121:5, 122:5,
    152:6, 153:6, 182:7, 183:7,
    213:8, 214:8, 244:9, 245:9,
    274:10, 275:10, 305:11, 306:11,
    335:12, 336:12
}

def doy_to_month(doy):
    return DOY_TO_MONTH.get(int(doy), None)

def parse_filename(fname):
    try:
        parts = fname.split(".")
        date_part = parts[1]
        year = int(date_part[1:5])
        doy  = int(date_part[5:])
        month = doy_to_month(doy)
        return year, month
    except Exception:
        return None, None

def force_multipolygon(geom):
    """Convertit un Polygon en MultiPolygon. Retourne None si geometrie invalide."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    return geom

# ─────────────────────────────────────────────
# 1. CONTOUR GUYANE
# ─────────────────────────────────────────────
def get_contour_guyane():
    local = DATA_DIR / "contour_guyane.geojson"
    if not local.exists():
        print("  Téléchargement contour Guyane (IGN/france-geojson)...")
        r = requests.get(CONTOUR_URL, timeout=30)
        r.raise_for_status()
        local.write_bytes(r.content)
        print(f"  OK ({local.stat().st_size/1024:.0f} Ko)")

    gdf = gpd.read_file(local)
    gdf = gdf.to_crs("EPSG:4326")
    print(f"  Contour chargé : {len(gdf)} entité(s)")
    return gdf

# ─────────────────────────────────────────────
# 2. CONNEXION SFTP
# ─────────────────────────────────────────────
def connect_sftp():
    print(f"  Connexion SFTP → {SFTP_HOST}...")
    transport = paramiko.Transport((SFTP_HOST, 22))
    transport.connect(username=SFTP_USER, password=SFTP_PASS)
    sftp = paramiko.SFTPClient.from_transport(transport)
    print("  Connecté.")
    return transport, sftp

# ─────────────────────────────────────────────
# 3. LISTE DES FICHIERS DISPONIBLES
# ─────────────────────────────────────────────
def list_remote_files(sftp, years, months_filter):
    to_download = []
    for year in years:
        remote_dir = f"{SFTP_BASE}/{year}"
        try:
            files = sftp.listdir(remote_dir)
        except FileNotFoundError:
            print(f"  [{year}] Dossier inexistant sur le serveur")
            continue

        tar_files = [f for f in files
                     if f.endswith(".tar.gz") and "burndate" in f]

        for fname in sorted(tar_files):
            yr, mo = parse_filename(fname)
            if yr is None:
                continue
            if months_filter and mo not in months_filter:
                continue
            to_download.append({
                "remote_path": f"{remote_dir}/{fname}",
                "local_path":  DATA_DIR / fname,
                "year": yr,
                "month": mo,
                "fname": fname,
            })
    print(f"  {len(to_download)} fichiers à télécharger")
    return to_download

# ─────────────────────────────────────────────
# 4. TÉLÉCHARGEMENT
# ─────────────────────────────────────────────
def download_files(sftp, file_list):
    downloaded = []
    for item in tqdm(file_list, desc="Téléchargement"):
        local = item["local_path"]
        if local.exists() and local.stat().st_size > 1000:
            downloaded.append(item)
            continue
        try:
            attr = sftp.stat(item["remote_path"])
            size = attr.st_size
            with tqdm(total=size, unit="B", unit_scale=True,
                      desc=f"  {item['fname'][:40]}", leave=False) as bar:
                def callback(transferred, total, b=bar):
                    b.update(transferred - b.n)
                sftp.get(item["remote_path"], str(local), callback=callback)
            downloaded.append(item)
            time.sleep(0.2)
        except Exception as e:
            print(f"  Erreur {item['fname']} : {e}")
    return downloaded

# ─────────────────────────────────────────────
# 5. EXTRACTION .tar.gz → .shp
# ─────────────────────────────────────────────
def extract_shapefiles(file_list):
    shp_files = []
    for item in tqdm(file_list, desc="Extraction"):
        tar_path = item["local_path"]
        year     = item["year"]
        month    = item["month"]
        out_subdir = EXTRACT_DIR / f"{year}_{month:02d}"
        shp_target = out_subdir / f"mcd64_{year}_{month:02d}.shp"

        if shp_target.exists():
            shp_files.append({**item, "shp_path": shp_target})
            continue
        out_subdir.mkdir(exist_ok=True)

        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                for member in tar.getmembers():
                    ext = Path(member.name).suffix.lower()
                    if ext in [".shp",".shx",".dbf",".prj",".cpg"]:
                        member.name = Path(member.name).name
                        tar.extract(member, path=out_subdir)
                shp_found = list(out_subdir.glob("*.shp"))
                if shp_found:
                    src = shp_found[0]
                    for f in out_subdir.glob(src.stem + ".*"):
                        new_name = out_subdir / f"mcd64_{year}_{month:02d}{f.suffix}"
                        f.rename(new_name)
                    shp_files.append({**item, "shp_path": shp_target})
        except Exception as e:
            print(f"  Erreur extraction {tar_path.name} : {e}")

    print(f"  {len(shp_files)} shapefiles extraits")
    return shp_files

# ─────────────────────────────────────────────
# 6. CLIP SUR GUYANE + FUSION
# ─────────────────────────────────────────────
def clip_and_merge(shp_files, contour_guyane):
    gdfs = []

    for item in tqdm(shp_files, desc="Clip + fusion"):
        shp_path = item["shp_path"]
        year     = item["year"]
        month    = item["month"]
        clip_path = OUT_DIR / f"mcd64_guyane_{year}_{month:02d}.shp"

        if clip_path.exists():
            try:
                gdf_clip = gpd.read_file(clip_path)
                if len(gdf_clip) > 0:
                    gdfs.append(gdf_clip)
                    continue
            except Exception:
                pass

        try:
            gdf = gpd.read_file(shp_path)
            if gdf.empty or gdf.geometry.is_empty.all():
                continue

            gdf = gdf.to_crs("EPSG:4326")
            gdf_clip = gpd.clip(gdf, contour_guyane)
            if gdf_clip.empty:
                continue

            gdf_clip["year"]  = year
            gdf_clip["month"] = month
            gdf_clip["period"] = f"{year}-{month:02d}"

            gdf_clip.to_file(clip_path)
            gdfs.append(gdf_clip)

        except Exception as e:
            print(f"  Erreur clip {shp_path.name} : {e}")
            continue

    if not gdfs:
        print("  Aucune donnée après découpe.")
        return gpd.GeoDataFrame()

    print(f"  Fusion de {len(gdfs)} couches mensuelles...")
    merged = gpd.GeoDataFrame(
        pd.concat(gdfs, ignore_index=True),
        crs="EPSG:4326"
    )

    # Renommage MCD64 : la colonne d'origine peut etre "DN" ou "BurnDate"
    if "DN" in merged.columns and "BurnDate" not in merged.columns:
        merged = merged.rename(columns={"DN": "BurnDate"})

    # Calculer la surface réelle en ha (en projetant en UTM22N)
    merged_utm = merged.to_crs("EPSG:2972")
    merged["area_ha"] = (merged_utm.geometry.area / 10000).round(2)

    total_ha = merged["area_ha"].sum()
    print(f"  Surface brûlée totale : {total_ha:,.0f} ha")
    print(f"  Polygones : {len(merged):,}")

    return merged

# ─────────────────────────────────────────────
# 7. EXPORT FINAL (GeoPackage + GeoJSON + SQL)
# ─────────────────────────────────────────────
def export_final(gdf, out_dir):
    """Export GeoPackage + GeoJSON + SQL du fichier fusionné."""

    def safe_write(path, write_fn, label):
        """
        Execute une fonction d'ecriture en capturant les PermissionError.
        Affiche un message clair si le fichier est verrouille par QGIS/editeur.
        """
        try:
            write_fn()
            size = path.stat().st_size
            unit = "Mo" if size > 1024 * 1024 else "Ko"
            val = size / 1024 / 1024 if unit == "Mo" else size / 1024
            print(f"  OK {path.name}  ({val:.1f} {unit})")
            return True
        except PermissionError:
            print(f"  /!\\ {path.name} est verrouille (ouvert dans QGIS ou un editeur ?)")
            print(f"      -> Ferme l'application, puis relance le script.")
            print(f"      -> L'export continue avec les autres fichiers.")
            return False
        except Exception as e:
            print(f"  /!\\ Erreur sur {path.name} : {e}")
            return False

    # GeoPackage (EPSG:4326, comme avant — utile pour QGIS)
    gpkg_path = out_dir / "mcd64_guyane_complet.gpkg"
    safe_write(
        gpkg_path,
        lambda: gdf.to_file(gpkg_path, driver="GPKG", layer="surfaces_brulees"),
        "GeoPackage 4326"
    )

    # GeoJSON
    geojson_path = out_dir / "mcd64_guyane_complet.geojson"
    safe_write(
        geojson_path,
        lambda: gdf.to_file(geojson_path, driver="GeoJSON"),
        "GeoJSON"
    )

    # CSV résumé par année
    if "year" in gdf.columns and "area_ha" in gdf.columns:
        resume = (gdf.groupby("year")
                     .agg(n_polygones=("area_ha","count"),
                          area_ha_total=("area_ha","sum"))
                     .reset_index()
                     .round({"area_ha_total":1}))
        csv_path = out_dir / "mcd64_guyane_resume_annuel.csv"
        safe_write(
            csv_path,
            lambda: resume.to_csv(csv_path, index=False),
            "CSV resume"
        )
        print("\n  Résumé annuel :")
        print(resume.to_string(index=False))

    # ═══════════════════════════════════════════════════════
    # Export GeoPackage en EPSG:2972 + commande ogr2ogr
    # ═══════════════════════════════════════════════════════
    export_for_postgis(gdf, out_dir)


def export_for_postgis(gdf, out_dir):
    """
    Prepare un GeoPackage aligne sur le schema de la table PostGIS
    (EPSG:2972, MultiPolygon, colonnes BurnDate/year/month/period/area_ha)
    et genere un script .bat + .sh pour l'import via ogr2ogr.
    """
    print("\n  Preparation pour import PostGIS...")

    # Copie pour ne pas modifier le gdf d'origine
    gdf_pg = gdf.copy()

    # 1. Reprojection vers EPSG:2972
    if gdf_pg.crs is None or gdf_pg.crs.to_epsg() != SQL_SRID:
        gdf_pg = gdf_pg.to_crs(f"EPSG:{SQL_SRID}")

    # 2. Conversion en MultiPolygon (contrainte de la table)
    gdf_pg["geometry"] = gdf_pg.geometry.apply(force_multipolygon)
    gdf_pg = gdf_pg[gdf_pg.geometry.notna()]

    # 3. Ne garder QUE les colonnes presentes dans la table cible
    cols_cibles = ["BurnDate", "year", "month", "period", "area_ha", "geometry"]
    cols_dispo = [c for c in cols_cibles if c in gdf_pg.columns]
    gdf_pg = gdf_pg[cols_dispo]

    # 4. Ecrire le GeoPackage dedie a l'import PostGIS
    gpkg_postgis = out_dir / "mcd64_guyane_postgis.gpkg"
    try:
        gdf_pg.to_file(gpkg_postgis, driver="GPKG", layer=SQL_TABLE)
        print(f"  OK {gpkg_postgis.name}  ({gpkg_postgis.stat().st_size/1024/1024:.1f} Mo, EPSG:{SQL_SRID})")
    except PermissionError:
        print(f"  /!\\ {gpkg_postgis.name} verrouille (ouvert dans QGIS ?)")
        print(f"      Ferme QGIS puis relance le script.")
        return
    except Exception as e:
        print(f"  /!\\ Erreur sur {gpkg_postgis.name} : {e}")
        return

    # 5. Generer les scripts d'import (Windows .bat + Linux/Mac .sh)
    gpkg_abs = gpkg_postgis.resolve()

    bat_content = f"""@echo off
REM ============================================================
REM Import MCD64 Guyane dans PostGIS via ogr2ogr
REM Table : {SQL_SCHEMA}.{SQL_TABLE}
REM ============================================================

REM >> ADAPTER ces variables avant lancement <<
set PGHOST=localhost
set PGPORT=15432
set PGUSER=icare
set PGDATABASE=icare_3.2.3
REM Le mot de passe sera demande interactivement (ou mettre dans %APPDATA%\\postgresql\\pgpass.conf)

REM (Optionnel) Vider la table avant import complet :
REM psql -h %PGHOST% -p %PGPORT% -U %PGUSER% -d %PGDATABASE% -c "TRUNCATE {SQL_SCHEMA}.{SQL_TABLE} RESTART IDENTITY;"

ogr2ogr ^
  -f PostgreSQL ^
  PG:"host=%PGHOST% port=%PGPORT% user=%PGUSER% dbname=%PGDATABASE%" ^
  "{gpkg_abs}" ^
  -nln {SQL_SCHEMA}.{SQL_TABLE} ^
  -append ^
  -lco GEOMETRY_NAME=geom ^
  -nlt MULTIPOLYGON ^
  -t_srs EPSG:{SQL_SRID}

echo.
echo Import termine. Verification :
psql -h %PGHOST% -p %PGPORT% -U %PGUSER% -d %PGDATABASE% -c ^
  "SELECT COUNT(*) AS nb, SUM(area_ha)::numeric(12,2) AS ha, MIN(year) AS y1, MAX(year) AS y2 FROM {SQL_SCHEMA}.{SQL_TABLE};"

pause
"""
    bat_path = out_dir / "import_postgis.bat"
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"  OK {bat_path.name}  (Windows)")

    # Version Linux/Mac
    sh_content = f"""#!/bin/bash
# ============================================================
# Import MCD64 Guyane dans PostGIS via ogr2ogr
# Table : {SQL_SCHEMA}.{SQL_TABLE}
# ============================================================

PGHOST=localhost
PGPORT=15432
PGUSER=icare
PGDATABASE=icare_3.2.3

# (Optionnel) Vider la table avant import complet :
# psql -h $PGHOST -p $PGPORT -U $PGUSER -d $PGDATABASE \\
#   -c "TRUNCATE {SQL_SCHEMA}.{SQL_TABLE} RESTART IDENTITY;"

ogr2ogr \\
  -f PostgreSQL \\
  PG:"host=$PGHOST port=$PGPORT user=$PGUSER dbname=$PGDATABASE" \\
  "{gpkg_abs}" \\
  -nln {SQL_SCHEMA}.{SQL_TABLE} \\
  -append \\
  -lco GEOMETRY_NAME=geom \\
  -nlt MULTIPOLYGON \\
  -t_srs EPSG:{SQL_SRID}

psql -h $PGHOST -p $PGPORT -U $PGUSER -d $PGDATABASE \\
  -c "SELECT COUNT(*), SUM(area_ha)::numeric(12,2), MIN(year), MAX(year) FROM {SQL_SCHEMA}.{SQL_TABLE};"
"""
    sh_path = out_dir / "import_postgis.sh"
    sh_path.write_text(sh_content, encoding="utf-8")
    print(f"  OK {sh_path.name}  (Linux/Mac)")

# ─────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────
def run():
    print("=" * 60)
    print("  MCD64A1 — Surface Brûlée Guyane Française")
    print(f"  Années   : {YEAR_START} → {YEAR_END}")
    mois_str = str(MONTHS) if MONTHS else "tous"
    print(f"  Mois     : {mois_str}")
    print("=" * 60)

    years = list(range(YEAR_START, YEAR_END + 1))
    months_filter = MONTHS

    print("\n>> Contour Guyane...")
    contour = get_contour_guyane()

    print("\n>> Connexion SFTP...")
    try:
        transport, sftp = connect_sftp()
    except Exception as e:
        print(f"  Connexion impossible : {e}")
        print("  Vérifie ta connexion Internet / pare-feu port 22.")
        return

    try:
        print("\n>> Listage des fichiers disponibles...")
        file_list = list_remote_files(sftp, years, months_filter)
        if not file_list:
            print("  Aucun fichier trouvé pour cette plage.")
            return

        print(f"\n>> Téléchargement ({len(file_list)} fichiers)...")
        downloaded = download_files(sftp, file_list)

    finally:
        sftp.close()
        transport.close()
        print("  SFTP déconnecté.")

    if not downloaded:
        print("  Aucun fichier téléchargé.")
        return

    print(f"\n>> Extraction des shapefiles...")
    shp_files = extract_shapefiles(downloaded)
    if not shp_files:
        print("  Aucun shapefile extrait.")
        return

    print(f"\n>> Découpe sur Guyane + fusion...")
    gdf_final = clip_and_merge(shp_files, contour)
    if gdf_final.empty:
        print("  Aucun polygone dans l'emprise Guyane.")
        return

    print(f"\n>> Export final...")
    export_final(gdf_final, OUT_DIR)

    print("\n" + "=" * 60)
    print("  TERMINÉ")
    print("=" * 60)
    print(f"\n  Fichiers dans : {OUT_DIR.resolve()}")
    print("\n  Import QGIS :")
    print("    Glisser mcd64_guyane_complet.gpkg dans QGIS (EPSG:4326)")
    print("\n  Import PostGIS via ogr2ogr :")
    print(f"    Double-clic sur : {(OUT_DIR / 'import_postgis.bat').resolve()}")
    print("    (apres avoir adapte les variables PGHOST/PGPORT/PGUSER/PGDATABASE)")
    print("\n  Ou en ligne de commande :")
    print(f'    ogr2ogr -f PostgreSQL PG:"host=localhost port=15432 user=icare dbname=icare_3.2.3" \\')
    print(f'      "{(OUT_DIR / "mcd64_guyane_postgis.gpkg").resolve()}" \\')
    print(f'      -nln {SQL_SCHEMA}.{SQL_TABLE} -append \\')
    print(f'      -lco GEOMETRY_NAME=geom -nlt MULTIPOLYGON -t_srs EPSG:{SQL_SRID}')
    print("\n  Colonnes de la table :")
    print("    BurnDate (int2)  : jour julien de la combustion (1-366)")
    print("    year     (int2)  : année")
    print("    month    (int2)  : mois")
    print("    period   (varchar): YYYY-MM")
    print("    area_ha  (numeric): surface en hectares (calculée en UTM22N)")
    print("    geom (multipolygon, 2972) : géométrie reprojetée en UTM22N")


if __name__ == "__main__":
    missing = []
    for pkg in ["paramiko","geopandas","shapely","pandas","tqdm","requests"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Packages manquants : {', '.join(missing)}")
        print(f"Installer avec : pip install {' '.join(missing)}")
    else:
        run()