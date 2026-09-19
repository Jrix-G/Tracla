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

        from urllib.parse import urlparse
        hote = (urlparse(url).hostname or DOMAINE).lower()

        limite = time.time() + ATTENTE_MAX
        resultat = {"ok": False, "cookies": [],
                    "message": "Fenetre fermee avant la connexion."}
        # A la toute premiere connexion on ne connait encore aucun mp3 : on ne
        # peut pas les lire sans etre identifie. La fenetre en trouve donc un
        # elle-meme dans les donnees du lecteur, puis le teste.
        cible = url_test if (url_test or "").lower().endswith(".mp3") else None
        while time.time() < limite:
            if not contexte.pages:
                break                      # l'utilisateur a ferme la fenetre
            if cible is None:
                cible = _trouver_un_mp3(contexte, url)
                if cible is None:
                    time.sleep(1.5)
                    continue
            code, ctype, _ = _sonder(contexte, cible)
            if code in (200, 206) and ctype.startswith("audio/"):
                resultat = {"ok": True,
                            "cookies": _cookies_du_domaine(contexte, hote),
                            "message": "Connexion reussie."}
                break
            time.sleep(1.5)

        try:
            contexte.close()
        except Exception:
            pass

    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(resultat, f)


def _sonder(contexte, url, plage=True):
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
    except Exception:
        return None, "", b""
    ctype = (r.headers.get("content-type") or "").lower()
    try:
        corps = r.body()[:8192]
    except Exception:
        corps = b""
    return r.status, ctype, corps


_MP3_DANS_TEXTE = re.compile(r"[A-Za-z0-9_.\-]+\.mp3", re.IGNORECASE)
_SENT_LE_LOGIN = re.compile(
    rb"loginform|/login/index\.php|SAMLRequest|name=[\"']password[\"']",
    re.IGNORECASE)

# Les fichiers de donnees ou chercher un nom de mp3, en plus de la page.
_SOURCES = ("data/presentation.xml", "data/presentationData.js",
            "data/vt_data.js", "data/slides.xml")


def _trouver_un_mp3(contexte, url):
    """Un mp3 du cours, ou None tant qu'on ne voit qu'une page de connexion.

    Moodle renvoie sa page de connexion en 200 : on la reconnait a son
    contenu, jamais au code HTTP.
    """
    base = url.rsplit("/", 1)[0] + "/"

    def lire(lien):
        code, _, corps = _sonder(contexte, lien, plage=False)
        if code is None or code >= 400:
            return None
        return corps

    corps = lire(url)
    if corps is None or _SENT_LE_LOGIN.search(corps):
        return None
    for source in (corps,) + tuple(_SOURCES):
        texte = source if isinstance(source, bytes) else lire(base + source)
        if not texte:
            continue
        m = _MP3_DANS_TEXTE.search(texte.decode("utf-8", "replace"))
        if m:
            return base + "data/" + m.group(0).rsplit("/", 1)[-1]
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

    @property
    def en_cours(self):
        return self.processus is not None and self.processus.poll() is None

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
            else:
                self.etat = "echec"
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
