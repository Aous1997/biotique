"""
=============================================================
  FIRMS MODIS — Conversion Points → MultiPolygones
  Chaque point MODIS devient un rectangle scan×track (km²)
  Les rectangles adjacents sont fusionnés en MultiPolygones
  par commune et par année → export GeoJSON + GeoPackage
=============================================================
Lancement :
    pip install pandas geopandas shapely
    python firms_polygones.py

Entrée  : outputs/firms_guyane_detections.csv
Sorties : outputs/
    firms_polygones_par_annee.geojson
    firms_polygones_par_commune_annee.geojson
    firms_polygones_par_commune_annee.gpkg   <- import QGIS/PostGIS
=============================================================
"""

import pandas as pd
import geopandas as gpd
from shapely.geometry import box, MultiPolygon
from shapely.ops import unary_union
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
IN_CSV  = Path("./outputs/firms_guyane_detections.csv")
OUT_DIR = Path("./outputs")
OUT_DIR.mkdir(exist_ok=True)

# Tolérance de fusion : pixels distants de moins de BUFFER_DEG
# sont considérés comme adjacents et fusionnés
# 0.01° ≈ 1.1 km (légèrement > taille pixel MODIS 1km)
BUFFER_DEG = 0.009

# ─────────────────────────────────────────────
# 1. LECTURE DES DÉTECTIONS
# ─────────────────────────────────────────────
def load_detections(path: Path) -> pd.DataFrame:
    print(f"  Lecture : {path.name}")
    df = pd.read_csv(path)
    df.columns = df.columns.str.lower().str.strip()

    # Colonnes obligatoires
    for col in ["latitude","longitude","scan","track","area_ha","year","date"]:
        if col not in df.columns:
            if col == "area_ha":
                df["area_ha"] = df["scan"] * df["track"] * 100
            elif col == "year":
                df["year"] = pd.to_datetime(df["acq_date"]).dt.year
            elif col == "date":
                df["date"] = pd.to_datetime(df["acq_date"]).dt.date.astype(str)

    df["latitude"]  = pd.to_numeric(df["latitude"],  errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["scan"]      = pd.to_numeric(df["scan"],      errors="coerce").fillna(1.0)
    df["track"]     = pd.to_numeric(df["track"],     errors="coerce").fillna(1.0)
    df["area_ha"]   = pd.to_numeric(df["area_ha"],   errors="coerce").fillna(100.0)
    df["year"]      = pd.to_numeric(df["year"],      errors="coerce").astype("Int64")

    if "commune" not in df.columns:
        df["commune"] = "Inconnue"

    df = df.dropna(subset=["latitude","longitude"])
    print(f"  {len(df):,} détections chargées")
    return df

# ─────────────────────────────────────────────
# 2. POINT → RECTANGLE PIXEL MODIS
# ─────────────────────────────────────────────
# Conversion km → degrés à la latitude de la Guyane (~4°N)
# 1° latitude  ≈ 111.0 km  (constant)
# 1° longitude ≈ 111.0 × cos(4°) ≈ 110.8 km
LAT_GUYANE   = 4.0
import math
KM_PER_DEG_LAT = 111.0
KM_PER_DEG_LON = 111.0 * math.cos(math.radians(LAT_GUYANE))

def point_to_box(lon, lat, scan_km, track_km):
    """
    Crée un rectangle centré sur (lon, lat).
    scan  = largeur  du pixel en km (direction E-O)
    track = hauteur  du pixel en km (direction N-S)
    """
    half_lon = (scan_km  / 2.0) / KM_PER_DEG_LON
    half_lat = (track_km / 2.0) / KM_PER_DEG_LAT
    return box(
        lon - half_lon,   # xmin
        lat - half_lat,   # ymin
        lon + half_lon,   # xmax
        lat + half_lat,   # ymax
    )

# ─────────────────────────────────────────────
# 3. FUSION EN MULTIPOLYGONES
# ─────────────────────────────────────────────
def build_polygons(df: pd.DataFrame, group_by: list) -> gpd.GeoDataFrame:
    """
    Pour chaque groupe (ex: year+commune) :
      - crée un rectangle par point
      - applique un léger buffer pour connecter les adjacents
      - fusionne tout avec unary_union → MultiPolygon
      - retire le buffer pour retrouver les vraies tailles
    Retourne un GeoDataFrame avec une ligne par groupe.
    """
    records = []
    groups  = df.groupby(group_by)
    total   = len(groups)

    print(f"  Fusion de {total} groupes ({'+'.join(group_by)})...")

    for i, (key, grp) in enumerate(groups):
        if (i+1) % 50 == 0 or i == 0:
            print(f"    {i+1}/{total}...")

        # Créer les rectangles pixel
        boxes = []
        for _, row in grp.iterrows():
            b = point_to_box(
                row["longitude"], row["latitude"],
                float(row["scan"]),   # scan en km
                float(row["track"])   # track en km
            )
            boxes.append(b)

        if not boxes:
            continue

        # Buffer léger → fusion des adjacents → retrait buffer
        buffered    = [b.buffer(BUFFER_DEG)  for b in boxes]
        merged      = unary_union(buffered)
        final_geom  = merged.buffer(-BUFFER_DEG)

        # Si le résultat est vide (très rare), garder union simple
        if final_geom.is_empty:
            final_geom = unary_union(boxes)

        # Métriques agrégées
        rec = {col: (key[j] if isinstance(key, tuple) else key)
               for j, col in enumerate(group_by)}
        rec["n_detections"]  = len(grp)
        rec["n_fire_days"]   = grp["date"].nunique()
        rec["area_ha_total"] = round(float(grp["area_ha"].sum()), 1)
        rec["frp_total_mw"]  = round(float(grp["frp"].sum()), 1) if "frp" in grp.columns else 0
        rec["first_fire"]    = grp["date"].min()
        rec["last_fire"]     = grp["date"].max()
        rec["geometry"]      = final_geom

        records.append(rec)

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    return gdf

# ─────────────────────────────────────────────
# 4. EXPORT
# ─────────────────────────────────────────────
def export(gdf: gpd.GeoDataFrame, name: str):
    """Export GeoJSON et GeoPackage."""
    # GeoJSON
    p_geojson = OUT_DIR / f"{name}.geojson"
    gdf.to_file(p_geojson, driver="GeoJSON")
    print(f"  ✅ {p_geojson.name}  ({len(gdf)} entités)")

    # GeoPackage (mieux pour PostGIS / QGIS avec grands fichiers)
    p_gpkg = OUT_DIR / f"{name}.gpkg"
    gdf.to_file(p_gpkg, driver="GPKG", layer=name)
    print(f"  ✅ {p_gpkg.name}")

# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────
def run():
    print("=" * 58)
    print("  FIRMS MODIS — Points → MultiPolygones")
    print("=" * 58)

    if not IN_CSV.exists():
        print(f"❌ Fichier introuvable : {IN_CSV}")
        print("   Lance d'abord firms_guyane.py pour générer les données.")
        return

    # 1. Chargement
    print("\n>> Chargement des détections...")
    df = load_detections(IN_CSV)

    # Aperçu des colonnes scan/track
    print(f"\n  scan  min={df['scan'].min():.2f}  max={df['scan'].max():.2f}  moy={df['scan'].mean():.2f} km")
    print(f"  track min={df['track'].min():.2f}  max={df['track'].max():.2f}  moy={df['track'].mean():.2f} km")
    print(f"  area  min={df['area_ha'].min():.0f}  max={df['area_ha'].max():.0f}  moy={df['area_ha'].mean():.0f} ha/pixel")

    # 2. Polygones par année
    print("\n>> Polygones par année...")
    gdf_annee = build_polygons(df, group_by=["year"])
    export(gdf_annee, "firms_polygones_par_annee")

    # 3. Polygones par commune + année
    print("\n>> Polygones par commune + année...")
    gdf_comm = build_polygons(df, group_by=["commune","year"])
    export(gdf_comm, "firms_polygones_par_commune_annee")

    # 4. Polygones par commune (toutes années fusionnées)
    print("\n>> Polygones par commune (toutes années)...")
    gdf_comm_all = build_polygons(df, group_by=["commune"])
    export(gdf_comm_all, "firms_polygones_par_commune_total")

    # 5. Résumé
    print("\n" + "=" * 58)
    print("  RÉSUMÉ PAR COMMUNE (toutes années)")
    print("=" * 58)
    cols_show = ["commune","n_detections","n_fire_days","area_ha_total","first_fire","last_fire"]
    cols_ok   = [c for c in cols_show if c in gdf_comm_all.columns]
    print(gdf_comm_all[cols_ok].sort_values("area_ha_total", ascending=False).to_string(index=False))

    print(f"\n✅ Terminé — fichiers dans : {OUT_DIR.resolve()}")
    print("\n  Import QGIS :")
    print("  Glisser-déposer les .gpkg ou .geojson dans QGIS")
    print("  Les géométries sont en EPSG:4326 (WGS84)")
    print("\n  Import PostGIS :")
    print("  ogr2ogr -f PostgreSQL PG:'host=... dbname=...' firms_polygones_par_commune_annee.gpkg")

if __name__ == "__main__":
    run()