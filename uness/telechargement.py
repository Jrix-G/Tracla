# -*- coding: utf-8 -*-
"""Recuperation des fichiers du cours : un client HTTP prudent et un cache.

Deux idees a retenir :

1. **Moodle ment sur le code HTTP.** Quand la session manque, il renvoie une
   page de connexion en 200, pas un 401. On ne considere donc une reponse
   comme valide que si son Content-Type est bien de l'audio.

2. **On ne retelecharge jamais ce qui est deja la.** Le cache est un dossier
   par cours ; un fichier deja present et decodable est accepte tel quel,
   ce qui rend la reprise apres reconnexion quasi instantanee.
"""

import json
import os
import random
import re
import time

import requests

# En-tetes d'un navigateur ordinaire : on ne se deguise pas, on evite
# seulement d'etre pris pour un robot par le pare-feu applicatif.
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
    "Accept": "*/*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

DELAI = 0.25          # pause entre deux requetes : on reste un lecteur poli
ESSAIS = 3
TIMEOUT = (10, 60)    # (connexion, lecture)
TAILLE_MINI = 200     # un mp3 plus petit que ca est un fichier d'erreur


class SessionExpiree(Exception):
    """La reponse recue est une page de connexion, pas le fichier demande."""


class ErreurReseau(Exception):
    """Echec reseau apres tous les essais."""


def _est_audio(reponse):
    t = (reponse.headers.get("Content-Type") or "").lower()
    return t.startswith("audio/") or t in ("application/octet-stream",)


def _sent_le_login(reponse, debut=b""):
    """Page de connexion deguisee en 200 : HTML, ou redirection vers /login/."""
    t = (reponse.headers.get("Content-Type") or "").lower()
    if "html" in t:
        return True
    if any("/login/" in r.headers.get("Location", "") for r in reponse.history):
        return True
    if "/login/" in reponse.url:
        return True
    return debut[:15].lstrip().lower().startswith((b"<!doctype", b"<html"))


class Client:
    """Un requests.Session avec les cookies de l'utilisateur, et des garde-fous."""

    def __init__(self, cookies=None, delai=DELAI):
        self.s = requests.Session()
        self.s.headers.update(ENTETES)
        self.delai = delai
        self.appliquer_cookies(cookies or [])
        self._dernier = 0.0
        # Adresse reellement atteinte par le dernier texte() : Moodle redirige
        # beaucoup, et c'est elle qui sert de base aux liens relatifs.
        self.derniere_url = None

    def appliquer_cookies(self, cookies):
        """cookies : liste de dicts {name, value, domain, path} (format Playwright).
        On ne journalise jamais leur contenu."""
        self.s.cookies.clear()
        for c in cookies:
            try:
                self.s.cookies.set(c["name"], c["value"],
                                   domain=c.get("domain") or "formation.uness.fr",
                                   path=c.get("path") or "/")
            except Exception:
                continue

    def _attendre(self):
        reste = self.delai - (time.time() - self._dernier)
        if reste > 0:
            time.sleep(reste)
        self._dernier = time.time()

    # -- lectures simples ---------------------------------------------------

    def texte(self, url):
        """Contenu texte d'une ressource, ou None si elle n'existe pas.

        Sert a lire index.htm et les fichiers de donnees du lecteur. Une page
        de connexion renvoyee a la place leve SessionExpiree.
        """
        try:
            r = self._get(url, flux=False)
        except ErreurReseau:
            return None
        self.derniere_url = r.url
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            return None
        contenu = r.content
        if _sent_le_login(r, contenu) and _page_de_connexion(contenu):
            raise SessionExpiree(url)
        r.encoding = r.encoding or "utf-8"
        try:
            return contenu.decode(r.encoding, "replace")
        except Exception:
            return contenu.decode("utf-8", "replace")

    def existe(self, url):
        """Le fichier audio est-il la ? (sondage, sans le telecharger)"""
        try:
            r = self._get(url, flux=True, entetes={"Range": "bytes=0-1023"})
        except ErreurReseau:
            return False
        try:
            if r.status_code in (200, 206) and _est_audio(r):
                return True
            if r.status_code in (200, 206):
                debut = next(r.iter_content(512), b"")
                if _sent_le_login(r, debut) and _page_de_connexion(debut):
                    raise SessionExpiree(url)
            return False
        finally:
            r.close()

    def session_valide(self, url_audio):
        """Seule preuve acceptee : ce fichier audio-la repond bien de l'audio."""
        try:
            return self.existe(url_audio)
        except SessionExpiree:
            return False

    # -- telechargement -----------------------------------------------------

    def telecharger(self, url, cible):
        """Ecrit le fichier sur disque en streaming. Renvoie la taille.

        Leve SessionExpiree si on recoit une page de connexion, ErreurReseau
        si le reseau lache, FileNotFoundError si le fichier n'existe pas.
        """
        r = self._get(url, flux=True)
        try:
            if r.status_code == 404:
                raise FileNotFoundError(url)
            if r.status_code >= 400:
                raise ErreurReseau("Le serveur a repondu %d." % r.status_code)
            premier = next(r.iter_content(64 * 1024), b"")
            if not _est_audio(r) or _sent_le_login(r, premier):
                if _page_de_connexion(premier):
                    raise SessionExpiree(url)
                raise ErreurReseau(
                    "Le serveur n'a pas renvoye de l'audio (%s)."
                    % (r.headers.get("Content-Type") or "type inconnu"))
            partiel = cible + ".part"
            taille = 0
            with open(partiel, "wb") as f:
                if premier:
                    f.write(premier)
                    taille += len(premier)
                for bloc in r.iter_content(256 * 1024):
                    if bloc:
                        f.write(bloc)
                        taille += len(bloc)
        finally:
            r.close()
        if taille < TAILLE_MINI:
            os.remove(partiel)
            raise ErreurReseau("Fichier audio vide ou tronque.")
        os.replace(partiel, cible)
        return taille

    # -- socle --------------------------------------------------------------

    def _get(self, url, flux, entetes=None):
        derniere = None
        for essai in range(ESSAIS):
            self._attendre()
            try:
                r = self.s.get(url, stream=flux, timeout=TIMEOUT,
                               headers=entetes or {}, allow_redirects=True)
            except requests.RequestException as e:
                derniere = type(e).__name__
            else:
                # 5xx et 429 sont des pannes passageres : on retente.
                # 404 et 403 sont des reponses definitives : on les rend.
                if r.status_code < 500 and r.status_code != 429:
                    return r
                derniere = "HTTP %d" % r.status_code
                r.close()
                if essai == ESSAIS - 1:
                    return r
            # Backoff avec un peu de hasard : deux diapos qui echouent en
            # meme temps ne repartent pas ensemble.
            time.sleep((2 ** essai) * 0.7 + random.random() * 0.3)
        raise ErreurReseau("Pas de reponse de %s (%s)."
                           % (url.split("/")[2], derniere))


# Une page de connexion, pas une page qui PARLE de connexion.
#
# La nuance a l'air subtile et ne l'est pas du tout : une page Moodle ou on
# est parfaitement connecte contient un en-tete, un pied de page et des liens
# vers /login/... Chercher "/login/index.php" ou "Se connecter" dans le
# contenu faisait donc passer la page de cours de l'utilisateur pour une page
# de connexion, et l'application attendait indefiniment une session qu'elle
# avait deja. On exige maintenant une vraie marque de formulaire d'identi-
# fication : un champ de mot de passe, un formulaire qui POSTE vers une page
# de connexion, ou une redirection d'authentification federee.
_LOGIN_RE = re.compile(
    rb"""<input[^>]{0,200}type\s*=\s*["']?password"""
    rb"""|<form[^>]{0,300}action\s*=\s*["'][^"']{0,200}/login/index\.php"""
    rb"""|SAMLRequest|shibboleth\.sso"""
    rb"""|class\s*=\s*["'][^"']{0,80}loginform"""
    rb"""|<title>[^<]{0,80}(?:Connexion|Se connecter|Login)""",
    re.IGNORECASE)


def _page_de_connexion(debut):
    """Vrai seulement si la page EST un formulaire d'identification."""
    return bool(_LOGIN_RE.search(debut or b""))


# ---------------------------------------------------------------------------
# Cache par cours
# ---------------------------------------------------------------------------

class Cache:
    """Un dossier par cours : les mp3 d'origine, l'audio assemble, le plan."""

    def __init__(self, racine, cle):
        # La cle est le nom du dossier : c'est elle que l'historique renvoie a
        # l'interface pour designer un cours.
        self.cle = cle
        self.racine = racine
        self.dossier = os.path.join(racine, cle)
        self.audio = os.path.join(self.dossier, "diapos")
        os.makedirs(self.audio, exist_ok=True)

    @property
    def assemble(self):
        return os.path.join(self.dossier, "cours.mp3")

    @property
    def chapitres(self):
        return os.path.join(self.dossier, "chapitres.json")

    def fichier(self, nom):
        return os.path.join(self.audio, os.path.basename(nom))

    def valide(self, nom):
        """Deja telecharge et exploitable ? On verifie que le fichier se
        decode vraiment : un .part renomme a la main ou une coupure de
        courant laissent des mp3 de taille correcte mais illisibles."""
        chemin = self.fichier(nom)
        if not os.path.isfile(chemin) or os.path.getsize(chemin) < TAILLE_MINI:
            return False
        return duree_mp3(chemin) is not None

    @property
    def absentes(self):
        return os.path.join(self.dossier, "sans-audio.json")

    def noter_absentes(self, numeros):
        """Memorise les diapos qui n'ont pas d'audio, pour ne pas redemander
        au serveur un fichier dont on sait deja qu'il n'existe pas."""
        try:
            with open(self.absentes, "w", encoding="utf-8") as f:
                json.dump(sorted(set(numeros)), f)
        except Exception:
            pass

    def lire_absentes(self):
        try:
            with open(self.absentes, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()

    def ecrire_chapitres(self, donnees):
        tmp = self.chapitres + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(donnees, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.chapitres)

    def lire_chapitres(self):
        try:
            with open(self.chapitres, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def vider(self):
        import shutil
        shutil.rmtree(self.dossier, ignore_errors=True)
        os.makedirs(self.audio, exist_ok=True)


def duree_mp3(chemin):
    """Duree en secondes, ou None si le fichier ne se decode pas."""
    import av
    try:
        with av.open(chemin) as c:
            flux = next((s for s in c.streams if s.type == "audio"), None)
            if flux is None:
                return None
            if c.duration:
                return c.duration / av.time_base
            if flux.duration and flux.time_base:
                return float(flux.duration * flux.time_base)
            # Pas de duree annoncee : on decode pour de vrai.
            n = sum(t.samples for t in c.decode(flux))
            return n / float(flux.rate) if flux.rate else None
    except Exception:
        return None
