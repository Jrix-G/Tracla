# -*- coding: utf-8 -*-
"""Test de fumee sur l'executable construit (utilise par GitHub Actions).

Lance dist/Transcripteur/Transcripteur.exe, attend le serveur, deroule le
parcours complet, puis verifie que /api/quitter arrete bien le processus.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "tests"))
from parcours import parcours  # noqa: E402

EXE = os.path.join(RACINE, "dist", "Transcripteur",
                   "Transcripteur.exe" if os.name == "nt" else "Transcripteur")
VERROU = os.path.join(tempfile.gettempdir(), "transcripteur.port")


def attendre_serveur(delai=90):
    fin = time.time() + delai
    while time.time() < fin:
        if os.path.exists(VERROU):
            try:
                port = int(open(VERROU, encoding="utf-8").read().strip())
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/config" % port, timeout=2) as r:
                    json.load(r)
                return port
            except Exception:
                pass
        time.sleep(1)
    return None


def main():
    if not os.path.exists(EXE):
        print("[KO] executable introuvable : %s" % EXE)
        return 1
    if os.path.exists(VERROU):
        os.remove(VERROU)

    env = dict(os.environ, TRANSCRIPTEUR_SANS_NAVIGATEUR="1")
    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE), env=env)
    try:
        port = attendre_serveur()
        if not port:
            print("[KO] le serveur n'a pas demarre dans les temps")
            return 1
        print("[ok] serveur demarre sur le port %d" % port)

        base = "http://127.0.0.1:%d" % port
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            page = r.read().decode("utf-8")
        if "Glisse ton fichier audio ici" not in page:
            print("[KO] l'interface embarquee n'est pas servie")
            return 1
        print("[ok] interface embarquee servie (ui/ present dans l'exe)")

        if not parcours(base, modele="base"):
            return 1

        # Arret propre par l'interface
        req = urllib.request.Request(base + "/api/quitter", data=b"{}",
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
        try:
            proc.wait(timeout=20)
            print("[ok] arret propre via /api/quitter (code %s)" % proc.returncode)
        except subprocess.TimeoutExpired:
            print("[KO] le processus ne s'est pas arrete")
            return 1
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
