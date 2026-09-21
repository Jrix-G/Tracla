# -*- coding: utf-8 -*-
"""Ce qui reste des cours deja recuperes : le contenu du dossier de cache.

Ce module ne connait ni Flask ni Whisper : il lit des fichiers et renvoie des
structures Python. app.py se charge de les exposer.

Deux regles tenues d'un bout a l'autre :

1. **On ne leve jamais.** Un dossier de cache est de l'etat sur disque, qu'une
   coupure de courant ou un disque plein peut laisser a moitie ecrit. Un cours
   illisible doit apparaitre dans la liste (pour pouvoir etre oublie), pas
   faire echouer la page entiere.

2. **On reste leger.** La liste est demandee a chaque affichage de l'accueil :
   on se contente de stat() et de la duree annoncee par l'en-tete du mp3, on ne
   decode jamais un cours en entier.
"""

import json
import os
import re

TAILLE_MINI = 200          # en dessous, le mp3 est un fichier d'erreur


def _cle_sure(cle):
    """Une cle vient du navigateur : elle ne doit designer qu'un sous-dossier
    direct du cache, jamais '..' ni un chemin absolu."""
    cle = (cle or "").strip()
    if not cle or cle in (".", "..") or re.search(r"[\\/]", cle):
        return None
    return cle


def dossier_de(racine, cle):
    cle = _cle_sure(cle)
    if not cle:
        return None
    chemin = os.path.join(racine, cle)
    return chemin if os.path.isdir(chemin) else None


def audio_de(racine, cle):
    """Chemin du mp3 assemble, seulement s'il est reellement lisible."""
    dossier = dossier_de(racine, cle)
    if not dossier:
        return None
    mp3 = os.path.join(dossier, "cours.mp3")
    return mp3 if duree_entete(mp3) is not None else None


def duree_entete(chemin):
    """Duree annoncee par le conteneur, ou None si le fichier ne s'ouvre pas.

    On s'arrete a l'en-tete (plus une trame au pire) : decoder un cours de
    40 min pour dessiner une liste couterait des secondes a chaque affichage.
    """
    try:
        if not os.path.isfile(chemin) or os.path.getsize(chemin) < TAILLE_MINI:
            return None
        import av
        with av.open(chemin) as c:
            flux = next((s for s in c.streams if s.type == "audio"), None)
            if flux is None:
                return None
            if c.duration:
                return c.duration / av.time_base
            if flux.duration and flux.time_base:
                return float(flux.duration * flux.time_base)
            # Pas de duree annoncee : une seule trame suffit a prouver que le
            # fichier se decode, la duree viendra du plan.
            for _ in c.decode(flux):
                return 0.0
            return None
    except Exception:
        return None


def taille_dossier(chemin):
    total = 0
    for base, _dossiers, fichiers in os.walk(chemin):
        for nom in fichiers:
            try:
                total += os.path.getsize(os.path.join(base, nom))
            except OSError:
                continue
    return total


def _plan(dossier):
    """Le chapitres.json du cours, ou {} s'il est absent ou illisible."""
    try:
        with open(os.path.join(dossier, "chapitres.json"), encoding="utf-8") as f:
            donnees = json.load(f)
        return donnees if isinstance(donnees, dict) else {}
    except Exception:
        return {}


def _absentes(dossier):
    try:
        with open(os.path.join(dossier, "sans-audio.json"), encoding="utf-8") as f:
            return [int(n) for n in json.load(f)]
    except Exception:
        return []


def _date(dossier, plan_present):
    """Date de la derniere recuperation. Le plan est ecrit en dernier, donc
    c'est lui qui fait foi ; sinon on se rabat sur ce qui existe."""
    pistes = ["chapitres.json"] if plan_present else []
    pistes += ["cours.mp3", "sans-audio.json", "diapos"]
    for nom in pistes:
        try:
            return int(os.path.getmtime(os.path.join(dossier, nom)))
        except OSError:
            continue
    try:
        return int(os.path.getmtime(dossier))
    except OSError:
        return 0


def _transcriptions(dossier_txt):
    """{stem: (chemin, date)} pour tous les .txt deja produits."""
    trouves = {}
    try:
        for nom in os.listdir(dossier_txt or ""):
            if not nom.lower().endswith(".txt"):
                continue
            chemin = os.path.join(dossier_txt, nom)
            try:
                trouves[os.path.splitext(nom)[0]] = (
                    chemin, int(os.path.getmtime(chemin)))
            except OSError:
                continue
    except Exception:
        return {}
    return trouves


def _transcription_de(titre, textes, nom_fichier):
    """Le .txt produit pour ce cours, s'il existe encore.

    app.py numerote les doublons (« Titre (2).txt ») : on accepte ces
    variantes et on garde la plus recente, qui est celle que l'utilisateur
    vient de lire.
    """
    if not titre or not textes:
        return None
    base = nom_fichier(titre)
    candidats = [v for stem, v in textes.items()
                 if stem == base or stem.startswith(base + " (")]
    if not candidats:
        return None
    chemin, date = max(candidats, key=lambda v: v[1])
    return {"chemin": chemin, "date": date}


def _sans_accent_ni_slash(nom):
    """Repli quand app.py ne fournit pas sa propre fonction de nommage."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", nom).strip(" .")[:120]


def decrire(racine, cle, textes=None, nom_fichier=None):
    """Un cours du cache, ou None si la cle ne designe pas un dossier.

    Ne leve jamais : un cache a moitie ecrit donne une entree avec des champs
    vides, pas une exception.
    """
    dossier = dossier_de(racine, cle)
    if not dossier:
        return None
    nom_fichier = nom_fichier or _sans_accent_ni_slash
    plan = _plan(dossier)
    diapos = plan.get("diapos")
    diapos = diapos if isinstance(diapos, list) else []
    sans_audio = plan.get("sans_audio")
    if not isinstance(sans_audio, list):
        sans_audio = _absentes(dossier)

    mp3 = os.path.join(dossier, "cours.mp3")
    duree_reelle = duree_entete(mp3)
    duree = plan.get("duree")
    if not isinstance(duree, (int, float)) or duree <= 0:
        duree = duree_reelle or 0.0

    titre = plan.get("titre") or ""
    return {
        "cle": cle,
        "titre": titre or cle,
        "url": plan.get("url") or "",
        "diapos": len(diapos),
        "sans_audio": [n for n in sans_audio if isinstance(n, int)],
        "duree": round(float(duree), 3),
        "date": _date(dossier, bool(plan)),
        "taille_mo": round(taille_dossier(dossier) / (1024.0 * 1024.0), 2),
        "audio_pret": duree_reelle is not None,
        "transcription": _transcription_de(
            titre, textes if textes is not None else {}, nom_fichier),
    }


def _tient_quelque_chose(dossier):
    """Ce dossier contient-il la moindre trace de cours ?

    Cache() cree son arborescence des qu'on l'instancie, meme si la
    recuperation echoue aussitot. Un lien mal analyse laisse donc un dossier
    parfaitement vide, qui n'est pas un cours et n'a rien a faire dans la
    liste des cours. On exige au moins un mp3 ou un plan.
    """
    if _plan(dossier):
        return True
    for racine, _, fichiers in os.walk(dossier):
        if any(f.lower().endswith(".mp3") for f in fichiers):
            return True
    return False


def lister(racine, dossier_txt=None, nom_fichier=None):
    """Tous les cours du cache, du plus recent au plus ancien."""
    try:
        cles = sorted(os.listdir(racine))
    except Exception:
        return []
    textes = _transcriptions(dossier_txt) if dossier_txt else {}
    cours = []
    for cle in cles:
        try:
            dossier = dossier_de(racine, cle)
            if not dossier or not _tient_quelque_chose(dossier):
                continue
            fiche = decrire(racine, cle, textes, nom_fichier)
        except Exception:
            fiche = None
        if fiche:
            cours.append(fiche)
    cours.sort(key=lambda c: c["date"], reverse=True)
    return cours


def oublier(racine, cle):
    """Supprime le dossier de cache de ce cours. Ne touche a rien d'autre :
    le .txt de Documents\\Transcriptions appartient a l'utilisateur, pas au
    cache."""
    import shutil
    dossier = dossier_de(racine, cle)
    if not dossier:
        return False
    shutil.rmtree(dossier, ignore_errors=True)
    return not os.path.isdir(dossier)


def rouvrir(racine, cle):
    """Tout ce qu'il faut pour reecouter un cours sans rien retelecharger,
    ou None si son audio n'est pas exploitable."""
    fiche = decrire(racine, cle)
    if not fiche:
        return None
    mp3 = audio_de(racine, cle)
    if not mp3:
        return None
    plan = _plan(os.path.join(racine, cle))
    chapitres = plan.get("diapos")
    chapitres = chapitres if isinstance(chapitres, list) else []
    duree = fiche["duree"]
    if duree <= 0:
        duree = max((c.get("fin", 0) for c in chapitres), default=0.0)
    return {
        "chemin": mp3,
        "titre": fiche["titre"],
        "url": fiche["url"],
        "duree": float(duree),
        "chapitres": chapitres,
        "sans_audio": fiche["sans_audio"],
    }
