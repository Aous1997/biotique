"""
=============================================================
 Telechargement des donnees meteo HORAIRES - GUYANE (973)
 Source : data.gouv.fr / Meteo-France (Licence Ouverte Etalab)

 OBJECTIF : telecharger les ressources data.gouv.fr et les
 enregistrer localement.

 Mode de sauvegarde, configurable ci-dessous :
   - COLONNES_A_GARDER = None    -> CSV complets (toutes colonnes)
   - COLONNES_A_GARDER = [...]   -> filtre uniquement ces colonnes
=============================================================
"""

import requests
import gzip
import io
import pandas as pd
from pathlib import Path
from datetime import datetime

# =============================================================
# PARAMETRES
# =============================================================

# Liste des ressources data.gouv.fr a telecharger
RESOURCE_IDS = [
    "3d95b695-f26f-4f2f-b7f2-689affabb213",
    "a2407432-afc2-4149-b8d7-eee82e6f0442",
]

# FILTRAGE DES COLONNES
# ---------------------
# Option 1 - TOUT GARDER (maximum de flexibilite pour le futur) :
# COLONNES_A_GARDER = None
#
# Option 2 - NE GARDER QUE LES COLONNES UTILES (fichiers plus legers) :
# COLONNES_A_GARDER = [
#     "NUM_POSTE", "NOM_USUEL",
#     "LAT", "LON", "ALTI",
#     "AAAAMMJJHH",
#     "T",        # Temperature (C)
#     "GLO",      # Rayonnement global (J/cm2)
# ]
#
# Option 3 - Colonnes utiles + un peu plus (pour analyses elargies) :
COLONNES_A_GARDER = [
    # Identification station
    "NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI",
    # Date
    "AAAAMMJJHH",
    # Variables principales demandees
    "T",          # Temperature sous abri (C)
    "GLO",        # Rayonnement global (J/cm2)
    # Variables complementaires utiles (a commenter si pas besoin)
    "TD",         # Point de rosee (C)
    "U",          # Humidite relative (%)
    "PSTAT",      # Pression station (hPa)
    "RR1",        # Precipitations horaires (mm)
    "FF",         # Vent moyen a 10m (m/s)
    "DD",         # Direction du vent (degres)
    "INS",        # Insolation horaire (min)
    "DIR",        # Rayonnement direct (J/cm2)
    "DIF",        # Rayonnement diffus (J/cm2)
]

DOSSIER_SORTIE = Path("./donnees_meteo_guyane/brut")
DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)

URL_API = "https://www.data.gouv.fr/api/1/datasets/r/{id}"


# =============================================================
# FONCTIONS
# =============================================================
def telecharger_ressource(resource_id, dossier, colonnes_filtre=None):
    """
    Telecharge une ressource data.gouv.fr.

    colonnes_filtre : None  -> garde toutes les colonnes
                      liste -> ne conserve que les colonnes listees (presentes)
    """
    url = URL_API.format(id=resource_id)
    print("[>] Telechargement {}".format(resource_id))

    try:
        r = requests.get(url, timeout=600, allow_redirects=True)
        r.raise_for_status()
        contenu = r.content

        # Detection gzip
        if contenu[:2] == b"\x1f\x8b":
            with gzip.open(io.BytesIO(contenu), "rt", encoding="utf-8") as f:
                df = pd.read_csv(f, sep=";", low_memory=False)
        else:
            df = pd.read_csv(io.BytesIO(contenu), sep=";",
                             low_memory=False, encoding="utf-8")

        taille_brute_mo = len(contenu) / 1024 / 1024
        nb_cols_init = len(df.columns)

        # --- FILTRAGE DES COLONNES ------------------------------
        if colonnes_filtre is not None:
            cols_gardees = [c for c in colonnes_filtre if c in df.columns]
            cols_manquantes = [c for c in colonnes_filtre if c not in df.columns]

            if cols_manquantes:
                print("    Colonnes demandees absentes : {}".format(cols_manquantes))

            if not cols_gardees:
                print("    ATTENTION : aucune colonne filtree presente dans ce fichier")
                return None

            df = df[cols_gardees]
            print("    Filtrage : {} colonnes gardees sur {}".format(
                len(cols_gardees), nb_cols_init))

        # Nom de fichier (ID + periode si possible)
        periode = ""
        if "AAAAMMJJHH" in df.columns and len(df) > 0:
            try:
                dmin = str(int(df["AAAAMMJJHH"].min()))[:6]
                dmax = str(int(df["AAAAMMJJHH"].max()))[:6]
                periode = "_{}_{}".format(dmin, dmax)
            except Exception:
                periode = ""

        fichier_csv = dossier / "meteo_{}{}.csv".format(resource_id[:8], periode)
        df.to_csv(fichier_csv, sep=";", index=False, encoding="utf-8")

        taille_finale_mo = fichier_csv.stat().st_size / 1024 / 1024
        print("    OK  {:,} lignes, {} cols | reseau {:.1f} Mo -> disque {:.1f} Mo".format(
            len(df), len(df.columns), taille_brute_mo, taille_finale_mo))

        return fichier_csv

    except Exception as e:
        print("    ERREUR : {}".format(e))
        return None


# =============================================================
# EXECUTION
# =============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  Telechargement donnees meteo Guyane")
    print("  Date d'execution : {}".format(datetime.now().strftime("%Y-%m-%d %H:%M")))
    print("  Dossier sortie   : {}".format(DOSSIER_SORTIE.resolve()))

    if COLONNES_A_GARDER is None:
        print("  Mode             : COMPLET (toutes colonnes)")
    else:
        print("  Mode             : FILTRE ({} colonnes demandees)".format(
            len(COLONNES_A_GARDER)))
        print("  Colonnes         : {}".format(", ".join(COLONNES_A_GARDER)))
    print("=" * 60)
    print()

    fichiers_ok = []
    for rid in RESOURCE_IDS:
        chemin = telecharger_ressource(rid, DOSSIER_SORTIE, COLONNES_A_GARDER)
        if chemin is not None:
            fichiers_ok.append(chemin)

    # Resume
    print("\n" + "=" * 60)
    print("  TERMINE")
    print("=" * 60)
    print("  {} / {} fichiers telecharges".format(len(fichiers_ok), len(RESOURCE_IDS)))

    if fichiers_ok:
        taille_totale = sum(f.stat().st_size for f in fichiers_ok) / 1024 / 1024
        print("  Taille totale    : {:.1f} Mo".format(taille_totale))
        print("\n  Fichiers dans {} :".format(DOSSIER_SORTIE))
        for f in fichiers_ok:
            taille_mo = f.stat().st_size / 1024 / 1024
            print("    - {}  ({:.1f} Mo)".format(f.name, taille_mo))

        print("\n  Etape suivante :")
        print("    python traiter_meteo_biotique.py")
    else:
        print("\n  Aucun fichier telecharge. Verifie tes IDs et ta connexion.")