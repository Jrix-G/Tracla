# -*- coding: utf-8 -*-
"""Parcours complet contre un serveur Transcripteur deja demarre.

Verifie : upload d'un fichier au nom accentue, flux SSE, reprise du flux
(parametre 'depuis'), requetes Range sur l'audio, sauvegarde incrementale,
export texte, arret propre.

  python tests/parcours.py http://127.0.0.1:PORT [--modele base] [--audio X.wav]
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from outils_test import NOM_PIEGE, audio_de_test  # noqa: E402

OK, KO = [], []


def verifie(nom, condition, detail=""):
    (OK if condition else KO).append(nom)
    print(("  [ok] " if condition else "  [KO] ") + nom +
          (" — " + str(detail) if detail else ""))
    return condition


def lire_flux(url, secondes=6):
    """Lit un flux SSE pendant quelques secondes et renvoie les evenements.

    Une fois le rattrapage termine, le serveur se tait jusqu'au ping suivant :
    un timeout de lecture est donc la fin normale du rattrapage, pas une
    erreur. Bloquer en attendant une ligne rendait le test dependant du
    hasard (course avec le ping de 15 s : gagnee sous Linux, perdue sous
    Windows)."""
    evts = []
    debut = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url),
                                    timeout=secondes) as flux:
            for ligne in flux:
                ligne = ligne.decode("utf-8").strip()
                if ligne.startswith("data: "):
                    evts.append(json.loads(ligne[6:]))
                if time.time() - debut > secondes:
                    break
    except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
        pass
    return evts


def demande(url, data=None, entetes=None, methode=None):
    req = urllib.request.Request(url, data=data, method=methode,
                                 headers=entetes or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def parcours(base, modele="base", chemin_audio=None, duree_max=900):
    print("== Parcours sur %s (modele %s)" % (base, modele))

    # --- audio de test au nom pige (accents, espaces, tiret cadratin) -----
    dossier = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    os.makedirs(dossier, exist_ok=True)
    if chemin_audio:
        source = chemin_audio
    else:
        source = os.path.join(dossier, "source.wav")
        if not os.path.exists(source):
            print("  (fabrication de l'audio de test : %s)" % audio_de_test(source))
    nom_envoye = os.path.splitext(NOM_PIEGE)[0] + os.path.splitext(source)[1]
    octets = open(source, "rb").read()

    # --- config ----------------------------------------------------------
    s, _, corps = demande(base + "/api/config")
    cfg = json.loads(corps)
    verifie("GET /api/config", s == 200 and "modeles" in cfg)

    # --- upload ----------------------------------------------------------
    s, _, corps = demande(base + "/api/upload", data=octets,
                          entetes={"X-Nom-Fichier": quote(nom_envoye),
                                   "Content-Type": "application/octet-stream"})
    rep = json.loads(corps)
    verifie("upload d'un nom accentue (%s)" % nom_envoye,
            s == 200 and rep.get("duree", 0) > 0, rep)
    duree_audio = rep.get("duree", 0)

    # --- Range -----------------------------------------------------------
    for essai in range(20):
        s, h, _ = demande(base + "/api/audio", entetes={"Range": "bytes=0-99"})
        if s != 425:
            break
        time.sleep(1.0)  # transcodage de secours en cours
    verifie("Range partiel -> 206", s == 206, "statut %s" % s)
    verifie("en-tete Content-Range", "bytes 0-99/" in h.get("Content-Range", ""),
            h.get("Content-Range"))
    verifie("Accept-Ranges annonce", h.get("Accept-Ranges") == "bytes")
    s2, h2, c2 = demande(base + "/api/audio", entetes={"Range": "bytes=200-299"})
    verifie("Range au milieu du fichier", s2 == 206 and len(c2) == 100, len(c2))
    taille = int(h.get("Content-Range", "/0").split("/")[-1])
    s3, _, _ = demande(base + "/api/audio",
                       entetes={"Range": "bytes=%d-" % (taille + 10)})
    verifie("Range hors limites -> 416", s3 == 416, "statut %s" % s3)

    # --- lancement -------------------------------------------------------
    s, _, corps = demande(
        base + "/api/start",
        data=json.dumps({"modele": modele, "langue": "fr",
                         "hesitations": True, "vocabulaire": "Kelsen"}).encode(),
        entetes={"Content-Type": "application/json"})
    verifie("POST /api/start", s == 200, corps[:200])

    # --- SSE -------------------------------------------------------------
    segments, fini, erreur = [], False, None
    dernier_i = -1
    debut = time.time()
    req = urllib.request.Request(base + "/api/stream?depuis=0")
    with urllib.request.urlopen(req, timeout=duree_max) as flux:
        for ligne in flux:
            if time.time() - debut > duree_max:
                break
            ligne = ligne.decode("utf-8").strip()
            if not ligne.startswith("data: "):
                continue
            evt = json.loads(ligne[6:])
            if "i" in evt:
                dernier_i = evt["i"]
            if evt["type"] == "segment":
                segments.append(evt)
                if len(segments) <= 3:
                    print("     [%s] %s" % (evt["ts"], evt["texte"][:70]))
            elif evt["type"] == "progres":
                pass
            elif evt["type"] == "etat":
                print("     etat: %s — %s" % (evt["etat"], evt.get("message", "")))
                if evt["etat"] in ("fini", "arrete", "erreur"):
                    fini = True
                    if evt["etat"] == "erreur":
                        erreur = evt.get("message")
                    break
    verifie("flux SSE jusqu'a la fin", fini and not erreur, erreur or "")
    verifie("au moins un segment recu", len(segments) > 0, "%d segments" % len(segments))
    if segments:
        verifie("horodatages croissants",
                all(segments[i]["debut"] <= segments[i + 1]["debut"]
                    for i in range(len(segments) - 1)))
        verifie("horodatages dans la duree du fichier",
                segments[-1]["fin"] <= duree_audio + 5,
                "%.1f vs %.1f" % (segments[-1]["fin"], duree_audio))

    # --- reprise du flux (rechargement de page) --------------------------
    rejoue = lire_flux(base + "/api/stream?depuis=%d" % max(0, dernier_i - 1))
    verifie("reprise SSE depuis un index", len(rejoue) >= 1,
            "%d evenements rejoues" % len(rejoue))
    rejoue_tout = lire_flux(base + "/api/stream?depuis=0")
    verifie("reprise depuis 0 rejoue tous les segments",
            sum(1 for e in rejoue_tout if e["type"] == "segment") == len(segments),
            "%d rejoues / %d attendus"
            % (sum(1 for e in rejoue_tout if e["type"] == "segment"), len(segments)))

    # --- export texte ----------------------------------------------------
    s, _, corps = demande(base + "/api/texte?ts=1")
    avec = corps.decode("utf-8")
    s2, _, corps2 = demande(base + "/api/texte?ts=0")
    sans = corps2.decode("utf-8")
    verifie("export avec horodatages", s == 200 and ("[00:00:" in avec or not segments))
    verifie("export sans horodatages", s2 == 200 and "[00:00:" not in sans)

    # --- sauvegarde incrementale ----------------------------------------
    chemin_txt = None
    for e in rejoue_tout:
        if e["type"] == "fichier":
            chemin_txt = e["chemin"]
    verifie("fichier de sauvegarde annonce", bool(chemin_txt), chemin_txt)
    if chemin_txt:
        verifie("fichier de sauvegarde ecrit sur disque",
                os.path.isfile(chemin_txt) and os.path.getsize(chemin_txt) > 0,
                chemin_txt)
        verifie("nom de fichier accentue conserve",
                "séance" in os.path.basename(chemin_txt),
                os.path.basename(chemin_txt))

    print("\n== %d verifications reussies, %d echecs" % (len(OK), len(KO)))
    for k in KO:
        print("   echec : " + k)
    return not KO


if __name__ == "__main__":
    base = sys.argv[1].rstrip("/")
    args = sys.argv[2:]
    modele = args[args.index("--modele") + 1] if "--modele" in args else "base"
    audio = args[args.index("--audio") + 1] if "--audio" in args else None
    sys.exit(0 if parcours(base, modele, audio) else 1)
