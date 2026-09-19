# -*- coding: utf-8 -*-
"""La fenetre de connexion, depuis l'EXE CONSTRUIT (pas depuis le script).

C'est le point que le packaging peut casser sans que rien d'autre ne bouge :
Playwright embarque un binaire node dans playwright/driver/, et PyInstaller
doit le livrer. Ce test lance dist/Transcripteur/Transcripteur.exe avec
--fenetre-connexion, comme le fait l'application, contre le faux Moodle.

Deroule :
  1. la fenetre s'ouvre sur le cours, le faux serveur repond sa page de
     CONNEXION en 200 -> la fenetre ne doit surtout pas croire que c'est bon ;
  2. on simule l'utilisateur qui s'identifie (le faux serveur se met a poser
     le cookie) -> la fenetre doit detecter l'audio et rendre les cookies ;
  3. le profil doit rester sur le disque, pour que la session soit memorisee.

  python tests/uness_fenetre_exe.py [--visible]

Une vraie fenetre Edge s'ouvre pendant ~15 s. C'est voulu : c'est justement
ce qu'on veut voir marcher.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
sys.path.insert(0, ICI)

import faux_uness                                          # noqa: E402

EXE = os.path.join(RACINE, "dist", "Transcripteur", "Transcripteur.exe")
# --source : meme test, mais sur le script Python, pour valider la logique
# sans attendre un rebuild complet.
if "--source" in sys.argv:
    EXE = None

OK, KO = [], []


def verifie(nom, condition, detail=""):
    (OK if condition else KO).append(nom)
    print(("  [ok] " if condition else "  [KO] ") + nom +
          (" — " + str(detail) if detail else ""))
    return bool(condition)


def main():
    lancement = ([sys.executable, os.path.join(RACINE, "app.py")] if EXE is None
                 else [EXE])
    if EXE is not None and not os.path.isfile(EXE):
        print("[KO] executable introuvable : %s\n"
              "     Lance build.bat d'abord." % EXE)
        return 1

    travail = tempfile.mkdtemp(prefix="uness-fenetre-")
    profil = os.path.join(travail, "profil")
    sortie = os.path.join(travail, "resultat.json")
    srv, base = faux_uness.demarrer(nb_diapos=4)
    url_index = base + "index.htm"
    # Cas de la PREMIERE connexion : on ne connait encore aucun mp3 (il faut
    # etre identifie pour lire la page qui les liste). La fenetre doit donc en
    # trouver un elle-meme une fois l'utilisateur connecte. C'est le chemin le
    # plus fragile, donc celui qu'on teste.
    url_test = url_index
    if "--mp3-connu" in sys.argv:
        url_test = base + "data/%s1.mp3" % faux_uness.PREFIXE

    print("Faux UNESS : %s" % url_index)
    print("Une fenetre Edge va s'ouvrir. Ne la touche pas : le test simule "
          "la connexion tout seul.\n")

    try:
        print("[1] Lancement de la fenetre depuis l'exe")
        debut = time.time()
        proc = subprocess.Popen(
            lancement + ["--fenetre-connexion", url_index, profil,
                         url_test, sortie],
            cwd=RACINE if EXE is None else os.path.dirname(EXE),
            env=dict(os.environ, TRANSCRIPTEUR_SANS_NAVIGATEUR="1"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        # On laisse la fenetre s'ouvrir et sonder la page de connexion.
        time.sleep(12)
        verifie("le sous-processus tourne toujours (Playwright a demarre)",
                proc.poll() is None,
                "code %s" % proc.returncode if proc.poll() is not None else "")
        verifie("une page de connexion en 200 ne vaut PAS une session",
                not os.path.exists(sortie))

        print("\n[2] L'utilisateur se connecte (simule)")
        srv.auto_connexion = True

        try:
            journal = proc.communicate(timeout=90)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            journal = b""
            verifie("la fenetre se ferme une fois connectee", False,
                    "toujours ouverte apres 90 s")
        else:
            verifie("la fenetre se ferme une fois connectee", True,
                    "en %.0f s" % (time.time() - debut))

        resultat = {}
        if os.path.exists(sortie):
            with open(sortie, encoding="utf-8") as f:
                resultat = json.load(f)
        verifie("un resultat a ete ecrit", bool(resultat))
        verifie("connexion declaree reussie", resultat.get("ok") is True,
                resultat.get("message"))
        cookies = resultat.get("cookies") or []
        verifie("le cookie de session est remonte",
                any(c["name"] == faux_uness.COOKIE and
                    c["value"] == faux_uness.VALEUR for c in cookies),
                [c["name"] for c in cookies])

        print("\n[3] Session memorisee")
        verifie("le profil du navigateur est sur le disque",
                os.path.isdir(profil) and os.listdir(profil))

        texte = (journal or b"").decode("utf-8", "replace")
        verifie("aucune valeur de cookie dans la sortie du processus",
                faux_uness.VALEUR not in texte,
                texte[-300:] if faux_uness.VALEUR in texte else "")
        if texte.strip() and KO:
            print("\n--- sortie du sous-processus ---\n%s" % texte[-3000:])

    finally:
        srv.shutdown()
        shutil.rmtree(travail, ignore_errors=True)

    print("\n%d reussis, %d echoues" % (len(OK), len(KO)))
    if KO:
        print("Echecs : " + ", ".join(KO))
    return 1 if KO else 0


if __name__ == "__main__":
    sys.exit(main())
