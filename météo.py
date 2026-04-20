"""
=============================================================
 Téléchargement des données météo HORAIRES - GUYANE (973)
 Source : data.gouv.fr / Météo-France (Licence Ouverte Etalab)
 Documentation : https://static.data.gouv.fr/resources/
   donnees-climatologiques-de-base-horaires/20260401-165114/
   climatologie-donnees-horaires-descriptif-20260401.pdf

 OBJECTIF : produire un CSV prêt à être inséré dans la table
            biotique.biotique_meteo (PostgreSQL).

 Formules :
   • T_kelvin = T_celsius + 273.15
   • PAR (µmol/m²/s) = GLO (J/cm²) × 0.454 × (10000/3600) × 2.02

 Compatible Python 3.7+ (pas de syntaxe "X | None")
=============================================================
"""

import requests
import pandas as pd
import gzip
import io
from pathlib import Path
from datetime import datetime
from typing import Optional, List

# =============================================================
# PARAMÈTRES
# =============================================================

# Liste des ressources data.gouv.fr à télécharger (à compléter)
RESOURCE_IDS = [
    "3d95b695-f26f-4f2f-b7f2-689affabb213",
    "a2407432-afc2-4149-b8d7-eee82e6f0442",
]

# Métadonnées à écrire dans la table
NUMREG = "03"          # code région Guyane
NUMDEP = "973"         # code département Guyane
SOURCE = "Météo-France (data.gouv.fr)"
VERSION = datetime.now().strftime("%Y-%m-%d")
COMMENTAIRES = "Heures en fuseau local (UTC-3). PAR=GLO*0.454*(10000/3600)*2.02. Agregation inter-stations par mediane."

# Coefficient PAR (≈ 2,5474)
COEF_PAR = 0.454 * (10000 / 3600) * 2.02

DOSSIER_SORTIE = Path("./donnees_meteo_guyane")
DOSSIER_SORTIE.mkdir(exist_ok=True)

URL_API = "https://www.data.gouv.fr/api/1/datasets/r/{id}"


# =============================================================
# FONCTIONS
# =============================================================
def telecharger_ressource(resource_id):
    # type: (str) -> Optional[pd.DataFrame]
    """Télécharge une ressource data.gouv.fr (CSV.gz) -> DataFrame."""
    url = URL_API.format(id=resource_id)
    print("[>] Telechargement " + resource_id)
    try:
        r = requests.get(url, timeout=600, allow_redirects=True)
        r.raise_for_status()
        contenu = r.content

        if contenu[:2] == b"\x1f\x8b":  # gzip
            with gzip.open(io.BytesIO(contenu), "rt", encoding="utf-8") as f:
                df = pd.read_csv(f, sep=";", low_memory=False)
        else:
            df = pd.read_csv(io.BytesIO(contenu), sep=";",
                             low_memory=False, encoding="utf-8")

        cols_presentes = [c for c in ("T", "GLO", "NUM_POSTE", "AAAAMMJJHH")
                          if c in df.columns]
        print("    OK  {:,} lignes  |  colonnes cles : {}".format(
            len(df), cols_presentes))
        return df
    except Exception as e:
        print("    ERREUR : {}".format(e))
        return None


def fusionner(dfs):
    # type: (List[pd.DataFrame]) -> pd.DataFrame
    """Concatène puis dédoublonne sur (NUM_POSTE, AAAAMMJJHH)."""
    df_all = pd.concat(dfs, ignore_index=True, sort=False)
    if "NUM_POSTE" in df_all.columns and "AAAAMMJJHH" in df_all.columns:
        df_all = (
            df_all.sort_values(["NUM_POSTE", "AAAAMMJJHH"])
            .groupby(["NUM_POSTE", "AAAAMMJJHH"], as_index=False)
            .first()
        )
    return df_all


# =============================================================
# 1. TELECHARGEMENT
# =============================================================
print("Telechargement de {} ressource(s) data.gouv.fr\n".format(len(RESOURCE_IDS)))

dfs = []
for rid in RESOURCE_IDS:
    df = telecharger_ressource(rid)
    if df is not None:
        dfs.append(df)

if not dfs:
    raise SystemExit("Aucune donnee telechargee.")

# =============================================================
# 2. FUSION
# =============================================================
df = fusionner(dfs)
print("\n[OK] {:,} lignes apres fusion.\n".format(len(df)))

# =============================================================
# 3. CONSTRUCTION DU DATAFRAME AU FORMAT DE LA TABLE CIBLE
# =============================================================
for col in ("NUM_POSTE", "AAAAMMJJHH"):
    if col not in df.columns:
        raise SystemExit("Colonne '{}' absente des donnees.".format(col))

# Horodatage
date_series = pd.to_datetime(df["AAAAMMJJHH"].astype(str),
                             format="%Y%m%d%H", errors="coerce")

# Temperature
t_celsius = pd.to_numeric(df.get("T"), errors="coerce")
t_kelvin = t_celsius + 273.15

# Rayonnement et PAR
rg = pd.to_numeric(df.get("GLO"), errors="coerce")
par = rg * COEF_PAR

# DataFrame final
out = pd.DataFrame({
    "numreg":             NUMREG,
    "numdep":             NUMDEP,
    "date":               date_series,
    "t_ambiante_celsius": t_celsius,
    "t_ambiante_kelvin":  t_kelvin,
    "rg":                 rg,
    "unite_rg":           "J/cm²",
    "par":                par,
    "unite_par":          "µmol/m²/s",
    "source":             SOURCE,
    "version":            VERSION,
    "commentaires":       COMMENTAIRES,
})

out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

# =============================================================
# 4. EXPORT BRUT PAR STATION
# =============================================================
fichier_brut = DOSSIER_SORTIE / "biotique_meteo_brut_par_station.csv"
out_brut = out.assign(num_poste=df["NUM_POSTE"].values)[
    ["num_poste"] + list(out.columns)
]
out_brut.to_csv(fichier_brut, sep=";", index=False, encoding="utf-8")
print("[FICHIER] Brut par station  : {}  ({:,} lignes)".format(
    fichier_brut, len(out_brut)))

# =============================================================
# 4b. EXPORT MOYENNE DEPARTEMENTALE (prêt pour INSERT SQL)
# =============================================================
# Agregation par MEDIANE (plus robuste aux valeurs extremes qu'une moyenne)
# Une station avec un capteur defectueux ou une valeur aberrante ne biaise
# plus la valeur finale du departement.
cols_agg = ["t_ambiante_celsius", "t_ambiante_kelvin", "rg", "par"]
out_dep = out.groupby("date", as_index=False)[cols_agg].median()

out_dep["numreg"] = NUMREG
out_dep["numdep"] = NUMDEP
out_dep["unite_rg"] = "J/cm²"
out_dep["unite_par"] = "µmol/m²/s"
out_dep["source"] = SOURCE
out_dep["version"] = VERSION
out_dep["commentaires"] = COMMENTAIRES

ordre = [
    "numreg", "numdep", "date",
    "t_ambiante_celsius", "t_ambiante_kelvin",
    "rg", "unite_rg",
    "par", "unite_par",
    "source", "version", "commentaires",
]
out_dep = out_dep[ordre]

fichier_table = DOSSIER_SORTIE / "biotique_meteo.csv"
out_dep.to_csv(fichier_table, sep=";", index=False, encoding="utf-8",
               date_format="%Y-%m-%d %H:%M:%S")
print("[FICHIER] Pret pour INSERT  : {}  ({:,} lignes)".format(
    fichier_table, len(out_dep)))

# =============================================================
# 4c. SCRIPT SQL COMPANION
# =============================================================
# IMPORTANT : la commande \COPY de psql doit etre sur UNE SEULE LIGNE.
# On utilise le chemin absolu pour eviter tout probleme de repertoire courant.
chemin_csv_sql = str(fichier_table.resolve()).replace("\\", "/")

sql = """-- Chargement du CSV dans la table biotique.biotique_meteo
-- Lancement : psql -h HOST -p PORT -U USER -d BASE -f import_postgres.sql

SET client_encoding = 'UTF8';

-- (Optionnel) Pour eviter les doublons lors d'un re-import, decommenter :
-- DELETE FROM biotique.biotique_meteo WHERE numdep = '{dep}';

\\COPY biotique.biotique_meteo (numreg, numdep, date, t_ambiante_celsius, t_ambiante_kelvin, rg, unite_rg, par, unite_par, source, version, commentaires) FROM '{chemin}' WITH (FORMAT csv, HEADER true, DELIMITER ';', ENCODING 'UTF8', NULL '');

-- Verification
SELECT COUNT(*) AS nb_lignes, MIN(date) AS date_min, MAX(date) AS date_max FROM biotique.biotique_meteo WHERE numdep = '{dep}';
""".format(chemin=chemin_csv_sql, dep=NUMDEP)
(DOSSIER_SORTIE / "import_postgres.sql").write_text(sql, encoding="utf-8")
print("[FICHIER] Script SQL COPY   : {}".format(DOSSIER_SORTIE / "import_postgres.sql"))

# =============================================================
# 5. APERCU
# =============================================================
print("\nApercu (moyenne departementale, pret pour insertion) :")
print(out_dep.head(10).to_string(index=False))

print("\nStatistiques :")
print("   Periode    : {} -> {}".format(out_dep['date'].min(), out_dep['date'].max()))
print("   T mediane  : {:.2f} C".format(out_dep['t_ambiante_celsius'].median()))
print("   RG median  : {:.2f} J/cm2".format(out_dep['rg'].median()))
print("   PAR median : {:.2f} umol/m2/s".format(out_dep['par'].median()))
print("   Coef PAR   : {:.4f}".format(COEF_PAR))