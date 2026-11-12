"""
=============================================================
  MCD64A1 - Surface Brulee Guyane Francaise
  Telechargement SFTP + Extraction + Clip + Fusion
  Source : sftp://fuoco.geog.umd.edu (login public)
=============================================================
Lancement :
    pip install paramiko geopandas shapely pandas tqdm requests
    python mcd64_guyane.py

Import en base :
    python import_mcd64_postgis.py    (methode Python pure)

Configuration :
    Modifier YEAR_START et YEAR_END ci-dessous
    Modifier MONTHS pour filtrer certains mois (None = tous)

Sorties dans ./outputs/mcd64/ :
    mcd64_guyane_YYYY_MM.shp     (fichiers mensuels decoupes en EPSG:4326)
    mcd64_guyane_complet.gpkg    (fusion EPSG:4326, pour QGIS)
    mcd64_guyane_complet.geojson (pour dashboards web)
    mcd64_guyane_postgis.gpkg    (EPSG:2972, MultiPolygon, a importer en base)
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

import os, tarfile, time
import paramiko
import requests
import pandas as pd
import geopandas as gpd
from pathlib import Path
from tqdm import tqdm
from shapely.geometry import MultiPolygon, Polygon

# ---------------------------------------------
# CONFIGURATION - A MODIFIER
# ---------------------------------------------

YEAR_START = 2023        # Annee de debut
YEAR_END   = 2024        # Annee de fin (incluse)

# Mois a telecharger (1=Jan ... 12=Dec)
# None = tous les mois
# Exemple saison seche : MONTHS = [7, 8, 9, 10, 11]
MONTHS = None

# ---------------------------------------------
# PARAMETRES SFTP (identifiants publics du guide MCD64)
# ---------------------------------------------
SFTP_HOST = "fuoco.geog.umd.edu"
SFTP_USER = "fire"
SFTP_PASS = "burnt"
SFTP_BASE = "/data/MODIS/C61/MCD64A1/SHP/Win05"

# Contour Guyane
CONTOUR_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson"
    "/master/regions/guyane/departements-guyane.geojson"
)

# Schema et table cibles
SQL_SCHEMA = "biotique"
SQL_TABLE  = "mcd64_guyane_complet"
SQL_SRID   = 2972   # CRS de la colonne geom

# Dossiers
DATA_DIR    = Path("./data/mcd64_raw")
EXTRACT_DIR = Path("./data/mcd64_shp")
OUT_DIR     = Path("./outputs/mcd64")
for d in (DATA_DIR, EXTRACT_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------
# UTILITAIRES
# ---------------------------------------------
DOY_TO_MONTH = {
    1:1, 32:2, 60:3, 61:3,
    91:4, 92:4, 121:5, 122:5,
    152:6, 153:6, 182:7, 183:7,
    213:8, 214:8, 244:9, 245:9,
    274:10, 275:10, 305:11, 306:11,
    335:12, 336:12,
}

def doy_to_month(doy):
    return DOY_TO_MONTH.get(int(doy), None)

def parse_filename(fname):
    """MCD64monthly.A2016213.Win05.006.burndate.shapefiles.tar.gz -> (2016, 8)"""
    try:
        date_part = fname.split(".")[1]
        year = int(date_part[1:5])
        doy  = int(date_part[5:])
        return year, doy_to_month(doy)
    except Exception:
        return None, None

def force_multipolygon(geom):
    """Convertit un Polygon en MultiPolygon. Retourne None si invalide."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    return geom


# ---------------------------------------------
# 1. CONTOUR GUYANE
# ---------------------------------------------
def get_contour_guyane():
    local = DATA_DIR / "contour_guyane.geojson"
    if not local.exists():
        print("  Telechargement contour Guyane...")
        r = requests.get(CONTOUR_URL, timeout=30)
        r.raise_for_status()
        local.write_bytes(r.content)
    gdf = gpd.read_file(local).to_crs("EPSG:4326")
    print(f"  Contour charge : {len(gdf)} entite(s)")
    return gdf


# ---------------------------------------------
# 2. SFTP
# ---------------------------------------------
def connect_sftp():
    print(f"  Connexion SFTP -> {SFTP_HOST}...")
    transport = paramiko.Transport((SFTP_HOST, 22))
    transport.connect(username=SFTP_USER, password=SFTP_PASS)
    sftp = paramiko.SFTPClient.from_transport(transport)
    print("  Connecte.")
    return transport, sftp

def list_remote_files(sftp, years, months_filter):
    to_download = []
    for year in years:
        remote_dir = f"{SFTP_BASE}/{year}"
        try:
            files = sftp.listdir(remote_dir)
        except FileNotFoundError:
            print(f"  [{year}] Dossier absent sur le serveur")
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
                "year": yr, "month": mo, "fname": fname,
            })
    print(f"  {len(to_download)} fichiers a telecharger")
    return to_download

def download_files(sftp, file_list):
    downloaded = []
    for item in tqdm(file_list, desc="Telechargement"):
        local = item["local_path"]
        if local.exists() and local.stat().st_size > 1000:
            downloaded.append(item)
            continue
        try:
            attr = sftp.stat(item["remote_path"])
            with tqdm(total=attr.st_size, unit="B", unit_scale=True,
                      desc=f"  {item['fname'][:40]}", leave=False) as bar:
                def cb(tr, to, b=bar):
                    b.update(tr - b.n)
                sftp.get(item["remote_path"], str(local), callback=cb)
            downloaded.append(item)
            time.sleep(0.2)
        except Exception as e:
            print(f"  Erreur {item['fname']} : {e}")
    return downloaded


# ---------------------------------------------
# 3. EXTRACTION .tar.gz -> .shp
# ---------------------------------------------
def extract_shapefiles(file_list):
    shp_files = []
    for item in tqdm(file_list, desc="Extraction"):
        tar_path = item["local_path"]
        year, month = item["year"], item["month"]
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
                    if ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                        member.name = Path(member.name).name
                        tar.extract(member, path=out_subdir)
                shp_found = list(out_subdir.glob("*.shp"))
                if shp_found:
                    src = shp_found[0]
                    for f in out_subdir.glob(src.stem + ".*"):
                        f.rename(out_subdir / f"mcd64_{year}_{month:02d}{f.suffix}")
                    shp_files.append({**item, "shp_path": shp_target})
        except Exception as e:
            print(f"  Erreur extraction {tar_path.name} : {e}")
    print(f"  {len(shp_files)} shapefiles extraits")
    return shp_files


# ---------------------------------------------
# 4. CLIP SUR GUYANE + FUSION
# ---------------------------------------------
def clip_and_merge(shp_files, contour_guyane):
    gdfs = []
    for item in tqdm(shp_files, desc="Clip + fusion"):
        shp_path = item["shp_path"]
        year, month = item["year"], item["month"]
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

            gdf_clip["year"]   = year
            gdf_clip["month"]  = month
            gdf_clip["period"] = f"{year}-{month:02d}"

            gdf_clip.to_file(clip_path)
            gdfs.append(gdf_clip)
        except Exception as e:
            print(f"  Erreur clip {shp_path.name} : {e}")
            continue

    if not gdfs:
        print("  Aucune donnee apres decoupe.")
        return gpd.GeoDataFrame()

    print(f"  Fusion de {len(gdfs)} couches mensuelles...")
    merged = gpd.GeoDataFrame(
        pd.concat(gdfs, ignore_index=True),
        crs="EPSG:4326",
    )

    # Renommage : la colonne d'origine peut etre "DN" ou "BurnDate"
    if "DN" in merged.columns and "BurnDate" not in merged.columns:
        merged = merged.rename(columns={"DN": "BurnDate"})

    # Surface reelle en hectares (projetee en UTM22N)
    merged_utm = merged.to_crs("EPSG:2972")
    merged["area_ha"] = (merged_utm.geometry.area / 10000).round(2)

    total_ha = merged["area_ha"].sum()
    print(f"  Surface brulee totale : {total_ha:,.0f} ha")
    print(f"  Polygones : {len(merged):,}")
    return merged


# ---------------------------------------------
# 5. EXPORT FINAL (GeoPackage + GeoJSON + CSV)
# ---------------------------------------------
def export_final(gdf, out_dir):
    """Export GeoPackage QGIS + GeoJSON + CSV resume + GeoPackage PostGIS."""

    def safe_write(path, write_fn):
        """Execute une fonction d'ecriture en capturant PermissionError."""
        try:
            write_fn()
            size = path.stat().st_size
            val = size / 1024 / 1024 if size > 1024 * 1024 else size / 1024
            unit = "Mo" if size > 1024 * 1024 else "Ko"
            print(f"  OK {path.name}  ({val:.1f} {unit})")
            return True
        except PermissionError:
            print(f"  /!\\ {path.name} est verrouille (ouvert dans QGIS ou un editeur ?)")
            print(f"      -> Ferme l'application, puis relance le script.")
            return False
        except Exception as e:
            print(f"  /!\\ Erreur sur {path.name} : {e}")
            return False

    # GeoPackage EPSG:4326 pour QGIS
    gpkg_path = out_dir / "mcd64_guyane_complet.gpkg"
    safe_write(
        gpkg_path,
        lambda: gdf.to_file(gpkg_path, driver="GPKG", layer="surfaces_brulees"),
    )

    # GeoJSON pour dashboards web
    geojson_path = out_dir / "mcd64_guyane_complet.geojson"
    safe_write(
        geojson_path,
        lambda: gdf.to_file(geojson_path, driver="GeoJSON"),
    )

    # CSV resume par annee
    if "year" in gdf.columns and "area_ha" in gdf.columns:
        resume = (gdf.groupby("year")
                     .agg(n_polygones=("area_ha", "count"),
                          area_ha_total=("area_ha", "sum"))
                     .reset_index()
                     .round({"area_ha_total": 1}))
        csv_path = out_dir / "mcd64_guyane_resume_annuel.csv"
        safe_write(csv_path, lambda: resume.to_csv(csv_path, index=False))
        print("\n  Resume annuel :")
        print(resume.to_string(index=False))

    # GeoPackage PostGIS (EPSG:2972, MultiPolygon) pour import_mcd64_postgis.py
    export_for_postgis(gdf, out_dir)


def export_for_postgis(gdf, out_dir):
    """
    Prepare un GeoPackage aligne sur le schema de la table PostGIS :
    - EPSG:2972 (UTM22N Guyane)
    - MultiPolygon
    - Colonnes : BurnDate, year, month, period, area_ha, geometry
    A importer ensuite avec import_mcd64_postgis.py
    """
    print("\n  Preparation du GeoPackage PostGIS...")

    gdf_pg = gdf.copy()

    # 1. Reprojection vers EPSG:2972
    if gdf_pg.crs is None or gdf_pg.crs.to_epsg() != SQL_SRID:
        gdf_pg = gdf_pg.to_crs(f"EPSG:{SQL_SRID}")

    # 2. Conversion en MultiPolygon (contrainte de la table)
    gdf_pg["geometry"] = gdf_pg.geometry.apply(force_multipolygon)
    gdf_pg = gdf_pg[gdf_pg.geometry.notna()]

    # 3. Ne garder que les colonnes de la table cible
    cols_cibles = ["BurnDate", "year", "month", "period", "area_ha", "geometry"]
    gdf_pg = gdf_pg[[c for c in cols_cibles if c in gdf_pg.columns]]

    # 4. Ecrire le GeoPackage
    gpkg_postgis = out_dir / "mcd64_guyane_postgis.gpkg"
    try:
        gdf_pg.to_file(gpkg_postgis, driver="GPKG", layer=SQL_TABLE)
        size_mo = gpkg_postgis.stat().st_size / 1024 / 1024
        print(f"  OK {gpkg_postgis.name}  ({size_mo:.1f} Mo, EPSG:{SQL_SRID})")
    except PermissionError:
        print(f"  /!\\ {gpkg_postgis.name} verrouille (ouvert dans QGIS ?)")
        print(f"      Ferme QGIS puis relance le script.")
    except Exception as e:
        print(f"  /!\\ Erreur sur {gpkg_postgis.name} : {e}")


# ---------------------------------------------
# PIPELINE PRINCIPAL
# ---------------------------------------------
def run():
    print("=" * 60)
    print("  MCD64A1 - Surface Brulee Guyane Francaise")
    print(f"  Annees : {YEAR_START} -> {YEAR_END}")
    print(f"  Mois   : {MONTHS if MONTHS else 'tous'}")
    print("=" * 60)

    years = list(range(YEAR_START, YEAR_END + 1))

    print("\n>> Contour Guyane...")
    contour = get_contour_guyane()

    print("\n>> Connexion SFTP...")
    try:
        transport, sftp = connect_sftp()
    except Exception as e:
        print(f"  Connexion impossible : {e}")
        print("  Verifie ta connexion Internet / pare-feu port 22.")
        return

    try:
        print("\n>> Listage...")
        file_list = list_remote_files(sftp, years, MONTHS)
        if not file_list:
            print("  Aucun fichier trouve.")
            return

        print(f"\n>> Telechargement ({len(file_list)} fichiers)...")
        downloaded = download_files(sftp, file_list)
    finally:
        sftp.close()
        transport.close()
        print("  SFTP deconnecte.")

    if not downloaded:
        print("  Aucun fichier telecharge.")
        return

    print("\n>> Extraction...")
    shp_files = extract_shapefiles(downloaded)
    if not shp_files:
        print("  Aucun shapefile extrait.")
        return

    print("\n>> Decoupe + fusion...")
    gdf_final = clip_and_merge(shp_files, contour)
    if gdf_final.empty:
        print("  Aucun polygone dans l'emprise Guyane.")
        return

    print("\n>> Export final...")
    export_final(gdf_final, OUT_DIR)

    print("\n" + "=" * 60)
    print("  TERMINE")
    print("=" * 60)
    print(f"\n  Fichiers dans : {OUT_DIR.resolve()}")
    print("\n  Visualisation QGIS :")
    print("    Glisser mcd64_guyane_complet.gpkg dans QGIS (EPSG:4326)")
    print("\n  Import en base PostGIS :")
    print("    python import_mcd64_postgis.py")
    print("\n  Colonnes de la table :")
    print("    BurnDate (int2)   : jour julien de la combustion (1-366)")
    print("    year     (int2)   : annee")
    print("    month    (int2)   : mois")
    print("    period   (varchar): YYYY-MM")
    print("    area_ha  (numeric): surface en hectares (UTM22N)")
    print("    geom (multipolygon, 2972) : geometrie reprojetee")


if __name__ == "__main__":
    missing = []
    for pkg in ("paramiko", "geopandas", "shapely", "pandas", "tqdm", "requests"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Packages manquants : {', '.join(missing)}")
        print(f"Installer avec : pip install {' '.join(missing)}")
    else:
        run()