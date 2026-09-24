# -*- coding: utf-8 -*-
"""Tests de l'import d'un cours UNESS, contre le faux Moodle de faux_uness.py.

Couvre ce que je ne peux pas verifier sur le vrai site : detection d'une
session absente (page de login en 200), reprise apres reconnexion, diapos sans
audio, cache, decalages exacts de l'assemblage, rattachement des segments,
titres accentues et non tronques, et refus des URL hors liste blanche.

  python tests/uness_test.py
"""

import os
import shutil
import sys
import tempfile
import threading

os.environ["TRANSCRIPTEUR_TEST_UNESS"] = "1"

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
sys.path.insert(0, os.path.dirname(ICI))

import faux_uness                                              # noqa: E402
from uness import assemblage, cours, recuperation, session     # noqa: E402
from uness.telechargement import Cache, Client, duree_mp3      # noqa: E402

OK, KO = [], []


def verifie(nom, condition, detail=""):
    (OK if condition else KO).append(nom)
    print(("  [ok] " if condition else "  [KO] ") + nom +
          (" — " + str(detail) if detail else ""))
    return bool(condition)


class FauxCoffre:
    """Un coffre en memoire : les tests n'ont pas besoin de disque ici."""

    def __init__(self, cookies=None):
        self._cookies = list(cookies or [])

    def lire(self):
        return list(self._cookies)

    def ecrire(self, cookies):
        self._cookies = list(cookies)

    def effacer(self):
        self._cookies = []


def cookies_valides():
    return [{"name": faux_uness.COOKIE, "value": faux_uness.VALEUR,
             "domain": "127.0.0.1", "path": "/"}]


def recup(base, coffre, cache_racine, evts=None):
    journal = evts if evts is not None else []
    return recuperation.Recuperation(
        base + "index.htm", coffre, cache_racine,
        lambda t, d, durable=True: journal.append((t, d)))


# ---------------------------------------------------------------------------

def test_url():
    print("\n[1] Validation de l'URL")
    bons = ["https://formation.uness.fr/formation/pluginfile.php/116687/"
            "mod_resource/content/0/index.htm",
            "formation.uness.fr/formation/pluginfile.php/1/mod_resource/content/0/",
            "https://formation.uness.fr/a/b/content/0/index.htm?forcedownload=0"]
    for u in bons:
        try:
            index, base = cours.verifier_url(u)
            verifie("accepte %s" % u[:52], base.endswith("/") and "index.htm" in index)
        except cours.ErreurCours as e:
            verifie("accepte %s" % u[:52], False, e)

    mauvais = [("http://formation.uness.fr/x/index.htm", "https obligatoire"),
               ("https://evil.example.com/x/index.htm", "domaine hors liste"),
               ("https://formation.uness.fr.evil.com/x/", "domaine suffixe"),
               ("", "champ vide")]
    for u, quoi in mauvais:
        # La tolerance 127.0.0.1 du mode test ne doit pas ouvrir autre chose.
        try:
            cours.verifier_url(u)
            verifie("refuse : " + quoi, False, "accepte a tort")
        except cours.ErreurCours as e:
            verifie("refuse : " + quoi, True, str(e)[:48])


def test_session_absente(base):
    print("\n[2] Session absente : Moodle repond une page de login en 200")
    nu = Client([], delai=0)
    url_mp3 = base + "data/%s1.mp3" % faux_uness.PREFIXE
    verifie("session_valide() est faux sans cookie",
            nu.session_valide(url_mp3) is False)
    try:
        nu.texte(base + "index.htm")
        verifie("texte() leve SessionExpiree sur la page de login", False)
    except Exception as e:
        verifie("texte() leve SessionExpiree sur la page de login",
                type(e).__name__ == "SessionExpiree")

    avec = Client(cookies_valides(), delai=0)
    verifie("session_valide() est vrai avec le cookie",
            avec.session_valide(url_mp3) is True)
    verifie("la page se lit avec le cookie",
            "<title>" in (avec.texte(base + "index.htm") or ""))


def test_decouverte(base):
    print("\n[3] Decouverte du plan")
    c = Client(cookies_valides(), delai=0)
    index, racine = cours.verifier_url(base + "index.htm")
    titre, diapos, methode = cours.decouvrir(c, index, racine)
    verifie("titre du cours trouve", titre == faux_uness.TITRE_COURS, titre)
    verifie("12 diapos trouvees", len(diapos) == 12, len(diapos))
    verifie("methode = le manifeste XML", "presentation.xml" in methode, methode)
    verifie("numeros de diapo continus",
            [d["n"] for d in diapos] == list(range(1, 13)))
    verifie("noms de fichiers deduits de la page (pas en dur)",
            diapos[3]["fichier"] == "%s4.mp3" % faux_uness.PREFIXE,
            diapos[3]["fichier"])
    # Le point sensible : le plan HTML tronque a 22 caracteres, le XML non.
    attendu = faux_uness.TITRES[3]
    verifie("titres COMPLETS, pas ceux tronques du plan",
            diapos[3]["titre"] == attendu, "%r" % diapos[3]["titre"])
    verifie("accents conserves",
            any("é" in d["titre"] for d in diapos))

    voc = cours.vocabulaire(titre, diapos)
    verifie("vocabulaire construit et borne",
            faux_uness.TITRE_COURS in voc and len(voc) <= 850, len(voc))
    return diapos


def test_page_moodle(base, cache_racine):
    """Ce que l'utilisateur colle VRAIMENT : l'adresse de sa barre d'adresse,
    c'est-a-dire la page Moodle de la ressource, pas celle du lecteur."""
    print("\n[3b] Page Moodle mod/resource/view.php?id=...")
    url = SERVEUR.url_moodle

    # La chaine de requete fait partie de l'adresse : la perdre donne
    # « Identifiant de module de cours non valide ».
    garde, base_nulle = cours.verifier_url(url)
    verifie("la chaine ?id=... est conservee", "id=44419" in garde, garde)
    verifie("une page Moodle n'est pas prise pour un dossier de cours",
            base_nulle is None, base_nulle)

    # Le piege : une page Moodle CONNECTEE contient des liens vers /login/.
    # Une detection qui cherche ce motif dans le contenu prend la page de
    # cours de l'utilisateur pour une page de connexion, et l'application
    # attend indefiniment une session qu'elle a deja.
    c = Client(cookies_valides(), delai=0)
    try:
        page = c.texte(garde)
    except Exception as e:
        page = None
        verifie("une page Moodle connectee se lit malgre ses liens /login/",
                False, "%s : %s" % (type(e).__name__, str(e)[:60]))
    else:
        verifie("une page Moodle connectee se lit malgre ses liens /login/",
                page is not None and "resourcecontent" in (page or ""))
    verifie("...et elle contient bien le piege",
            "/login/index.php" in (page or ""))
    if page is None:
        return   # inutile de poursuivre : la page ne se lit meme pas

    lecteur, racine = cours.resoudre_lecteur(c, garde)
    verifie("le lecteur est trouve dans la page",
            lecteur.endswith("index.htm"), lecteur)
    verifie("le dossier du cours en decoule",
            racine.endswith("/content/0/"), racine)

    # Et le parcours complet doit marcher depuis cette adresse-la.
    coffre = FauxCoffre(cookies_valides())
    r = recuperation.Recuperation(url, coffre, cache_racine + "-moodle",
                                  lambda t, d, durable=True: None)
    r.client.delai = 0
    r.decouvrir()
    verifie("12 diapos decouvertes depuis la page Moodle",
            len(r.diapos) == 12, len(r.diapos))
    verifie("titres complets malgre le detour",
            r.diapos[3]["titre"] == faux_uness.TITRES[3], r.diapos[3]["titre"])
    shutil.rmtree(cache_racine + "-moodle", ignore_errors=True)

    # Une page sans lecteur doit le dire clairement.
    try:
        cours.resoudre_lecteur(c, base + "data/presentation.xml")
        verifie("une page sans lecteur est signalee", False, "accepte a tort")
    except cours.ErreurCours as e:
        verifie("une page sans lecteur est signalee",
                "lecteur" in str(e).lower(), str(e)[:60])


def test_sondage():
    print("\n[4] Repli : sondage des numeros quand le manifeste manque")
    srv, base = faux_uness.demarrer(nb_diapos=9, sans_audio=(3,))
    try:
        c = Client(cookies_valides(), delai=0)
        graine = "%s1.mp3" % faux_uness.PREFIXE
        # Sans total connu : on doit depasser le trou de la diapo 3.
        diapos = cours.sonder(c, base, graine, None)
        avec = [d["n"] for d in diapos if d["fichier"]]
        verifie("le trou (diapo 3) n'arrete pas le sondage",
                3 not in avec and 8 in avec, avec)
        verifie("s'arrete apres deux absences consecutives",
                len(diapos) == 9, len(diapos))
    finally:
        srv.shutdown()


def test_ispring():
    print("\n[4b] Lecteur iSpring : plan compresse dans presInfo")
    import base64
    import json
    import zlib

    # Forme du cours 590089 : diapos, ressources audio, narration par diapo.
    # La diapo 3 n'a pas de narration, la 4 en a deux dans le desordre.
    donnees = {
        "t": "index",
        "s": [{"t": "Aspects cliniques de l’asthme"},
              {"t": "", "x": "EXAMEN DE LA RÉPONSE\r\nSuite"},
              {"t": "Sans audio"},
              {"t": "Deux pistes"}],
        "o": [{"i": "sndAsset%d" % i,
               "h": '<audio><source src="data/sound%d.mp3" '
                    'type="audio/mpeg"/></audio>' % (i + 1)}
              for i in range(4)],
        "n": {"a": [{"i": "sndAsset0", "st": {"s": 0, "i": 0}},
                    {"i": "sndAsset1", "st": {"s": 1, "i": 0}},
                    {"i": "sndAsset3", "st": {"s": 3, "i": 5}},
                    {"i": "sndAsset2", "st": {"s": 3, "i": 0}}]},
    }
    code = base64.b64encode(zlib.compress(
        json.dumps(donnees).encode("utf-8"))).decode("ascii")
    html = ('<!-- Created with iSpring --><title>index</title><script>'
            'var presInfo = "%s";</script>' % code)

    titre, diapos = cours.ispring(html)
    verifie("titre du cours pris sur la 1re diapo (<title> vaut 'index')",
            titre == "Aspects cliniques de l’asthme", titre)
    verifie("mp3 dans l'ordre du cours, deux pistes d'une diapo triees",
            [d["fichier"] for d in diapos] ==
            ["sound1.mp3", "sound2.mp3", None, "sound3.mp3", "sound4.mp3"],
            [d["fichier"] for d in diapos])
    verifie("titre vide remplace par la 1re ligne du texte",
            diapos[1]["titre"] == "EXAMEN DE LA RÉPONSE", diapos[1]["titre"])
    verifie("une page Adobe Presenter n'est pas prise pour iSpring",
            cours.ispring("<html><script src='data/a24x1.mp3'></script>") is None)


def test_telechargement_et_cache(base, cache_racine):
    print("\n[5] Telechargement, diapos sans audio, cache")
    coffre = FauxCoffre(cookies_valides())
    r = recup(base, coffre, cache_racine)
    r.client.delai = 0
    r.decouvrir()
    verifie("telechargement complet", r.telecharger() is True)
    verifie("diapos sans audio signalees", r.sans_audio == [5, 9], r.sans_audio)
    verifie("message lisible", "5, 9" in r.resume(), r.resume())
    presents = [d for d in r.diapos if d["fichier"]]
    verifie("10 mp3 sur disque",
            all(r.cache.valide(d["fichier"]) for d in presents) and
            len(presents) == 10, len(presents))

    # Deuxieme passage : rien ne doit repartir sur le reseau.
    srv = SERVEUR
    srv.requetes.clear()
    r2 = recup(base, coffre, cache_racine)
    r2.client.delai = 0
    r2.decouvrir()
    r2.telecharger()
    mp3_relus = [q for q in srv.requetes if q.endswith(".mp3")]
    verifie("le cache evite tout retelechargement", not mp3_relus, mp3_relus)
    return r


def test_reprise(base, cache_racine):
    print("\n[6] Session qui expire en plein telechargement")
    coffre = FauxCoffre(cookies_valides())
    r = recup(base, coffre, cache_racine + "-reprise")
    r.client.delai = 0
    r.decouvrir()

    # On coupe la session apres 3 diapos, comme le ferait Moodle.
    vrai_tel = r.client.telecharger
    compteur = {"n": 0}

    def coupe(url, cible):
        compteur["n"] += 1
        if compteur["n"] == 4:
            SERVEUR.exige_reconnexion = True
        return vrai_tel(url, cible)

    r.client.telecharger = coupe
    verifie("telecharger() signale l'expiration", r.telecharger() is False)
    verifie("l'app sait qu'il faut se reconnecter", r.attend_connexion is True)
    deja = sum(1 for d in r.diapos if d["fichier"] and r.cache.valide(d["fichier"]))
    verifie("les diapos deja recuperees sont gardees", deja == 3, deja)

    # "Reconnexion" : le faux serveur reaccepte le cookie.
    SERVEUR.exige_reconnexion = False
    SERVEUR.requetes.clear()
    r.client.telecharger = vrai_tel
    r.rafraichir_cookies()
    verifie("la reprise va au bout", r.telecharger() is True)
    redemandes = {q.rsplit("/", 1)[-1] for q in SERVEUR.requetes
                  if q.endswith(".mp3")}
    trois_premieres = {"%s%d.mp3" % (faux_uness.PREFIXE, n) for n in (1, 2, 3)}
    verifie("les 3 premieres ne sont pas retelechargees",
            not (redemandes & trois_premieres), sorted(redemandes & trois_premieres))
    shutil.rmtree(cache_racine + "-reprise", ignore_errors=True)


def test_retentative(base, cache_racine):
    print("\n[7] Panne passagere du serveur sur une diapo")
    nom = "%s2.mp3" % faux_uness.PREFIXE
    SERVEUR.echecs[nom] = 2          # deux 503 puis ca passe
    coffre = FauxCoffre(cookies_valides())
    r = recup(base, coffre, cache_racine + "-retry")
    r.client.delai = 0
    r.decouvrir()
    r.telecharger()
    verifie("la diapo 2 finit par arriver malgre deux 503",
            r.cache.valide(nom), SERVEUR.echecs.get(nom))
    SERVEUR.echecs.clear()
    shutil.rmtree(cache_racine + "-retry", ignore_errors=True)


def test_assemblage(r):
    print("\n[8] Assemblage : duree totale et decalages exacts")
    chemin, chapitres, duree = r.assembler()
    verifie("un seul fichier produit", os.path.isfile(chemin))
    verifie("un chapitre par diapo", len(chapitres) == len(r.diapos))

    # Somme des durees reellement decodees + 1 s entre deux diapos avec audio.
    decodees, annoncees = 0.0, 0.0
    for d in r.diapos:
        if d["fichier"]:
            chemin = r.cache.fichier(d["fichier"])
            decodees += assemblage.duree_decodee(chemin)
            annoncees += duree_mp3(chemin) or 0.0
    n_avec = sum(1 for d in r.diapos if d["fichier"])
    silences = assemblage.SILENCE * max(0, n_avec - 1)

    # Le fichier assemble annonce une poignee de ms de plus que ce qu'on y a
    # ecrit : c'est le bourrage de la derniere trame mp3 (1152 echantillons) et
    # le silence d'amorce de l'encodeur. C'est une constante, pas une derive :
    # les controles suivants verifient qu'elle ne grandit pas avec les diapos.
    ecart = abs(duree - (decodees + silences))
    verifie("duree totale = somme des diapos decodees + silences (a 150 ms pres)",
            ecart < 0.15,
            "%.3f s d'ecart (%.2f vs %.2f)" % (ecart, duree, decodees + silences))

    # Compare aussi a ce qu'annoncent les conteneurs mp3 : l'ecart doit rester
    # celui du silence d'amorce (quelques dizaines de ms par fichier), jamais
    # une derive qui ferait glisser tout le cours.
    derive = abs(duree - (annoncees + silences))
    verifie("l'ecart avec les durees annoncees reste du silence d'amorce",
            derive < 0.08 * n_avec,
            "%.3f s sur %d diapos, soit %.0f ms/diapo"
            % (derive, n_avec, 1000 * derive / n_avec))

    fin = max(c["fin"] for c in chapitres)
    verifie("la duree du fichier colle au dernier chapitre",
            abs(duree - fin) < 0.3, "%.3f s" % abs(duree - fin))

    # Chaque titre du plan doit mener au bon moment : on verifie que le seek
    # sur le debut d'un chapitre retombe bien dans SON audio, en comparant la
    # duree du chapitre a celle du mp3 source.
    mauvais = []
    for c in chapitres:
        if c["sans_audio"]:
            continue
        src = duree_mp3(r.cache.fichier(c["fichier"])) or 0.0
        if abs((c["fin"] - c["debut"]) - src) > 0.12:
            mauvais.append((c["n"], round(c["fin"] - c["debut"], 2), round(src, 2)))
    verifie("chaque chapitre dure exactement son mp3 d'origine",
            not mauvais, mauvais[:3])

    croissant = all(chapitres[i]["debut"] <= chapitres[i + 1]["debut"]
                    for i in range(len(chapitres) - 1))
    verifie("chapitres ordonnes", croissant)

    silences = [round(chapitres[i + 1]["debut"] - chapitres[i]["fin"], 2)
                for i in range(len(chapitres) - 1)
                if not chapitres[i]["sans_audio"] and
                not chapitres[i + 1]["sans_audio"]]
    verifie("environ 1 s de silence entre deux diapos",
            silences and all(abs(s - 1.0) < 0.06 for s in silences),
            sorted(set(silences))[:4])

    vides = [c["n"] for c in chapitres if c["sans_audio"]]
    verifie("les diapos sans audio sont marquees, pas inventees",
            vides == [5, 9], vides)

    plan = r.cache.lire_chapitres()
    verifie("chapitres.json ecrit et relisible",
            plan and plan.get("coherent") and len(plan["diapos"]) == len(chapitres))
    verifie("titres accentues intacts dans le JSON",
            any("é" in d["titre"] for d in plan["diapos"]))
    return chapitres


def test_rattachement(chapitres):
    print("\n[9] Rattachement des segments transcrits aux diapos")
    cas = []
    for c in chapitres:
        if c["sans_audio"]:
            continue
        milieu = (c["debut"] + c["fin"]) / 2
        cas.append((milieu, c["n"], "milieu de la diapo %d" % c["n"]))
        cas.append((c["debut"] + 0.05, c["n"], "tout debut de la diapo %d" % c["n"]))
    segments = [{"debut": t, "fin": t + 0.5, "texte": ""} for t, _, _ in cas]
    obtenus = assemblage.rattacher(segments, chapitres)
    faux = [(nom, attendu, eu) for (_, attendu, nom), eu in zip(cas, obtenus)
            if eu != attendu]
    verifie("chaque segment tombe dans la bonne diapo", not faux, faux[:3])

    # Un segment qui demarre dans le silence appartient a la diapo suivante.
    avec = [c for c in chapitres if not c["sans_audio"]]
    if len(avec) >= 2:
        t = avec[1]["debut"] - 0.4          # dans le silence qui precede
        n = assemblage.rattacher([{"debut": t, "fin": t + 0.2}], chapitres)[0]
        verifie("un segment dans le silence reste sur la diapo precedente",
                n == avec[0]["n"], n)
    verifie("diapo_a() donne le meme resultat",
            assemblage.diapo_a(avec[2]["debut"] + 0.1, chapitres) == avec[2]["n"])


def test_decoupe_frontieres():
    """Whisper produit parfois UN segment couvrant trois diapos : le VAD
    retire les silences avant de lui donner l'audio, il n'a donc plus de
    pause a entendre. Sans recoupe au mot, le texte de trois diapos
    atterrirait sous un seul intertitre."""
    print("\n[9b] Recoupe d'un segment a cheval sur plusieurs diapos")
    chapitres = [
        {"n": 1, "debut": 0.0, "fin": 10.0, "sans_audio": False},
        {"n": 2, "debut": 11.0, "fin": 20.0, "sans_audio": False},
        {"n": 3, "debut": 20.0, "fin": 20.0, "sans_audio": True},
        {"n": 4, "debut": 21.0, "fin": 30.0, "sans_audio": False},
    ]
    # Un mot par seconde, de 0 a 29.
    mots = [(float(i), i + 0.9, " mot%d" % i) for i in range(30)]
    texte = "".join(m[2] for m in mots).strip()

    bouts = assemblage.decouper_aux_frontieres(0.0, 30.0, texte, mots, chapitres)
    verifie("un segment a cheval est coupe en trois", len(bouts) == 3, len(bouts))
    verifie("chaque bout commence dans sa diapo",
            [assemblage.diapo_a(b[0], chapitres) for b in bouts] == [1, 2, 4],
            [assemblage.diapo_a(b[0], chapitres) for b in bouts])
    verifie("aucun mot perdu, aucun duplique",
            " ".join(b[2] for b in bouts).split() == texte.split())
    verifie("la coupe tombe au bon mot",
            bouts[1][2].split()[0] == "mot11", bouts[1][2][:20])

    # Un segment qui tient dans une seule diapo n'est pas touche.
    dedans = assemblage.decouper_aux_frontieres(
        12.0, 18.0, "bonjour", [(12.0, 13.0, "bonjour")], chapitres)
    verifie("un segment d'une seule diapo reste entier",
            dedans == [(12.0, 18.0, "bonjour")], dedans)

    # Sans horodatage de mot (modele sans word_timestamps), on ne devine pas :
    # le segment est rendu tel quel plutot que coupe au hasard.
    sans = assemblage.decouper_aux_frontieres(0.0, 30.0, texte, [], chapitres)
    verifie("sans horodatage de mot, le segment est rendu tel quel",
            sans == [(0.0, 30.0, texte)], len(sans))


def test_cookie_manuel():
    print("\n[10] Repli : cookie colle a la main")
    for texte, attendu in (
            ("MoodleSession=abc123", ("MoodleSession", "abc123")),
            ("abc123", ("MoodleSession", "abc123")),
            ("Cookie: MoodleSession=abc123; autre=x", ("MoodleSession", "abc123")),
            ("  MoodleSession=abc123 ;  ", ("MoodleSession", "abc123"))):
        c = session.cookies_depuis_texte(texte)
        verifie("accepte %r" % texte[:30],
                (c[0]["name"], c[0]["value"]) == attendu, c[0]["name"])
    try:
        session.cookies_depuis_texte("   ")
        verifie("refuse un champ vide", False)
    except session.ErreurConnexion:
        verifie("refuse un champ vide", True)


def test_nom_accentue(base, cache_racine):
    print("\n[11] Nom de fichier de sortie avec accents")
    sys.path.insert(0, os.path.dirname(ICI))
    import app                                                   # noqa: E402
    nom = app.nom_de_fichier_sur(faux_uness.TITRE_COURS)
    verifie("accents gardes dans le nom du .txt",
            "é" in nom and "/" not in nom and ":" not in nom, nom)
    dossier = os.path.join(cache_racine, "sortie")
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, nom + ".txt")
    open(chemin, "w", encoding="utf-8").write("test")
    verifie("le fichier s'ecrit vraiment sur disque", os.path.isfile(chemin))


# ---------------------------------------------------------------------------

def test_filtre_hallucinations():
    """Les motifs anti-hallucination sont compares au texte NORMALISE, dont
    toute la ponctuation a disparu. Ecrits avec des tirets et des points, ils
    ne matchaient jamais -- et le residu passait dans le texte final."""
    print("\n[12] Filtre anti-hallucination")
    import app                                                   # noqa: E402
    residus = [
        "Sous-titres réalisés par la communauté d'Amara.org",
        "Sous-titrage Société Radio-Canada",
        "Merci d'avoir regardé cette vidéo !",
        "Abonnez-vous à la chaîne !",
        "[Musique]",
        "Au revoir.",
    ]
    for r in residus:
        verifie("jette %r dans un silence" % r[:34],
                app.est_hallucination(r, 0.9) is True)
        # ... mais jamais quand il y a bien de la parole.
        verifie("garde %r si c'est prononce" % r[:22],
                app.est_hallucination(r, 0.1) is False)

    # Un segment sans le moindre mot n'est jamais du contenu, quel que soit
    # le doute sur la parole.
    for vide in ("♪♪♪", "...", "   "):
        verifie("jette %r meme sans doute sur la parole" % vide,
                app.est_hallucination(vide, 0.0) is True)

    for vrai in ("Le poumon droit comporte trois lobes.",
                 "Merci de votre attention, passons à la suite.",
                 "La musique de fond du cours est un choix pédagogique."):
        verifie("garde le vrai contenu : %r" % vrai[:34],
                app.est_hallucination(vrai, 0.9) is False)

    # Le prompt recrache : un titre de diapo restitue tel quel dans un silence.
    amorces = app.amorces_de("Sibilants et ronchi, Pneumothorax")
    verifie("jette un titre de diapo recrache dans un silence",
            app.est_hallucination("Sibilants et ronchi", 0.9, amorces) is True)
    verifie("garde ce meme titre s'il est prononce",
            app.est_hallucination("Sibilants et ronchi", 0.2, amorces) is False)


def test_taille_reelle(cache_racine):
    """79 diapos, la taille du vrai cours : ce qui compte ici est qu'aucune
    derive ne s'accumule sur la longueur."""
    print("\n[13] Cours de 79 diapos (taille reelle)")
    srv, base = faux_uness.demarrer(nb_diapos=79, sans_audio=(12, 45))
    racine = cache_racine + "-79"
    try:
        coffre = FauxCoffre(cookies_valides())
        r = recup(base, coffre, racine)
        r.client.delai = 0
        r.decouvrir()
        verifie("79 diapos decouvertes", len(r.diapos) == 79, len(r.diapos))
        r.telecharger()
        verifie("diapos 12 et 45 sans audio", r.sans_audio == [12, 45], r.sans_audio)
        _, chapitres, duree = r.assembler()

        decodees = sum(assemblage.duree_decodee(r.cache.fichier(d["fichier"]))
                       for d in r.diapos if d["fichier"])
        n = sum(1 for d in r.diapos if d["fichier"])
        attendu = decodees + assemblage.SILENCE * (n - 1)
        verifie("duree juste sur 79 diapos (a 150 ms pres)",
                abs(duree - attendu) < 0.15,
                "%.3f s d'ecart pour %s d'audio" % (abs(duree - attendu),
                                                    hhmmss(duree)))

        # Le controle qui compte pour l'utilisateur : chaque titre du plan
        # mene au bon moment, meme la 79e diapo.
        mauvais = []
        for c in chapitres:
            if c["sans_audio"]:
                continue
            src = assemblage.duree_decodee(r.cache.fichier(c["fichier"]))
            if abs((c["fin"] - c["debut"]) - src) > 0.02:
                mauvais.append(c["n"])
        verifie("aucune derive : chaque chapitre cale sur son mp3",
                not mauvais, mauvais[:5])
        verifie("le dernier chapitre finit avec le fichier",
                abs(max(c["fin"] for c in chapitres) - duree) < 0.15)
    finally:
        srv.shutdown()
        shutil.rmtree(racine, ignore_errors=True)


def hhmmss(s):
    s = int(s)
    return "%dh%02dm%02ds" % (s // 3600, (s % 3600) // 60, s % 60)


SERVEUR = None


def main():
    global SERVEUR
    cache_racine = tempfile.mkdtemp(prefix="uness-test-")
    SERVEUR, base = faux_uness.demarrer(nb_diapos=12, sans_audio=(5, 9))
    print("Faux UNESS sur %s" % base)
    try:
        test_url()
        test_session_absente(base)
        test_decouverte(base)
        test_page_moodle(base, cache_racine)
        test_sondage()
        test_ispring()
        r =test_telechargement_et_cache(base, cache_racine)
        test_reprise(base, cache_racine)
        test_retentative(base, cache_racine)
        chapitres = test_assemblage(r)
        test_rattachement(chapitres)
        test_decoupe_frontieres()
        test_cookie_manuel()
        test_nom_accentue(base, cache_racine)
        test_filtre_hallucinations()
        if "--rapide" not in sys.argv:
            test_taille_reelle(cache_racine)
    finally:
        SERVEUR.shutdown()
        shutil.rmtree(cache_racine, ignore_errors=True)

    print("\n%d reussis, %d echoues" % (len(OK), len(KO)))
    if KO:
        print("Echecs : " + ", ".join(KO))
    return 1 if KO else 0


if __name__ == "__main__":
    sys.exit(main())
