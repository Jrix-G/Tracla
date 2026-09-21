# -*- coding: utf-8 -*-
"""La fenetre publie-t-elle ce qu'elle voit PENDANT qu'elle cherche ?

Le cas qui compte est celui qui echoue : tant que la fenetre ne trouve rien,
l'utilisateur doit pouvoir lire ce qu'elle interroge. Publier la trace
seulement a la fermeture, ou seulement quand la recherche aboutit, laisse
justement muet le seul cas ou l'information sert.

  python tests/uness_trace_test.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

os.environ["TRANSCRIPTEUR_TEST_UNESS"] = "1"

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
sys.path.insert(0, ICI)
sys.path.insert(0, RACINE)

import faux_uness                                          # noqa: E402

OK, KO = [], []


def verifie(nom, condition, detail=""):
    (OK if condition else KO).append(nom)
    print(("  [ok] " if condition else "  [KO] ") + nom +
          (" — " + str(detail) if detail else ""))
    return bool(condition)


def main():
    travail = tempfile.mkdtemp(prefix="uness-trace-")
    profil = os.path.join(travail, "profil")
    sortie = os.path.join(travail, "resultat.json")
    # Pas de auto_connexion : la fenetre ne trouvera jamais rien. C'est voulu.
    srv, base = faux_uness.demarrer(nb_diapos=3, sso=True)
    url = srv.url_moodle
    print("Faux UNESS (jamais connecte) : %s" % url)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(RACINE, "app.py"),
         "--fenetre-connexion", url, profil, url, sortie],
        cwd=RACINE, env=dict(os.environ, TRANSCRIPTEUR_SANS_NAVIGATEUR="1"),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        print("\n[1] Trace publiee pendant la recherche")
        trace, limite = [], time.time() + 60
        while time.time() < limite:
            try:
                with open(sortie, encoding="utf-8") as f:
                    donnees = json.load(f)
                trace = donnees.get("trace") or []
                if trace:
                    break
            except Exception:
                pass
            time.sleep(1.0)

        verifie("la fenetre tourne toujours (elle n'a rien trouve)",
                proc.poll() is None)
        verifie("une trace est publiee AVANT la fermeture", bool(trace),
                "%d ligne(s)" % len(trace))
        if trace:
            for ligne in trace:
                print("      " + ligne)
            verifie("la trace dit ce qui a ete interroge",
                    any("->" in l or "connexion" in l.lower() for l in trace))
            verifie("aucun jeton d'authentification dans la trace",
                    not any("?" in l.split(" -> ")[0] for l in trace))

        print("\n[2] Trace finale a la fermeture")
        proc.terminate()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
        srv.shutdown()
        if getattr(srv, "idp", None):
            srv.idp.shutdown()
        shutil.rmtree(travail, ignore_errors=True)

    print("\n%d reussis, %d echoues" % (len(OK), len(KO)))
    if KO:
        print("Echecs : " + ", ".join(KO))
    return 1 if KO else 0


if __name__ == "__main__":
    sys.exit(main())
