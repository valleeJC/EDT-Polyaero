# Affichage EDT Polyaéro

Écran d'accueil de Polyaéro (Tallard) : emploi du temps des promos, tiré d'ADE (AMU).

- `generer_edt.py` télécharge les flux ADE et écrit `site/data.js`.
- `site/index.html` est la page affichée par la TV. Elle relit `data.js` toutes les 5 minutes
  et calcule elle-même l'heure, le jour affiché et le cours en cours.

## Utilisation locale

```bash
pip install requests icalendar tzdata
python generer_edt.py
python -m http.server 8765 --directory site
```

Puis ouvrir http://localhost:8765.

## Paramètres de test (dans l'URL)

- `?maintenant=2026-11-16T10:15` : simule une date et une heure (heure de Paris).
- `?debug=1` : affiche la résolution, le ratio de pixels et le navigateur de l'écran (utile pour identifier la TV).

## Réglages

En haut de `generer_edt.py` : les promos (ressources ADE et couleurs) et la durée de chaque diapo.
