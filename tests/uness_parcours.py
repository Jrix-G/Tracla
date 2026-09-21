# -*- coding: utf-8 -*-
"""Parcours complet de l'import UNESS a travers le vrai serveur Flask.

Complete uness_test.py, qui teste les modules : ici on passe par les routes
HTTP, comme le fait l'interface. On n'utilise volontairement pas Whisper --
la transcription elle-meme est deja couverte par tests/parcours.py.

  python tests/uness_parcours.py
"""

import json
import re
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

os.environ["TRANSCRIPTEUR_TEST_UNESS"] = "1"
os.environ["TRANSCRIPTEUR_SANS_NAVIGATEUR"] = "1"

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
sys.path.insert(0, ICI)
sys.path.insert(0, RACINE)

import faux_uness                                    # noqa: E402

OK, KO = [], []


def verifie(nom, condition, detail=""):
    (OK if condition else KO).append(nom)
    print(("  [ok] " if condition else "  [KO] ") + nom +
          (" — " + str(detail) if detail else ""))
    return bool(condition)


def appel(base, chemin, corps=None, brut=False):
    req = urllib.request.Request(
        base + chemin,
        data=json.dumps(corps).encode("utf-8") if corps is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if corps is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            donnees = r.read()
            return r.status, (donnees.decode("utf-8") if brut
                              else json.loads(donnees or b"{}"))
    except urllib.error.HTTPError as e:
        donnees = e.read()
        try:
            return e.code, json.loads(donnees or b"{}")
        except Exception:
            return e.code, {"corps": donnees.decode("utf-8", "replace")}


def attendre(base, etats, secondes=180):
    """Attend que le job atteigne un de ces etats."""
    limite = time.time() + secondes
    dernier = None
    while time.time() < limite:
        _, c = appel(base, "/api/config")
        dernier = c.get("etat")
        if dernier in etats:
            return dernier
        time.sleep(0.4)
    return dernier


def main():
    travail = tempfile.mkdtemp(prefix="uness-parcours-")
    # parole=True : ce test fait tourner Whisper pour de vrai, il lui faut donc
    # de vrais mots. Avec un signal pur, le filtre anti-hallucination jette (a
    # juste titre) tout ce que Whisper invente, et il ne reste rien a verifier.
    srv, base_cours = faux_uness.demarrer(nb_diapos=10, sans_audio=(4,),
                                          parole=True)

    # Le serveur doit ranger ses dossiers dans le repertoire de test, pas a
    # cote du depot.
    import app                                                   # noqa: E402
    app.DOSSIER_SESSION = os.path.join(travail, "session")
    app.DOSSIER_COURS = os.path.join(travail, "cours")
    from uness import session as uness_session                   # noqa: E402
    app.COFFRE = uness_session.Coffre(app.DOSSIER_SESSION)
    app.CONNEXION = uness_session.Connexion(app.COFFRE)

    port = app.port_libre()
    base = "http://127.0.0.1:%d" % port
    from waitress import serve                                   # noqa: E402
    threading.Thread(
        target=lambda: serve(app.app, host="127.0.0.1", port=port, threads=8),
        daemon=True).start()
    for _ in range(80):
        try:
            appel(base, "/api/config")
            break
        except Exception:
            time.sleep(0.1)

    url_cours = base_cours + "index.htm"
    try:
        print("\n[1] Etat initial : pas de session")
        _, s = appel(base, "/api/uness/etat")
        verifie("aucun cookie enregistre", s["cookies"] is False)
        verifie("le domaine autorise est annonce",
                s["domaine"] == "formation.uness.fr", s["domaine"])
        code, j = appel(base, "/api/uness/demarrer",
                        {"url": url_cours, "modele": "base"})
        verifie("demarrer refuse sans connexion", code == 401, j.get("erreur"))

        print("\n[2] URL hors liste blanche")
        code, j = appel(base, "/api/uness/verifier",
                        {"url": "https://exemple.invalide/cours/index.htm"})
        verifie("refus explicite", code == 400 and "UNESS" in j.get("erreur", ""),
                j.get("erreur"))

        print("\n[3] Cookie colle a la main (le repli)")
        code, j = appel(base, "/api/uness/cookie", {"texte": "   "})
        verifie("un champ vide est refuse", code == 400, j.get("erreur"))
        code, j = appel(base, "/api/uness/cookie",
                        {"texte": "%s=%s" % (faux_uness.COOKIE, faux_uness.VALEUR),
                         "url": url_cours})
        verifie("le cookie est accepte", code == 200 and j.get("ok"))

        print("\n[4] Verification de session")
        _, j = appel(base, "/api/uness/verifier", {"url": url_cours})
        verifie("session reconnue valide", j.get("connecte") is True, j.get("message"))
        verifie("titre du cours remonte",
                j.get("titre") == faux_uness.TITRE_COURS, j.get("titre"))
        verifie("10 diapos annoncees", j.get("diapos") == 10, j.get("diapos"))

        # Le point le plus important : Moodle renvoie sa page de login en 200.
        srv.exige_reconnexion = True
        _, j = appel(base, "/api/uness/verifier", {"url": url_cours})
        verifie("une page de login en 200 n'est PAS prise pour une session",
                j.get("connecte") is False, j.get("message"))
        verifie("le message dit quoi faire",
                "reconnecte" in (j.get("message") or "").lower(), j.get("message"))
        srv.exige_reconnexion = False

        print("\n[5] Recuperation de l'audio du cours")
        code, j = appel(base, "/api/uness/demarrer",
                        {"url": url_cours, "modele": "base", "langue": "fr"})
        verifie("demarrage accepte", code == 200 and j.get("ok"), j)

        # On attend que l'audio soit pret, sans attendre la transcription :
        # c'est exactement ce que l'utilisateur doit pouvoir faire.
        pret = False
        limite = time.time() + 120
        while time.time() < limite:
            _, c = appel(base, "/api/config")
            if c.get("chapitres"):
                pret = True
                break
            if c.get("etat") == "erreur":
                break
            time.sleep(0.3)
        _, c = appel(base, "/api/config")
        verifie("chapitres disponibles avant la fin de la transcription", pret,
                c.get("etat"))
        chapitres = c.get("chapitres") or []
        verifie("un chapitre par diapo", len(chapitres) == 10, len(chapitres))
        verifie("diapo 4 marquee sans audio",
                c.get("sans_audio") == [4], c.get("sans_audio"))
        verifie("titre du cours utilise comme nom",
                c.get("titre_cours") == faux_uness.TITRE_COURS, c.get("titre_cours"))
        verifie("duree annoncee coherente",
                abs(c.get("duree", 0) - max(x["fin"] for x in chapitres)) < 0.3,
                "%.2f vs %.2f" % (c.get("duree", 0),
                                  max(x["fin"] for x in chapitres)))

        print("\n[6] L'audio assemble se lit avec des requetes Range")
        req = urllib.request.Request(base + "/api/audio",
                                     headers={"Range": "bytes=0-2047"})
        with urllib.request.urlopen(req, timeout=20) as r:
            entetes = dict(r.headers)
            bloc = r.read()
            code_audio = r.status
        verifie("reponse 206", code_audio == 206, code_audio)
        verifie("Content-Type audio",
                entetes.get("Content-Type", "").startswith("audio/"),
                entetes.get("Content-Type"))
        verifie("Accept-Ranges annonce", entetes.get("Accept-Ranges") == "bytes")
        verifie("2048 octets recus", len(bloc) == 2048, len(bloc))

        # Le seek doit tomber au bon endroit : on demande l'octet correspondant
        # au dernier chapitre et on verifie que le serveur sait le servir.
        with urllib.request.urlopen(
                urllib.request.Request(base + "/api/audio",
                                       headers={"Range": "bytes=0-0"}),
                timeout=20) as r:
            total = int(r.headers["Content-Range"].split("/")[-1])
        verifie("taille du fichier connue du navigateur", total > 10000, total)

        print("\n[7] Transcription structuree par diapo")
        fin = attendre(base, ("fini", "erreur", "arrete"), secondes=600)
        verifie("transcription menee a terme", fin == "fini", fin)

        _, texte = appel(base, "/api/texte?ts=1", brut=True)
        _, md = appel(base, "/api/texte?ts=1&md=1", brut=True)
        _, sans_ts = appel(base, "/api/texte?ts=0", brut=True)
        sortie = app.JOB.sortie
        contenu = ""
        if sortie and os.path.isfile(sortie):
            contenu = open(sortie, encoding="utf-8").read()
            os.remove(sortie)

        # La structure ne depend pas de ce qui a ete dit : elle se verifie
        # sur n'importe quelle machine.
        verifie("le titre du cours ouvre le fichier",
                texte.startswith(faux_uness.TITRE_COURS), texte[:60])
        verifie("le soulignement demande est la", "==============" in texte)
        verifie("variante Markdown : titre en #", md.startswith("# "))
        verifie("variante sans horodatages : plus de [00:00:xx]",
                "[00:00:" not in sans_ts)
        verifie("le .txt est enregistre et ouvre sur le titre du cours",
                contenu.startswith(faux_uness.TITRE_COURS),
                os.path.basename(sortie or "") or "absent")

        # Le CONTENU, lui, suppose que les diapos parlent. Sur une machine
        # sans voix de synthese, ce sont des signaux purs : Whisper n'en tire
        # rien -- c'est le comportement voulu, le filtre anti-hallucination
        # jette ce qu'il invente. Echouer ici ne dirait rien du code ; on le
        # dit clairement et on passe.
        if not faux_uness.PAROLE_REELLE:
            print("  [--] contenu transcrit NON verifie : pas de synthese "
                  "vocale sur cette machine (diapos = signaux purs). "
                  "La structure ci-dessus, elle, est verifiee.")
        else:
            verifie("les intertitres de diapo sont presents",
                    "Diapo 1 — " in texte, texte[:200].replace("\n", " | "))
            verifie("les titres complets sont utilises",
                    faux_uness.TITRES[0] in texte)

            # La verification qui compte vraiment : chaque diapo dit son
            # propre numero ("Diapositive numero 7"). Si le rattachement etait
            # decale, ce numero tomberait sous le mauvais intertitre.
            bloc, attendu, places, egares = None, None, 0, []
            for ligne in texte.splitlines():
                m = re.match(r"^Diapo (\d+) —", ligne)
                if m:
                    bloc, attendu = m.group(1), True
                    continue
                if attendu and ligne.strip().startswith("["):
                    mots = re.search(r"num[ée]ro\s+(\d+)", ligne, re.IGNORECASE)
                    if mots:
                        places += 1
                        if mots.group(1) != bloc:
                            egares.append((bloc, mots.group(1)))
                    attendu = False
            if not verifie("le numero dit par chaque diapo tombe sous le bon "
                           "intertitre", places >= 6 and not egares,
                           "%d diapos verifiees, egarees : %s"
                           % (places, egares)):
                print("\n--- texte obtenu ---\n%s\n--- fin ---\n" % texte)
                print("chapitres : %s" % [(c["n"], c["debut"], c["fin"])
                                          for c in chapitres])

            verifie("variante Markdown : diapos en ##", "\n## Diapo 1" in md)
            verifie("variante sans horodatages : les diapos restent",
                    "Diapo 1 — " in sans_ts)
            verifie("le .txt enregistre a la structure par diapo",
                    "Diapo 1 — " in contenu, os.path.basename(sortie or ""))

        print("\n[8] Deconnexion")
        code, _ = appel(base, "/api/uness/deconnexion", {})
        _, s = appel(base, "/api/uness/etat")
        verifie("la session effacee ne laisse rien", s["cookies"] is False)

    finally:
        srv.shutdown()
        shutil.rmtree(travail, ignore_errors=True)

    print("\n%d reussis, %d echoues" % (len(OK), len(KO)))
    if KO:
        print("Echecs : " + ", ".join(KO))
    return 1 if KO else 0


if __name__ == "__main__":
    sys.exit(main())
