#!/usr/bin/env python3
"""
01_fix_feux_2013_2020.py

Réintègre les feux de forêt 2013 et 2020 (snap 110301) absents de
l'inventaire alors que les données MCD64 existent.

Diagnostic (2026-07-13) :
  - biotique.mcd64_guyane_complet contient bien 2013 (4 feux, 118 ha)
    et 2020 (3 feux, 71 ha) — polygones réels, géométries distinctes
  - biotique.wood_burning_spatial ne les a jamais reçus (import initial
    incomplet — script d'import mcd64→wood_burning non conservé)
  - Toute la chaîne aval est en VUES (wood_burning_surfaces →
    _emi_carbone → _emi_fin) → insérer les polygones suffit
  - general.regions_a_calculer couvre 2013 et 2020 ✓

Étapes :
  1. INSERT mcd64 2013/2020 → wood_burning_spatial (gid à la suite)
  2. SELECT synthese.synthese_biotique()  (~70 s — DELETE/INSERT complet
     du secteur biotique dans emissions_totales + refresh spatial_surfacique
     'woodburn')
  3. Rappel : lancer ensuite la spatialisation commune :
     python3 systems/patch_spatialisation_schema.py \\
         --schema-source biotique --annees 2013,2020

Usage :
    python3 biotique/fixes/01_fix_feux_2013_2020.py [--dry-run]
"""

import psycopg2
import sys

DRY_RUN = '--dry-run' in sys.argv
ANNEES = [2013, 2020]

DB = dict(host='localhost', port=5434, dbname='icare_3.2.3',
          user='icare', password='icare', connect_timeout=10,
          keepalives=1, keepalives_idle=30, keepalives_interval=10,
          keepalives_count=5)


def main():
    print(f"{'[DRY-RUN] ' if DRY_RUN else ''}Connexion icare...")
    conn = psycopg2.connect(**DB)
    conn.autocommit = False
    cur = conn.cursor()

    # ── État avant ────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT "year", COUNT(*), ROUND(SUM(area_ha)::numeric, 1)
        FROM biotique.mcd64_guyane_complet
        WHERE "year" = ANY(%s) GROUP BY 1 ORDER BY 1
    """, (ANNEES,))
    print("\nMCD64 disponible :")
    for r in cur.fetchall():
        print(f"  {r[0]} : {r[1]} feux, {r[2]} ha")

    cur.execute("""
        SELECT annee_ref, COUNT(*) FROM biotique.wood_burning_spatial
        WHERE annee_ref::int = ANY(%s) GROUP BY 1
    """, (ANNEES,))
    deja = cur.fetchall()
    if deja:
        print(f"\nwood_burning_spatial contient déjà : {deja} — abandon.")
        conn.close()
        return

    if DRY_RUN:
        print("\n[DRY-RUN] Aucune modification.")
        conn.rollback()
        conn.close()
        return

    # ── 1. Insertion des polygones ────────────────────────────────────────────
    cur.execute("SELECT COALESCE(MAX(gid), 0) FROM biotique.wood_burning_spatial")
    max_gid = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO biotique.wood_burning_spatial
            (gid, the_geom, jour, annee_ref, numreg, nomreg, date)
        SELECT
            %s + row_number() OVER (ORDER BY "year", "BurnDate"),
            geom,
            "BurnDate",
            "year"::text,
            '03', 'Guyane', NULL
        FROM biotique.mcd64_guyane_complet
        WHERE "year" = ANY(%s)
    """, (max_gid, ANNEES))
    print(f"\nInséré {cur.rowcount} polygones dans wood_burning_spatial "
          f"(gid {max_gid + 1}+)")

    # ── 2. Relance synthese_biotique ──────────────────────────────────────────
    print("Relance synthese.synthese_biotique() (~70 s)...")
    cur.execute("SELECT synthese.synthese_biotique()")

    conn.commit()
    print("Commit OK.")

    # ── Vérification ──────────────────────────────────────────────────────────
    cur.execute("""
        SELECT annee_ref::int, ROUND(SUM(emi)::numeric, 0)
        FROM synthese.emissions_totales
        WHERE schema_source='biotique' AND numreg='03' AND snap='110301'
          AND polluant='CO2BIO'
        GROUP BY 1 ORDER BY 1
    """)
    print("\nsnap 110301 CO2BIO après fix :")
    for r in cur.fetchall():
        marque = '  ← nouveau' if r[0] in ANNEES else ''
        print(f"  {r[0]}  {r[1]} t{marque}")

    conn.close()
    print("\nTerminé. Lancer maintenant la spatialisation commune :")
    print("  python3 systems/patch_spatialisation_schema.py "
          "--schema-source biotique --annees 2013,2020")


if __name__ == '__main__':
    main()
