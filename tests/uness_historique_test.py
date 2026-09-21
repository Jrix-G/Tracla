# -*- coding: utf-8 -*-
"""L'historique des cours deja recuperes, et le compte a rebours du
telechargement.

Deux choses sont verifiees ici, et elles n'ont pas d'autre garde-fou :

  - un dossier de cache abime (plan illisible, mp3 tronque, dossier vide) doit
    quand meme apparaitre dans la liste, pour pouvoir etre oublie, et ne
    jamais faire echouer la page entiere ;
  - le temps restant annonce vient du debit REELLEMENT observe, et vaut null
    tant que la mesure ne veut rien dire.

  python tests/uness_historique_test.py
"""

import os
import shutil
import sys
import tempfile
import time

os.environ["TRANSCRIPTEUR_TEST_UNESS"] = "1"
os.environ["TRANSCRIPTEUR_SANS_NAVIGATEUR"] = "1"

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
sys.path.insert(0, ICI)
sys.path.insert(0, RACINE)

import faux_uness                                       # noqa: E402
import app                                              # noqa: E402
from uness import historique, recuperation, telechargement   # noqa: E402

OK, KO = [], []


def verifie(nom, condition, detail=""):
    (OK if condition else KO).append(nom)
    print(("  [ok] " if condition else "  [KO] ") + nom +
          (" — " + str(detail) if detail else ""))
    return bool(condition)


# ---------------------------------------------------------------------------
# Fabrication de faux dossiers de cache
# ---------------------------------------------------------------------------

def poser_cours(racine, cle, titre, mp3_source, nb=3, sans_audio=(2,),
                plan=True, audio=True, quand=None):
    """Un dossier de cache comme en laisse une recuperation reussie."""
    cache = telechargement.Cache(racine, cle)
    if audio:
        shutil.copy(mp3_source, cache.assemble)
    else:
        # Un mp3 tronque : la taille a l'air bonne, rien ne se decode.
        with open(cache.assemble, "wb") as f:
            f.write(b"\xff\xfb" + b"\x00" * 4000)
    chapitres = [{"n": i + 1, "titre": "Diapo %d" % (i + 1),
                  "debut": 2.0 * i, "fin": 2.0 * (i + 1),
                  "fichier": "a24x%d.mp3" % (i + 1)} for i in range(nb)]
    cache.noter_absentes(sans_audio)
    if plan:
        cache.ecrire_chapitres({
            "titre": titre,
            "url": "https://formation.uness.fr/%s/index.htm" % cle,
            "duree": 2.0 * nb,
            "duree_attendue": 2.0 * nb,
            "coherent": True,
            "sans_audio": list(sans_audio),
            "diapos": chapitres,
        })
    else:
        with open(cache.chapitres, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du json")
    if quand:
        for nom in (cache.chapitres, cache.assemble, cache.absentes):
            if os.path.exists(nom):
                os.utime(nom, (quand, quand))
        os.utime(cache.dossier, (quand, quand))
    return cache


class FauxCoffre:
    def lire(self):
        return []


class FauxClient:
    """Telecharge sans reseau, en prenant un temps connu par diapo."""

    def __init__(self, cout=0.05, taille=1500):
        self.cout = cout
        self.taille = taille
        self.appels = 0

    def telecharger(self, url, cible):
        time.sleep(self.cout)
        self.appels += 1
        with open(cible, "wb") as f:
            f.write(b"\x00" * self.taille)
        return self.taille

    def appliquer_cookies(self, cookies):
        pass


def recuperation_factice(travail, diapos, cout=0.05):
    r = recuperation.Recuperation(
        "http://127.0.0.1:9/faux-cours/index.htm", FauxCoffre(),
        os.path.join(travail, "cache-%d" % time.time_ns()), lambda *a, **k: None)
    r.client = FauxClient(cout=cout)
    r.diapos = list(diapos)
    evenements = []
    r.evenement = lambda t, d, durable=True: evenements.append(d)
    return r, evenements


def audio(evenements):
    return [e for e in evenements if e.get("etape") == "audio"]


# ---------------------------------------------------------------------------

def main():
    travail = tempfile.mkdtemp(prefix="uness-historique-")
    racine = os.path.join(travail, "cours")
    textes = os.path.join(travail, "Transcriptions")
    os.makedirs(textes)

    # Un vrai mp3 decodable, fabrique par le faux serveur (lecture seule ici).
    faux_uness.fabriquer_mp3(os.path.join(travail, "src"), [1], [3.0])
    mp3 = os.path.join(travail, "src", "%s1.mp3" % faux_uness.PREFIXE)

    app.DOSSIER_COURS = racine
    app.documents_dir = lambda: travail
    client = app.app.test_client()

    try:
        print("\n[1] Historique vide")
        verifie("un cache inexistant ne leve pas",
                historique.lister(os.path.join(travail, "nulle-part")) == [])
        os.makedirs(racine, exist_ok=True)
        verifie("un cache vide donne une liste vide",
                historique.lister(racine) == [])
        r = client.get("/api/uness/historique")
        verifie("la route repond 200 sans cours",
                r.status_code == 200 and r.get_json() == {"cours": []},
                r.status_code)

        print("\n[2] Un cours complet")
        maintenant = time.time()
        poser_cours(racine, "cours-a", "Sémiologie respiratoire", mp3,
                    nb=4, sans_audio=(2,), quand=maintenant - 100)
        with open(os.path.join(textes, "Sémiologie respiratoire.txt"),
                  "w", encoding="utf-8") as f:
            f.write("texte")
        liste = client.get("/api/uness/historique").get_json()["cours"]
        verifie("un cours listé", len(liste) == 1, len(liste))
        c = liste[0]
        verifie("la clé est le nom du dossier", c["cle"] == "cours-a", c["cle"])
        verifie("le titre vient du plan",
                c["titre"] == "Sémiologie respiratoire", c["titre"])
        verifie("l'URL d'origine est rendue",
                c["url"].endswith("cours-a/index.htm"), c["url"])
        verifie("le nombre de diapos est rendu", c["diapos"] == 4, c["diapos"])
        verifie("les diapos sans audio sont rendues",
                c["sans_audio"] == [2], c["sans_audio"])
        verifie("la durée est rendue", abs(c["duree"] - 8.0) < 0.01, c["duree"])
        verifie("la date est un epoch récent",
                abs(c["date"] - (maintenant - 100)) < 5, c["date"])
        verifie("la place occupée est mesurée",
                c["taille_mo"] > 0, c["taille_mo"])
        verifie("l'audio est déclaré prêt", c["audio_pret"] is True)
        verifie("la transcription est retrouvée",
                c["transcription"] and
                os.path.basename(c["transcription"]["chemin"]) ==
                "Sémiologie respiratoire.txt", c["transcription"])
        verifie("la date de la transcription est un epoch",
                c["transcription"]["date"] > 0, c["transcription"])

        # Le doublon numerote par app.py doit etre reconnu, et le plus recent
        # gagner : c'est celui que l'utilisateur vient de produire.
        recent = os.path.join(textes, "Sémiologie respiratoire (2).txt")
        with open(recent, "w", encoding="utf-8") as f:
            f.write("plus recent")
        os.utime(recent, (time.time() + 60, time.time() + 60))
        c = client.get("/api/uness/historique").get_json()["cours"][0]
        verifie("le doublon « (2) » est reconnu et l'emporte",
                c["transcription"]["chemin"] == recent, c["transcription"])

        print("\n[3] Caches abîmés")
        poser_cours(racine, "cours-casse", "Perdu", mp3, nb=3, sans_audio=(1, 3),
                    plan=False, audio=False, quand=maintenant - 200)
        os.makedirs(os.path.join(racine, "cours-vide"), exist_ok=True)
        liste = client.get("/api/uness/historique").get_json()["cours"]
        par_cle = {c["cle"]: c for c in liste}
        # Un dossier entierement vide n'est PAS un cours : Cache() cree son
        # arborescence des son instanciation, donc un lien mal analyse laisse
        # un dossier vide derriere lui. Observe en vrai : un dossier
        # 'mod-resource' apparaissait dans l'historique de l'utilisateur sous
        # le titre 'mod-resource', sans rien dedans et sans rien a en faire.
        verifie("seuls les dossiers qui contiennent quelque chose sont listés",
                len(liste) == 2, sorted(par_cle))
        casse = par_cle.get("cours-casse") or {}
        verifie("un chapitres.json illisible ne fait pas échouer la liste",
                bool(casse))
        verifie("le titre retombe sur la clé", casse.get("titre") == "cours-casse",
                casse.get("titre"))
        verifie("aucune diapo annoncée sans plan lisible",
                casse.get("diapos") == 0, casse.get("diapos"))
        verifie("les diapos sans audio viennent alors de sans-audio.json",
                casse.get("sans_audio") == [1, 3], casse.get("sans_audio"))
        verifie("un mp3 tronqué n'est pas déclaré prêt",
                casse.get("audio_pret") is False, casse.get("audio_pret"))
        verifie("un cours sans transcription rend null",
                casse.get("transcription") is None, casse.get("transcription"))
        verifie("un dossier entièrement vide n'apparaît pas comme un cours",
                "cours-vide" not in par_cle, sorted(par_cle))
        # Mais un cache abime, lui, reste visible : il contient de vrais
        # fichiers, et l'utilisateur doit pouvoir le voir pour le supprimer.
        verifie("un cache abîmé reste visible pour pouvoir être supprimé",
                "cours-casse" in par_cle, sorted(par_cle))

        print("\n[4] Tri du plus récent au plus ancien")
        poser_cours(racine, "cours-neuf", "Tout frais", mp3, nb=2,
                    quand=maintenant + 500)
        liste = client.get("/api/uness/historique").get_json()["cours"]
        dates = [c["date"] for c in liste]
        verifie("trié par date décroissante", dates == sorted(dates, reverse=True),
                dates)
        verifie("le plus récent est en tête", liste[0]["cle"] == "cours-neuf",
                liste[0]["cle"])

        print("\n[5] Rouvrir un cours en cache")
        app.JOB.reset()
        avant = app.JOB.id
        r = client.post("/api/uness/historique/rouvrir", json={"cle": "cours-a"})
        j = r.get_json()
        verifie("rouvrir répond 200", r.status_code == 200 and j.get("ok"), j)
        verifie("un nouveau job est annoncé", j.get("job") == avant + 1, j)
        verifie("le job pointe sur le mp3 assemblé",
                app.JOB.chemin == os.path.join(racine, "cours-a", "cours.mp3"),
                app.JOB.chemin)
        verifie("le mp3 est aussi la version lisible",
                app.JOB.chemin_lecture == app.JOB.chemin)
        verifie("la durée est rechargée", abs(app.JOB.duree - 8.0) < 0.01,
                app.JOB.duree)
        verifie("le plan est rechargé", len(app.JOB.chapitres) == 4,
                len(app.JOB.chapitres))
        verifie("le titre du cours est rechargé",
                app.JOB.titre_cours == "Sémiologie respiratoire" and
                app.JOB.nom == app.JOB.titre_cours, app.JOB.titre_cours)
        verifie("les diapos sans audio sont rechargées",
                app.JOB.sans_audio == [2], app.JOB.sans_audio)
        verifie("l'état devient « pret »", app.JOB.etat == "pret", app.JOB.etat)

        r = client.post("/api/uness/historique/rouvrir",
                        json={"cle": "cours-casse"})
        verifie("rouvrir refuse un cours sans audio décodable",
                r.status_code == 404, r.status_code)
        r = client.post("/api/uness/historique/rouvrir", json={"cle": "inconnu"})
        verifie("rouvrir refuse une clé inconnue", r.status_code == 404,
                r.status_code)
        app.JOB.etat = "transcription"
        r = client.post("/api/uness/historique/rouvrir", json={"cle": "cours-a"})
        verifie("rouvrir refuse pendant une transcription", r.status_code == 409,
                r.status_code)
        app.JOB.etat = "pret"

        print("\n[6] Oublier un cours")
        r = client.post("/api/uness/historique/oublier", json={"cle": "cours-a"})
        verifie("oublier refuse le cours ouvert (409)", r.status_code == 409,
                r.status_code)
        r = client.post("/api/uness/historique/oublier",
                        json={"cle": "../cours"})
        verifie("une clé qui remonte l'arborescence est refusée",
                r.status_code == 404 and os.path.isdir(racine), r.status_code)
        r = client.post("/api/uness/historique/oublier", json={"cle": "inconnu"})
        verifie("une clé inconnue est refusée", r.status_code == 404,
                r.status_code)

        app.JOB.cle_cours = None
        r = client.post("/api/uness/historique/oublier", json={"cle": "cours-a"})
        verifie("oublier répond ok", r.status_code == 200 and r.get_json()["ok"],
                r.get_json())
        verifie("le dossier de cache a disparu",
                not os.path.isdir(os.path.join(racine, "cours-a")))
        verifie("le .txt de Documents n'est JAMAIS touché",
                os.path.isfile(recent))
        cles = [c["cle"] for c in
                client.get("/api/uness/historique").get_json()["cours"]]
        verifie("le cours a quitté la liste", "cours-a" not in cles, cles)

        print("\n[7] Temps restant estimé sur le débit observé")
        diapos = [{"n": i + 1, "fichier": "a24x%d.mp3" % (i + 1)}
                  for i in range(6)]
        # 0,4 s par diapo : assez lent pour que le compte a rebours annonce
        # autre chose que zero, ce qui est justement ce qu'on verifie.
        r, evts = recuperation_factice(travail, diapos, cout=0.4)
        verifie("tout est téléchargé", r.telecharger() is True)
        evts = audio(evts)
        verifie("le champ reste_s existe sur chaque évènement audio",
                all("reste_s" in e for e in evts), len(evts))
        verifie("le champ octets existe sur chaque évènement audio",
                all("octets" in e for e in evts), len(evts))
        verifie("null tant que moins de 3 diapos sont téléchargées",
                [e["reste_s"] for e in evts[:2]] == [None, None],
                [e["reste_s"] for e in evts[:3]])
        verifie("une estimation apparaît ensuite",
                isinstance(evts[2]["reste_s"], int) and evts[2]["reste_s"] >= 1,
                evts[2]["reste_s"])
        verifie("l'estimation décroît", evts[2]["reste_s"] >= evts[4]["reste_s"],
                [e["reste_s"] for e in evts])
        verifie("elle tombe à zéro à la dernière diapo",
                evts[-1]["reste_s"] == 0, evts[-1]["reste_s"])
        verifie("les octets s'accumulent",
                [e["octets"] for e in evts] == [1500 * (i + 1) for i in range(6)],
                [e["octets"] for e in evts])
        # 3 diapos restantes a ~0,4 s : environ 1 s, et surement pas 10.
        verifie("l'estimation reste dans l'ordre de grandeur observé",
                1 <= evts[2]["reste_s"] <= 3, evts[2]["reste_s"])

        print("\n[8] Ce qui ne coûte rien ne compte pas dans l'estimation")
        diapos = [{"n": i + 1, "fichier": "a24x%d.mp3" % (i + 1)}
                  for i in range(3)]
        diapos += [{"n": i, "fichier": None} for i in (4, 5, 6)]
        r, evts = recuperation_factice(travail, diapos, cout=0.05)
        r.telecharger()
        evts = audio(evts)
        verifie("les diapos sans audio ne gonflent pas le compte à rebours",
                evts[2]["reste_s"] == 0, [e["reste_s"] for e in evts])

        # Une reprise qui ne fait que relire le cache ne mesure aucun debit :
        # annoncer un temps la-dessus serait inventer un chiffre.
        diapos = [{"n": i + 1, "fichier": "a24x%d.mp3" % (i + 1)}
                  for i in range(6)]
        r, evts = recuperation_factice(travail, diapos, cout=0.05)
        r.cache.valide = lambda nom: True
        r.telecharger()
        evts = audio(evts)
        verifie("une reprise entièrement en cache ne télécharge rien",
                r.client.appels == 0, r.client.appels)
        verifie("et n'annonce aucun temps restant",
                all(e["reste_s"] is None for e in evts),
                [e["reste_s"] for e in evts])
        verifie("ni aucun octet", all(e["octets"] == 0 for e in evts))

        print("\n[9] Modèles déjà téléchargés")
        faux_hub = os.path.join(travail, "hub")
        for depot in ("models--Systran--faster-whisper-base",
                      "models--Systran--faster-whisper-small"):
            os.makedirs(os.path.join(faux_hub, depot, "snapshots", "abc"),
                        exist_ok=True)
        # 'small' est la, mais sans son poids : c'est un telechargement coupe.
        with open(os.path.join(faux_hub,
                               "models--Systran--faster-whisper-base",
                               "snapshots", "abc", "model.bin"), "wb") as f:
            f.write(b"0")
        ancien = os.environ.get("HUGGINGFACE_HUB_CACHE")
        os.environ["HUGGINGFACE_HUB_CACHE"] = faux_hub
        try:
            conf = client.get("/api/config").get_json()
        finally:
            if ancien is None:
                os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
            else:
                os.environ["HUGGINGFACE_HUB_CACHE"] = ancien
        presents = conf.get("modeles_presents")
        verifie("/api/config annonce les modèles présents",
                isinstance(presents, dict) and
                set(presents) == set(app.MODELS), presents)
        verifie("un modèle complet est déclaré présent",
                presents.get("base") is True, presents)
        verifie("un téléchargement coupé n'est pas déclaré présent",
                presents.get("small") is False, presents)
        verifie("un modèle jamais téléchargé est absent",
                presents.get("medium") is False and
                presents.get("large-v3-turbo") is False, presents)
        os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(travail, "nulle-part")
        try:
            verifie("un cache de modèles inexistant ne lève pas",
                    all(v is False for v in app.modeles_presents().values()))
        finally:
            if ancien is None:
                os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
            else:
                os.environ["HUGGINGFACE_HUB_CACHE"] = ancien

    finally:
        app.JOB.reset()
        shutil.rmtree(travail, ignore_errors=True)

    print("\n%d reussis, %d echoues" % (len(OK), len(KO)))
    if KO:
        print("Echecs : " + ", ".join(KO))
    return 1 if KO else 0


if __name__ == "__main__":
    sys.exit(main())
