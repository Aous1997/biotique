"""
=============================================================
  FIRMS MODIS — Feux actifs Guyane Française
  Téléchargement + Analyse + Dashboard HTML + Carte Leaflet
  Source : firms2.modaps.eosdis.nasa.gov — SANS compte
=============================================================
Lancement :
    pip install pandas requests tqdm geopandas shapely
    python firms_guyane.py

Sorties dans ./outputs/ :
    firms_guyane_annuel.csv
    firms_guyane_mensuel.csv
    firms_guyane_par_commune.csv
    firms_guyane_grille.geojson
    firms_guyane_dashboard.html  <- ouvrir dans un navigateur
=============================================================
"""

import time, json, webbrowser
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from pathlib import Path
from tqdm import tqdm

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BASE_URL = "https://firms2.modaps.eosdis.nasa.gov/data/country/modis"
YEARS    = list(range(2001, 2024))
COUNTRY  = "French_Guiana"
CONF_MIN = 30

# GeoJSON communes Guyane — france-geojson (IGN/INSEE, open data)
COMMUNES_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson"
    "/master/regions/guyane/communes-guyane.geojson"
)

DATA_DIR = Path("./data/firms_guyane")
OUT_DIR  = Path("./outputs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

MONTHS_FR = ["Jan","Fev","Mar","Avr","Mai","Jun",
             "Jul","Aou","Sep","Oct","Nov","Dec"]

# ─────────────────────────────────────────────
# 1. TÉLÉCHARGEMENT CSV MODIS
# ─────────────────────────────────────────────
def download_year(year):
    local = DATA_DIR / f"modis_{year}_{COUNTRY}.csv"
    if local.exists() and local.stat().st_size > 200:
        print(f"  [{year}] deja present ({local.stat().st_size/1024:.0f} Ko)")
        return local
    url = f"{BASE_URL}/{year}/modis_{year}_{COUNTRY}.csv"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 404:
            print(f"  [{year}] non disponible (404)")
            return None
        r.raise_for_status()
        local.write_bytes(r.content)
        print(f"  [{year}] OK  {local.stat().st_size/1024:.0f} Ko")
        time.sleep(0.3)
        return local
    except Exception as e:
        print(f"  [{year}] Erreur : {e}")
        return None

# ─────────────────────────────────────────────
# 2. LECTURE & NETTOYAGE
# ─────────────────────────────────────────────
def read_csv(path):
    try:
        df = pd.read_csv(path, dtype={"acq_time": str})
    except Exception as e:
        print(f"  Lecture {path.name} : {e}")
        return pd.DataFrame()

    df.columns = df.columns.str.lower().str.strip()
    df["acq_date"]   = pd.to_datetime(df["acq_date"],  errors="coerce")
    df["latitude"]   = pd.to_numeric(df["latitude"],   errors="coerce")
    df["longitude"]  = pd.to_numeric(df["longitude"],  errors="coerce")
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")
    df["frp"]   = pd.to_numeric(df["frp"]   if "frp"   in df.columns else 0, errors="coerce").fillna(0)
    df["scan"]  = pd.to_numeric(df["scan"]  if "scan"  in df.columns else 1, errors="coerce").fillna(1)
    df["track"] = pd.to_numeric(df["track"] if "track" in df.columns else 1, errors="coerce").fillna(1)

    df = df[df["confidence"] >= CONF_MIN]
    df["area_ha"] = (df["scan"] * df["track"] * 100).round(1)
    df["year"]    = df["acq_date"].dt.year
    df["month"]   = df["acq_date"].dt.month
    df["date"]    = df["acq_date"].dt.date.astype(str)
    df["doy"]     = df["acq_date"].dt.dayofyear
    return df.dropna(subset=["latitude", "longitude", "acq_date"])

# ─────────────────────────────────────────────
# 3. JOINTURE SPATIALE → COMMUNE
# ─────────────────────────────────────────────
def assign_communes(df):
    """
    Télécharge le GeoJSON des communes de Guyane (IGN/france-geojson)
    et fait une jointure spatiale pour attribuer une commune à chaque détection.
    """
    print("  Telechargement communes Guyane (IGN)...")
    communes_local = DATA_DIR / "communes_guyane.geojson"

    if not communes_local.exists():
        try:
            r = requests.get(COMMUNES_URL, timeout=30)
            r.raise_for_status()
            communes_local.write_bytes(r.content)
            print(f"  OK communes_guyane.geojson ({communes_local.stat().st_size/1024:.0f} Ko)")
        except Exception as e:
            print(f"  Impossible de telecharger les communes : {e}")
            df["commune"] = "Inconnue"
            df["code_commune"] = ""
            return df

    try:
        communes = gpd.read_file(communes_local)
        # Standardiser le nom de colonne commune selon la source
        if "nom" in communes.columns:
            communes = communes.rename(columns={"nom": "commune", "code": "code_commune"})
        elif "NOM_COM" in communes.columns:
            communes = communes.rename(columns={"NOM_COM": "commune", "INSEE_COM": "code_commune"})

        # Créer GeoDataFrame des détections
        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
            crs="EPSG:4326"
        )
        communes = communes.to_crs("EPSG:4326")

        # Jointure spatiale
        joined = gpd.sjoin(gdf, communes[["commune","code_commune","geometry"]],
                           how="left", predicate="within")
        joined["commune"]      = joined["commune"].fillna("Hors Guyane")
        joined["code_commune"] = joined["code_commune"].fillna("")

        # Retourner DataFrame simple (sans géométrie)
        result = pd.DataFrame(joined.drop(columns=["geometry","index_right"]))
        print(f"  {joined['commune'].nunique()} communes avec feux detectes")
        return result

    except Exception as e:
        print(f"  Jointure spatiale echouee : {e}")
        df["commune"] = "Inconnue"
        df["code_commune"] = ""
        return df

# ─────────────────────────────────────────────
# 4. AGRÉGATIONS
# ─────────────────────────────────────────────
def agg_annual(df):
    a = df.groupby("year").agg(
        n_detections  =("latitude","count"),
        n_fire_days   =("date",    "nunique"),
        area_ha_total =("area_ha", "sum"),
        frp_total_mw  =("frp",    "sum"),
        frp_mean_mw   =("frp",    "mean"),
        first_fire    =("date",   "min"),
        last_fire     =("date",   "max"),
    ).reset_index()
    a["frp_mean_mw"]   = a["frp_mean_mw"].round(1)
    a["frp_total_mw"]  = a["frp_total_mw"].round(0)
    a["area_ha_total"] = a["area_ha_total"].round(0)
    return a

def agg_monthly(df):
    return df.groupby(["year","month"]).agg(
        n_detections  =("latitude","count"),
        n_fire_days   =("date",   "nunique"),
        area_ha_total =("area_ha","sum"),
        frp_total_mw  =("frp",   "sum"),
    ).reset_index()

def agg_commune(df):
    """Agrégation par commune : surface totale, nb détections, nb jours."""
    if "commune" not in df.columns:
        return pd.DataFrame()
    return (df.groupby("commune").agg(
        n_detections  =("latitude","count"),
        n_fire_days   =("date",   "nunique"),
        area_ha_total =("area_ha","sum"),
        frp_total_mw  =("frp",   "sum"),
        first_fire    =("date",  "min"),
        last_fire     =("date",  "max"),
        years         =("year",  lambda x: ",".join(map(str,sorted(x.unique())))),
    ).reset_index()
    .sort_values("area_ha_total", ascending=False)
    .round({"area_ha_total":0,"frp_total_mw":0}))

def agg_grid(df, res=0.05):
    d = df.copy()
    d["lon_bin"] = (d["longitude"] // res * res).round(4)
    d["lat_bin"] = (d["latitude"]  // res * res).round(4)
    comm_col = "commune" if "commune" in d.columns else None
    agg_dict = dict(
        n_detections  =("latitude","count"),
        area_ha_total =("area_ha","sum"),
        frp_total_mw  =("frp",   "sum"),
        first_date    =("date",  "min"),
        last_date     =("date",  "max"),
        years         =("year",  lambda x: ",".join(map(str,sorted(x.unique())))),
    )
    if comm_col:
        agg_dict["commune"] = ("commune", lambda x: x.mode()[0] if len(x)>0 else "")
    return d.groupby(["lon_bin","lat_bin"]).agg(**agg_dict).reset_index()

# ─────────────────────────────────────────────
# 5. EXPORT GEOJSON
# ─────────────────────────────────────────────
def to_geojson(df_grid, path):
    gdf = gpd.GeoDataFrame(
        df_grid,
        geometry=[Point(lon,lat) for lon,lat in zip(df_grid["lon_bin"],df_grid["lat_bin"])],
        crs="EPSG:4326"
    )
    gdf.to_file(path, driver="GeoJSON")
    print(f"  OK {path.name}")

# ─────────────────────────────────────────────
# 6. DASHBOARD HTML
# ─────────────────────────────────────────────
def build_dashboard(df, df_annual, df_monthly, df_commune, df_grid, out_path):

    # ── Données JSON ─────────────────────────────────────────
    years_json   = json.dumps(df_annual["year"].tolist())
    det_json     = json.dumps(df_annual["n_detections"].tolist())
    fdays_json   = json.dumps(df_annual["n_fire_days"].tolist())
    area_json    = json.dumps(df_annual["area_ha_total"].tolist())
    frp_json     = json.dumps(df_annual["frp_total_mw"].tolist())
    first_json   = json.dumps(df_annual["first_fire"].tolist())
    last_json    = json.dumps(df_annual["last_fire"].tolist())
    months_json  = json.dumps(MONTHS_FR)

    mArea = (df_monthly.groupby("month")["area_ha_total"]
             .mean().reindex(range(1,13),fill_value=0).round(0).tolist())
    mDet  = (df_monthly.groupby("month")["n_detections"]
             .mean().reindex(range(1,13),fill_value=0).round(0).tolist())
    mArea_json = json.dumps(mArea)
    mDet_json  = json.dumps(mDet)

    hm = {}
    for _, row in df_monthly.iterrows():
        hm[f"{int(row.year)}-{int(row.month)}"] = int(row.n_detections)
    hm_json = json.dumps(hm)

    # Données carte : points feux avec commune
    map_pts = []
    sample = df_grid.copy()
    for _, row in sample.iterrows():
        comm = row.get("commune","") if "commune" in row else ""
        map_pts.append({
            "lon": float(row["lon_bin"]),
            "lat": float(row["lat_bin"]),
            "n":   int(row["n_detections"]),
            "ha":  int(row["area_ha_total"]),
            "frp": int(row["frp_total_mw"]),
            "fd":  str(row.get("first_date","")),
            "ld":  str(row.get("last_date","")),
            "yr":  str(row.get("years","")),
            "com": comm,
        })
    map_json = json.dumps(map_pts)

    # Données détaillées par année+commune pour filtrage dynamique
    # comm_detail : liste de toutes les détections agrégées par (year, commune)
    comm_detail = []
    if "commune" in df.columns:
        grp = df.groupby(["year","commune"]).agg(
            n_detections  =("latitude","count"),
            n_fire_days   =("date",   "nunique"),
            area_ha_total =("area_ha","sum"),
            frp_total_mw  =("frp",   "sum"),
            first_fire    =("date",  "min"),
            last_fire     =("date",  "max"),
        ).reset_index()
        grp["area_ha_total"] = grp["area_ha_total"].round(0)
        grp["frp_total_mw"]  = grp["frp_total_mw"].round(0)
        for _, row in grp.iterrows():
            comm_detail.append({
                "yr":  int(row["year"]),
                "com": str(row["commune"]),
                "n":   int(row["n_detections"]),
                "fd":  int(row["n_fire_days"]),
                "ha":  int(row["area_ha_total"]),
                "frp": int(row["frp_total_mw"]),
                "ff":  str(row["first_fire"]),
                "lf":  str(row["last_fire"]),
            })
    comm_json = json.dumps(comm_detail)

    # Liste des communes uniques pour le sélecteur
    all_communes = sorted(df["commune"].dropna().unique().tolist()) if "commune" in df.columns else []
    communes_list_json = json.dumps(all_communes)

    total_det  = int(df["latitude"].count())
    total_area = int(df["area_ha"].sum())
    total_days = int(df["date"].nunique())
    total_frp  = int(df["frp"].sum())
    peak_year  = int(df_annual.loc[df_annual["n_detections"].idxmax(),"year"])
    yr_min     = int(df_annual["year"].min())
    yr_max     = int(df_annual["year"].max())
    n_communes = int(df_commune["commune"].nunique()) if not df_commune.empty else 0

    # ── CSS ──────────────────────────────────────────────────
    css = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#f0f0eb;color:#222;font-size:14px}
header{background:#1a3a2a;color:#fff;padding:16px 24px;display:flex;align-items:center;gap:16px}
header h1{font-size:18px;font-weight:600}
header p{font-size:11px;opacity:.7;margin-top:3px}
.container{max-width:1280px;margin:0 auto;padding:16px}
.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:18px}
.metric{background:#fff;border-radius:8px;padding:12px 14px;border:1px solid #e0e0d8}
.metric .lbl{font-size:10px;color:#777;text-transform:uppercase;letter-spacing:.05em}
.metric .val{font-size:22px;font-weight:700;color:#1a3a2a;margin-top:3px}
.metric .sub{font-size:10px;color:#aaa;margin-top:1px}
.tabs{display:flex;gap:2px;margin-bottom:14px;border-bottom:2px solid #ddd}
.tab{padding:8px 16px;font-size:13px;cursor:pointer;border:none;background:transparent;
     color:#666;border-bottom:3px solid transparent;margin-bottom:-2px;border-radius:4px 4px 0 0}
.tab.active{color:#1a3a2a;border-bottom-color:#2d7a4f;font-weight:600;background:#fff}
.tab:hover:not(.active){background:#eee}
.panel{display:none}.panel.active{display:block}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:14px}
.card{background:#fff;border-radius:8px;padding:14px;border:1px solid #e0e0d8}
.card h3{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#888;margin-bottom:10px}
.chart-wrap{position:relative;width:100%}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:#666;font-weight:600;font-size:10px;padding:5px 7px;
   border-bottom:1px solid #e0e0d8;background:#f7f7f3;text-transform:uppercase}
td{padding:5px 7px;border-bottom:1px solid #f0f0e8;color:#333}
tr:hover td{background:#f7f7f3}
.badge{display:inline-block;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:600}
.hi{background:#d4edda;color:#155724}.md{background:#fff3cd;color:#856404}
.lo{background:#f0f0e8;color:#555}
.hm-wrap{overflow-x:auto}
.hm-tbl{font-size:10px;border-collapse:collapse}
.hm-tbl th,.hm-tbl td{padding:3px 5px;text-align:center;white-space:nowrap}
.hm-tbl th{background:#f0f0e8;color:#555;font-weight:600}
#map{width:100%;height:520px;border-radius:8px;border:1px solid #e0e0d8}
.leaflet-popup-content{font-size:12px;line-height:1.6}
.leaflet-popup-content b{color:#1a3a2a}
.map-legend{background:#fff;border-radius:6px;padding:10px 12px;
            border:1px solid #ddd;font-size:11px;line-height:1.8}
.map-legend .dot{display:inline-block;border-radius:50%;vertical-align:middle;margin-right:5px}
.year-filter{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.year-filter label{font-size:12px;color:#555}
.year-filter select{font-size:12px;padding:3px 8px;border-radius:4px;
                    border:1px solid #ccc;background:#fff}
.footer{text-align:center;font-size:10px;color:#bbb;padding:16px}
@media(max-width:800px){
  .metrics{grid-template-columns:repeat(3,1fr)}
  .row,.row3{grid-template-columns:1fr}
}
"""

    # ── HTML structure ────────────────────────────────────────
    html_parts = []
    html_parts.append('<!DOCTYPE html>\n<html lang="fr">\n<head>\n')
    html_parts.append('<meta charset="UTF-8">\n')
    html_parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">\n')
    html_parts.append('<title>FIRMS MODIS \u2014 Feux Guyane</title>\n')
    html_parts.append('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n')
    html_parts.append('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>\n')
    html_parts.append('<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n')
    html_parts.append('<style>' + css + '</style>\n</head>\n<body>\n')

    html_parts.append(
        '<header>'
        '<div><h1>FIRMS MODIS C6.1 \u2014 Feux actifs \u00b7 Guyane Fran\u00e7aise</h1>'
        '<p>Source\u00a0: NASA FIRMS \u00b7 ' + str(yr_min) + '\u2013' + str(yr_max) +
        ' \u00b7 Confiance \u2265 ' + str(CONF_MIN) + '% \u00b7 Atmo Guyane</p></div>'
        '</header>\n'
    )

    html_parts.append('<div class="container">\n')

    # Métriques
    html_parts.append('<div class="metrics">')
    metrics = [
        ("D\u00e9tections", f"{total_det:,}".replace(",","&thinsp;"), "pixels feux MODIS"),
        ("Surface estim\u00e9e", str(round(total_area/1000)) + "k ha", "scan\u00d7track\u00d7100"),
        ("Jours de feux", f"{total_days:,}".replace(",","&thinsp;"), "dates distinctes"),
        ("FRP cumul\u00e9", str(round(total_frp/1000)) + "k MW", "puissance radiative"),
        ("Ann\u00e9e pic", str(peak_year), "plus de d\u00e9tections"),
        ("Communes touch\u00e9es", str(n_communes), "avec \u22651 feu"),
    ]
    for lbl, val, sub in metrics:
        html_parts.append(
            '<div class="metric"><div class="lbl">' + lbl + '</div>'
            '<div class="val">' + val + '</div>'
            '<div class="sub">' + sub + '</div></div>'
        )
    html_parts.append('</div>\n')

    # Onglets
    html_parts.append(
        '<div class="tabs">'
        '<button class="tab active" onclick="showTab(\'carte\',this)">Carte interactive</button>'
        '<button class="tab" onclick="showTab(\'communes\',this)">Par commune</button>'
        '<button class="tab" onclick="showTab(\'annuel\',this)">Vue annuelle</button>'
        '<button class="tab" onclick="showTab(\'saisonnier\',this)">Saisonnalit\u00e9</button>'
        '<button class="tab" onclick="showTab(\'heatmap\',this)">Calendrier</button>'
        '</div>\n'
    )

    # ── Panneau CARTE ────────────────────────────────────────
    html_parts.append('<div id="carte" class="panel active">\n')
    html_parts.append(
        '<div class="year-filter">'
        '<label>Filtrer par ann\u00e9e :</label>'
        '<select id="mapYearSel" onchange="filterMap()">'
        '<option value="all">Toutes les ann\u00e9es</option>'
        '</select>'
        '<label style="margin-left:12px">Taille cercle\u00a0:</label>'
        '<select id="mapSizeSel" onchange="filterMap()">'
        '<option value="det">Nb d\u00e9tections</option>'
        '<option value="ha">Surface (ha)</option>'
        '<option value="frp">FRP (MW)</option>'
        '</select>'
        '</div>'
    )
    html_parts.append('<div id="map"></div>\n')
    html_parts.append('</div>\n')

    # ── Panneau COMMUNES ─────────────────────────────────────
    html_parts.append('<div id="communes" class="panel">\n')
    html_parts.append("""
<div class="card" style="margin-bottom:12px">
  <h3>Filtres</h3>
  <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end;margin-top:8px">
    <div>
      <label style="font-size:11px;color:#666;display:block;margin-bottom:3px">Annee</label>
      <select id="commYearSel" onchange="filterCommunes()" style="font-size:12px;padding:4px 8px;border-radius:4px;border:1px solid #ccc">
        <option value="all">Toutes les annees</option>
      </select>
    </div>
    <div>
      <label style="font-size:11px;color:#666;display:block;margin-bottom:3px">Commune</label>
      <select id="commNameSel" onchange="filterCommunes()" style="font-size:12px;padding:4px 8px;border-radius:4px;border:1px solid #ccc">
        <option value="all">Toutes les communes</option>
      </select>
    </div>
    <div>
      <label style="font-size:11px;color:#666;display:block;margin-bottom:3px">Trier par</label>
      <select id="commSortSel" onchange="filterCommunes()" style="font-size:12px;padding:4px 8px;border-radius:4px;border:1px solid #ccc">
        <option value="ha">Surface (ha)</option>
        <option value="n">Detections</option>
        <option value="fd">Jours feux</option>
        <option value="frp">FRP (MW)</option>
      </select>
    </div>
    <div id="comm_summary" style="font-size:12px;color:#555;padding:4px 0"></div>
  </div>
</div>
<div class="row">
  <div class="card" style="grid-column:1/-1">
    <h3>Surface brulee par commune <span id="comm_chart_title" style="font-weight:normal;color:#999"></span></h3>
    <div class="chart-wrap" style="height:260px"><canvas id="c_comm_bar"></canvas></div>
  </div>
</div>
<div class="card">
  <h3>Detail par commune</h3>
  <div style="overflow-x:auto">
    <table><thead><tr>
      <th>#</th><th>Commune</th><th>Annee</th><th>Detections</th><th>Jours feux</th>
      <th>Surface (ha)</th><th>FRP (MW)</th><th>1er feu</th><th>Dernier feu</th><th>Intensite</th>
    </tr></thead><tbody id="comm_table"></tbody></table>
  </div>
</div>
""")
    html_parts.append('</div>\n')

    # ── Panneau ANNUEL ────────────────────────────────────────
    html_parts.append("""
<div id="annuel" class="panel">
  <div class="row">
    <div class="card"><h3>Detections par annee</h3>
      <div class="chart-wrap" style="height:240px"><canvas id="c_det"></canvas></div></div>
    <div class="card"><h3>Surface estimee par annee (ha)</h3>
      <div class="chart-wrap" style="height:240px"><canvas id="c_area"></canvas></div></div>
  </div>
  <div class="row">
    <div class="card"><h3>Jours de feux par annee</h3>
      <div class="chart-wrap" style="height:200px"><canvas id="c_days"></canvas></div></div>
    <div class="card"><h3>FRP total par annee (MW)</h3>
      <div class="chart-wrap" style="height:200px"><canvas id="c_frp"></canvas></div></div>
  </div>
  <div class="card"><h3>Tableau annuel</h3>
    <div style="overflow-x:auto">
      <table><thead><tr>
        <th>Annee</th><th>Detections</th><th>Jours feux</th>
        <th>Surface (ha)</th><th>FRP (MW)</th><th>FRP moy (MW)</th>
        <th>1er feu</th><th>Dernier feu</th><th>Intensite</th>
      </tr></thead><tbody id="annual_table"></tbody></table>
    </div>
  </div>
</div>
""")

    # ── Panneau SAISONNIER ────────────────────────────────────
    html_parts.append("""
<div id="saisonnier" class="panel">
  <div class="row">
    <div class="card"><h3>Surface moyenne par mois (ha)</h3>
      <div class="chart-wrap" style="height:250px"><canvas id="c_mon_area"></canvas></div></div>
    <div class="card"><h3>Detections moyennes par mois</h3>
      <div class="chart-wrap" style="height:250px"><canvas id="c_mon_det"></canvas></div></div>
  </div>
</div>
""")

    # ── Panneau HEATMAP ───────────────────────────────────────
    html_parts.append("""
<div id="heatmap" class="panel">
  <div class="card"><h3>Intensite par mois et annee (nb detections)</h3>
    <div class="hm-wrap" id="hm_container"></div>
  </div>
</div>
""")

    html_parts.append('</div>\n')  # fin container
    html_parts.append('<div class="footer">FIRMS MODIS C6.1 &middot; NASA LANCE &middot; IGN/france-geojson &middot; Atmo Guyane</div>\n')

    # ── JavaScript ────────────────────────────────────────────
    js = (
        "<script>\n"
        "var YEARS="   + years_json  + ";\n"
        "var MONTHS="  + months_json + ";\n"
        "var det="     + det_json    + ";\n"
        "var fdays="   + fdays_json  + ";\n"
        "var area="    + area_json   + ";\n"
        "var frp="     + frp_json    + ";\n"
        "var mArea="   + mArea_json  + ";\n"
        "var mDet="    + mDet_json   + ";\n"
        "var hmData="  + hm_json     + ";\n"
        "var mapPts="  + map_json    + ";\n"
        "var commData="+ comm_json   + ";\n"
        "var communesList=" + communes_list_json + ";\n"
        "var first="   + first_json  + ";\n"
        "var last="    + last_json   + ";\n"
    )

    js += """
// ── Navigation ────────────────────────────────────────────
function showTab(id,btn){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});
  document.querySelectorAll('.panel').forEach(function(p){p.classList.remove('active');});
  document.getElementById(id).classList.add('active');
  if(btn) btn.classList.add('active');
  if(id==='carte' && !mapInit) initMap();
}

// ── Couleurs ──────────────────────────────────────────────
var CG='rgba(45,122,79,0.82)', CA='rgba(186,117,23,0.82)',
    CR='rgba(180,55,40,0.82)', CT='rgba(15,110,86,0.82)';

function barC(id,labels,data,color,ylabel){
  new Chart(document.getElementById(id),{
    type:'bar',
    data:{labels:labels,datasets:[{data:data,backgroundColor:color,borderRadius:3}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{grid:{display:false},ticks:{font:{size:10},maxRotation:45,autoSkip:false}},
        y:{ticks:{font:{size:10},callback:function(v){return v>=1000?Math.round(v/1000)+'k':v}},
           title:{display:true,text:ylabel,font:{size:10}}}
      }
    }
  });
}
function lineC(id,labels,data,color,ylabel){
  new Chart(document.getElementById(id),{
    type:'line',
    data:{labels:labels,datasets:[{data:data,borderColor:color,
          backgroundColor:color.replace('0.82','0.10'),
          fill:true,tension:0.35,pointRadius:4,pointBackgroundColor:color}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{grid:{display:false},ticks:{font:{size:10},maxRotation:45,autoSkip:false}},
        y:{ticks:{font:{size:10},callback:function(v){return v>=1000?Math.round(v/1000)+'k':v}},
           title:{display:true,text:ylabel,font:{size:10}}}
      }
    }
  });
}

barC('c_det', YEARS,det, CG,'detections');
barC('c_area',YEARS,area,CT,'ha');
lineC('c_days',YEARS,fdays,CA,'jours');
barC('c_frp', YEARS,frp, CR,'MW');

var mClr=MONTHS.map(function(_,i){return [6,7,8].includes(i)?CR:CG;});
new Chart(document.getElementById('c_mon_area'),{
  type:'bar',data:{labels:MONTHS,datasets:[{data:mArea,backgroundColor:mClr,borderRadius:3}]},
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
    scales:{x:{grid:{display:false},ticks:{font:{size:11}}},
            y:{ticks:{font:{size:10},callback:function(v){return v>=1000?Math.round(v/1000)+'k':v}}}}}
});
new Chart(document.getElementById('c_mon_det'),{
  type:'bar',data:{labels:MONTHS,datasets:[{data:mDet,backgroundColor:mClr,borderRadius:3}]},
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
    scales:{x:{grid:{display:false},ticks:{font:{size:11}}},y:{ticks:{font:{size:10}}}}}
});

// ── Tableau annuel ────────────────────────────────────────
var tb=document.getElementById('annual_table');
var maxD=Math.max.apply(null,det);
YEARS.forEach(function(y,i){
  var cls=det[i]>maxD*0.7?'hi':det[i]>maxD*0.3?'md':'lo';
  var lbl=det[i]>maxD*0.7?'Eleve':det[i]>maxD*0.3?'Modere':'Faible';
  var frpMoy=det[i]>0?(frp[i]/det[i]).toFixed(1):'0';
  tb.innerHTML+='<tr>'
    +'<td><b>'+y+'</b></td><td>'+det[i].toLocaleString('fr')+'</td>'
    +'<td>'+fdays[i]+'</td><td>'+Math.round(area[i]).toLocaleString('fr')+'</td>'
    +'<td>'+Math.round(frp[i]).toLocaleString('fr')+'</td><td>'+frpMoy+'</td>'
    +'<td>'+first[i]+'</td><td>'+last[i]+'</td>'
    +'<td><span class="badge '+cls+'">'+lbl+'</span></td></tr>';
});

// ── Heatmap ───────────────────────────────────────────────
var allV=Object.values(hmData);
var maxV=Math.max.apply(null,allV.concat([1]));
var hmH='<table class="hm-tbl"><thead><tr><th>Annee</th>';
MONTHS.forEach(function(m){hmH+='<th>'+m+'</th>';});
hmH+='</tr></thead><tbody>';
YEARS.forEach(function(y){
  hmH+='<tr><td><b>'+y+'</b></td>';
  for(var m=1;m<=12;m++){
    var v=hmData[y+'-'+m]||0;
    var a=v/maxV;
    var r=Math.round(26+a*154),g=Math.round(58+a*42),b=Math.round(42-a*2);
    var bg=v>0?'rgba('+r+','+g+','+b+','+(0.15+a*0.85).toFixed(2)+')':'#f9f9f5';
    var tc=a>0.5?'#fff':'#555';
    hmH+='<td style="background:'+bg+';color:'+tc+';border-radius:2px">'+(v||'')+'</td>';
  }
  hmH+='</tr>';
});
hmH+='</tbody></table>';
document.getElementById('hm_container').innerHTML=hmH;

// ── Tableau communes dynamique ────────────────────────────
// Alimenter les sélecteurs
YEARS.forEach(function(y){
  var o=document.createElement('option');
  o.value=y; o.textContent=y;
  document.getElementById('commYearSel').appendChild(o);
});
communesList.forEach(function(c){
  var o=document.createElement('option');
  o.value=c; o.textContent=c;
  document.getElementById('commNameSel').appendChild(o);
});

var commChart=null;

function filterCommunes(){
  var yr   = document.getElementById('commYearSel').value;
  var com  = document.getElementById('commNameSel').value;
  var sort = document.getElementById('commSortSel').value;

  // Filtrer
  var filtered=commData.filter(function(r){
    var okYr  = yr==='all'  || r.yr===parseInt(yr);
    var okCom = com==='all' || r.com===com;
    return okYr && okCom;
  });

  // Agréger si filtre multi-années sur une seule commune (ou inversement)
  var agg={};
  filtered.forEach(function(r){
    var key=r.com+'||'+r.yr;
    if(!agg[key]) agg[key]={com:r.com,yr:r.yr,n:0,fd:0,ha:0,frp:0,ff:r.ff,lf:r.lf};
    agg[key].n   += r.n;
    agg[key].fd  += r.fd;
    agg[key].ha  += r.ha;
    agg[key].frp += r.frp;
    if(r.ff < agg[key].ff) agg[key].ff=r.ff;
    if(r.lf > agg[key].lf) agg[key].lf=r.lf;
  });
  var rows=Object.values(agg);

  // Trier
  rows.sort(function(a,b){return b[sort]-a[sort];});

  // Résumé
  var totHA=rows.reduce(function(s,r){return s+r.ha;},0);
  var totN =rows.reduce(function(s,r){return s+r.n;}, 0);
  document.getElementById('comm_summary').innerHTML=
    '<b>'+rows.length+'</b> enregistrements &mdash; '
    +'<b>'+totN.toLocaleString('fr')+'</b> detections &mdash; '
    +'surface totale\u00a0: <b>'+totHA.toLocaleString('fr')+' ha</b>';

  // Titre graphique
  var titre = yr==='all'?'(toutes annees)':'('+yr+')';
  if(com!=='all') titre+=' \u2014 '+com;
  document.getElementById('comm_chart_title').textContent=titre;

  // Graphique top 15 communes par ha
  var top15=rows.slice().sort(function(a,b){return b.ha-a.ha;}).slice(0,15);
  var barColors=top15.map(function(r){
    return r.ha>5000?'rgba(139,0,0,0.8)':r.ha>2000?'rgba(215,48,39,0.8)':
           r.ha>500?'rgba(252,141,89,0.8)':r.ha>100?'rgba(254,224,139,0.85)':'rgba(145,207,96,0.8)';
  });
  if(commChart) commChart.destroy();
  commChart=new Chart(document.getElementById('c_comm_bar'),{
    type:'bar',
    data:{
      labels:top15.map(function(r){return r.com+(yr==='all'?' ('+r.yr+')':'');}),
      datasets:[{data:top15.map(function(r){return r.ha;}),
                 backgroundColor:barColors,borderRadius:3}]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      indexAxis:'y',
      plugins:{legend:{display:false},
        tooltip:{callbacks:{label:function(ctx){
          return ' '+ctx.raw.toLocaleString('fr')+' ha';
        }}}
      },
      scales:{
        x:{ticks:{font:{size:10},callback:function(v){return v>=1000?Math.round(v/1000)+'k':v;}},
           title:{display:true,text:'Surface brulee (ha)',font:{size:10}}},
        y:{ticks:{font:{size:10}},grid:{display:false}}
      }
    }
  });

  // Tableau
  var ct=document.getElementById('comm_table');
  ct.innerHTML='';
  var maxHA=rows.length>0?Math.max.apply(null,rows.map(function(r){return r.ha;})):1;
  rows.forEach(function(r,i){
    var cls=r.ha>maxHA*0.5?'hi':r.ha>maxHA*0.15?'md':'lo';
    var lbl=r.ha>maxHA*0.5?'Eleve':r.ha>maxHA*0.15?'Modere':'Faible';
    ct.innerHTML+='<tr>'
      +'<td>'+(i+1)+'</td>'
      +'<td><b>'+r.com+'</b></td>'
      +'<td>'+r.yr+'</td>'
      +'<td>'+r.n.toLocaleString('fr')+'</td>'
      +'<td>'+r.fd+'</td>'
      +'<td>'+r.ha.toLocaleString('fr')+'</td>'
      +'<td>'+r.frp+'</td>'
      +'<td>'+r.ff+'</td>'
      +'<td>'+r.lf+'</td>'
      +'<td><span class="badge '+cls+'">'+lbl+'</span></td>'
      +'</tr>';
  });
  if(rows.length===0){
    ct.innerHTML='<tr><td colspan="10" style="text-align:center;color:#aaa;padding:20px">Aucune donnee pour cette selection</td></tr>';
  }
}

// Initialiser avec toutes les données
filterCommunes();

// ── Carte Leaflet ─────────────────────────────────────────
var mapInit=false;
var leafletMap=null;
var circleLayer=null;
var commLayer=null;

// Alimenter le sélecteur d'années
YEARS.forEach(function(y){
  var o=document.createElement('option');
  o.value=y; o.textContent=y;
  document.getElementById('mapYearSel').appendChild(o);
});

function getColor(ha){
  return ha>5000?'#8b0000':ha>2000?'#d73027':ha>500?'#fc8d59':ha>100?'#fee08b':'#91cf60';
}

function initMap(){
  mapInit=true;
  leafletMap=L.map('map').setView([4.0,-53.0],8);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
    attribution:'&copy; OpenStreetMap contributors',maxZoom:18
  }).addTo(leafletMap);

  // Charger les communes depuis france-geojson (IGN)
  fetch('https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/regions/guyane/communes-guyane.geojson')
    .then(function(r){return r.json();})
    .then(function(gj){
      commLayer=L.geoJSON(gj,{
        style:function(){
          return{color:'#2d5a3d',weight:1.5,fillColor:'#e8f5e9',fillOpacity:0.15};
        },
        onEachFeature:function(feat,layer){
          var nom=feat.properties.nom||feat.properties.NOM_COM||'';
          layer.bindTooltip(nom,{sticky:true,className:'commune-tooltip'});
        }
      }).addTo(leafletMap);

      // Légende communes
      var legend=L.control({position:'bottomright'});
      legend.onAdd=function(){
        var d=L.DomUtil.create('div','map-legend');
        d.innerHTML='<b style="color:#1a3a2a">Intensite (ha)</b><br>'
          +'<span class="dot" style="width:10px;height:10px;background:#8b0000"></span>>5 000 ha<br>'
          +'<span class="dot" style="width:10px;height:10px;background:#d73027"></span>2 000-5 000<br>'
          +'<span class="dot" style="width:10px;height:10px;background:#fc8d59"></span>500-2 000<br>'
          +'<span class="dot" style="width:10px;height:10px;background:#fee08b"></span>100-500<br>'
          +'<span class="dot" style="width:10px;height:10px;background:#91cf60"></span>&lt;100 ha';
        return d;
      };
      legend.addTo(leafletMap);
    })
    .catch(function(){console.log('Communes non disponibles hors ligne');});

  filterMap();
}

function filterMap(){
  if(!mapInit) return;
  if(circleLayer) leafletMap.removeLayer(circleLayer);

  var yr=document.getElementById('mapYearSel').value;
  var sizeBy=document.getElementById('mapSizeSel').value;

  // Filtrer selon l'année
  var pts=mapPts;
  if(yr!=='all'){
    var yrStr=String(yr);
    pts=mapPts.filter(function(p){
      return p.yr.split(',').indexOf(yrStr)>=0;
    });
  }

  var maxVal=1;
  pts.forEach(function(p){
    var v=sizeBy==='ha'?p.ha:sizeBy==='frp'?p.frp:p.n;
    if(v>maxVal) maxVal=v;
  });

  var circles=[];
  pts.forEach(function(p){
    var v=sizeBy==='ha'?p.ha:sizeBy==='frp'?p.frp:p.n;
    var r=Math.max(4, Math.round(Math.sqrt(v/maxVal)*28));
    var col=getColor(p.ha);
    var c=L.circleMarker([p.lat,p.lon],{
      radius:r,
      fillColor:col,color:col,
      weight:1,fillOpacity:0.75
    });
    var popup='<b>Commune\u00a0:</b> '+(p.com||'N/A')+'<br>'
      +'<b>Coordonn\u00e9es\u00a0:</b> '+p.lat+'\u00b0N, '+p.lon+'\u00b0E<br>'
      +'<b>D\u00e9tections\u00a0:</b> '+p.n+'<br>'
      +'<b>Surface estim\u00e9e\u00a0:</b> '+p.ha.toLocaleString('fr')+' ha<br>'
      +'<b>FRP\u00a0:</b> '+p.frp+' MW<br>'
      +'<b>1er feu\u00a0:</b> '+p.fd+'<br>'
      +'<b>Dernier feu\u00a0:</b> '+p.ld+'<br>'
      +'<b>Ann\u00e9es\u00a0:</b> '+p.yr;
    c.bindPopup(popup);
    circles.push(c);
  });

  circleLayer=L.layerGroup(circles).addTo(leafletMap);
}

// Initialiser la carte au chargement (onglet actif par défaut)
initMap();
</script>
"""

    html_parts.append(js)
    html_parts.append("</body>\n</html>")

    out_path.write_text("".join(html_parts), encoding="utf-8")
    print(f"  OK {out_path.name}")

# ─────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────
def run():
    print("=" * 58)
    print("  FIRMS MODIS — Feux actifs Guyane Francaise")
    print(f"  Annees : {YEARS[0]}-{YEARS[-1]}")
    print("=" * 58)

    print("\n>> Telechargement CSV...")
    paths = []
    for y in tqdm(YEARS, desc="annees"):
        p = download_year(y)
        if p: paths.append(p)

    if not paths:
        print("Aucun fichier telecharge.")
        return

    print(f"\n>> Lecture de {len(paths)} fichiers...")
    dfs = [d for p in paths for d in [read_csv(p)] if not d.empty]
    if not dfs:
        print("Aucune donnee valide.")
        return

    df = pd.concat(dfs, ignore_index=True)
    print(f"  -> {len(df):,} detections")

    print("\n>> Jointure spatiale avec les communes...")
    df = assign_communes(df)

    print("\n>> Agregation...")
    df_annual  = agg_annual(df)
    df_monthly = agg_monthly(df)
    df_commune = agg_commune(df)
    df_grid    = agg_grid(df)

    print("\n>> Export CSV & GeoJSON...")
    df_annual .to_csv(OUT_DIR/"firms_guyane_annuel.csv",     index=False)
    df_monthly.to_csv(OUT_DIR/"firms_guyane_mensuel.csv",    index=False)
    df_commune.to_csv(OUT_DIR/"firms_guyane_par_commune.csv",index=False)
    df_grid   .to_csv(OUT_DIR/"firms_guyane_grille.csv",     index=False)
    df        .to_csv(OUT_DIR/"firms_guyane_detections.csv", index=False)
    to_geojson(df_grid, OUT_DIR/"firms_guyane_grille.geojson")
    for f in ["firms_guyane_annuel.csv","firms_guyane_mensuel.csv",
              "firms_guyane_par_commune.csv","firms_guyane_grille.csv"]:
        print(f"  OK {f}")

    print("\n>> Generation du dashboard HTML...")
    dash = OUT_DIR / "firms_guyane_dashboard.html"
    build_dashboard(df, df_annual, df_monthly, df_commune, df_grid, dash)

    print("\n" + "=" * 58)
    print("  RESUME PAR COMMUNE (top 10)")
    print("=" * 58)
    if not df_commune.empty:
        print(df_commune[["commune","n_detections","n_fire_days","area_ha_total"]
                         ].head(10).to_string(index=False))

    print(f"\nTermine — ouvrir : {dash.resolve()}")
    try:
        webbrowser.open(dash.resolve().as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    run()