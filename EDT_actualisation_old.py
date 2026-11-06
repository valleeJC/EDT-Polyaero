import sys
import time
import datetime
import zoneinfo
import requests
from icalendar import Calendar

# ==============================================================================
# CONFIGURATION
# ==============================================================================

TEMPS_AFFICHAGE_SECONDES = 5
NB_TENTATIVES_MAX = 20
DELAI_REESSAI_SECONDES = 3

TIMEZONE_FRANCE = zoneinfo.ZoneInfo("Europe/Paris")

URLS_PROMOTIONS = {
    "LP MIA": (
        "https://agenda-web-consult.univ-amu.fr/jsp/custom/modules/plannings/anonymous_cal.jsp?projectId=8&resources=127685,127688,127687&calType=ical&firstDate=2026-08-17&lastDate=2027-08-15"
    ),
    "BUT 3 SNRV": (
        "https://agenda-web-consult.univ-amu.fr/jsp/custom/modules/plannings/anonymous_cal.jsp?projectId=8&resources=58374,58375&calType=ical&firstDate=2026-08-17&lastDate=2027-08-15"
    ),
    "M1 MPAD": (
        "https://agenda-web-consult.univ-amu.fr/jsp/custom/modules/plannings/anonymous_cal.jsp?projectId=8&resources=70202&calType=ical&firstDate=2026-08-17&lastDate=2027-08-15"
    ),
    "M2 MPAD": (
        "https://agenda-web-consult.univ-amu.fr/jsp/custom/modules/plannings/anonymous_cal.jsp?projectId=8&resources=391&calType=ical&firstDate=2026-08-17&lastDate=2027-08-15"
    ),
}

MOIS_FR = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
    7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"
}
JOURS_FR = {
    0: "Lundi", 1: "Mardi", 2: "Mercredi", 3: "Jeudi", 4: "Vendredi", 5: "Samedi", 6: "Dimanche"
}


# ==============================================================================
# FONCTIONS DE RÉCUPÉRATION ET REQUÊTES ADE (10 TENTATIVES)
# ==============================================================================

def recuperer_ics_avec_retry(url, nb_essais=NB_TENTATIVES_MAX, delai=DELAI_REESSAI_SECONDES):
    """Effectue jusqu'à nb_essais tentatives pour télécharger le flux ICS d'ADE."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    for essai in range(1, nb_essais + 1):
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200 and b"BEGIN:VCALENDAR" in res.content:
                return res.content
            print(
                f"  [ATTENTE] Tentative {essai}/{nb_essais} échouée (Status {res.status_code}). Nouvelle tentative dans {delai}s...")
        except Exception as e:
            print(f"  [ATTENTE] Tentative {essai}/{nb_essais} en erreur ({e}). Nouvelle tentative dans {delai}s...")
        time.sleep(delai)

    print(f"[ERREUR ABSOLUE] Échec de connexion ADE après {nb_essais} tentatives pour : {url}", file=sys.stderr)
    return None


def normaliser_datetime(dt):
    """Convertit les objets date/datetime vers le fuseau horaire Europe/Paris."""
    if isinstance(dt, datetime.datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(TIMEZONE_FRANCE)
        return dt.astimezone(TIMEZONE_FRANCE)
    elif isinstance(dt, datetime.date):
        return datetime.datetime.combine(dt, datetime.time.min, tzinfo=TIMEZONE_FRANCE)
    return dt


def recuperer_evenements_ics(url):
    """Télécharge le fichier ICS et convertit l'ensemble des dates à l'heure française."""
    contenu = recuperer_ics_avec_retry(url)
    if not contenu:
        return []

    try:
        cal = Calendar.from_ical(contenu)
        evenements = []
        for component in cal.walk('vevent'):
            debut = normaliser_datetime(component.get('dtstart').dt)
            fin = normaliser_datetime(component.get('dtend').dt)

            summary = str(component.get('summary', ''))
            location = str(component.get('location', 'Inconnue'))
            description = str(component.get('description', ''))

            evenements.append({
                'titre': summary,
                'debut': debut,
                'fin': fin,
                'salle': location,
                'description': description
            })
        return evenements
    except Exception as e:
        print(f"[ERREUR PARSING ICS] {e}", file=sys.stderr)
        return []


def extraire_details(event, promo):
    """Extrait le nom du cours, l'enseignant et cible l'affectation TD1 / TD2 / PROMO."""
    titre = event['titre']
    desc = event['description']
    salle = event['salle']

    lignes = [l.strip() for l in desc.split('\n') if l.strip()]
    enseignant = ""
    groupe = "PROMO"  # Par défaut : cours commun

    if promo == "LP MIA":
        texte_complet = (titre + " " + desc).upper()
        a_td1 = "TD1" in texte_complet or "TD 1" in texte_complet or "G1" in texte_complet
        a_td2 = "TD2" in texte_complet or "TD 2" in texte_complet or "G2" in texte_complet

        if a_td1 and not a_td2:
            groupe = "TD1"
        elif a_td2 and not a_td1:
            groupe = "TD2"
        else:
            groupe = "PROMO"

    for l in lignes:
        if ("," in l or " " in l) and not enseignant and "Exporté" not in l and "GROUPE" not in l.upper():
            enseignant = l

    return {
        'titre': titre,
        'debut': event['debut'],
        'fin': event['fin'],
        'salle': salle,
        'enseignant': enseignant,
        'groupe': groupe
    }


def filtrer_semaine_et_jour(evenements, promo):
    """Filtre les événements pour la semaine courante et le jour J."""
    aujourdhui = datetime.datetime.now(TIMEZONE_FRANCE).date()
    debut_semaine = aujourdhui - datetime.timedelta(days=aujourdhui.weekday())
    fin_semaine = debut_semaine + datetime.timedelta(days=6)

    cours_semaine = []
    cours_aujourdhui = []

    for ev in evenements:
        date_ev = ev['debut'].date()
        details = extraire_details(ev, promo)

        if debut_semaine <= date_ev <= fin_semaine:
            cours_semaine.append(details)
        if date_ev == aujourdhui:
            cours_aujourdhui.append(details)

    cours_semaine.sort(key=lambda x: x['debut'])
    cours_aujourdhui.sort(key=lambda x: x['debut'])

    return cours_semaine, cours_aujourdhui


def grouper_par_creneaux(liste_cours):
    """Regroupe les cours ayant les mêmes heures de début/fin sur un même créneau horaire."""
    creneaux = []
    for c in liste_cours:
        cle_creneau = (c['debut'], c['fin'])
        trouve = False
        for cr in creneaux:
            if cr['debut'] == c['debut'] and cr['fin'] == c['fin']:
                cr['cours'].append(c)
                trouve = True
                break
        if not trouve:
            creneaux.append({
                'debut': c['debut'],
                'fin': c['fin'],
                'cours': [c]
            })
    return creneaux


# ==============================================================================
# GÉNÉRATION DU CODE HTML / JS
# ==============================================================================

def generer_carte_cours_html(c):
    """Génère la structure HTML d'une carte de cours."""
    badge_groupe = f'<span class="badge-group">{c["groupe"]}</span>' if c['groupe'] != "PROMO" else ''

    return f"""
    <div class="event-card">
        <div class="event-header">
            <span class="event-time">{c['debut'].strftime('%H:%M')} - {c['fin'].strftime('%H:%M')}</span>
            {badge_groupe}
        </div>
        <div class="event-title">{c['titre']}</div>
        <div class="event-room">📍 {c['salle']}</div>
        {f'<div class="event-teacher">👤 {c["enseignant"]}</div>' if c['enseignant'] else ''}
    </div>
    """


def generer_creneau_html(creneau):
    """Affiche une rangée rigide côte à côte si TD1/TD2 en parallèle, ou 100% si PROMO."""
    cours_liste = creneau['cours']

    # S'il n'y a qu'un cours promo (ou 1 seul cours)
    if len(cours_liste) == 1:
        c = cours_liste[0]
        return f'<div class="slot-row promo-slot">{generer_carte_cours_html(c)}</div>'

    # S'il y a 2 cours (TD1 et TD2 en parallèle)
    td1_cours = next((c for c in cours_liste if c['groupe'] == 'TD1'), None)
    td2_cours = next((c for c in cours_liste if c['groupe'] == 'TD2'), None)

    html = '<div class="slot-row split-slot">'
    html += generer_carte_cours_html(td1_cours) if td1_cours else '<div class="event-card empty-card"></div>'
    html += generer_carte_cours_html(td2_cours) if td2_cours else '<div class="event-card empty-card"></div>'
    html += '</div>'
    return html


def generer_page_html(donnees_promos):
    slides_html = ""
    jours_semaine = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]

    aujourdhui_dt = datetime.datetime.now(TIMEZONE_FRANCE).date()
    debut_semaine = aujourdhui_dt - datetime.timedelta(days=aujourdhui_dt.weekday())

    index_slide = 0
    for promo, data in donnees_promos.items():
        slides_html += f"""
        <div class="slide {'active' if index_slide == 0 else ''}" id="slide-{index_slide}">
            <h2>Planning Semaine — <span class="highlight">{promo}</span></h2>
            <div class="week-grid">
        """

        for i, nom_jour in enumerate(jours_semaine):
            date_jour = debut_semaine + datetime.timedelta(days=i)
            cours_du_jour = [c for c in data['semaine'] if c['debut'].date() == date_jour]
            creneaux_du_jour = grouper_par_creneaux(cours_du_jour)

            slides_html += f"""
            <div class="day-column {'today' if date_jour == aujourdhui_dt else ''}">
                <div class="day-header">{nom_jour} {date_jour.strftime('%d/%m')}</div>
                <div class="events-container">
            """

            if not creneaux_du_jour:
                slides_html += '<div class="no-event">Aucun cours</div>'
            else:
                for cr in creneaux_du_jour:
                    slides_html += generer_creneau_html(cr)
            slides_html += "</div></div>"

        slides_html += "</div></div>"
        index_slide += 1

    # Date du jour
    nom_jour_fr = JOURS_FR[aujourdhui_dt.weekday()]
    nom_mois_fr = MOIS_FR[aujourdhui_dt.month]
    date_jour_str = f"{nom_jour_fr} {aujourdhui_dt.day} {nom_mois_fr} {aujourdhui_dt.year}"

    # Slide Synthèse du jour
    slides_html += f"""
    <div class="slide" id="slide-{index_slide}">
        <h2>Aujourd'hui à Polyaéro — <span class="highlight">{date_jour_str}</span></h2>
        <div class="today-grid">
    """
    for promo, data in donnees_promos.items():
        creneaux_aujourdhui = grouper_par_creneaux(data['aujourdhui'])
        slides_html += f"""
        <div class="today-column">
            <div class="promo-header">{promo}</div>
            <div class="events-container">
        """
        if not creneaux_aujourdhui:
            slides_html += '<div class="no-event">Pas de cours aujourd\'hui</div>'
        else:
            for cr in creneaux_aujourdhui:
                slides_html += generer_creneau_html(cr)
        slides_html += "</div></div>"
    slides_html += "</div></div>"

    nb_total_slides = len(donnees_promos) + 1

    html_doc = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Affichage TV Polyaéro Tallard</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            overflow: hidden;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        header {{
            background-color: #1e293b;
            padding: 15px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #334155;
        }}
        header h1 {{
            font-size: 1.8rem;
            color: #38bdf8;
        }}
        #clock {{
            font-size: 1.8rem;
            font-weight: bold;
            color: #f1f5f9;
        }}
        main {{
            flex: 1;
            position: relative;
            padding: 20px;
        }}
        .slide {{
            display: none;
            height: 100%;
            flex-direction: column;
        }}
        .slide.active {{
            display: flex;
        }}
        h2 {{
            font-size: 1.5rem;
            margin-bottom: 15px;
            color: #cbd5e1;
        }}
        .highlight {{
            color: #38bdf8;
        }}

        .week-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 15px;
            height: calc(100% - 40px);
        }}
        .day-column {{
            background-color: #1e293b;
            border-radius: 10px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            border: 1px solid #334155;
        }}
        .day-column.today {{
            border: 2px solid #38bdf8;
        }}
        .day-header {{
            font-weight: bold;
            text-align: center;
            padding-bottom: 8px;
            margin-bottom: 10px;
            border-bottom: 1px solid #475569;
            color: #94a3b8;
        }}
        .day-column.today .day-header {{
            color: #38bdf8;
        }}

        .today-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            height: calc(100% - 40px);
        }}
        .today-column {{
            background-color: #1e293b;
            border-radius: 10px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            border: 1px solid #334155;
        }}
        .promo-header {{
            font-weight: bold;
            text-align: center;
            padding-bottom: 8px;
            margin-bottom: 10px;
            border-bottom: 1px solid #475569;
            color: #38bdf8;
            font-size: 1.2rem;
        }}

        /* Structure par Créneaux horaires strictes */
        .events-container {{
            flex: 1;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .slot-row {{
            display: flex;
            gap: 8px;
            width: 100%;
        }}
        .split-slot .event-card {{
            flex: 1;
            width: 50%;
        }}
        .promo-slot .event-card {{
            width: 100%;
        }}

        .event-card {{
            background-color: #334155;
            padding: 8px 10px;
            border-radius: 6px;
            border-left: 4px solid #38bdf8;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}
        .event-card.empty-card {{
            background-color: transparent;
            border: none;
        }}
        .event-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .event-time {{
            font-size: 0.8rem;
            font-weight: bold;
            color: #cbd5e1;
        }}
        .badge-group {{
            background-color: #f59e0b;
            color: #0f172a;
            font-size: 0.7rem;
            font-weight: bold;
            padding: 1px 5px;
            border-radius: 4px;
        }}
        .event-title {{
            font-size: 0.85rem;
            font-weight: 600;
            margin: 4px 0;
            color: #ffffff;
        }}
        .event-room, .event-teacher {{
            font-size: 0.75rem;
            color: #94a3b8;
        }}
        .no-event {{
            text-align: center;
            color: #64748b;
            font-style: italic;
            margin-top: 20px;
        }}

        footer {{
            background-color: #1e293b;
            height: 8px;
            width: 100%;
            position: relative;
        }}
        #progress-bar {{
            height: 100%;
            background-color: #38bdf8;
            width: 0%;
            transition: width 0.1s linear;
        }}
    </style>
</head>
<body>
    <header>
        <h1>Polyaéro Tallard — Emploi du Temps</h1>
        <div id="clock">00:00:00</div>
    </header>

    <main>
        {slides_html}
    </main>

    <footer>
        <div id="progress-bar"></div>
    </footer>

    <script>
        function updateClock() {{
            const now = new Date();
            document.getElementById('clock').textContent = now.toLocaleTimeString('fr-FR');
        }}
        setInterval(updateClock, 1000);
        updateClock();

        const totalSlides = {nb_total_slides};
        const duration = {TEMPS_AFFICHAGE_SECONDES} * 1000;
        let currentSlide = 0;
        let startTime = Date.now();

        function switchSlide() {{
            document.getElementById(`slide-${{currentSlide}}`).classList.remove('active');
            currentSlide = (currentSlide + 1) % totalSlides;
            document.getElementById(`slide-${{currentSlide}}`).classList.add('active');
            startTime = Date.now();
        }}

        function updateProgressBar() {{
            const elapsed = Date.now() - startTime;
            const percentage = Math.min((elapsed / duration) * 100, 100);
            document.getElementById('progress-bar').style.width = percentage + '%';

            if (elapsed >= duration) {{
                switchSlide();
            }}
        }}

        setInterval(updateProgressBar, 100);
    </script>
</body>
</html>
"""
    return html_doc


# ==============================================================================
# SCRIPT PRINCIPAL
# ==============================================================================

def main():
    print("[INFO] Récupération des plannings ADE (Aix-Marseille Université)...")
    donnees_promos = {}

    for promo, url in URLS_PROMOTIONS.items():
        print(f"  -> Traitement : {promo}")
        evenements = recuperer_evenements_ics(url)
        semaine, aujourdhui = filtrer_semaine_et_jour(evenements, promo)
        donnees_promos[promo] = {
            'semaine': semaine,
            'aujourdhui': aujourdhui
        }

    print("[INFO] Génération de la page HTML TV...")
    html_output = generer_page_html(donnees_promos)

    nom_fichier = "index.html"
    with open(nom_fichier, "w", encoding="utf-8") as f:
        f.write(html_output)

    print(f"[SUCCÈS] Fichier '{nom_fichier}' généré avec succès !")


if __name__ == "__main__":
    main()
