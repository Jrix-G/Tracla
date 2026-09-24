# -*- coding: utf-8 -*-
"""La fenetre de connexion a UNESS, et les cookies qui en sortent.

Principe : l'application ne voit jamais le mot de passe. Elle ouvre une vraie
fenetre du navigateur Edge deja installe sur la machine, l'utilisateur s'y
connecte comme d'habitude (SSO, double authentification comprise), et
l'application se contente de relire les cookies de cette fenetre.

Le profil du navigateur est un dossier a cote de l'exe : la connexion est donc
memorisee d'un lancement a l'autre, et le bouton "Se deconnecter" se reduit a
supprimer ce dossier.

Pourquoi un sous-processus : Playwright et le serveur web veulent chacun leur
boucle d'evenements, et la fenetre doit survivre a une requete HTTP. On relance
donc l'application elle-meme avec --fenetre-connexion, qui ecrit le resultat en
JSON sur sa sortie standard.
"""

import json
import re
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin, urlparse

# Meme definition que pour les telechargements : une page de connexion est
# un formulaire d'identification, pas une page qui contient un lien vers
# la page de connexion.
from .telechargement import _page_de_connexion as _est_page_de_connexion
from .cours import ispring

ATTENTE_MAX = 15 * 60        # l'utilisateur a 15 min pour se connecter
DOMAINE = "formation.uness.fr"


class ErreurConnexion(Exception):
    """Erreur montrable telle quelle a l'utilisateur."""


# ---------------------------------------------------------------------------
# Stockage local
# ---------------------------------------------------------------------------

class Coffre:
    """Le profil du navigateur et les cookies, sur disque, chez l'utilisateur.

    Rien n'est chiffre : ce sont exactement les memes donnees que celles que
    le navigateur garde deja. L'interet du dossier dedie est qu'un seul bouton
    suffit a tout effacer.
    """

    def __init__(self, racine):
        self.racine = racine
        self.profil = os.path.join(racine, "profil-navigateur")
        self.cookies = os.path.join(racine, "cookies.json")
        os.makedirs(racine, exist_ok=True)

    def lire(self):
        try:
            with open(self.cookies, encoding="utf-8") as f:
                donnees = json.load(f)
            return donnees.get("cookies") or []
        except Exception:
            return []

    def ecrire(self, cookies):
        tmp = self.cookies + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies, "date": int(time.time())}, f)
        os.replace(tmp, self.cookies)
        try:
            os.chmod(self.cookies, 0o600)
        except Exception:
            pass

    def effacer(self):
        import shutil
        shutil.rmtree(self.profil, ignore_errors=True)
        try:
            os.remove(self.cookies)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Cookie colle a la main (le filet de securite)
# ---------------------------------------------------------------------------

def cookies_depuis_texte(texte, domaine=DOMAINE):
    """Accepte ce que l'utilisateur arrive a copier :
    'MoodleSession=abc', 'abc' tout court, ou une ligne d'en-tete Cookie
    complete avec plusieurs valeurs.

    'domaine' est celui du lien colle : un cookie pose sur le mauvais domaine
    ne serait tout simplement jamais envoye.
    """
    domaine = domaine or DOMAINE
    texte = (texte or "").strip().strip(";")
    if not texte:
        raise ErreurConnexion("Colle la valeur du cookie avant de valider.")
    if texte.lower().startswith("cookie:"):
        texte = texte.split(":", 1)[1].strip()
    cookies = []
    if "=" in texte:
        for bout in texte.split(";"):
            if "=" not in bout:
                continue
            nom, valeur = bout.split("=", 1)
            nom, valeur = nom.strip(), valeur.strip().strip('"')
            if nom and valeur:
                cookies.append({"name": nom, "value": valeur,
                                "domain": domaine, "path": "/"})
    else:
        cookies.append({"name": "MoodleSession", "value": texte,
                        "domain": domaine, "path": "/"})
    if not cookies:
        raise ErreurConnexion(
            "Cette valeur ne ressemble pas a un cookie. Attendu : quelque "
            "chose comme MoodleSession=a1b2c3...")
    return cookies


# ---------------------------------------------------------------------------
# La fenetre (cote sous-processus)
# ---------------------------------------------------------------------------

def _ouvrir_fenetre(url, profil, url_test, sortie):
    """Tourne dans le sous-processus. Ouvre Edge, attend que la session soit
    valide, ecrit les cookies en JSON sur stdout.

    'url_test' est l'URL d'un fichier audio du cours : la seule preuve
    acceptable que la session fonctionne vraiment (Moodle renvoie sa page de
    connexion en 200, un code HTTP ne prouve rien).
    """
    from playwright.sync_api import sync_playwright

    os.makedirs(profil, exist_ok=True)
    with sync_playwright() as p:
        erreurs = []
        contexte = None
        # msedge d'abord : present sur tout Windows 10/11, rien a telecharger.
        # chrome ensuite, et enfin le Chromium de Playwright si quelqu'un l'a
        # installe -- ce que notre livrable ne fait pas.
        for canal in ("msedge", "chrome", None):
            try:
                contexte = p.chromium.launch_persistent_context(
                    profil, headless=False, channel=canal,
                    args=["--no-first-run", "--no-default-browser-check"],
                    viewport={"width": 1100, "height": 820})
                break
            except Exception as e:
                erreurs.append("%s : %s" % (canal or "chromium", e))
        if contexte is None:
            raise RuntimeError(
                "Aucun navigateur utilisable. " + " | ".join(erreurs))

        page = contexte.pages[0] if contexte.pages else contexte.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        hote = (urlparse(url).hostname or DOMAINE).lower()

        limite = time.time() + ATTENTE_MAX
        resultat = {"ok": False, "cookies": [],
                    "message": "Fenetre fermee avant la connexion."}
        # A la toute premiere connexion on ne connait encore aucun mp3 : on ne
        # peut pas les lire sans etre identifie. La fenetre en trouve donc un
        # elle-meme dans les donnees du lecteur, puis le teste.
        cible = url_test if (url_test or "").lower().endswith(".mp3") else None
        dernier_retour = 0.0
        trace = []
        while time.time() < limite:
            if not contexte.pages:
                break                      # l'utilisateur a ferme la fenetre
            if time.time() - dernier_retour > 8:
                _ramener_au_cours(page, url, hote)
                dernier_retour = time.time()
            # On ne garde que les dernieres observations : une attente de 15
            # minutes produirait des milliers de lignes inutiles.
            del trace[:-12]
            # Publier AVANT le premier 'continue' : c'est precisement quand on
            # ne trouve rien que l'utilisateur a besoin de voir ce qu'on
            # cherche. Publier seulement en fin de boucle laissait le cas qui
            # echoue completement muet.
            _ecrire(sortie, {"ok": False, "encours": True, "trace": trace[-8:],
                             "onglet": _ou_est_l_onglet(page)})
            if cible is None:
                cible = _trouver_un_mp3(contexte, url, trace, page)
                _ecrire(sortie, {"ok": False, "encours": True,
                                 "trace": trace[-8:], "onglet": _ou_est_l_onglet(page)})
                if cible is None:
                    time.sleep(1.5)
                    continue
            code, ctype, _ = _sonder(contexte, cible, trace=trace)
            if not (code in (200, 206) and ctype.startswith("audio/")) \
                    and _meme_site(page, cible):
                # Meme repli que pour les pages : le contexte peut ne pas
                # porter la session que l'onglet, lui, possede.
                rep = _via_la_page(page, cible)
                if rep and rep.get("code"):
                    code, ctype = rep["code"], (rep.get("type") or "").lower()
                    if trace is not None:
                        trace.append("%s -> %s %s via l'onglet"
                                     % (_sans_parametres(cible), code, ctype))
            if code in (200, 206) and ctype.startswith("audio/"):
                resultat = {"ok": True,
                            "cookies": _cookies_du_domaine(contexte, hote),
                            "message": "Connexion reussie."}
                break
            # On publie la trace au fil de l'eau : sinon l'application reste
            # muette tant que l'utilisateur n'a pas ferme la fenetre, c'est-a-
            # dire exactement quand il aurait besoin de savoir ce qui bloque.
            _ecrire(sortie, {"ok": False, "encours": True, "trace": trace[-8:],
                             "onglet": _ou_est_l_onglet(page)})
            time.sleep(1.5)
        if not resultat["ok"]:
            resultat["trace"] = trace[-8:]

        try:
            contexte.close()
        except Exception:
            pass

    _ecrire(sortie, resultat)


def _ecrire(chemin, donnees):
    """Ecriture atomique : l'application lit ce fichier pendant qu'on l'ecrit."""
    tmp = chemin + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(donnees, f)
        os.replace(tmp, chemin)
    except Exception:
        pass


def _ramener_au_cours(page, url, hote):
    """Redemande la page du cours quand Moodle a depose l'utilisateur ailleurs.

    Observe sur le vrai site : l'authentification passe par CAS
    (auth.uness.fr), reussit, puis Moodle **perd le 'wantsurl'** parce que la
    cible est un pluginfile.php, et renvoie sur le tableau de bord
    (/formation/my/).

    Ce n'est PAS ce qui bloquait la detection -- le sondage passe par le
    contexte et se moque de la page affichee (verifie : le scenario C du test
    passe aussi sans cette fonction). C'est du confort : l'utilisateur doit
    voir son cours a l'ecran, pas un tableau de bord, sans quoi il croit que
    rien ne s'est passe.

    On ne touche a rien tant qu'on est sur le domaine du fournisseur
    d'identite : c'est la que l'utilisateur tape ses identifiants et son code
    de double authentification. On ne redemande le cours qu'une fois revenu
    sur le site du cours, et jamais si un champ de mot de passe est affiche.
    """
    try:
        ici = page.url or ""
    except Exception:
        return
    if not ici or ici.split("?")[0].rstrip("/") == url.split("?")[0].rstrip("/"):
        return
    if (urlparse(ici).hostname or "").lower() != hote:
        return                       # on est chez le fournisseur d'identite
    try:
        if page.evaluate(
                "() => !!document.querySelector('input[type=password]')"):
            return                   # l'utilisateur est en train de se connecter
    except Exception:
        return
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass


def _sans_parametres(url):
    """Une URL sans sa chaine de requete : les redirections d'authentification
    y transportent des tickets et des codes a usage unique, qui n'ont rien a
    faire dans un journal."""
    return (url or "").split("?")[0]


def _via_la_page(page, url, entier=False):
    """Recupere une URL en executant le fetch DANS l'onglet.

    Repli quand le contexte ne transmet pas la session : un fetch lance depuis
    la page part avec les cookies de la page, exactement comme si l'utilisateur
    cliquait. Limite connue : soumis aux regles CORS, donc utilisable seulement
    quand l'onglet est deja sur le site vise -- ce que l'appelant verifie.
    """
    script = """async (a) => {
        try {
          const o = a.entier ? {} : {headers: {Range: 'bytes=0-1023'}};
          o.credentials = 'include';
          const r = await fetch(a.u, o);
          const t = a.entier ? await r.text() : '';
          return {code: r.status, type: r.headers.get('content-type') || '',
                  texte: t, url: r.url};
        } catch (e) { return {code: 0, type: '', texte: '', url: '' }; }
    }"""
    try:
        return page.evaluate(script, {"u": url, "entier": bool(entier)})
    except Exception:
        return None


def _ou_est_l_onglet(page):
    """Ou en est la fenetre, du point de vue de l'utilisateur. Sans cette
    information, impossible de distinguer "il n'est pas connecte" de "il est
    connecte mais la session ne parvient pas jusqu'a nos requetes"."""
    try:
        return "%s  (%s)" % (_sans_parametres(page.url), (page.title() or "")[:70])
    except Exception:
        return ""


def _meme_site(page, url):
    try:
        return (urlparse(page.url).hostname or "").lower() == \
               (urlparse(url).hostname or "").lower()
    except Exception:
        return False


def _sonder(contexte, url, plage=True, trace=None, limite=8192):
    """GET par le contexte du navigateur. Renvoie (code, content-type, debut).

    Pourquoi pas un fetch() execute dans la page : pendant un SSO, l'onglet se
    trouve sur le domaine de la federation d'identite, et un fetch vers
    formation.uness.fr devient une requete cross-origin que le navigateur
    bloque, Moodle n'envoyant aucun en-tete CORS. La fenetre ne pourrait alors
    jamais constater que la connexion a abouti. Le contexte, lui, partage les
    cookies de la fenetre sans etre soumis a ces regles.
    """
    entetes = {"Range": "bytes=0-1023"} if plage else {}
    try:
        r = contexte.request.get(url, headers=entetes, timeout=25000)
    except Exception as e:
        if trace is not None:
            trace.append("%s -> erreur reseau (%s)"
                         % (_sans_parametres(url), type(e).__name__))
        return None, "", b""
    ctype = (r.headers.get("content-type") or "").lower()
    try:
        corps = r.body()
        # 'limite' ne sert qu'a ne pas charger un mp3 entier pour un sondage.
        # Une page qu'on doit ANALYSER se lit en entier : l'en-tete d'une page
        # Moodle depasse a lui seul 8 Ko, et le lien vers le lecteur se trouve
        # bien plus loin. Le tronquer revenait a ne jamais le voir.
        if limite:
            corps = corps[:limite]
    except Exception:
        corps = b""
    if trace is not None:
        note = "%s -> %d %s, %d o" % (_sans_parametres(url), r.status,
                                      ctype or "type inconnu", len(corps))
        if _est_page_de_connexion(corps):
            note += "  [PAGE DE CONNEXION]"
        if _sans_parametres(r.url) != _sans_parametres(url):
            note += "  [redirige vers %s]" % _sans_parametres(r.url)
        trace.append(note)
    return r.status, ctype, corps


_MP3_DANS_TEXTE = re.compile(r"[A-Za-z0-9_.\-]+\.mp3", re.IGNORECASE)


# Les fichiers de donnees ou chercher un nom de mp3, en plus de la page.
_SOURCES = ("data/presentation.xml", "data/presentationData.js",
            "data/vt_data.js", "data/slides.xml")

# Les fichiers que la page charge elle-meme : c'est la seule facon fiable de
# decouvrir la structure d'un lecteur qu'on n'a jamais vu.
_RESSOURCE = re.compile(
    r"""(?:src|href|data)\s*=\s*["']([^"']+\.(?:js|xml|json|txt|htm|html))["']""",
    re.IGNORECASE)

# Le lecteur, tel que Moodle l'insere dans sa page (cadre, objet ou lien).
_LECTEUR = re.compile(
    r"""["'(]([^"'()\s]*pluginfile\.php/[^"'()\s]*?\.html?)(?:\?[^"'()\s]*)?["')]""",
    re.IGNORECASE)


def _trouver_un_mp3(contexte, url, trace=None, page=None):
    """Un mp3 du cours, ou None tant qu'on ne voit qu'une page de connexion.

    Moodle renvoie sa page de connexion en 200 : on la reconnait a son
    contenu, jamais au code HTTP.
    """
    base = url.rsplit("/", 1)[0] + "/"

    def lire(lien):
        """L'onglet d'abord, le contexte ensuite.

        Sur UNESS, le contexte Playwright ne porte pas la session : chaque
        requete revient en page de connexion CAS. L'onglet, lui, l'a --
        l'utilisateur y voit son cours. On interroge donc l'onglet en premier,
        et on ne retombe sur le contexte que s'il ne peut pas repondre (autre
        origine, donc bloque par CORS).
        """
        if page is not None and _meme_site(page, lien):
            rep = _via_la_page(page, lien, entier=True)
            if rep and rep.get("code") and rep["code"] < 400:
                brut = (rep.get("texte") or "").encode("utf-8", "replace")
                if not _est_page_de_connexion(brut):
                    if trace is not None:
                        trace.append("%s -> %d via l'onglet, %d o"
                                     % (_sans_parametres(lien), rep["code"],
                                        len(brut)))
                    return brut
        # limite=None : on analyse ces pages, on les lit donc en entier.
        code, _, corps = _sonder(contexte, lien, plage=False, trace=trace,
                                 limite=None)
        if code is None or code >= 400 or _est_page_de_connexion(corps):
            return None
        return corps

    corps = lire(url)
    if corps is None:
        return None

    # On cherche D'ABORD ce qu'on veut, et on ne conclut "page de connexion"
    # qu'en dernier. L'inverse -- ecarter la page des qu'elle ressemble a une
    # connexion -- faisait rejeter la page de cours de l'utilisateur, pourtant
    # valide, parce que le pied de page de Moodle contient un lien /login/.
    texte = corps.decode("utf-8", "replace")

    # L'utilisateur colle ce que son navigateur affiche : la page Moodle
    # (mod/resource/view.php?id=...), pas le lecteur. Elle ne contient aucun
    # mp3 -- mais elle contient l'adresse du lecteur, qui elle en contient.
    if "pluginfile.php/" not in url:
        m = _LECTEUR.search(texte)
        if m:
            vrai = urljoin(url, m.group(1)).split("?")[0]
            if trace is not None:
                trace.append("lecteur trouve dans la page : %s"
                             % _sans_parametres(vrai))
            return _trouver_un_mp3(contexte, vrai, trace, page)

    def premier_mp3(contenu):
        if not contenu:
            return None
        if isinstance(contenu, bytes):
            contenu = contenu.decode("utf-8", "replace")
        # Lecteur iSpring : les noms des mp3 sont dans un bloc compresse de
        # la page, invisibles pour une simple recherche de texte.
        plan = ispring(contenu)
        if plan:
            for d in plan[1]:
                if d["fichier"]:
                    return base + "data/" + d["fichier"]
        m = _MP3_DANS_TEXTE.search(contenu)
        return base + "data/" + m.group(0).rsplit("/", 1)[-1] if m else None

    # La page elle-meme d'abord.
    trouve = premier_mp3(texte)
    if trouve:
        return trouve

    # Puis les fichiers que la page reference VRAIMENT. Deviner des noms de
    # manifestes ne marche que pour les lecteurs qu'on connait deja ; suivre
    # ce que la page charge marche pour tous. Les noms devines restent en
    # complement, au cas ou le fichier serait charge dynamiquement.
    references = []
    for m in _RESSOURCE.finditer(texte):
        chemin = m.group(1)
        if chemin.startswith(("http://", "https://", "//", "data:")):
            continue
        if chemin not in references:
            references.append(chemin)
    if trace is not None and references:
        trace.append("la page reference : " + ", ".join(references[:6]))

    for chemin in references[:12] + [c for c in _SOURCES
                                     if c not in references]:
        trouve = premier_mp3(lire(urljoin(url, chemin)))
        if trouve:
            return trouve

    if trace is not None and _est_page_de_connexion(corps):
        trace.append("%s : formulaire de connexion, on attend"
                     % _sans_parametres(url))
    return None


def _cookies_du_domaine(contexte, hote=DOMAINE):
    """Uniquement les cookies du site du cours : on n'emporte jamais le reste
    du profil du navigateur."""
    hote = (hote or DOMAINE).lower()
    gardes = []
    for c in contexte.cookies():
        d = (c.get("domain") or "").lstrip(".").lower()
        if d == hote or d.endswith("." + hote) or hote.endswith("." + d):
            gardes.append({"name": c["name"], "value": c["value"],
                           "domain": c.get("domain") or hote,
                           "path": c.get("path") or "/"})
    return gardes


# ---------------------------------------------------------------------------
# La fenetre (cote application)
# ---------------------------------------------------------------------------

def playwright_disponible():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def _commande(url, profil, url_test, sortie):
    """La meme application, relancee avec un drapeau. Sous PyInstaller,
    sys.executable EST l'exe : il n'y a pas d'interpreteur Python a trouver."""
    if getattr(sys, "frozen", False):
        base = [sys.executable]
    else:
        base = [sys.executable, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")]
    return base + ["--fenetre-connexion", url, profil, url_test, sortie]


class Connexion:
    """Pilote la fenetre de connexion depuis le serveur, sans le bloquer."""

    def __init__(self, coffre):
        self.coffre = coffre
        self.verrou = threading.RLock()
        self.processus = None
        self.fichier = None
        self.etat = "repos"          # repos|ouverte|reussie|echec
        self.message = ""
        self.trace = []              # ce que la fenetre a vu, en cas d'echec

    @property
    def en_cours(self):
        return self.processus is not None and self.processus.poll() is None

    def trace_vive(self):
        """Ce que la fenetre voit EN CE MOMENT. Sans ca, l'utilisateur n'a
        aucune information tant qu'il n'a pas ferme la fenetre."""
        if not self.en_cours or not self.fichier:
            return list(self.trace)
        try:
            with open(self.fichier, encoding="utf-8") as f:
                donnees = json.load(f)
            lignes = list((donnees.get("trace") or [])[-8:])
            if donnees.get("onglet"):
                lignes.insert(0, "onglet affiche : " + donnees["onglet"])
            return lignes
        except Exception:
            return list(self.trace)

    def ouvrir(self, url, url_test, quand_fini=None):
        with self.verrou:
            if self.en_cours:
                return "deja"
            if not playwright_disponible():
                raise ErreurConnexion(
                    "La fenetre de connexion n'est pas disponible sur cette "
                    "installation. Utilise la methode manuelle ci-dessous.")
            import tempfile
            self.fichier = os.path.join(tempfile.gettempdir(),
                                        "uness-connexion-%d.json" % os.getpid())
            try:
                os.remove(self.fichier)
            except FileNotFoundError:
                pass
            cmd = _commande(url, self.coffre.profil, url_test, self.fichier)
            env = dict(os.environ, TRANSCRIPTEUR_SANS_NAVIGATEUR="1")
            # CREATE_NO_WINDOW : l'exe est construit avec une console, et sans
            # ce drapeau le sous-processus ferait clignoter une fenetre noire
            # a cote de la fenetre de connexion.
            drapeaux = 0x08000000 if os.name == "nt" else 0
            try:
                self.processus = subprocess.Popen(
                    cmd, env=env, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, close_fds=True,
                    creationflags=drapeaux)
            except Exception as e:
                raise ErreurConnexion(
                    "La fenetre de connexion n'a pas pu s'ouvrir (%s). "
                    "Utilise la methode manuelle ci-dessous." % e)
            self.etat = "ouverte"
            self.message = ("Connecte-toi dans la fenetre qui vient de "
                            "s'ouvrir. Cette page se mettra a jour toute seule.")
        threading.Thread(target=self._attendre, args=(quand_fini,),
                         daemon=True).start()
        return "ouverte"

    def _attendre(self, quand_fini):
        p = self.processus
        _, err = p.communicate()
        resultat = {}
        try:
            with open(self.fichier, encoding="utf-8") as f:
                resultat = json.load(f)
            os.remove(self.fichier)
        except Exception:
            pass
        with self.verrou:
            if resultat.get("ok") and resultat.get("cookies"):
                self.coffre.ecrire(resultat["cookies"])
                self.etat, self.message = "reussie", "Connecte a UNESS."
                self.trace = []
            else:
                self.etat = "echec"
                # Ce que la fenetre a reellement recu : c'est la seule facon
                # de diagnostiquer un site qu'on ne peut pas reproduire.
                self.trace = resultat.get("trace") or []
                # On ne remonte jamais la sortie brute du navigateur : elle
                # peut contenir des URL de session.
                self.message = resultat.get("message") or (
                    "La fenetre s'est fermee avant la fin de la connexion."
                    if p.returncode == 0 else
                    "La fenetre de connexion n'a pas pu s'ouvrir. Verifie que "
                    "Microsoft Edge est installe, ou utilise la methode "
                    "manuelle ci-dessous.")
            self.processus = None
        if quand_fini:
            try:
                quand_fini(self.etat == "reussie", self.message)
            except Exception:
                pass

    def annuler(self):
        with self.verrou:
            if self.en_cours:
                try:
                    self.processus.terminate()
                except Exception:
                    pass


def main_fenetre(argv):
    """Point d'entree du sous-processus : app.py --fenetre-connexion ..."""
    url, profil, url_test, sortie = argv[:4]
    try:
        _ouvrir_fenetre(url, profil, url_test, sortie)
        return 0
    except Exception as e:
        try:
            with open(sortie, "w", encoding="utf-8") as f:
                json.dump({"ok": False, "cookies": [],
                           "message": "Le navigateur n'a pas demarre : %s" % e}, f)
        except Exception:
            pass
        return 1
