# -*- coding: utf-8 -*-
"""Faux Moodle + faux lecteur Adobe Presenter, pour tester sans compte UNESS.

Imite ce qui compte vraiment :
  - index.htm et data/presentation.xml avec des titres COMPLETS (le vrai
    lecteur tronque a l'affichage, pas dans les donnees) ;
  - des mp3 numerotes 'a24x1.mp3'... avec un prefixe qui n'est pas en dur ;
  - des reponses 206 avec en-tete Range et Accept-Ranges ;
  - et surtout : sans le bon cookie, une PAGE DE CONNEXION renvoyee en 200,
    jamais un 401, exactement comme Moodle.

Avec sso=True, la page de connexion part sur un fournisseur d'identite servi
sur un SECOND PORT, et l'onglet y reste. Sans ca, tout tenait sur une seule
origine et le test ne pouvait pas voir qu'un fetch() lance depuis la page se
fait bloquer par CORS pendant une connexion federee.

  python tests/faux_uness.py [--port 0] [--diapos 12] [--sans-audio 3,7] [--sso]
"""

import argparse
import json
import math
import os
import re
import struct
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COOKIE = "MoodleSession"
VALEUR = "faux-jeton-de-session"
PREFIXE = "a24x"

TITRE_COURS = "Sémiologie des troubles respiratoires"

# Des titres longs et accentues : c'est le cas qui casse les parseurs naifs.
TITRES = [
    "Introduction et objectifs pédagogiques",
    "Rappels d'anatomie thoracique",
    "Poumons et bronches",
    "Segmentation pulmonaire et scissures",
    "L'interrogatoire du patient dyspnéique",
    "Dyspnée : définition et quantification",
    "Échelle mMRC et NYHA",
    "La toux : sèche ou productive",
    "Expectorations et hémoptysie",
    "Inspection du thorax",
    "Palpation : vibrations vocales",
    "Percussion : matité et tympanisme",
    "Auscultation : le murmure vésiculaire",
    "Les râles crepitants",
    "Sibilants et ronchi",
    "Le frottement pleural",
    "Syndrome d'épanchement pleural liquidien",
    "Pneumothorax : sémiologie",
    "Fibroscopie bronchique : indications",
    "Synthèse et points clés",
]


# ---------------------------------------------------------------------------
# Fabrication des mp3 de test
# ---------------------------------------------------------------------------

def _wav_parle(chemin, secondes, hauteur):
    """Un signal module en amplitude : ce n'est pas de la parole, mais ca a une
    duree exacte et connue, qui est ce qu'on verifie ici."""
    sr = 22050
    n = int(sr * secondes)
    with wave.open(chemin, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        trames = bytearray()
        for i in range(n):
            t = i / sr
            enveloppe = 0.5 * (1 + math.sin(2 * math.pi * 2.3 * t))
            v = int(12000 * enveloppe * math.sin(2 * math.pi * hauteur * t))
            trames += struct.pack("<h", max(-32768, min(32767, v)))
        w.writeframes(bytes(trames))


def _phrase(n, titre):
    """De quoi faire dire quelque chose de reconnaissable a la diapo n."""
    return ("Diapositive numero %d. %s. "
            "Nous allons maintenant detailler ce point du cours, "
            "avec les elements cliniques a retenir pour l'examen." % (n, titre))


def fabriquer_mp3(dossier, numeros, durees, titres=None, parole=False):
    """Encode un mp3 par diapo avec PyAV, comme le fait le vrai lecteur.

    'parole=True' fabrique de la vraie parole (synthese vocale Windows) : c'est
    indispensable des qu'un test fait tourner Whisper, sinon le filtre
    anti-hallucination jette a juste titre tout ce qui sort d'un signal pur.
    Les tests qui ne verifient que des durees s'en passent : c'est bien plus
    rapide.
    """
    import av
    os.makedirs(dossier, exist_ok=True)
    faits = {}
    titres = titres or {}
    for n, duree in zip(numeros, durees):
        nom = "%s%d.mp3" % (PREFIXE, n)
        cible = os.path.join(dossier, nom)
        if os.path.isfile(cible) and os.path.getsize(cible) > 500:
            faits[n] = (nom, duree)
            continue
        wav = cible + ".wav"
        dit = False
        if parole:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from outils_test import parole_espeak, parole_sapi
            texte = _phrase(n, titres.get(n, "Point du cours"))
            dit = parole_sapi(wav, texte) or parole_espeak(wav, texte)
        if not dit:
            _wav_parle(wav, duree, 180 + 25 * (n % 7))
        with av.open(wav) as entree, av.open(cible, "w", format="mp3") as sortie:
            fin = next(s for s in entree.streams if s.type == "audio")
            fout = sortie.add_stream("libmp3lame", rate=22050)
            fout.bit_rate = 64000
            r = av.AudioResampler(format=fout.codec_context.format,
                                  layout=fout.codec_context.layout, rate=22050)
            for trame in entree.decode(fin):
                for out in r.resample(trame):
                    for p in fout.encode(out):
                        sortie.mux(p)
            for p in fout.encode(None):
                sortie.mux(p)
        os.remove(wav)
        faits[n] = (nom, duree)
    return faits


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

PAGE_LOGIN = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>UNESS : Se connecter au site</title></head>
<body><div class="loginform">
<h1>Vous n'&ecirc;tes pas connect&eacute;</h1>
<form action="/login/index.php" method="post">
<label>Identifiant <input name="username"></label>
<label>Mot de passe <input name="password" type="password"></label>
<button>Se connecter</button></form>
<a href="/login/index.php?saml=on">Connexion via la f&eacute;d&eacute;ration d'identit&eacute;</a>
</div></body></html>
"""

# Variante SSO : Moodle renvoie sa page de connexion, qui part aussitot sur le
# fournisseur d'identite -- sur une AUTRE ORIGINE. C'est le cas qui compte :
# un fetch() execute dans cet onglet vers formation.uness.fr devient une
# requete cross-origin, que le navigateur bloque faute d'en-tetes CORS.
PAGE_LOGIN_SSO = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>UNESS : redirection</title>
<meta http-equiv="refresh" content="0;url=%s">
</head><body><div class="loginform">
<p>Redirection vers la f&eacute;d&eacute;ration d'identit&eacute;&hellip;</p>
<input name="password" type="password" hidden>
</div></body></html>
"""

PAGE_IDP = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>F&eacute;d&eacute;ration d'identit&eacute;</title></head>
<body><h1>Authentification</h1>
<p>Double authentification en cours. Cette page reste ouverte&nbsp;: c'est
exactement ce qui pi&egrave;ge un fetch() lanc&eacute; depuis l'onglet.</p>
</body></html>
"""


class _Idp(BaseHTTPRequestHandler):
    """Un fournisseur d'identite minimal, sur un autre port donc une autre
    origine. Il ne fait rien d'autre que rester affiche."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        corps = PAGE_IDP.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)


def page_index(diapos):
    """index.htm : un plan tronque a l'affichage (comme le vrai lecteur) et
    un script de donnees. Les titres COMPLETS ne sont que dans le XML."""
    lignes = []
    for n, titre, duree in diapos:
        court = titre if len(titre) <= 22 else titre[:22] + "..."
        lignes.append(
            '<li class="outline-item"><span class="num">%d</span>'
            '<span class="outline-title">%s</span>'
            '<span class="dur">%d:%02d</span></li>'
            % (n, court, int(duree) // 60, int(duree) % 60))
    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>%s</title>
<script src="data/presentationData.js"></script>
</head>
<body>
<div id="lecteur"><div id="scene"></div>
<ul id="outline">%s</ul></div>
<script>var slideCount = %d;</script>
</body></html>
""" % (TITRE_COURS, "\n".join(lignes), len(diapos))


def presentation_xml(diapos):
    def echap(t):
        return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    corps = []
    for n, titre, duree in diapos:
        nom = "%s%d.mp3" % (PREFIXE, n)
        corps.append(
            '  <Slide id="slide%d" title="%s" navTitle="%s" '
            'duration="%d"><Audio src="%s"/></Slide>'
            % (n, echap(titre), echap(titre), int(duree * 1000), nom))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Presentation><presentationTitle>%s</presentationTitle>\n'
            '<Slides>\n%s\n</Slides></Presentation>\n'
            % (echap(TITRE_COURS), "\n".join(corps)))


def presentation_js(diapos):
    """Le JS que charge index.htm : il ne contient PAS les titres. C'est
    volontaire : le parseur doit aller chercher le XML pour les avoir."""
    return "var presentationData = %s;\n" % json.dumps(
        {"prefix": PREFIXE, "slideCount": len(diapos),
         "dataFile": "presentation.xml"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Serveur
# ---------------------------------------------------------------------------

class Faux(BaseHTTPRequestHandler):
    server_version = "FauxMoodle/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        if self.server.bavard:
            sys.stderr.write("  faux-uness %s\n" % (a[0] % a[1:]))

    # -- session ------------------------------------------------------------

    def connecte(self):
        if self.server.exige_reconnexion:
            return False
        brut = self.headers.get("Cookie") or ""
        return ("%s=%s" % (COOKIE, self.server.valeur)) in brut

    def repondre_login(self):
        """Comme Moodle : 200, du HTML, pas de 401."""
        corps = ((PAGE_LOGIN_SSO % self.server.idp_url) if self.server.idp_url
                 else PAGE_LOGIN).encode("utf-8")
        self.send_response(200)
        # 'auto_connexion' simule l'utilisateur qui vient de s'identifier dans
        # la fenetre : le serveur pose le cookie, et la requete SUIVANTE passe.
        # Sans ca, tester la fenetre de connexion demanderait un humain.
        if self.server.auto_connexion:
            self.send_header("Set-Cookie", "%s=%s; Path=/"
                             % (COOKIE, self.server.valeur))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    # -- routage ------------------------------------------------------------

    def do_GET(self):
        chemin = self.path.split("?", 1)[0]
        racine = self.server.racine_url

        if chemin == "/login/index.php":
            # La "connexion" du test : on pose le cookie et on redirige.
            self.send_response(303)
            self.send_header("Set-Cookie",
                             "%s=%s; Path=/; HttpOnly" % (COOKIE, self.server.valeur))
            self.send_header("Location", racine + "index.htm")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.server.exige_reconnexion = False
            return

        if not chemin.startswith(racine):
            self.envoyer(404, b"", "text/plain")
            return
        reste = chemin[len(racine):] or "index.htm"

        if not self.connecte():
            self.repondre_login()
            return

        self.server.requetes.append(reste)

        if reste in ("index.htm", "index.html"):
            self.envoyer(200, page_index(self.server.diapos).encode("utf-8"),
                         "text/html; charset=utf-8")
        elif reste == "data/presentation.xml":
            self.envoyer(200, presentation_xml(self.server.diapos).encode("utf-8"),
                         "text/xml; charset=utf-8")
        elif reste == "data/presentationData.js":
            self.envoyer(200, presentation_js(self.server.diapos).encode("utf-8"),
                         "application/javascript; charset=utf-8")
        elif reste.startswith("data/") and reste.endswith(".mp3"):
            self.envoyer_mp3(os.path.basename(reste))
        else:
            self.envoyer(404, b"Not found", "text/plain")

    def do_HEAD(self):
        self.do_GET()

    # -- envoi --------------------------------------------------------------

    def envoyer(self, code, corps, type_):
        self.send_response(code)
        self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corps)

    def envoyer_mp3(self, nom):
        chemin = os.path.join(self.server.dossier_mp3, nom)
        if not os.path.isfile(chemin):
            self.envoyer(404, b"Not found", "text/plain")
            return
        if self.server.echecs.get(nom, 0) > 0:
            # Panne simulee : on decremente, l'essai suivant passera.
            self.server.echecs[nom] -= 1
            self.envoyer(503, b"Service Unavailable", "text/plain")
            return

        donnees = open(chemin, "rb").read()
        taille = len(donnees)
        plage = self.headers.get("Range", "")
        entetes = [("Accept-Ranges", "bytes"),
                   ("Content-Type", "audio/mp3"),
                   ("Content-Disposition", 'inline; filename="%s"' % nom)]
        m = re.match(r"bytes=(\d*)-(\d*)", plage)
        if m:
            d = int(m.group(1) or 0)
            f = int(m.group(2)) if m.group(2) else taille - 1
            f = min(f, taille - 1)
            bout = donnees[d:f + 1]
            self.send_response(206)
            for k, v in entetes:
                self.send_header(k, v)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (d, f, taille))
            self.send_header("Content-Length", str(len(bout)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(bout)
            return
        self.send_response(200)
        for k, v in entetes:
            self.send_header(k, v)
        self.send_header("Content-Length", str(taille))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(donnees)


# ---------------------------------------------------------------------------
# Demarrage
# ---------------------------------------------------------------------------

class _Serveur(ThreadingHTTPServer):
    """Le client ferme la connexion des qu'il a ses 1024 premiers octets
    (sondage d'existence) : c'est normal, et la trace de pile qui va avec
    noierait la sortie des tests."""

    def handle_error(self, requete, adresse):
        if not isinstance(sys.exc_info()[1], (ConnectionResetError,
                                              ConnectionAbortedError,
                                              BrokenPipeError)):
            ThreadingHTTPServer.handle_error(self, requete, adresse)


def demarrer(nb_diapos=12, sans_audio=(), port=0, dossier=None, bavard=False,
             parole=False, sso=False,
             racine_url="/formation/pluginfile.php/116687/mod_resource/content/0/"):
    """Lance le faux serveur dans un thread. Renvoie (serveur, base_url)."""
    # Un dossier par serveur : sinon les mp3 d'un test precedent trainent et
    # une diapo censee etre sans audio en retrouve un.
    dossier = dossier or tempfile.mkdtemp(prefix="faux-uness-mp3-")
    diapos = []
    for i in range(1, nb_diapos + 1):
        titre = TITRES[(i - 1) % len(TITRES)]
        if nb_diapos > len(TITRES) and i > len(TITRES):
            titre = "%s (suite %d)" % (titre, i // len(TITRES))
        # Durees variees, comme un vrai cours : de 5 a 33 secondes.
        diapos.append((i, titre, 5.0 + (i * 7) % 28))

    presents = [d for d in diapos if d[0] not in set(sans_audio)]
    fabriquer_mp3(dossier, [d[0] for d in presents], [d[2] for d in presents],
                  titres={d[0]: d[1] for d in presents}, parole=parole)
    if parole:
        # La duree affichee dans le plan doit rester celle du fichier reel :
        # la synthese vocale ne fait pas exactement la duree demandee.
        from uness.telechargement import duree_mp3
        diapos = [(n, t, duree_mp3(os.path.join(dossier, "%s%d.mp3"
                                                % (PREFIXE, n))) or d)
                  if n not in set(sans_audio) else (n, t, d)
                  for n, t, d in diapos]

    srv = _Serveur(("127.0.0.1", port), Faux)
    srv.daemon_threads = True
    srv.diapos = diapos
    srv.dossier_mp3 = dossier
    srv.racine_url = racine_url
    srv.valeur = VALEUR
    srv.exige_reconnexion = False
    srv.auto_connexion = False
    srv.idp_url = None
    srv.idp = None
    if sso:
        # Le fournisseur d'identite tourne sur un AUTRE port : autre origine.
        srv.idp = _Serveur(("127.0.0.1", 0), _Idp)
        srv.idp.daemon_threads = True
        threading.Thread(target=srv.idp.serve_forever, daemon=True).start()
        srv.idp_url = "http://127.0.0.1:%d/idp" % srv.idp.server_address[1]
    srv.echecs = {}
    srv.requetes = []
    srv.bavard = bavard
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d%s" % (srv.server_address[1], racine_url)
    return srv, base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--diapos", type=int, default=12)
    ap.add_argument("--sans-audio", default="")
    ap.add_argument("--sso", action="store_true",
                    help="page de connexion sur une autre origine")
    args = ap.parse_args()
    manquantes = tuple(int(x) for x in args.sans_audio.split(",") if x.strip())
    srv, base = demarrer(args.diapos, manquantes, args.port, bavard=True,
                         sso=args.sso)
    print("Faux UNESS : %sindex.htm" % base)
    print("Cookie attendu : %s=%s" % (COOKIE, VALEUR))
    print("Se 'connecter' : http://127.0.0.1:%d/login/index.php"
          % srv.server_address[1])
    if srv.idp_url:
        print("Fournisseur d'identite (autre origine) : %s" % srv.idp_url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
