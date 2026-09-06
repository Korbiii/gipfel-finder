"""Dreistufige Pipeline des Gipfel-Finders.

Stufe 1  pipeline.fahrplan – erreichbare Bahnhöfe (manueller Fahrplan: PDF/TXT-Import → daten/fahrplan.csv)
Stufe 2  pipeline.gipfel    – Gipfel pro Bahnhof (Overpass API)
Stufe 3  pipeline.touren    – Tourenvorschläge pro Gipfel (Outdooractive/Fallback)
Cache    pipeline.cache     – SQLite-Cache (cache.db) für alle drei Stufen
"""