fonction spatial.decoupe_clc_par('object= iris ou commune ou foret')




┌──────────────────────────────┐
│ spatial.description_objet    │
│ (catalogue)                  │
│                              │
│ objet | nom | dimension      │
└──────┬───────────────────────┘
       │ vérifie dim=2
       ▼
┌──────────────────────────────┐        ┌──────────────────┐
│ spatial.spatial_surfacique   │        │ spatial.clc      │
│ WHERE description=desc_objet │        │                  │
│                              │        │ gid, code,       │
│ (ex: communes de Guyane)     │        │ annee_source,    │
│                              │        │ the_geom         │
└──────────┬───────────────────┘        └────────┬─────────┘
           │                                     │
           │       INTERSECTION GÉOMÉTRIQUE      │
           │       ST_Intersection(a, b)         │
           └──────────────┬──────────────────────┘
                          │
                          │  ON ST_Intersects(a, b)
                          │  + jointure via cor_clc_annee
                          │    pour matcher l'année
                          ▼
                ┌──────────────────────────────┐
                │ spatial.spatial_surfacique   │
                │ WHERE description=           │
                │ 'dec_clc_par_<objet>'        │
                │                              │
                │ = les polygones résultats    │
                └──────────────────────────────┘


Données BDtopo: [text](https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_BD-TOPO)
données bbox pour carto : https://bboxfinder.com/#0.000000,0.000000,0.000000,0.000000