# -*- coding: utf-8 -*-
"""Lecture de la page d'un cours UNESS : quelles diapos, quels mp3, quels titres.

Rien n'est code en dur sur un cours precis. On part de index.htm, on suit les
fichiers de donnees du lecteur Adobe Presenter, et on ne retombe sur le sondage
des numeros qu'en dernier recours.
"""

import json
import os
import re
import unicodedata
from urllib.parse import unquote, urljoin, urlparse
from xml.etree import ElementTree

# Liste blanche : la session de l'utilisateur ne part jamais ailleurs.
DOMAINES = ("formation.uness.fr",)


def _mode_test():
    """Les tests font tourner un faux Moodle en http sur 127.0.0.1. Cette
    ouverture n'existe que si la variable d'environnement est posee, ce qui
    n'arrive jamais dans le livrable."""
    return os.environ.get("TRANSCRIPTEUR_TEST_UNESS") == "1"

# Fichiers de donnees connus des sorties Adobe Presenter / Captivate. On les
# essaie dans cet ordre ; le premier qui donne des diapos gagne.
MANIFESTES = (
    "data/presentation.xml",
    "data/presentationData.js",
    "data/vt_data.js",
    "data/slides.xml",
    "data/manifest.xml",
    "data/data.js",
    "data/project.txt",
    "data/assets/presentation.xml",
    "presentation.xml",
    "imsmanifest.xml",
)

MP3_RE = re.compile(r"[A-Za-z0-9_.\-]+\.mp3", re.IGNORECASE)
# 'a24x76.mp3' -> ('a24x', '76'). Le prefixe est propre a chaque cours.
MOTIF_RE = re.compile(r"^(.*?)(\d+)(\.mp3)$", re.IGNORECASE)

MAX_DIAPOS = 2000  # garde-fou : on ne sonde jamais indefiniment

# Les cles sous lesquelles un lecteur range le titre d'une diapo. On ne
# connait pas la liste des lecteurs : on cherche donc les memes cles dans le
# XML, dans le JSON et dans le JavaScript, au lieu d'un format en dur.
CLES_TITRE = ("title", "label", "name", "displayname", "navtitle",
              "slidetitle", "heading", "caption")


class ErreurCours(Exception):
    """Erreur montrable telle quelle a l'utilisateur."""


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------

def verifier_url(url):
    """Valide l'URL saisie et renvoie (url, base) ou leve ErreurCours.

    'base' est le dossier du cours, termine par '/' : c'est la racine a
    laquelle 'data/xxx.mp3' est relatif.

    'base' vaut **None** quand l'URL est une page Moodle (par exemple
    .../mod/resource/view.php?id=44419) : c'est la page qui CONTIENT le
    lecteur, pas le lecteur. C'est pourtant celle que l'utilisateur a sous les
    yeux dans sa barre d'adresse, donc celle qu'il colle. Il faut la lire,
    connecte, pour trouver l'adresse reelle du lecteur : voir
    resoudre_lecteur().
    """
    url = (url or "").strip()
    if not url:
        raise ErreurCours("Colle d'abord le lien de ton cours UNESS.")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    p = urlparse(url)
    hote = (p.hostname or "").lower()
    local = _mode_test() and hote in ("127.0.0.1", "localhost")
    if p.scheme.lower() != "https" and not local:
        raise ErreurCours("Le lien doit commencer par https:// "
                          "(connexion chiffree).")
    if hote not in DOMAINES and not local:
        raise ErreurCours(
            "Ce lien ne semble pas etre un cours UNESS. Seuls les liens de "
            "%s sont acceptes, et celui-ci pointe vers %s."
            % (DOMAINES[0], hote or "un site inconnu"))
    chemin = p.path or "/"
    # La chaine de requete fait partie de l'adresse : sans le '?id=44419',
    # mod/resource/view.php repond "Identifiant de module de cours non valide".
    requete = ("?" + p.query) if p.query else ""
    racine = "%s://%s" % (p.scheme.lower(), p.netloc)
    dernier = chemin.rsplit("/", 1)[-1]

    if dernier.endswith(".php"):
        return racine + chemin + requete, None

    # On accepte l'URL du dossier comme celle d'un fichier de la page.
    if chemin.endswith("/"):
        base_chemin, index = chemin, chemin + "index.htm"
    elif "." in dernier:
        base_chemin = chemin.rsplit("/", 1)[0] + "/"
        index = chemin
    else:
        base_chemin = chemin + "/"
        index = base_chemin + "index.htm"
    return racine + index + requete, racine + base_chemin


# Le lecteur, tel que Moodle l'insere dans sa page : un iframe, un object, un
# lien "ouvrir dans une nouvelle fenetre", ou une redirection directe.
LECTEUR_RE = re.compile(
    r"""["'(]([^"'()\s]*pluginfile\.php/[^"'()\s]*?\.html?)(?:\?[^"'()\s]*)?["')]""",
    re.IGNORECASE)


def resoudre_lecteur(client, url):
    """Depuis une page Moodle, trouve l'adresse du lecteur. -> (url, base)

    L'utilisateur colle ce que son navigateur affiche :
    .../mod/resource/view.php?id=44419. Ce n'est pas le lecteur, c'est la page
    qui l'affiche -- dans un cadre, ou derriere une redirection. On la lit
    (connecte) et on en extrait l'adresse reelle.
    """
    html = client.texte(url)
    if html is None:
        raise ErreurCours(
            "Impossible de lire cette page du cours. Verifie le lien, et que "
            "tu es bien connecte a UNESS.")

    # Moodle redirige parfois directement vers le fichier : dans ce cas
    # l'adresse finale est deja la bonne.
    finale = getattr(client, "derniere_url", None) or url
    if "pluginfile.php/" in finale and re.search(r"\.html?($|\?)", finale, re.I):
        propre = finale.split("?")[0]
        return propre, propre.rsplit("/", 1)[0] + "/"

    for m in LECTEUR_RE.finditer(html):
        lien = urljoin(finale, _desechapper(m.group(1))).split("?")[0]
        return lien, lien.rsplit("/", 1)[0] + "/"

    raise ErreurCours(
        "Cette page ne contient pas de lecteur de cours. Ouvre ton cours dans "
        "ton navigateur, va sur la page du lecteur (celle avec les diapos et "
        "le bouton de lecture), et copie SON adresse.")


def identifiant(base):
    """Cle de cache stable et sans surprise pour un cours donne."""
    p = urlparse(base)
    bouts = [b for b in p.path.split("/") if b]
    # .../pluginfile.php/116687/mod_resource/content/0/  -> '116687-content-0'
    interessants = [b for b in bouts if b not in
                    ("pluginfile.php", "mod_resource", "formation", "draftfile.php")]
    cle = "-".join(interessants[-3:]) or "cours"
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", cle)[:60]


# ---------------------------------------------------------------------------
# Titres
# ---------------------------------------------------------------------------

def _propre(txt):
    if not txt:
        return ""
    txt = unicodedata.normalize("NFC", str(txt))
    txt = re.sub(r"<[^>]+>", " ", txt)            # residus de balises
    txt = txt.replace(" ", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    # Le lecteur tronque a l'ecran ; un titre qui finit par des points de
    # suspension vient de l'affichage, pas des donnees.
    return txt.strip(" .…") if txt.endswith(("...", "…")) else txt


def _desechapper(txt):
    """Les fichiers de donnees du lecteur sont souvent doublement encodes."""
    if not txt:
        return txt
    for _ in range(2):
        avant = txt
        if "%" in txt:
            try:
                txt = unquote(txt)
            except Exception:
                pass
        if "&" in txt:
            for ent, car in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                             ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
                             ("&nbsp;", " ")):
                txt = txt.replace(ent, car)
        if "\\u" in txt:
            try:
                txt = txt.encode("utf-8").decode("unicode_escape")
            except Exception:
                pass
        if txt == avant:
            break
    return txt


# ---------------------------------------------------------------------------
# Analyse des fichiers de donnees
# ---------------------------------------------------------------------------

def _diapos_depuis_xml(texte):
    """Sortie Adobe Presenter : des elements de diapo portant un titre et,
    quelque part, un nom de mp3."""
    try:
        racine = ElementTree.fromstring(texte.encode("utf-8", "replace")
                                        if isinstance(texte, str) else texte)
    except Exception:
        return []

    diapos = []
    for el in racine.iter():
        nom = el.tag.rsplit("}", 1)[-1].lower()
        if nom not in ("slide", "item", "page", "frame", "resource"):
            continue
        attrs = {k.rsplit("}", 1)[-1].lower(): v for k, v in el.attrib.items()}
        # Le mp3 peut etre un attribut, un texte d'enfant, ou plus bas.
        blob = " ".join([texte_el for texte_el in
                         [el.text or ""] + list(attrs.values()) +
                         [(e.text or "") + " " + " ".join(e.attrib.values())
                          for e in el.iter()]])
        mp3 = MP3_RE.search(blob)
        titre = ""
        for cle in CLES_TITRE + ("text",):
            if attrs.get(cle):
                titre = _propre(_desechapper(attrs[cle]))
                if titre:
                    break
        if not titre:
            for enfant in el:
                if enfant.tag.rsplit("}", 1)[-1].lower() in CLES_TITRE + ("text",):
                    titre = _propre(_desechapper(enfant.text or ""))
                    if titre:
                        break
        if mp3 or titre:
            diapos.append({"titre": titre,
                           "fichier": mp3.group(0) if mp3 else None})
    return diapos


def _diapos_depuis_js(texte):
    """Sortie JS : souvent un gros objet JSON, parfois du JavaScript.
    On tente le JSON d'abord, puis on se rabat sur un appariement local
    titre <-> mp3 dans l'ordre du fichier."""
    diapos = []
    # La cle peut etre citee ("title": "...") comme nue (title: '...').
    cles = "|".join(CLES_TITRE)

    # 1) Un JSON complet quelque part dans le fichier. On ne tente que les
    #    quelques premieres accolades : au-dela on est dans du code, pas dans
    #    des donnees, et l'essai coute cher sur un fichier de 1 Mo.
    decodeur = json.JSONDecoder()
    for essai, m in enumerate(re.finditer(r"[\[{]", texte)):
        if essai >= 8:
            break
        try:
            donnees = decodeur.raw_decode(texte, m.start())[0]
        except Exception:
            continue
        trouve = _ranger(_diapos_depuis_json(donnees))
        if len(trouve) >= 2:
            return trouve

    # 2) Appariement dans l'ordre : chaque mp3 prend le titre le plus proche
    #    situe avant lui.
    titres = [(m.start(), _propre(_desechapper(m.group(1))))
              for m in re.finditer(
                  r"""(?:%s)["']?\s*[:=]\s*["']([^"']{2,200})["']""" % cles,
                  texte, re.IGNORECASE)]
    mp3 = list(MP3_RE.finditer(texte))

    # Deux listes paralleles que le JSON n'a pas su lire : tous les titres
    # d'abord, tous les fichiers ensuite. Prendre "le titre juste avant"
    # collerait alors le dernier titre a chacun des mp3.
    if mp3 and len(titres) == len(mp3) and all(t for _, t in titres) \
            and titres[-1][0] < mp3[0].start():
        return [{"titre": t, "fichier": m.group(0)}
                for (_, t), m in zip(titres, mp3)]

    # Un titre ne sert qu'une fois : un fichier de donnees qui n'annonce que
    # le titre du cours le collerait sinon a chacune de ses diapos, ce qui
    # revient a inventer 37 titres a partir d'un seul.
    dernier = 0
    for m in mp3:
        avant = [i for i, (pos, t) in enumerate(titres)
                 if i >= dernier and pos < m.start() and t]
        titre = ""
        if avant:
            titre = titres[avant[-1]][1]
            dernier = avant[-1] + 1
        diapos.append({"titre": titre, "fichier": m.group(0)})
    return diapos


def _diapos_paralleles(bas):
    """Un objet qui range les titres et les fichiers dans DEUX listes de meme
    longueur, au lieu d'un objet par diapo. C'est la forme du lecteur du cours
    116724, et le rang est alors la seule chose qui relie un titre a son
    audio : une entree vide reste donc une diapo, sans audio."""
    fichiers = None
    for v in bas.values():
        if isinstance(v, list) and len(v) >= 2 and any(
                isinstance(x, str) and x.lower().endswith(".mp3") for x in v):
            fichiers = v
            break
    if fichiers is None:
        return []

    titres = None
    for cle, v in bas.items():
        if v is fichiers or not isinstance(v, list) or len(v) != len(fichiers):
            continue
        if not all(isinstance(x, str) for x in v):
            continue
        if any(x.lower().endswith(".mp3") for x in v):
            continue
        if any(c in cle for c in CLES_TITRE):
            titres = v
            break
        # A cle inconnue, c'est la forme qui tranche : un titre de diapo porte
        # des espaces, un identifiant ('s1', 'slide_02') non. Sans ce
        # garde-fou on fabriquerait des titres a partir d'une liste d'ids.
        if titres is None and sum(1 for x in v if " " in x.strip()) > len(v) / 2:
            titres = v

    sortie = []
    for i, f in enumerate(fichiers):
        f = f.rsplit("/", 1)[-1] if isinstance(f, str) else ""
        sortie.append({
            "titre": _propre(_desechapper(titres[i])) if titres else "",
            "fichier": f if f.lower().endswith(".mp3") else None})
    return sortie


def _diapos_depuis_json(donnees):
    """Parcourt un objet JSON a la recherche de dictionnaires de diapo."""
    trouve = []

    def visiter(noeud):
        if isinstance(noeud, dict):
            bas = {str(k).lower(): v for k, v in noeud.items()}
            paralleles = _diapos_paralleles(bas)
            if paralleles:
                # Les listes sont deja le plan complet : descendre dedans ne
                # ferait que reproduire les memes fichiers sans leur titre.
                trouve.extend(paralleles)
                return
            mp3 = None
            for v in bas.values():
                if isinstance(v, str) and v.lower().endswith(".mp3"):
                    mp3 = v.rsplit("/", 1)[-1]
                    break
            titre = ""
            for cle in CLES_TITRE:
                v = bas.get(cle)
                if isinstance(v, str) and v.strip():
                    titre = _propre(_desechapper(v))
                    break
            if mp3:
                trouve.append({"titre": titre, "fichier": mp3})
            for v in noeud.values():
                visiter(v)
        elif isinstance(noeud, list):
            for v in noeud:
                visiter(v)

    visiter(donnees)
    return trouve


def _titres_depuis_html(html):
    """Le plan (Outline) dans index.htm, quand il y est en dur."""
    titres = []
    for m in re.finditer(
            r"""<[^>]*class=["'][^"']*(?:outline|toc|slide)[^"']*["'][^>]*>"""
            r"""(.*?)</[a-z]+>""", html, re.IGNORECASE | re.DOTALL):
        t = _propre(_desechapper(m.group(1)))
        if 2 <= len(t) <= 200:
            titres.append(t)
    return titres


# ---------------------------------------------------------------------------
# Decouverte
# ---------------------------------------------------------------------------

def _ranger(diapos):
    """Deduplique, numerote, et jette les entrees vides."""
    vues, propres = set(), []
    for d in diapos:
        f = (d.get("fichier") or "").strip() or None
        if f and f.lower() in vues:
            continue
        if f:
            vues.add(f.lower())
        propres.append({"titre": d.get("titre") or "", "fichier": f})
    return propres


def _numeroter(diapos):
    """Numerote un plan LU dans un document : l'ordre du document fait foi.

    Constate sur le cours 116724 : ses mp3 s'appellent a24x1..a24x37, mais le
    lecteur les liste dans l'ordre 3, 7, 4, 6... Ces nombres sont des
    identifiants d'asset, pas des numeros de diapo. Trier ou renumeroter
    d'apres eux remontait un cours entier dans le desordre, sans un mot.

    Le sondage est l'autre origine possible d'un plan, et la seule ou le
    nombre present dans le nom signifie quelque chose : c'est lui qui fabrique
    la sequence. Il numerote donc lui-meme, voir sonder().
    """
    return [{"n": i + 1, "titre": d.get("titre") or "", "fichier": d["fichier"]}
            for i, d in enumerate(diapos)]


def decouvrir(client, url_index, base, journal=None):
    """Renvoie (titre_cours, [{'n', 'titre', 'fichier'}...], 'methode').

    'client' est un uness.telechargement.Client deja authentifie : il expose
    .texte(url) -> str|None et .existe(url) -> bool.
    """
    dire = journal or (lambda *a: None)

    html = client.texte(url_index)
    if html is None:
        raise ErreurCours(
            "Impossible de lire la page du cours. Verifie le lien, et que tu "
            "es bien connecte a UNESS.")

    titre_cours = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        titre_cours = _propre(_desechapper(m.group(1)))

    # -- 1. Les manifestes connus, plus les scripts reellement charges -------
    candidats = list(MANIFESTES)
    # Suivre ce que la page charge vraiment est la seule facon de lire un
    # lecteur qu'on n'a jamais vu : celui du cours 116724 n'a aucun manifeste
    # connu, juste un fichier au nom qui lui est propre.
    for m in re.finditer(
            r"""(?:src|href|data)\s*=\s*["']([^"']+\.(?:js|xml|json|txt))["']""",
            html, re.IGNORECASE):
        chemin = m.group(1)
        if not chemin.startswith(("http://", "https://", "//")):
            candidats.append(chemin)

    vus = set()
    for chemin in candidats:
        url = urljoin(base, chemin)
        if url in vus:
            continue
        vus.add(url)
        texte = client.texte(url)
        if not texte:
            continue
        est_xml = texte.lstrip().startswith("<?xml") or chemin.endswith(".xml")
        diapos = (_diapos_depuis_xml(texte) if est_xml
                  else _diapos_depuis_js(texte))
        if not diapos:
            diapos = (_diapos_depuis_js(texte) if est_xml
                      else _diapos_depuis_xml(texte))
        diapos = _ranger(diapos)
        avec_audio = [d for d in diapos if d["fichier"]]
        if len(avec_audio) >= 2:
            dire("Plan trouve dans %s : %d diapos."
                 % (chemin, len(diapos)))
            titre_cours = titre_cours or _titre_dans(texte)
            return titre_cours, _numeroter(diapos), chemin

    # -- 2. Balayage : tout mp3 cite quelque part dans la page ---------------
    tous = _ranger([{"titre": "", "fichier": f} for f in MP3_RE.findall(html)])
    if len(tous) >= 2:
        dire("Noms de fichiers trouves directement dans la page.")
        titres = _titres_depuis_html(html)
        for i, d in enumerate(tous):
            if i < len(titres):
                d["titre"] = titres[i]
        return titre_cours, _numeroter(tous), "index.htm"

    # -- 3. Dernier recours : deduire le motif et sonder ---------------------
    graine = None
    for source in (html, *(client.texte(urljoin(base, c)) or "" for c in MANIFESTES)):
        m = MP3_RE.search(source or "")
        if m and MOTIF_RE.match(m.group(0)):
            graine = m.group(0)
            break
    if not graine:
        raise ErreurCours(
            "Aucun fichier audio trouve sur cette page. Verifie que le lien "
            "pointe bien vers le lecteur du cours (index.htm) et non vers la "
            "page Moodle qui le contient.")

    total = _total_annonce(html)
    dire("Motif deduit de %s : sondage des numeros." % graine)
    diapos = sonder(client, base, graine, total, dire)
    if not diapos:
        raise ErreurCours("Aucun fichier audio trouve sur cette page.")
    return titre_cours, diapos, "sondage"


def _titre_dans(texte):
    for motif in (r"""(?:presentationTitle|courseTitle|projectName)\s*[:=]\s*["']([^"']{2,200})["']""",
                  r"""<(?:title|presentationTitle)>([^<]{2,200})</"""):
        m = re.search(motif, texte, re.IGNORECASE)
        if m:
            return _propre(_desechapper(m.group(1)))
    return ""


def _total_annonce(html):
    """Nombre de diapos si la page le dit (sinon None)."""
    for motif in (r"""(?:slideCount|totalSlides|nbSlides|slides?Count)\s*[:=]\s*(\d+)""",
                  r"""(?:slide|diapo)\w*\s*=\s*(\d{1,4})\s*;"""):
        m = re.search(motif, html, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 < n <= MAX_DIAPOS:
                return n
    return None


def sonder(client, base, graine, total, dire=lambda *a: None):
    """Repli : a24x1.mp3, a24x2.mp3... On ne s'arrete pas au premier trou.

    - si le nombre de diapos est connu, on va jusqu'au bout (une diapo sans
      audio reste une diapo) ;
    - sinon, deux absences consecutives signent la fin.
    """
    m = MOTIF_RE.match(graine)
    prefixe, suffixe = m.group(1), m.group(3)
    largeur = len(m.group(2)) if m.group(2).startswith("0") else 0

    diapos, trous = [], 0
    n = 1
    while n <= (total or MAX_DIAPOS):
        nom = "%s%s%s" % (prefixe, str(n).zfill(largeur), suffixe)
        if client.existe(urljoin(base, "data/" + nom)):
            diapos.append({"n": n, "titre": "", "fichier": nom})
            trous = 0
        else:
            diapos.append({"n": n, "titre": "", "fichier": None})
            trous += 1
            if not total and trous >= 2:
                del diapos[-2:]          # les deux absences ne comptent pas
                break
        n += 1
    dire("Sondage termine : %d diapos, dont %d avec audio."
         % (len(diapos), sum(1 for d in diapos if d["fichier"])))
    return diapos


def vocabulaire(titre_cours, diapos, limite=850):
    """Amorce pour l'initial_prompt de Whisper : le titre du cours puis les
    titres de diapos, dedupliques, tronques a la limite du prompt (Whisper
    n'en garde que 224 jetons ; on reste large mais borne)."""
    bouts, vus = [], set()
    for t in [titre_cours] + [d.get("titre") or "" for d in diapos]:
        t = (t or "").strip(" .-—")
        cle = t.lower()
        if not t or cle in vus:
            continue
        vus.add(cle)
        bouts.append(t)
    sortie = ""
    for t in bouts:
        suivant = (sortie + ", " + t) if sortie else t
        if len(suivant) > limite:
            break
        sortie = suivant
    return sortie
