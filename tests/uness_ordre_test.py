# -*- coding: utf-8 -*-
"""Ordre des diapos et titres, sur le lecteur qui n'annonce rien.

Ce fichier garde deux defauts constates sur le vrai cours 116724 (37 diapos) :

1. **Les numeros des mp3 sont des identifiants d'asset, pas des numeros de
   diapo.** Ce cours liste ses diapos dans l'ordre 3, 7, 4, 6... et les
   renumeroter d'apres 'a24x7.mp3' remonterait un cours entier dans le
   desordre, sans un message d'erreur. Seul l'ordre du document fait foi.

2. **Les titres etaient tous vides.** Ce lecteur n'a aucun des manifestes
   connus : un index.htm de 2,5 Ko et un seul fichier de donnees au nom non
   standard, ou les titres et les mp3 vivent dans deux listes paralleles.

  python tests/uness_ordre_test.py
"""

import os
import sys

os.environ["TRANSCRIPTEUR_TEST_UNESS"] = "1"

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
sys.path.insert(0, os.path.dirname(ICI))

import faux_uness                                              # noqa: E402
from uness import cours                                        # noqa: E402
from uness.telechargement import Client                        # noqa: E402

OK, KO = [], []

# L'ordre reel du cours 116724, tel que les donnees du lecteur listent ses
# diapos : ce sont des identifiants d'asset, et la 1re diapo est 'a24x3.mp3'.
# La 29e diapo (asset 26) est la seule sans audio.
ORDRE_REEL = [3, 7, 4, 6, 8, 9, 10, 11, 12, 13, 28, 29, 31, 32, 18, 23, 20,
              24, 25, 33, 5, 15, 16, 14, 17, 19, 21, 22, 26, 27, 30, 34, 35,
              36, 1, 37, 2]
SANS_AUDIO = 26


def verifie(nom, condition, detail=""):
    (OK if condition else KO).append(nom)
    print(("  [ok] " if condition else "  [KO] ") + nom +
          (" — " + str(detail) if detail else ""))
    return bool(condition)


def cookies_valides():
    return [{"name": faux_uness.COOKIE, "value": faux_uness.VALEUR,
             "domain": "127.0.0.1", "path": "/"}]


def _decouvrir(srv, base):
    c = Client(cookies_valides(), delai=0)
    index, racine = cours.verifier_url(base + "index.htm")
    return cours.decouvrir(c, index, racine)


def _controler(srv, diapos, methode, sans_audio):
    """Les memes controles dans les deux cas : avec trou et sans trou."""
    attendus = [None if n == sans_audio else "%s%d.mp3" % (faux_uness.PREFIXE, n)
                for n, _, _ in srv.diapos]
    titres_attendus = [t for _, t, _ in srv.diapos]

    verifie("37 diapos", len(diapos) == 37, len(diapos))
    verifie("numerotation continue 1..37",
            [d["n"] for d in diapos] == list(range(1, 38)),
            [d["n"] for d in diapos][:6])
    obtenus = [d["fichier"] for d in diapos]
    verifie("l'ordre du document fait foi, pas le numero de fichier",
            obtenus == attendus,
            "obtenu %s / attendu %s" % (obtenus[:5], attendus[:5]))
    verifie("le plan est trouve dans le fichier de donnees du lecteur",
            "vt_9f3c" in methode, methode)

    vides = [d["n"] for d in diapos if not d["titre"]]
    verifie("aucun titre vide", not vides, vides[:5])
    verifie("chaque titre est apparie a SON fichier",
            [d["titre"] for d in diapos] == titres_attendus,
            [d["titre"] for d in diapos][:2])
    verifie("titres accentues et complets",
            any("é" in d["titre"] for d in diapos) and
            not any(d["titre"].endswith(("...", "…")) for d in diapos))


def test_ordre_avec_trou():
    """Le cours reel : une diapo sans audio au milieu."""
    print("\n[1] Cours 116724 : ordre du document, une diapo sans audio")
    srv, base = faux_uness.demarrer(sans_audio=(SANS_AUDIO,), variante="opaque",
                                    ordre=ORDRE_REEL, audio=False)
    try:
        titre, diapos, methode = _decouvrir(srv, base)
        verifie("titre du cours trouve", titre == faux_uness.TITRE_COURS, titre)
        _controler(srv, diapos, methode, SANS_AUDIO)
        verifie("la diapo sans audio garde sa place (29e)",
                diapos[28]["fichier"] is None and diapos[28]["titre"],
                diapos[28])
    finally:
        srv.shutdown()


def test_ordre_sans_trou():
    """Le cas qui aurait ete corrompu en silence : toutes les diapos ont un
    audio, donc rien n'empechait un tri par numero de fichier."""
    print("\n[2] Meme cours, mais toutes les diapos ont un audio")
    srv, base = faux_uness.demarrer(variante="opaque", ordre=ORDRE_REEL,
                                    audio=False)
    try:
        _, diapos, methode = _decouvrir(srv, base)
        _controler(srv, diapos, methode, None)
        verifie("la 1re diapo reste a24x3.mp3",
                diapos[0]["fichier"] == "%s3.mp3" % faux_uness.PREFIXE,
                diapos[0]["fichier"])
        verifie("la derniere diapo reste a24x2.mp3",
                diapos[-1]["fichier"] == "%s2.mp3" % faux_uness.PREFIXE,
                diapos[-1]["fichier"])
    finally:
        srv.shutdown()


def test_sondage_numerote_par_le_fichier():
    """Le repli, lui, FABRIQUE la sequence : a24x1, a24x2... Le numero du
    fichier y est le numero de diapo, et ca ne doit pas changer."""
    print("\n[3] Sondage : la ou le numero de fichier est significatif")
    srv, base = faux_uness.demarrer(nb_diapos=9, sans_audio=(3,))
    try:
        c = Client(cookies_valides(), delai=0)
        diapos = cours.sonder(c, base, "%s1.mp3" % faux_uness.PREFIXE, 9)
        verifie("9 diapos sondees", len(diapos) == 9, len(diapos))
        faux = [d for d in diapos if d["fichier"] and
                d["fichier"] != "%s%d.mp3" % (faux_uness.PREFIXE, d["n"])]
        verifie("le numero sonde est le numero de diapo", not faux, faux[:3])
        verifie("le trou reste un trou", diapos[2]["fichier"] is None,
                diapos[2])
    finally:
        srv.shutdown()


def test_numeroter_garde_l_ordre():
    print("\n[4] _numeroter() ne reordonne jamais un plan lu dans un document")
    plan = [{"titre": "Trois", "fichier": "a24x3.mp3"},
            {"titre": "Sept", "fichier": "a24x7.mp3"},
            {"titre": "Quatre", "fichier": "a24x4.mp3"}]
    sortie = cours._numeroter(plan)
    verifie("l'ordre est conserve",
            [d["fichier"] for d in sortie] ==
            ["a24x3.mp3", "a24x7.mp3", "a24x4.mp3"],
            [d["fichier"] for d in sortie])
    verifie("les diapos sont numerotees 1, 2, 3",
            [d["n"] for d in sortie] == [1, 2, 3])
    verifie("les titres suivent leur fichier",
            [d["titre"] for d in sortie] == ["Trois", "Sept", "Quatre"])


# ---------------------------------------------------------------------------
# Les formats de donnees plausibles : on n'en connait pas la liste, donc on
# couvre les quatre familles au lieu d'en coder un en dur.
# ---------------------------------------------------------------------------

def test_formats():
    print("\n[5] Titres : XML a attributs, XML a enfants, JS, listes paralleles")

    xml_attrs = ("""<?xml version="1.0" encoding="UTF-8"?>
<Presentation><Slides>
 <Slide navTitle="Auscultation : le murmure vésiculaire"><Audio src="a24x3.mp3"/></Slide>
 <Slide navTitle="Les râles crépitants"><Audio src="a24x7.mp3"/></Slide>
</Slides></Presentation>""")
    d = cours._diapos_depuis_xml(xml_attrs)
    verifie("XML a attributs : titres et ordre",
            [(x["titre"], x["fichier"]) for x in d] ==
            [("Auscultation : le murmure vésiculaire", "a24x3.mp3"),
             ("Les râles crépitants", "a24x7.mp3")], d)

    xml_enfants = ("""<?xml version="1.0" encoding="UTF-8"?>
<slides>
 <slide><title>Sibilants et ronchi</title><audio file="a24x28.mp3"/></slide>
 <slide><title>Le frottement pleural</title><audio file="a24x5.mp3"/></slide>
</slides>""")
    d = cours._diapos_depuis_xml(xml_enfants)
    verifie("XML a elements enfants : titres et ordre",
            [(x["titre"], x["fichier"]) for x in d] ==
            [("Sibilants et ronchi", "a24x28.mp3"),
             ("Le frottement pleural", "a24x5.mp3")], d)

    js = ('var conf = {"slides": ['
          '{"navTitle": "Pneumothorax : sémiologie", "audio": "data/a24x31.mp3"},'
          '{"navTitle": "Synthèse et points clés", "audio": "data/a24x9.mp3"}]};')
    d = cours._diapos_depuis_js(js)
    verifie("JS/JSON : un objet par diapo",
            [(x["titre"], x["fichier"]) for x in d] ==
            [("Pneumothorax : sémiologie", "a24x31.mp3"),
             ("Synthèse et points clés", "a24x9.mp3")], d)

    # Le format du vrai cours : deux listes paralleles, servies telles quelles
    # par le faux lecteur.
    trois = [(3, "Poumons et bronches", 7.0), (7, "Inspection du thorax", 9.0),
             (4, "Dyspnée : définition", 11.0)]
    d = cours._diapos_depuis_js(faux_uness.donnees_opaque(trois))
    verifie("listes paralleles : appariement positionnel",
            [(x["titre"], x["fichier"]) for x in d] ==
            [("Poumons et bronches", "a24x3.mp3"),
             ("Inspection du thorax", "a24x7.mp3"),
             ("Dyspnée : définition", "a24x4.mp3")], d)

    # Listes paralleles aux cles quelconques : c'est la forme des titres qui
    # les designe, pas leur nom.
    quelconque = ('vt = {"items": ["Le poumon droit et ses lobes",'
                  ' "La plèvre et ses feuillets"],'
                  ' "media": ["a24x5.mp3", "a24x2.mp3"]};')
    d = cours._diapos_depuis_js(quelconque)
    verifie("listes paralleles aux cles inconnues",
            [(x["titre"], x["fichier"]) for x in d] ==
            [("Le poumon droit et ses lobes", "a24x5.mp3"),
             ("La plèvre et ses feuillets", "a24x2.mp3")], d)

    # Et surtout : on n'invente pas un titre a partir d'une liste qui n'en
    # contient pas. Un identifiant n'est pas un titre.
    ids = ('vt = {"ids": ["s1", "s2"], "media": ["a24x5.mp3", "a24x2.mp3"]};')
    d = cours._diapos_depuis_js(ids)
    verifie("un identifiant n'est jamais pris pour un titre",
            [x["titre"] for x in d] == ["", ""],
            [x["titre"] for x in d])


def main():
    test_ordre_avec_trou()
    test_ordre_sans_trou()
    test_sondage_numerote_par_le_fichier()
    test_numeroter_garde_l_ordre()
    test_formats()
    print("\n%d reussis, %d echoues" % (len(OK), len(KO)))
    if KO:
        print("Echecs : " + ", ".join(KO))
    return 1 if KO else 0


if __name__ == "__main__":
    sys.exit(main())
