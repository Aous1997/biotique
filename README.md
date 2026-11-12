# README — Schéma biotique (icare)

## État des scripts (2026-08-31)

| Statut | Scripts |
|---|---|
| 🟢 Actif (automatique) | — (aucun PRE/POST enregistré, pipeline volontairement manuel) |
| 🟡 À relancer si nouvelle donnée | `mcd64.py`, `Import_mcd64_postgis.py`, `Firms polygone.py`, `Surface_brule_guyane.py`, `météo.py`, `trait_meteo_biotique.py` — pipeline satellite/météo à relancer à chaque nouvelle donnée MODIS/FIRMS/Météo-France (voir sections ci-dessous) |
| 🔵 Historique (déjà appliqué) | `fixes/01_fix_feux_2013_2020.py` |

Inventaire des émissions de la Guyane — **secteur biotique** (incendies de forêt, forêts, sols, végétation, zones humides, sels marins).

Base de données : `icare_3.2.3` (port 5434) — schéma `biotique`  
Synthèse : `synthese.synthese_biotique()` → `synthese.emissions_totales`

---

## SNAPs couverts

| SNAP   | Libellé                              | Sous-secteur      | Polluant(s)                                    | Max année |
|--------|--------------------------------------|-------------------|------------------------------------------------|-----------|
| 100102 | Végétation / COVNM biogéniques       | veget             | COVNM                                          | 2024      |
| 100205 | Végétation / autres COVNM            | veget             | COVNM                                          | 2024      |
| 110301 | Incendies de forêt                   | wood_burning      | CO2bio, CO, CH4, NOx, PM10, PM2.5, TSP, SO2… | 2024      |
| 110405 | Sols / émissions de NOx              | sols              | NOX                                            | 2024      |
| 110505 | Zones humides / CH4                  | zones_humides     | CH4                                            | 2024      |
| 110601 | Zones humides / autres CH4           | zones_humides     | CH4                                            | 2024      |
| 111116 | Forêts / COVNM                       | forets            | COVNM                                          | 2024      |
| 111117 | Forêts / NOx                         | forets            | NOX                                            | 2024      |
| 111215 | Forêts / COVNM (autre)               | forets            | COVNM                                          | 2024      |
| 111216 | Forêts / NOx (autre)                 | forets            | NOX                                            | 2024      |

Tous les SNAPs sont à jour jusqu'en **2024**.

---

## Pipeline général

```
Données sources (NASA, IGN, Météo-France…)
        │
        ▼
Tables sources (biotique.*)
        │
        ▼
VIEWs de calcul (*_emi_fin)
        │
        ▼
synthese.synthese_biotique()
        │
        ▼
synthese.emissions_totales
```

---

## Sous-secteurs et tables sources

### Incendies de forêt (SNAP 110301)

**Données** : MODIS MCD64A1 Collection 6.1 — surfaces brûlées mensuelles  
**Source** : SFTP `fuoco.geog.umd.edu` (login `fire/burnt`)  
**Chemin SFTP** : `/data/MODIS/C61/MCD64A1/SHP/Win05`

**Pipeline** :
```
mcd64.py
  → télécharge les shapefiles MCD64A1 via SFTP
  → clip sur le contour de la Guyane (EPSG:2972)
  → exports/mcd64/mcd64_guyane_postgis.gpkg

Import_mcd64_postgis.py
  → lit le GeoPackage (MODE="append")
  → insère dans biotique.mcd64_guyane_complet

[SQL] INSERT INTO wood_burning_spatial
  → transfère les polygones depuis mcd64_guyane_complet
  → colonnes : gid, the_geom (EPSG:2972), jour (BurnDate julien),
               annee_ref (TEXT), numreg='03', nomreg='Guyane'
```

**Tables clés** :

| Table / VIEW                  | Rôle                                              |
|-------------------------------|---------------------------------------------------|
| `mcd64_guyane_complet`        | Staging MCD64A1 (2010-2024, 386 polygones)        |
| `wood_burning_spatial`        | Source spatiale active (JOIN avec regions_a_calculer) |
| `wood_burning_surfaces`       | VIEW agrégée (surface brûlée m² par an/région)    |
| `wood_burning_emi_carbone`    | Calcul C                                          |
| `wood_burning_emi_fin`        | Émissions finales → synthèse                      |

**Données disponibles** :

| Année | Polygones | Surface brûlée |
|-------|-----------|----------------|
| 2023  | 57        | 3 897 ha       |
| 2024  | 16        | 452 ha         |

---

### Forêts (SNAPs 111116, 111117, 111215, 111216)

**Sources** : BD Forêt V1/V2, BD TOPO, IFN, CLC  
**Tables** : `forets_surfaces_bdforetv1/v2/bdtopo/ifn`, `forets_essences_*`  
**VIEWs** : `forets_bdclc_emi_fin`, `forets_bdforetv1_emi_fin`, etc. → `forets_emi_fin`

---

### Sols (SNAPs 110405, 111117, 111216)

**Tables** : `sols_fe`, `sols_surfaces`  
**VIEW** : `sols_flux_no`, `sols_emi_fin`

---

### Végétation (SNAPs 100102, 100205)

**Tables** : `veget_fe`, `veget_essences_corresp`  
**VIEWs** : `veget_flux_cov`, `veget_surfaces`, `veget_emi_fin`

---

### Zones humides (SNAPs 110505, 110601)

**Tables** : `zones_humides_fe`  
**VIEWs** : `zones_humides_saisonnalite`, `zones_humides_surfaces`, `zones_humides_emi_fin`

---

### Sels marins (non actif)

**Tables** : `sels_marins_especes`, `sels_marins_meteo`, `sels_marins_spatial`  
**VIEWs** : `sels_marins_emi_fin` → aucun SNAP actuellement calculé

---

## Mise à jour annuelle (incendies — SNAP 110301)

### Étape 1 — Télécharger les nouvelles données MCD64A1

```bash
# Dans /grafana/inventaire/biotique/
python3 mcd64.py
# Modifier YEAR_START et YEAR_END dans mcd64.py selon l'année à télécharger
# Sortie : outputs/mcd64/mcd64_guyane_postgis.gpkg
```

Paramètres à adapter dans `mcd64.py` :
- `YEAR_START` / `YEAR_END` : années à télécharger
- `SFTP_BASE` : `/data/MODIS/C61/MCD64A1/SHP/Win05` (Collection 6.1)

### Étape 2 — Importer dans icare

```bash
python3 Import_mcd64_postgis.py
# MODE="append" pour conserver les années existantes
```

### Étape 3 — Transférer vers wood_burning_spatial

```sql
-- Récupérer le gid max actuel
SELECT MAX(gid) FROM biotique.wood_burning_spatial;

-- Insérer les nouvelles années (adapter le gid de départ et les années)
INSERT INTO biotique.wood_burning_spatial (gid, the_geom, jour, annee_ref, numreg, nomreg)
SELECT
    <gid_max + 1> + ROW_NUMBER() OVER (ORDER BY year, "BurnDate") - 1,
    geom,
    "BurnDate",
    year::text,
    '03',
    'Guyane'
FROM biotique.mcd64_guyane_complet
WHERE year IN (<nouvelles_années>)
  AND "BurnDate" IS NOT NULL;
```

### Étape 4 — Relancer la synthèse

```sql
SELECT synthese.synthese_biotique();
```

### Étape 5 — Vérifier

```sql
SELECT snap, annee_ref, SUM(emi)
FROM synthese.emissions_totales
WHERE snap = '110301'
GROUP BY snap, annee_ref
ORDER BY annee_ref DESC;
```

---

## Scripts disponibles

| Script                     | Rôle                                                        |
|----------------------------|-------------------------------------------------------------|
| `mcd64.py`                 | Télécharge MCD64A1 SFTP, clip Guyane, GeoPackage           |
| `Import_mcd64_postgis.py`  | Importe le GeoPackage dans `mcd64_guyane_complet`          |
| `Surface_brule_guyane.py`  | Télécharge FIRMS MODIS (points feux actifs), dashboard HTML |
| `météo.py`                 | Traitement données Météo-France horaires                    |
| `trait_meteo_biotique.py`  | Import météo vers `biotique.biotique_meteo`                 |
| `Firms polygone.py`        | Téléchargement FIRMS polygones                              |

---

## Connexion base de données

Utiliser le fichier `.env` (ne pas commiter) :

```
PGHOST=localhost
PGPORT=5434
PGUSER=icare
PGDATABASE=icare_3.2.3
PGPASSWORD=icare
```

Le tunnel SSH doit être actif (port 5434 → icare).  
Démarrage des tunnels : `/grafana/infra/scripts/ssh_tunnels.py`

Exemple `.env` sans mot de passe fourni dans `.env.example`.

---

## Données Météo-France (horaires)

Source : [meteo.data.gouv.fr](https://meteo.data.gouv.fr/) — Données climatologiques de base horaires  
Documentation : [descriptif PDF](https://static.data.gouv.fr/resources/donnees-climatologiques-de-base-horaires/20260401-165114/climatologie-donnees-horaires-descriptif-20260401.pdf)

- Pas de temps : 1 heure | Fuseau Guyane : UTC-3
- Format date : `AAAAMMJJHH` | Séparateur CSV : `;`
- Deux fichiers par période : "RR-T-Vent" + "autres-paramètres" (fusionner sur `NUM_POSTE + AAAAMMJJHH`)

Colonnes clés pour `biotique.biotique_meteo` :
```python
["NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI", "AAAAMMJJHH", "T", "GLO"]
```

Stations principales Guyane :

| NUM_POSTE | NOM_USUEL        | Lat   | Lon    |
|-----------|------------------|-------|--------|
| 97301001  | CAYENNE-MATOURY  | 4.82  | -52.37 |
| 97302001  | KOUROU (CSG)     | 5.24  | -52.80 |
| 97304001  | SAINT-LAURENT    | 5.49  | -54.03 |
| 97306001  | MARIPASOULA      | 3.65  | -54.03 |
| 97307001  | SAUL             | 3.62  | -53.20 |
