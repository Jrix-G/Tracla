# -*- coding: utf-8 -*-
"""Enchainement complet : lien colle -> un mp3 assemble + les chapitres.

Ce module ne connait ni Flask ni Whisper. Il expose une classe Recuperation
qui avance par etapes et previent l'appelant a chaque diapo, ce qui permet a
app.py de diffuser la progression sur le flux SSE existant.
"""

import os
import threading
from urllib.parse import urljoin

from . import assemblage, cours, telechargement
from .telechargement import Client, ErreurReseau, SessionExpiree


class Interrompu(Exception):
    pass


class Recuperation:
    """Un cours en cours de recuperation. Un seul a la fois, comme le job."""

    def __init__(self, url, coffre, racine_cache, evenement, stop=None):
        self.url_index, self.base = cours.verifier_url(url)
        self.coffre = coffre
        self.cache = telechargement.Cache(racine_cache, cours.identifiant(self.base))
        self.evenement = evenement          # evenement(type, data)
        self.stop = stop or threading.Event()
        self.client = Client(coffre.lire())
        self.titre = ""
        self.diapos = []
        self.chapitres = []
        self.sans_audio = []
        # Mis a True quand la session a laches en cours de route : le serveur
        # demande alors une reconnexion, puis rappelle reprendre().
        self.attend_connexion = False

    # -- utilitaires --------------------------------------------------------

    def _dire(self, message):
        self.evenement("uness", {"etape": "info", "message": message})

    def _verifier_arret(self):
        if self.stop.is_set():
            raise Interrompu()

    def url_de_test(self):
        """Une URL de fichier audio du cours : la seule facon honnete de
        verifier que la session marche.

        A la toute premiere connexion on n'en connait aucune -- il faut etre
        identifie pour lire la page qui les liste. On renvoie alors la page du
        cours, et la fenetre de connexion se debrouille pour en extraire un
        mp3 une fois l'utilisateur identifie.
        """
        for d in self.diapos:
            if d.get("fichier"):
                return urljoin(self.base, "data/" + d["fichier"])
        plan = self.cache.lire_chapitres() or {}
        for d in plan.get("diapos", []):
            if d.get("fichier"):
                return urljoin(self.base, "data/" + d["fichier"])
        return self.url_index

    def rafraichir_cookies(self):
        self.client.appliquer_cookies(self.coffre.lire())
        self.attend_connexion = False

    def session_valide(self):
        cible = self.url_de_test()
        if cible == self.url_index:
            # Pas encore de mp3 connu : on se contente de verifier que la page
            # du cours n'est pas une page de connexion.
            try:
                texte = self.client.texte(cible)
            except SessionExpiree:
                return False
            return bool(texte)
        return self.client.session_valide(cible)

    # -- etapes -------------------------------------------------------------

    def decouvrir(self):
        self._verifier_arret()
        self.evenement("uness", {"etape": "analyse",
                                 "message": "Lecture de la page du cours..."})
        titre, diapos, methode = cours.decouvrir(
            self.client, self.url_index, self.base, self._dire)
        self.titre = titre or "Cours UNESS"
        self.diapos = diapos
        self._oublier_les_absentes()
        avec = sum(1 for d in diapos if d.get("fichier"))
        self.evenement("uness", {
            "etape": "plan", "titre": self.titre, "diapos": len(diapos),
            "avec_audio": avec, "methode": methode,
            "message": "%d diapos trouvees, dont %d avec audio." % (len(diapos), avec)})
        return self.diapos

    def _oublier_les_absentes(self):
        """Une diapo dont on sait deja qu'elle n'a pas d'audio ne vaut pas une
        requete de plus a chaque relance : le plan enregistre la fois
        precedente s'en souvient. Vider le cache remet tout a zero."""
        connues = self.cache.lire_absentes()
        if not connues:
            return
        for d in self.diapos:
            if d["n"] in connues and not self.cache.valide(d.get("fichier") or ""):
                d["fichier"] = None

    def telecharger(self):
        """Telecharge ce qui manque. Renvoie True si tout est la, False si la
        session a expire en route (l'appelant doit reconnecter puis rappeler)."""
        total = len(self.diapos)
        self.sans_audio = []
        for i, d in enumerate(self.diapos):
            self._verifier_arret()
            numero = d["n"]
            if not d.get("fichier"):
                self.sans_audio.append(numero)
                self._progres(i + 1, total, numero)
                continue
            if self.cache.valide(d["fichier"]):
                self._progres(i + 1, total, numero, cache=True)
                continue
            url = urljoin(self.base, "data/" + d["fichier"])
            try:
                self.client.telecharger(url, self.cache.fichier(d["fichier"]))
            except SessionExpiree:
                self.attend_connexion = True
                self.evenement("uness", {
                    "etape": "session",
                    "message": "Session UNESS expiree. Reconnecte-toi : le "
                               "telechargement reprendra a la diapo %d, sans "
                               "retelecharger les precedentes." % numero})
                return False
            except FileNotFoundError:
                # Une diapo sans audio : c'est prevu, on continue.
                d["fichier"] = None
                self.sans_audio.append(numero)
            except ErreurReseau as e:
                d["fichier"] = None
                self.sans_audio.append(numero)
                self._dire("Diapo %d ignoree : %s" % (numero, e))
            self._progres(i + 1, total, numero)
        self.cache.noter_absentes(self.sans_audio)
        return True

    def _progres(self, fait, total, numero, cache=False):
        self.evenement("uness", {
            "etape": "audio", "fait": fait, "total": total, "diapo": numero,
            "cache": cache,
            "message": "Recuperation de l'audio : diapo %d/%d" % (fait, total)},
            durable=False)

    def assembler(self):
        self._verifier_arret()
        self.evenement("uness", {"etape": "assemblage",
                                 "message": "Assemblage de l'audio du cours..."})
        self.chapitres = assemblage.assembler(
            self.diapos, self.cache, self.cache.assemble,
            progression=lambda f, t: self.evenement(
                "uness", {"etape": "assemblage", "fait": f, "total": t,
                          "message": "Assemblage : diapo %d/%d" % (f, t)},
                durable=False),
            stop=self.stop)

        ok, reelle, attendue = assemblage.verifier(self.cache.assemble,
                                                   self.chapitres)
        self.cache.ecrire_chapitres({
            "titre": self.titre,
            "url": self.url_index,
            "duree": round(reelle, 3),
            "duree_attendue": round(attendue, 3),
            "coherent": ok,
            "sans_audio": self.sans_audio,
            "diapos": self.chapitres,
        })
        if not ok:
            self._dire("Attention : duree assemblee %.1f s pour %.1f s "
                       "attendues." % (reelle, attendue))
        self.evenement("uness", {
            "etape": "pret", "duree": round(reelle, 3),
            "titre": self.titre, "sans_audio": self.sans_audio,
            "chapitres": self.chapitres,
            "message": "Audio du cours pret (%d diapos)." % len(self.chapitres)})
        return self.cache.assemble, self.chapitres, reelle

    # -- enchainement -------------------------------------------------------

    def executer(self):
        """Renvoie (chemin_mp3, chapitres, duree) ou leve.
        Si la session expire, renvoie None : l'appelant reconnecte puis
        rappelle executer(), qui reprend ou il s'etait arrete."""
        if not self.diapos:
            self.decouvrir()
        if not self.telecharger():
            return None
        return self.assembler()

    def resume(self):
        """Ce qu'on affiche a la fin : les diapos sans audio, clairement."""
        if not self.sans_audio:
            return ""
        nums = sorted(set(self.sans_audio))
        if len(nums) == 1:
            return "Diapo %d sans audio." % nums[0]
        return "Diapos sans audio : %s." % ", ".join(str(n) for n in nums)
