"""
Génère les données d'emploi du temps affichées sur l'écran de Polyaéro.

Le script télécharge les flux ICS d'ADE (AMU) pour chaque promo, les nettoie
(titre, type de cours, groupe, salle, enseignants) et écrit `site/data.js`.
La page `site/index.html` (statique) relit ce fichier toutes les 5 minutes :
l'heure, le jour affiché et le cours "en cours" sont calculés par l'écran
lui-même, il n'y a donc rien à régénérer à minuit.

Si ADE ne répond pas pour une promo, on réutilise le dernier flux valide
(dossier `cache/`) au lieu d'annuler toute la mise à jour.

Usage : python generer_edt.py
"""

import datetime
import json
import os
import re
import sys
import time
import zoneinfo

import requests
from icalendar import Calendar

# ==============================================================================
# CONFIGURATION
# ==============================================================================

URL_ADE = "https://agenda-web-consult.univ-amu.fr/jsp/custom/modules/plannings/anonymous_cal.jsp"
PROJET_ADE = 8

# Nom affiché, ressources ADE, couleur de la promo à l'écran (dans l'ordre d'affichage)
PROMOTIONS = [
    {"nom": "BUT 3 SNRV", "ressources": "58374,58375",          "couleur": "#a78bfa"},
    {"nom": "LP MIA",     "ressources": "127685,127688,127687", "couleur": "#38bdf8"},
    {"nom": "M1 MPAD",    "ressources": "70202",                "couleur": "#34d399"},
    {"nom": "M2 MPAD",    "ressources": "391",                  "couleur": "#fbbf24"},
]

# Durées d'affichage des diapositives (secondes), transmises à la page
DUREE_DIAPO_JOUR = 20
DUREE_DIAPO_SEMAINE = 12
DUREE_DIAPO_SEMAINE_VIDE = 4   # promo sans cours de la semaine

# Événements ADE qui ne sont pas des cours (période en entreprise...) : ignorés
MOTS_SANS_COURS = ["entreprise"]

# ADE limite le nombre de requêtes ("Too much requests", HTTP 408) :
# peu de tentatives, espacées, plutôt que beaucoup de tentatives rapprochées.
DELAIS_REESSAI_SECONDES = [10, 30, 60]
PAUSE_ENTRE_PROMOS_SECONDES = 3
TIMEOUT_SECONDES = 30

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_CACHE = os.path.join(DOSSIER, "cache")
FICHIER_SORTIE = os.path.join(DOSSIER, "site", "data.js")

TZ_PARIS = zoneinfo.ZoneInfo("Europe/Paris")

# ==============================================================================
# TÉLÉCHARGEMENT
# ==============================================================================


def url_promo(promo, debut, fin):
    """URL ADE limitée à la fenêtre utile (plus léger qu'une année entière)."""
    return (f"{URL_ADE}?projectId={PROJET_ADE}&resources={promo['ressources']}"
            f"&calType=ical&firstDate={debut:%Y-%m-%d}&lastDate={fin:%Y-%m-%d}")


def telecharger_ics(url):
    """Télécharge un flux ICS, avec quelques tentatives espacées. Renvoie None si échec."""
    entetes = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for essai, delai in enumerate([0] + DELAIS_REESSAI_SECONDES, start=1):
        if delai:
            print(f"    nouvelle tentative dans {delai}s...")
            time.sleep(delai)
        try:
            rep = requests.get(url, headers=entetes, timeout=TIMEOUT_SECONDES)
            if rep.status_code == 200 and b"BEGIN:VCALENDAR" in rep.content:
                return rep.content
            print(f"    tentative {essai} : HTTP {rep.status_code}")
        except requests.RequestException as e:
            print(f"    tentative {essai} : {e.__class__.__name__}")
    return None


def chemin_cache(promo):
    return os.path.join(DOSSIER_CACHE, re.sub(r"\W+", "_", promo["nom"]) + ".ics")


def obtenir_ics(promo, debut, fin):
    """Renvoie (contenu ICS, date du flux, est_perime). Utilise le cache si ADE ne répond pas."""
    contenu = telecharger_ics(url_promo(promo, debut, fin))
    cache = chemin_cache(promo)
    if contenu:
        os.makedirs(DOSSIER_CACHE, exist_ok=True)
        with open(cache, "wb") as f:
            f.write(contenu)
        return contenu, datetime.datetime.now(TZ_PARIS), False
    if os.path.exists(cache):
        date_cache = datetime.datetime.fromtimestamp(os.path.getmtime(cache), TZ_PARIS)
        print(f"    ADE injoignable : utilisation du cache du {date_cache:%d/%m %H:%M}")
        with open(cache, "rb") as f:
            return f.read(), date_cache, True
    print("    ADE injoignable et aucun cache disponible", file=sys.stderr)
    return None, None, True

# ==============================================================================
# NETTOYAGE DES ÉVÉNEMENTS
# ==============================================================================

# Morceaux de titre qui décrivent le type de cours (ex. "... - TD1", "... - Cours")
TYPES = [
    (re.compile(r"^(CM|cours|amphi)$", re.I), "CM"),
    (re.compile(r"^TD\s?\d*$", re.I), "TD"),
    (re.compile(r"^TP\s?\d*$", re.I), "TP"),
    (re.compile(r"^(test|examen|exam|DS|partiel|contr[ôo]le|CC)$", re.I), "Examen"),
]
RE_SEMESTRE = re.compile(r"^S\d{1,2}$")
RE_CODE_MODULE = re.compile(r"^R\d+\.\d+\s+")      # "R5.07 Métrologie" -> "Métrologie"
RE_GROUPE = re.compile(r"\b(TD|TP|G)\s?(\d)\b", re.I)
# Enseignant ADE : "NOM Prénom" (nom en majuscules, au moins un prénom en minuscules)
RE_ENSEIGNANT = re.compile(r"^[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ'\-]+(?: [A-ZÀ-ÖØ-Þ'\-]{2,})*(?: [A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'\-]+)+$")


def nettoyer_titre(resume, nom_promo):
    """'S5 - Techniques Digitales - TD1' -> ('Techniques Digitales', 'TD')."""
    morceaux = [m.strip() for m in re.split(r"\s+[-_–]\s+|\s+_\s*", resume) if m.strip()]
    mots_promo = set(nom_promo.upper().split())
    type_cours = ""
    garde = []
    for i, m in enumerate(morceaux):
        if i == 0 and RE_SEMESTRE.match(m):
            continue
        if m.upper() in mots_promo:
            continue
        trouve = next((t for r, t in TYPES if r.match(m)), None)
        if trouve and i > 0:
            type_cours = type_cours or trouve
            continue
        garde.append(m)
    titre = " – ".join(garde) or resume
    titre = RE_CODE_MODULE.sub("", titre)
    titre = re.sub(r"\s+(TD|TP|G)\s?\d$", "", titre, flags=re.I)  # le groupe est affiché à part
    return titre[:1].upper() + titre[1:], type_cours


def nettoyer_salle(lieu):
    """'SALLE TD-BLOCH' -> 'TD-BLOCH'. Plusieurs salles possibles, séparées par des virgules."""
    salles = [re.sub(r"^salle\s+", "", s.strip(), flags=re.I) for s in lieu.split(",")]
    return ", ".join(s for s in salles if s)


def nom_court(enseignant):
    """'CHAIX Jean Francois' -> 'J. F. Chaix'."""
    mots = enseignant.split()
    nom = [m for m in mots if m.isupper()]
    prenoms = [m for m in mots if not m.isupper()]
    initiales = " ".join(p[0] + "." for p in prenoms)
    return f"{initiales} {' '.join(n.capitalize() for n in nom)}".strip()


def analyser_evenement(composant, nom_promo):
    debut = composant.get("dtstart").dt
    fin = composant.get("dtend").dt if composant.get("dtend") else debut
    journee = not isinstance(debut, datetime.datetime)
    if journee:
        debut = datetime.datetime.combine(debut, datetime.time(8), TZ_PARIS)
        fin = datetime.datetime.combine(fin, datetime.time(0), TZ_PARIS)
    debut = debut.astimezone(TZ_PARIS)
    fin = fin.astimezone(TZ_PARIS)
    # Événements très longs (stage, période en entreprise...) : bandeau "toute la journée"
    if (fin - debut) >= datetime.timedelta(hours=8):
        journee = True

    resume = str(composant.get("summary", "")).strip()
    description = str(composant.get("description", ""))
    lignes = [l.strip() for l in description.splitlines() if l.strip() and not l.strip().startswith("(")]

    titre, type_cours = nettoyer_titre(resume, nom_promo)
    groupes = sorted({f"{g.upper()}{n}" for g, n in RE_GROUPE.findall(resume + "\n" + "\n".join(lignes))})
    # Plusieurs groupes (ex. TP1 + TP2) = toute la promo
    groupe = groupes[0] if len(groupes) == 1 else ""
    enseignants = [nom_court(l) for l in lignes if RE_ENSEIGNANT.match(l)]
    visio = any("visio" in l.lower() for l in lignes + [resume])
    salle = nettoyer_salle(str(composant.get("location", "")))

    return {
        "debut": int(debut.timestamp() * 1000),
        "fin": int(fin.timestamp() * 1000),
        "titre": titre,
        "type": type_cours,
        "groupe": groupe,
        "salle": salle or ("Visio" if visio else ""),
        "profs": enseignants,
        "visio": visio,
        "journee": journee,
    }


def analyser_ics(contenu, nom_promo):
    cal = Calendar.from_ical(contenu)
    evenements = []
    for composant in cal.walk("vevent"):
        resume = str(composant.get("summary", "")).lower()
        if any(mot in resume for mot in MOTS_SANS_COURS):
            continue
        try:
            evenements.append(analyser_evenement(composant, nom_promo))
        except Exception as e:  # un événement mal formé ne doit pas faire tomber l'affichage
            print(f"    événement ignoré ({e})", file=sys.stderr)
    evenements.sort(key=lambda e: (e["debut"], e["groupe"]))
    return evenements

# ==============================================================================
# SCRIPT PRINCIPAL
# ==============================================================================


def main():
    maintenant = datetime.datetime.now(TZ_PARIS)
    lundi = maintenant.date() - datetime.timedelta(days=maintenant.weekday())
    debut, fin = lundi, lundi + datetime.timedelta(days=13)  # semaine courante + suivante

    promos = []
    for i, promo in enumerate(PROMOTIONS):
        if i:
            time.sleep(PAUSE_ENTRE_PROMOS_SECONDES)
        print(f"[{promo['nom']}]")
        contenu, date_flux, perime = obtenir_ics(promo, debut, fin)
        evenements = analyser_ics(contenu, promo["nom"]) if contenu else []
        print(f"    {len(evenements)} événements")
        promos.append({
            "nom": promo["nom"],
            "couleur": promo["couleur"],
            "maj": int(date_flux.timestamp() * 1000) if date_flux else None,
            "perime": perime,
            "evenements": evenements,
        })

    if all(p["maj"] is None for p in promos):
        print("[ERREUR] Aucune donnée disponible : data.js n'est pas modifié.", file=sys.stderr)
        sys.exit(1)

    donnees = {
        "genere": int(maintenant.timestamp() * 1000),
        # Décalage Paris/UTC : l'écran affiche l'heure de Paris même si son fuseau est mal réglé
        "decalageParis": int(maintenant.utcoffset().total_seconds() * 1000),
        "dureeJour": DUREE_DIAPO_JOUR,
        "dureeSemaine": DUREE_DIAPO_SEMAINE,
        "dureeSemaineVide": DUREE_DIAPO_SEMAINE_VIDE,
        "promos": promos,
    }
    os.makedirs(os.path.dirname(FICHIER_SORTIE), exist_ok=True)
    temporaire = FICHIER_SORTIE + ".tmp"
    with open(temporaire, "w", encoding="utf-8") as f:
        f.write("window.EDT_DATA = ")
        json.dump(donnees, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    os.replace(temporaire, FICHIER_SORTIE)  # écriture atomique : jamais de fichier à moitié écrit
    print(f"[OK] {FICHIER_SORTIE}")


if __name__ == "__main__":
    main()
