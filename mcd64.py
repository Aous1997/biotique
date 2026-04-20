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
    mcd64_guyane_YYYY_MM.shp  (fichiers mensuels découpés)
    mcd64_guyane_complet.gpkg (fusion toutes années/mois)
    mcd64_guyane_complet.geojson
=============================================================
"""

import os, tarfile, shutil, time, math
import paramiko
import requests
import pandas as pd
import geopandas as gpd
from pathlib import Path
from tqdm import tqdm

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

# Dossiers
DATA_DIR = Path("./data/mcd64_raw")     # fichiers bruts téléchargés
EXTRACT_DIR = Path("./data/mcd64_shp") # shapefiles extraits
OUT_DIR  = Path("./outputs/mcd64")
for d in [DATA_DIR, EXTRACT_DIR, OUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────

# Correspondance DOY → mois (pour nommage fichiers)
# MCD64monthly.AYYYYDDD → DOY 1=jan, 32=fev, 60=mar ...
DOY_TO_MONTH = {
    1:1, 32:2, 60:3, 61:3,    # mars selon année bissextile
    91:4, 92:4, 121:5, 122:5,
    152:6, 153:6, 182:7, 183:7,
    213:8, 214:8, 244:9, 245:9,
    274:10, 275:10, 305:11, 306:11,
    335:12, 336:12
}

def doy_to_month(doy):
    """Retourne le mois à partir du DOY du nom de fichier MCD64."""
    return DOY_TO_MONTH.get(int(doy), None)

def parse_filename(fname):
    """
    Extrait année et mois depuis :
    MCD64monthly.A2016213.Win05.006.burndate.shapefiles.tar.gz
    → year=2016, month=8
    """
    try:
        parts = fname.split(".")
        date_part = parts[1]          # A2016213
        year = int(date_part[1:5])    # 2016
        doy  = int(date_part[5:])     # 213
        month = doy_to_month(doy)
        return year, month
    except Exception:
        return None, None

# ─────────────────────────────────────────────
# 1. CONTOUR GUYANE
# ─────────────────────────────────────────────
def get_contour_guyane():
    """
    Télécharge le contour de la Guyane française depuis france-geojson (IGN).
    Retourne un GeoDataFrame en EPSG:4326.
    """
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
    """
    Ouvre une connexion SFTP vers fuoco.geog.umd.edu.
    Retourne (transport, sftp_client).
    """
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
    """
    Parcourt les dossiers SFTP et retourne la liste des fichiers
    .tar.gz à télécharger selon la plage années/mois.
    """
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
    """Télécharge les fichiers .tar.gz depuis le serveur SFTP."""
    downloaded = []
    for item in tqdm(file_list, desc="Téléchargement"):
        local = item["local_path"]

        # Déjà téléchargé ?
        if local.exists() and local.stat().st_size > 1000:
            downloaded.append(item)
            continue

        try:
            # Récupérer la taille pour la barre de progression
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
    """
    Extrait les .tar.gz et retourne la liste des .shp extraits.
    Chaque archive contient : .shp .shx .dbf .prj
    """
    shp_files = []

    for item in tqdm(file_list, desc="Extraction"):
        tar_path = item["local_path"]
        year     = item["year"]
        month    = item["month"]

        # Dossier de destination
        out_subdir = EXTRACT_DIR / f"{year}_{month:02d}"
        shp_target = out_subdir / f"mcd64_{year}_{month:02d}.shp"

        if shp_target.exists():
            shp_files.append({**item, "shp_path": shp_target})
            continue

        out_subdir.mkdir(exist_ok=True)

        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                members = tar.getmembers()

                # Extraire tous les composants du shapefile
                for member in members:
                    ext = Path(member.name).suffix.lower()
                    if ext in [".shp",".shx",".dbf",".prj",".cpg"]:
                        member.name = Path(member.name).name
                        tar.extract(member, path=out_subdir)

                # Renommer le .shp principal
                shp_found = list(out_subdir.glob("*.shp"))
                if shp_found:
                    src = shp_found[0]
                    # Renommer tous les composants
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
    """
    Pour chaque shapefile MCD64 :
      - Lit le fichier
      - Découpe sur le contour de la Guyane
      - Ajoute les colonnes year / month
    Fusionne tout en un seul GeoDataFrame.
    """
    gdfs = []

    for item in tqdm(shp_files, desc="Clip + fusion"):
        shp_path = item["shp_path"]
        year     = item["year"]
        month    = item["month"]

        # Fichier clip déjà produit ?
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
            # Lire le shapefile MCD64
            gdf = gpd.read_file(shp_path)

            if gdf.empty or gdf.geometry.is_empty.all():
                continue

            # Harmoniser CRS
            gdf = gdf.to_crs("EPSG:4326")

            # Découpe sur Guyane
            gdf_clip = gpd.clip(gdf, contour_guyane)

            if gdf_clip.empty:
                continue

            # Ajouter colonnes temporelles
            gdf_clip["year"]  = year
            gdf_clip["month"] = month
            gdf_clip["period"] = f"{year}-{month:02d}"

            # Sauvegarder le clip mensuel
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

    # Nettoyage colonnes
    # MCD64 shapefile contient : DN (day of burn), geometry
    # On renomme pour clarté
    if "DN" in merged.columns:
        merged = merged.rename(columns={"DN": "burn_doy"})

    # Calculer la surface réelle en ha (en projetant en UTM22N)
    merged_utm = merged.to_crs("EPSG:2972")   # UTM 22N Guyane
    merged["area_ha"] = (merged_utm.geometry.area / 10000).round(2)

    # Surface totale
    total_ha = merged["area_ha"].sum()
    print(f"  Surface brûlée totale : {total_ha:,.0f} ha")
    print(f"  Polygones : {len(merged):,}")

    return merged

# ─────────────────────────────────────────────
# 7. EXPORT FINAL
# ─────────────────────────────────────────────
def export_final(gdf, out_dir):
    """Export GeoPackage + GeoJSON du fichier fusionné."""

    # GeoPackage (recommandé pour QGIS/PostGIS)
    gpkg_path = out_dir / "mcd64_guyane_complet.gpkg"
    gdf.to_file(gpkg_path, driver="GPKG", layer="surfaces_brulees")
    print(f"  OK {gpkg_path.name}  ({gpkg_path.stat().st_size/1024/1024:.1f} Mo)")

    # GeoJSON (pour dashboard web)
    geojson_path = out_dir / "mcd64_guyane_complet.geojson"
    gdf.to_file(geojson_path, driver="GeoJSON")
    print(f"  OK {geojson_path.name}")

    # CSV résumé par année
    if "year" in gdf.columns and "area_ha" in gdf.columns:
        resume = (gdf.groupby("year")
                     .agg(n_polygones=("area_ha","count"),
                          area_ha_total=("area_ha","sum"))
                     .reset_index()
                     .round({"area_ha_total":1}))
        csv_path = out_dir / "mcd64_guyane_resume_annuel.csv"
        resume.to_csv(csv_path, index=False)
        print(f"  OK {csv_path.name}")

        print("\n  Résumé annuel :")
        print(resume.to_string(index=False))

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
    months_filter = MONTHS  # None = tous

    # 1. Contour Guyane ─────────────────────────────────────
    print("\n>> Contour Guyane...")
    contour = get_contour_guyane()

    # 2. Connexion SFTP ─────────────────────────────────────
    print("\n>> Connexion SFTP...")
    try:
        transport, sftp = connect_sftp()
    except Exception as e:
        print(f"  Connexion impossible : {e}")
        print("  Vérifie ta connexion Internet / pare-feu port 22.")
        return

    try:
        # 3. Lister les fichiers ────────────────────────────
        print("\n>> Listage des fichiers disponibles...")
        file_list = list_remote_files(sftp, years, months_filter)

        if not file_list:
            print("  Aucun fichier trouvé pour cette plage.")
            return

        # 4. Téléchargement ─────────────────────────────────
        print(f"\n>> Téléchargement ({len(file_list)} fichiers)...")
        downloaded = download_files(sftp, file_list)

    finally:
        sftp.close()
        transport.close()
        print("  SFTP déconnecté.")

    if not downloaded:
        print("  Aucun fichier téléchargé.")
        return

    # 5. Extraction ─────────────────────────────────────────
    print(f"\n>> Extraction des shapefiles...")
    shp_files = extract_shapefiles(downloaded)

    if not shp_files:
        print("  Aucun shapefile extrait.")
        return

    # 6. Clip + Fusion ──────────────────────────────────────
    print(f"\n>> Découpe sur Guyane + fusion...")
    gdf_final = clip_and_merge(shp_files, contour)

    if gdf_final.empty:
        print("  Aucun polygone dans l'emprise Guyane.")
        return

    # 7. Export ─────────────────────────────────────────────
    print(f"\n>> Export final...")
    export_final(gdf_final, OUT_DIR)

    print("\n" + "=" * 60)
    print("  TERMINÉ")
    print("=" * 60)
    print(f"\n  Fichiers dans : {OUT_DIR.resolve()}")
    print("\n  Import QGIS :")
    print("    Glisser mcd64_guyane_complet.gpkg dans QGIS")
    print("    CRS : EPSG:4326 (WGS84)")
    print("\n  Import PostGIS :")
    print("    ogr2ogr -f PostgreSQL PG:'host=... dbname=...' \\")
    print("      mcd64_guyane_complet.gpkg -nln surfaces_brulees")
    print("\n  Colonnes disponibles :")
    print("    burn_doy  : jour julien de la combustion (1-366)")
    print("    year      : année")
    print("    month     : mois")
    print("    period    : YYYY-MM")
    print("    area_ha   : surface brûlée réelle en hectares (UTM22N)")
    print("    geometry  : MultiPolygon EPSG:4326")


if __name__ == "__main__":
    # ── Vérification dépendances ──────────────────────────
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