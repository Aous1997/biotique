# README — Variables Météo-France (données horaires)

Source : [meteo.data.gouv.fr](https://meteo.data.gouv.fr/) — Données climatologiques de base horaires
Documentation officielle : [climatologie-donnees-horaires-descriptif.pdf](https://static.data.gouv.fr/resources/donnees-climatologiques-de-base-horaires/20260401-165114/climatologie-donnees-horaires-descriptif-20260401.pdf)

---

## 📋 Informations générales

- **Pas de temps** : 1 heure
- **Fuseau horaire** :
  - Métropole → UTC
  - **Outre-mer (Guyane, Réunion, Martinique...) → Fuseau local (FU)**
    - Guyane = UTC-3
- **Format de la date** : `AAAAMMJJHH` (ex: `2023080114` = 1er août 2023, 14h)
- **Séparateur CSV** : point-virgule `;`
- **Décimales** : point `.`
- **Encodage** : UTF-8

---

## 🗂️ Deux types de fichiers par période

Les données sont réparties dans **deux fichiers complémentaires** par département et période :

### 1. Fichier "RR-T-Vent" (sans suffixe)
Contient précipitations, températures, vent.

### 2. Fichier "autres-parametres"
Contient rayonnement, humidité, pression, insolation.

Pour avoir toutes les colonnes, il faut télécharger **les deux fichiers** puis les fusionner sur `NUM_POSTE + AAAAMMJJHH`.

---

## 🏷️ Colonnes d'identification (dans tous les fichiers)

| Code | Description | Type |
|---|---|---|
| `NUM_POSTE` | Numéro Météo-France du poste (8 chiffres) | texte |
| `NOM_USUEL` | Nom usuel du poste (ex: "CAYENNE-MATOURY") | texte |
| `LAT` | Latitude en degrés décimaux (négatif au sud) | float |
| `LON` | Longitude en degrés décimaux (négatif à l'ouest) | float |
| `ALTI` | Altitude du pied de l'abri en mètres | int |
| `AAAAMMJJHH` | Date-heure au format AAAAMMJJHH | int |

---

## 🌡️ Températures

| Code | Description | Unité |
|---|---|---|
| `T` | Température sous abri instantanée | °C |
| `TD` | Température du point de rosée | °C |
| `TN` | Température minimale horaire | °C |
| `TX` | Température maximale horaire | °C |
| `HTN` | Heure de TN | hhmm |
| `HTX` | Heure de TX | hhmm |
| `T10` | Température à -10 cm (sol) | °C |
| `T20` | Température à -20 cm (sol) | °C |
| `T50` | Température à -50 cm (sol) | °C |
| `T100` | Température à -100 cm (sol) | °C |
| `TCHAUSSEE` | Température de chaussée | °C |

**Note** : les températures sont au 1/10 de degré.

---

## 💧 Humidité et pression

| Code | Description | Unité |
|---|---|---|
| `U` | Humidité relative | % |
| `UN` | Humidité relative minimale | % |
| `UX` | Humidité relative maximale | % |
| `HUN` | Heure de UN | hhmm |
| `HUX` | Heure de UX | hhmm |
| `TSV` | Tension de vapeur | hPa |
| `PSTAT` | Pression à la station | hPa |
| `PMER` | Pression ramenée au niveau de la mer | hPa |
| `PMERMIN` | Pression mer minimale | hPa |

---

## ☀️ Rayonnement et insolation

| Code | Description | Unité |
|---|---|---|
| `GLO` | **Rayonnement global horaire** | **J/cm²** |
| `GLO2` | Rayonnement global horaire sur surface inclinée | J/cm² |
| `DIR` | Rayonnement direct horaire | J/cm² |
| `DIR2` | Rayonnement direct horaire (capteur 2) | J/cm² |
| `DIF` | Rayonnement diffus horaire | J/cm² |
| `DIF2` | Rayonnement diffus horaire (capteur 2) | J/cm² |
| `UV` | Rayonnement UV | J/cm² |
| `UV_INDICE` | Indice UV | indice |
| `INFRAR` | Rayonnement infrarouge | J/cm² |
| `INS` | Durée d'insolation horaire | minutes |
| `INS2` | Durée d'insolation (capteur 2) | minutes |

### 🧮 Conversions utiles pour `GLO`

```
GLO est en J/cm² cumulé sur l'heure.

Pour obtenir W/m² (puissance moyenne) :
    GLO_W_m2 = GLO * 10000 / 3600      (≈ GLO × 2.778)

Pour obtenir PAR (µmol/m²/s, photosynthèse) :
    PAR = GLO * 0.454 * (10000/3600) * 2.02    (≈ GLO × 2.547)
```

---

## 🌧️ Précipitations

| Code | Description | Unité |
|---|---|---|
| `RR1` | Précipitations horaires | mm (1/10) |
| `DRR1` | Durée de précipitations dans l'heure | minutes |

---

## 💨 Vent

### Vent moyen à 10 m
| Code | Description | Unité |
|---|---|---|
| `FF` | Force du vent moyenné (10 min) | m/s |
| `DD` | Direction du vent moyenné | ° (0-360) |
| `FXY` | Force max du vent moyenné | m/s |
| `DXY` | Direction du vent max moyenné | ° |
| `HXY` | Heure du FXY | hhmm |

### Rafales (vent instantané)
| Code | Description | Unité |
|---|---|---|
| `FXI` | Force de la rafale maximale | m/s |
| `DXI` | Direction de la rafale maximale | ° |
| `HXI` | Heure de la rafale max | hhmm |
| `FXI2` | Rafale max sur 2 secondes | m/s |
| `FXI3S` | Rafale max sur 3 secondes | m/s |

### Vent à d'autres hauteurs
| Code | Description | Unité |
|---|---|---|
| `FF2` | Vent moyen à 2 m | m/s |
| `DD2` | Direction vent à 2 m | ° |

---

## ☁️ Nébulosité et visibilité

| Code | Description | Unité |
|---|---|---|
| `N` | Nébulosité totale | octas (0-8) |
| `NBAS` | Nébulosité de la couche la plus basse | octas |
| `HBAS` | Hauteur de la couche la plus basse | m |
| `CL` | Code synop nuages bas | code |
| `CM` | Code synop nuages moyens | code |
| `CH` | Code synop nuages élevés | code |
| `VV` | Visibilité horizontale | m |
| `DVV200` | Durée de visibilité < 200 m | minutes |

---

## 🌨️ Temps présent / état du sol

| Code | Description | Unité |
|---|---|---|
| `WW` | Code temps présent (synop) | code |
| `W1`, `W2` | Code temps passé 1 et 2 | code |
| `SOL` | État du sol sans neige | code |
| `SOLNG` | État du sol avec neige | code |
| `HNEIGEF` | Hauteur de neige fraîche | cm |
| `NEIGETOT` | Hauteur totale de neige au sol | cm |

---

## 📦 Colonnes recommandées selon l'usage

### Usage "biotique_meteo" (minimaliste)
Pour alimenter juste la table `biotique.biotique_meteo` :
```python
["NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI",
 "AAAAMMJJHH", "T", "GLO"]
```

### Usage "analyses écologiques" (équilibré) ⭐ **Recommandé**
Variables les plus utiles pour études environnementales :
```python
["NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI", "AAAAMMJJHH",
 "T", "TD",          # températures
 "U",                # humidité
 "PSTAT",            # pression
 "GLO", "DIR", "DIF", "INS",   # rayonnement et insolation
 "RR1",              # précipitations
 "FF", "DD"]         # vent
```

### Usage "climatologie complète"
Pour ne rien rater : `COLONNES_A_GARDER = None` (toutes colonnes)

---

## ⚠️ Valeurs manquantes

Quand une mesure est absente, la cellule est **vide** dans le CSV (pas `NaN`, pas `-999`). En pandas, ça donne automatiquement des `NaN` à la lecture.

Causes courantes :
- La station n'a pas le capteur concerné (ex: pas de pyranomètre → pas de `GLO`)
- Panne temporaire du capteur
- Données non encore validées

---

## 🔗 Stations de la Guyane

Principales stations (liste non exhaustive) :

| NUM_POSTE | NOM_USUEL | Lat | Lon |
|---|---|---|---|
| 97301001 | CAYENNE-MATOURY | 4.82 | -52.37 |
| 97302001 | KOUROU (CSG) | 5.24 | -52.80 |
| 97304001 | SAINT-LAURENT | 5.49 | -54.03 |
| 97306001 | MARIPASOULA | 3.65 | -54.03 |
| 97307001 | SAUL | 3.62 | -53.20 |

> Le script `telecharger_meteo.py` génère un fichier `stations.csv` avec la liste exacte des stations présentes dans les ressources téléchargées.

---

## 📚 Ressources

- Portail officiel : https://meteo.data.gouv.fr/
- Descriptif complet (PDF) : [climatologie-donnees-horaires-descriptif](https://static.data.gouv.fr/resources/donnees-climatologiques-de-base-horaires/20260401-165114/climatologie-donnees-horaires-descriptif-20260401.pdf)
- Dataset data.gouv.fr : https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-horaires
- Météo-France Guyane : https://meteofrance.gf/
- Licence : [Licence Ouverte Etalab 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence)

---

*README généré pour le projet biotique / Guyane — avril 2026*

## Reation de fichier .env
Le fichier .env est un fichier qui sert à garder les variable de connexion à la bd ou garde les mot de passe
pour créer le fichier c'est possible par de méthodes:
### méthode 1:
Méthodes notepad .env
puis insert des variable dedans:

PGHOST=localhost
PGPORT=15432
PGUSER=icare
PGDATABASE=icare_3.2.3
PGPASSWORD=ton_mot_de_passe_ici
### méthode 2:
dans powersheel: 
@"
PGHOST=localhost
PGPORT=15432
PGUSER=icare
PGDATABASE=icare_3.2.3
PGPASSWORD=ton_mot_de_passe_ici
"@ | Out-File -FilePath .env -Encoding utf8


pour insertion danas le scripts python il faudras :
tous d'abords installer la bibilitoheque python python-dotenv
dans le cas d'utilisation d'un git penser à mettre dans le le fichier .env dans le fichier .gitignore sinon il faudra créer un fichier dans le git envtest pour montrer les varibale à faire rentrée

## Chargement des variable env dans le powershell
** Get-Content .\.env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$'){
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
}
}**
après le lancement du psql -h $env:PGHOST -p $env:PGPORT -U $env:PGUSER -d $env:PGDATABASE
    -f .\donnees_......sql