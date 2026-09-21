# -*- coding: utf-8 -*-
"""
Transcripteur - transcription locale de cours audio, en direct.

Serveur local (Flask + waitress) + interface web statique.
Tout se passe sur la machine : aucun envoi reseau en dehors du
telechargement initial du modele depuis Hugging Face.
"""

import os
import sys

# --- Garde-fous a executer AVANT tout autre import lourd -------------------

def _fix_std_streams():
    """Sous PyInstaller sans console, sys.stdout/stderr valent None, ce qui
    fait planter tqdm et huggingface_hub. On les remplace par devnull."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def app_dir():
    """Dossier a cote de l'exe (ou du script) : c'est la qu'on range 'modeles'."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(rel):
    """Chemin d'une ressource embarquee (ui/) : gere sys._MEIPASS."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


_fix_std_streams()

# Cache des modeles : chemin ABSOLU a cote de l'exe, jamais relatif au cwd.
MODELS_DIR = os.path.join(app_dir(), "modeles")
os.makedirs(MODELS_DIR, exist_ok=True)
os.environ["HF_HOME"] = MODELS_DIR
os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(MODELS_DIR, "hub")
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
# Xet est un transport natif supplementaire : inutile ici et une
# dependance de plus a embarquer dans l'exe.
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ.setdefault("OMP_NUM_THREADS", str(max(1, (os.cpu_count() or 4) - 1)))

import json
import mimetypes
import re
import shutil
import socket
import tempfile
import threading
import time
import subprocess
import unicodedata
import urllib.error
import urllib.request
import webbrowser
from queue import Queue, Empty
from urllib.parse import unquote

import av
import numpy as np
from flask import Flask, Response, jsonify, request, send_file, stream_with_context

from uness import assemblage as uness_assemblage
from uness import cours as uness_cours
from uness import historique as uness_historique
from uness import recuperation as uness_recuperation
from uness import session as uness_session

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
# Fenetre de decodage : on ne charge jamais plus de ~10 min d'audio en RAM
# (10 min * 16000 Hz * 4 octets = ~38 Mo), meme pour un cours de 4 heures.
WINDOW_SECONDS = 600.0

MODELS = {
    "base":           {"taille": "145 Mo",  "label": "Rapide"},
    "small":          {"taille": "480 Mo",  "label": "\u00c9quilibr\u00e9"},
    "large-v3-turbo": {"taille": "1,6 Go",  "label": "Pr\u00e9cis"},
    "medium":         {"taille": "1,5 Go",  "label": "Tr\u00e8s pr\u00e9cis mais lent"},
}

# Depot Hugging Face de chaque modele. Recopie ici volontairement : savoir si
# un modele est deja telecharge ne doit pas couter l'import de faster_whisper
# (donc de CTranslate2) sur une simple requete de configuration.
DEPOTS_MODELES = {
    "base":           "Systran/faster-whisper-base",
    "small":          "Systran/faster-whisper-small",
    "medium":         "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}

EXTS_OK = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".wma",
           ".mp4", ".mkv", ".webm"}

# Mise a jour : depot public consulte pour connaitre la derniere version.
DEPOT = "Jrix-G/Tracla"
NOM_ZIP = "Transcripteur-windows.zip"
API_RELEASES = "https://api.github.com/repos/%s/releases/latest" % DEPOT

# Conteneurs / codecs que les navigateurs ne lisent pas : on transcode.
CODECS_NAVIGATEUR = {"mp3", "aac", "opus", "vorbis", "flac",
                     "pcm_s16le", "pcm_s24le", "pcm_f32le"}
EXTS_NAVIGATEUR = {".mp3", ".m4a", ".mp4", ".wav", ".ogg", ".oga",
                   ".webm", ".flac", ".aac"}

# Hallucinations classiques de Whisper dans les silences.
# ATTENTION : ces motifs sont compares au texte NORMALISE par normaliser(),
# qui remplace toute ponctuation par une espace. Ecrire "sous-titres" ou
# "amara.org" ici ne matcherait donc jamais : c'est "sous titres" et
# "amara org" qui arrivent.
JUNK = [
    r"sous titr(es|age).{0,40}",
    r".{0,30}amara org.{0,20}",
    r"merci d avoir regard(e|é).{0,30}",
    r"abonnez vous.{0,30}",
    r"(a bient(o|ô)t|au revoir)\s*",
    r"musique", r"générique", r"generique",
    r"thanks for watching.{0,20}",
]
JUNK_RE = re.compile(r"^(?:%s)$" % "|".join(JUNK), re.IGNORECASE)

PROMPT_HESITATIONS = ("Euh, alors, ben, voila, du coup... euh, bon. "
                      "Donc euh, je disais, hein, voila.")

# Import d'un cours UNESS : la session du navigateur et le cache des cours
# vivent a cote de l'exe, dans des dossiers que l'utilisateur peut supprimer.
DOSSIER_SESSION = os.path.join(app_dir(), "session-uness")
DOSSIER_COURS = os.path.join(app_dir(), "cours-uness")

# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def version_app():
    """Version du build. La CI ecrit version.txt au moment de construire l'exe ;
    sans ce fichier on est en developpement et la mise a jour est desactivee."""
    for base in (app_dir(), resource_path("")):
        chemin = os.path.join(base, "version.txt")
        if os.path.isfile(chemin):
            try:
                v = open(chemin, encoding="utf-8").read().strip()
                if re.fullmatch(r"v?\d+\.\d+\.\d+", v or ""):
                    return v if v.startswith("v") else "v" + v
            except Exception:
                pass
    return "dev"


VERSION = version_app()


def numero(v):
    """'v1.2.3' -> (1, 2, 3). None si ce n'est pas une version publiee."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


FICHIER_REGLAGES = os.path.join(app_dir(), "reglages.json")


def lire_reglages():
    try:
        with open(FICHIER_REGLAGES, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ecrire_reglage(cle, valeur):
    r = lire_reglages()
    r[cle] = valeur
    try:
        with open(FICHIER_REGLAGES, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return r


def documents_dir():
    """Dossier Documents de l'utilisateur (Windows, avec repli Linux/macOS)."""
    if os.name == "nt":
        try:
            import ctypes.wintypes as wt
            import ctypes
            buf = ctypes.create_unicode_buffer(wt.MAX_PATH)
            # CSIDL_PERSONAL = 5, SHGFP_TYPE_CURRENT = 0
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)
            if buf.value:
                return buf.value
        except Exception:
            pass
    for cand in ("Documents", "documents"):
        p = os.path.join(os.path.expanduser("~"), cand)
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~")


def nom_de_fichier_sur(nom):
    """Nettoie un nom pour en faire un nom de fichier valide sous Windows,
    en gardant les accents (c'est le nom du cours de l'etudiant)."""
    nom = unicodedata.normalize("NFC", nom)
    nom = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", nom).strip(" .")
    return nom[:120] or "transcription"


def hhmmss(sec):
    sec = max(0, int(sec))
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)


def port_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def normaliser_codec(nom):
    """PyAV expose le nom du DECODEUR, pas du codec : mp3 devient 'mp3float',
    vorbis peut devenir 'vorbisdec'. Sans ca, tous les mp3 seraient transcodes
    inutilement."""
    nom = (nom or "").lower()
    for suffixe in ("float", "fixed", "dec", "_at", "_mf"):
        if nom.endswith(suffixe) and len(nom) > len(suffixe):
            nom = nom[: -len(suffixe)]
    return nom


def infos_media(chemin):
    """Duree (s), codec audio, et si le navigateur sait probablement lire."""
    with av.open(chemin) as c:
        flux = next((s for s in c.streams if s.type == "audio"), None)
        if flux is None:
            raise ValueError("Ce fichier ne contient pas de piste audio.")
        duree = 0.0
        if c.duration:
            duree = c.duration / av.time_base
        elif flux.duration and flux.time_base:
            duree = float(flux.duration * flux.time_base)
        codec = normaliser_codec(flux.codec_context.name)
    ext = os.path.splitext(chemin)[1].lower()
    lisible = ext in EXTS_NAVIGATEUR and codec in CODECS_NAVIGATEUR
    return duree, codec, lisible


def decoder_fenetre(chemin, debut, duree):
    """Decode [debut, debut+duree] en float32 mono 16 kHz. RAM bornee."""
    morceaux = []
    fin = debut + duree
    with av.open(chemin) as c:
        flux = next(s for s in c.streams if s.type == "audio")
        flux.thread_type = "AUTO"
        if debut > 0:
            # Seek sur le FLUX (et pas sur le conteneur) : certains ogg/mkv
            # ignorent le seek conteneur et rejouent le fichier depuis zero,
            # ce qui rend la 3e heure d'un cours tres lente a atteindre.
            cible = max(0.0, debut - 1.0)
            try:
                c.seek(int(cible / flux.time_base), stream=flux, backward=True)
            except Exception:
                c.seek(int(cible / av.time_base), backward=True)
        resampler = av.AudioResampler(format="flt", layout="mono",
                                      rate=SAMPLE_RATE)
        for trame in c.decode(flux):
            t = float(trame.pts * flux.time_base) if trame.pts is not None else None
            if t is not None and t > fin:
                break
            for out in resampler.resample(trame):
                arr = out.to_ndarray().reshape(-1)
                t_out = (float(out.pts * out.time_base)
                         if out.pts is not None else t)
                if t_out is None:
                    morceaux.append(arr)
                    continue
                if t_out + len(arr) / SAMPLE_RATE <= debut:
                    continue
                if t_out < debut:  # coupe le debut de la trame
                    arr = arr[int((debut - t_out) * SAMPLE_RATE):]
                morceaux.append(arr)
    if not morceaux:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(morceaux).astype(np.float32)
    return audio[:int(duree * SAMPLE_RATE) + SAMPLE_RATE]


def encodeur_dispo():
    """Premier encodeur utilisable pour la version lisible par le navigateur."""
    for codec, fmt, ext, mime in (
        ("libmp3lame", "mp3", ".mp3", "audio/mpeg"),
        ("libvorbis", "ogg", ".ogg", "audio/ogg"),
        ("libopus", "ogg", ".ogg", "audio/ogg"),
        ("aac", "adts", ".aac", "audio/aac"),
    ):
        try:
            av.codec.Codec(codec, "w")
            return codec, fmt, ext, mime
        except Exception:
            continue
    return None


def transcoder(source, cible, codec, fmt, stop_event):
    """Transcode en tache de fond, sans bloquer la transcription."""
    with av.open(source) as entree, av.open(cible, "w", format=fmt) as sortie:
        flux_in = next(s for s in entree.streams if s.type == "audio")
        flux_in.thread_type = "AUTO"
        flux_out = sortie.add_stream(codec, rate=44100)
        resampler = av.AudioResampler(
            format=flux_out.codec_context.format,
            layout=flux_out.codec_context.layout,
            rate=44100)
        for trame in entree.decode(flux_in):
            if stop_event.is_set():
                return False
            for out in resampler.resample(trame):
                for paquet in flux_out.encode(out):
                    sortie.mux(paquet)
        for paquet in flux_out.encode(None):
            sortie.mux(paquet)
    return True


# ---------------------------------------------------------------------------
# Filtre anti-hallucination
# ---------------------------------------------------------------------------

def normaliser(txt):
    return re.sub(r"[\s\W_]+", " ", txt.lower(), flags=re.UNICODE).strip()


def couper_boucle(texte):
    """Whisper part parfois en boucle : 'oui oui oui oui...'. On plafonne les
    repetitions a 3 sans toucher au reste de la phrase."""
    mots = texte.split()
    if len(mots) < 12:
        return texte
    for taille in (1, 2, 3, 4, 5):
        i = 0
        sortie = []
        while i < len(mots):
            motif = mots[i:i + taille]
            n = 1
            j = i + taille
            while mots[j:j + taille] == motif:
                n += 1
                j += taille
            if n >= 6:  # boucle averee
                sortie.extend(motif * 3)
                i = j
            else:
                sortie.extend(mots[i:i + taille])
                i += taille
        mots = sortie
    return " ".join(mots)


def est_hallucination(texte, no_speech_prob, amorces=()):
    """Vrai seulement si le texte est un residu connu ET que Whisper doutait
    deja de la presence de parole : on ne supprime jamais de vrai contenu.

    'amorces' contient les titres de diapos passes en initial_prompt. Dans un
    silence, Whisper recrache parfois son prompt mot pour mot ; un titre de
    diapo restitue tel quel, la ou il n'y a pas de parole, est donc du bruit,
    pas du contenu. Le titre reste bien sur legitime s'il est prononce (le
    doute sur la parole est alors faible).
    """
    n = normaliser(texte)
    if not n:
        return True
    if no_speech_prob > 0.5 and JUNK_RE.match(n):
        return True
    return no_speech_prob > 0.6 and n in amorces


def amorces_de(vocabulaire):
    """Les titres du prompt, normalises, pour reperer un prompt recrache."""
    return {normaliser(t) for t in (vocabulaire or "").split(",")
            if len(normaliser(t)) > 3}


# ---------------------------------------------------------------------------
# Mise a jour
# ---------------------------------------------------------------------------

# Ce que l'on sait de la derniere version publiee. Rempli par un thread au
# demarrage, puis a la demande.
MAJ = {"verifie": False, "disponible": None, "erreur": None}


def maj_activee():
    return bool(lire_reglages().get("maj_auto", True))


def chercher_maj():
    """Interroge la page des releases du depot. Ne transmet rien d'autre
    qu'une requete GET anonyme : aucune donnee de l'utilisateur ne part."""
    if numero(VERSION) is None:          # build de developpement
        MAJ.update(verifie=True, disponible=None,
                   erreur="Version de developpement : mise a jour desactivee.")
        return MAJ
    if not maj_activee():
        MAJ.update(verifie=True, disponible=None, erreur=None)
        return MAJ
    try:
        req = urllib.request.Request(API_RELEASES, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Transcripteur/%s" % VERSION})
        with urllib.request.urlopen(req, timeout=12) as r:
            info = json.load(r)
    except urllib.error.HTTPError as e:
        # 404 = le depot n'a encore aucune release : ce n'est pas une erreur.
        MAJ.update(verifie=True, disponible=None,
                   erreur=None if e.code == 404 else
                   "GitHub a repondu %s." % e.code)
        return MAJ
    except Exception:
        MAJ.update(verifie=True, disponible=None,
                   erreur="Verification impossible (pas de connexion).")
        return MAJ

    tag = info.get("tag_name") or ""
    actuel, distant = numero(VERSION), numero(tag)
    zip_url, taille = None, 0
    for a in info.get("assets") or []:
        if a.get("name") == NOM_ZIP:
            zip_url = a.get("browser_download_url")
            taille = a.get("size") or 0
    if distant and actuel and distant > actuel and zip_url:
        MAJ.update(verifie=True, erreur=None, disponible={
            "version": tag, "url": zip_url, "taille": taille,
            "notes": (info.get("body") or "").strip()[:1500]})
    else:
        MAJ.update(verifie=True, disponible=None, erreur=None)
    return MAJ


def installer_maj():
    """Telecharge la nouvelle version, la decompresse, puis passe la main a un
    script qui attend la fermeture de l'application, remplace les fichiers et
    la relance. Le dossier 'modeles' n'est jamais touche : on copie par-dessus,
    on n'efface rien."""
    dispo = MAJ.get("disponible")
    if not dispo:
        raise RuntimeError("Aucune mise a jour disponible.")
    if os.name != "nt":
        raise RuntimeError("La mise a jour automatique n'existe que sous "
                           "Windows. Sous Linux, relance depuis les sources.")

    travail = os.path.join(tempfile.gettempdir(), "transcripteur-maj")
    shutil.rmtree(travail, ignore_errors=True)
    os.makedirs(travail, exist_ok=True)
    archive = os.path.join(travail, NOM_ZIP)

    JOB.emettre("maj", {"etape": "telechargement", "pct": 0}, durable=False)
    req = urllib.request.Request(dispo["url"], headers={
        "User-Agent": "Transcripteur/%s" % VERSION})
    with urllib.request.urlopen(req, timeout=60) as r, open(archive, "wb") as f:
        total = int(r.headers.get("Content-Length") or dispo["taille"] or 0)
        recu, dernier = 0, -1
        while True:
            bloc_ = r.read(256 * 1024)
            if not bloc_:
                break
            f.write(bloc_)
            recu += len(bloc_)
            pct = int(100 * recu / total) if total else 0
            if pct != dernier:
                dernier = pct
                JOB.emettre("maj", {"etape": "telechargement", "pct": pct},
                            durable=False)

    JOB.emettre("maj", {"etape": "extraction", "pct": 100}, durable=False)
    extrait = os.path.join(travail, "extrait")
    import zipfile
    with zipfile.ZipFile(archive) as z:
        z.extractall(extrait)
    os.remove(archive)

    # Le zip contient un dossier Transcripteur/ ; on accepte aussi une archive
    # dont les fichiers sont a la racine.
    source = os.path.join(extrait, "Transcripteur")
    if not os.path.isfile(os.path.join(source, "Transcripteur.exe")):
        source = extrait
    if not os.path.isfile(os.path.join(source, "Transcripteur.exe")):
        raise RuntimeError("L'archive telechargee ne contient pas "
                           "Transcripteur.exe.")

    script = os.path.join(tempfile.gettempdir(), "transcripteur-maj.bat")
    with open(script, "w", encoding="cp1252", errors="replace") as f:
        f.write(SCRIPT_MAJ % {
            "pid": os.getpid(),
            "source": source,
            "cible": app_dir(),
            "travail": travail,
        })

    JOB.emettre("maj", {"etape": "redemarrage", "pct": 100}, durable=False)
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP : le script survit a l'arret
    # de l'application.
    subprocess.Popen(["cmd", "/c", script], close_fds=True,
                     creationflags=0x00000008 | 0x00000200)
    threading.Timer(1.2, _arreter).start()


SCRIPT_MAJ = """@echo off
chcp 65001 >nul
title Mise a jour du Transcripteur
echo Mise a jour en cours, ne ferme pas cette fenetre...

rem Attendre que l'application soit vraiment fermee (sinon les fichiers sont
rem verrouilles par Windows et la copie echoue).
set TENTATIVES=0
:attendre
tasklist /FI "PID eq %(pid)d" 2>nul | find "%(pid)d" >nul
if errorlevel 1 goto copier
set /a TENTATIVES+=1
if %%TENTATIVES%% GTR 60 goto echec
timeout /t 1 /nobreak >nul
goto attendre

:copier
rem /E copie sans effacer : le dossier "modeles" (plusieurs centaines de Mo)
rem et les reglages sont conserves.
robocopy "%(source)s" "%(cible)s" /E /NFL /NDL /NJH /NJS /NP /R:3 /W:1 >nul
if errorlevel 8 goto echec

start "" "%(cible)s\\Transcripteur.exe"
rmdir /s /q "%(travail)s" 2>nul
exit /b 0

:echec
echo.
echo La mise a jour n'a pas pu etre installee.
echo Relance Transcripteur.exe : l'ancienne version fonctionne toujours.
echo Tu peux aussi telecharger la derniere version a la main sur
echo https://github.com/%(depot)s/releases/latest
echo.
pause
exit /b 1
""".replace("%(depot)s", DEPOT)


# ---------------------------------------------------------------------------
# Etat du job (un seul a la fois)
# ---------------------------------------------------------------------------

class Job:
    def __init__(self):
        self.verrou = threading.RLock()
        self.reset()

    def reset(self):
        self.id = 0
        self.chemin = None            # fichier audio d'origine (temporaire)
        self.chemin_lecture = None    # version lisible par le navigateur
        self.mime = "application/octet-stream"
        self.nom = ""
        self.duree = 0.0
        self.etat = "repos"           # repos|pret|modele|transcription|fini|erreur|arrete
        self.message = ""
        self.journal = []             # evenements durables (segments + cycle)
        self.abonnes = []             # queues SSE
        self.stop = threading.Event()
        self.thread = None
        self.sortie = None            # chemin du .txt
        self.debut_horloge = 0.0
        self.transcrit = 0.0
        self.options = {}
        # Cours UNESS : vide pour un simple fichier audio.
        self.chapitres = []           # [{n, titre, debut, fin, ...}]
        self.titre_cours = ""
        self.sans_audio = []
        # Cle de cache du cours charge : c'est elle qui empeche d'oublier un
        # cours pendant qu'on travaille dessus.
        self.cle_cours = None

    # -- diffusion ---------------------------------------------------------
    def emettre(self, type_, data, durable=True):
        with self.verrou:
            evt = dict(data)
            evt["type"] = type_
            if durable:
                evt["i"] = len(self.journal)
                self.journal.append(evt)
            for q in list(self.abonnes):
                q.put(evt)

    def abonner(self, depuis):
        q = Queue()
        with self.verrou:
            for evt in self.journal[max(0, depuis):]:
                q.put(evt)
            self.abonnes.append(q)
        return q

    def desabonner(self, q):
        with self.verrou:
            if q in self.abonnes:
                self.abonnes.remove(q)


JOB = Job()

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def modeles_presents():
    """Quels modeles sont deja sur le disque, donc utilisables hors ligne et
    sans attente.

    On lit l'arborescence du cache Hugging Face a la main : passer par
    huggingface_hub couterait un import lourd, et snapshot_download peut
    tenter le reseau. Cette fonction ne doit ni ralentir /api/config ni lever.
    """
    hub = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.path.join(MODELS_DIR, "hub")
    try:
        dossiers = os.listdir(hub)
    except Exception:
        dossiers = []
    presents = {}
    for nom in MODELS:
        attendu = "models--" + DEPOTS_MODELES.get(nom, nom).replace("/", "--")
        # Le depot d'un modele a deja change de proprietaire une fois : on
        # accepte aussi tout depot dont le nom finit par 'faster-whisper-<nom>'.
        suffixe = "faster-whisper-" + nom
        presents[nom] = any(
            (d == attendu or d.lower().endswith(suffixe))
            and _snapshot_complet(os.path.join(hub, d))
            for d in dossiers)
    return presents


def _snapshot_complet(dossier):
    """Un modele a moitie telecharge a bien son dossier, mais pas son poids."""
    instantanes = os.path.join(dossier, "snapshots")
    try:
        for rev in os.listdir(instantanes):
            if os.path.isfile(os.path.join(instantanes, rev, "model.bin")):
                return True
    except Exception:
        pass
    return False


def chemin_modele(nom, progression):
    """Retourne le dossier local du modele, en le telechargeant au besoin.
    'progression' est appelee avec un pourcentage (0-100)."""
    from huggingface_hub import snapshot_download
    from faster_whisper.utils import _MODELS

    repo = _MODELS.get(nom, nom)
    motifs = ["config.json", "preprocessor_config.json", "model.bin",
              "tokenizer.json", "vocabulary.*"]
    # 1) Deja en cache ? -> hors-ligne, pas un octet de reseau.
    try:
        return snapshot_download(repo, allow_patterns=motifs,
                                 local_files_only=True)
    except Exception:
        pass

    etat = {"total": 0, "fait": 0}

    class Barre:
        """tqdm minimal : huggingface_hub en instancie un par fichier."""
        format_dict = {}          # lu par certaines versions de hub
        disable = False

        def __init__(self, *a, **k):
            self.total = k.get("total") or 0
            self.n = 0
            self.desc = k.get("desc", "")
            etat["total"] += self.total

        def update(self, n=1):
            self.n += n
            etat["fait"] += n
            if etat["total"]:
                progression(min(99, 100.0 * etat["fait"] / etat["total"]))

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

        def __iter__(self):
            return iter(())

        def __getattr__(self, _nom):
            # huggingface_hub appelle plusieurs methodes de tqdm selon sa
            # version (set_description_str, refresh, reset...) : on les
            # absorbe toutes plutot que de casser le telechargement.
            return lambda *a, **k: None

    try:
        chemin = snapshot_download(repo, allow_patterns=motifs,
                                   tqdm_class=Barre, max_workers=4)
    except Exception as e:
        raise RuntimeError(
            "Le modele n'a pas pu etre telecharge. Verifie ta connexion "
            "Internet : le premier lancement a besoin d'Internet une seule "
            "fois, ensuite l'application marche hors-ligne. (%s)" % e)
    progression(100)
    return chemin


def construire_prompt(vocabulaire, hesitations):
    bouts = []
    if hesitations:
        bouts.append(PROMPT_HESITATIONS)
    voc = (vocabulaire or "").strip()
    if voc:
        bouts.append("Vocabulaire du cours : " + voc)
    return " ".join(bouts) or None


def boucle_transcription(job_id, opts):
    from faster_whisper import WhisperModel

    fichier_txt = None
    try:
        JOB.emettre("etat", {"etat": "modele",
                             "message": "Preparation du modele..."})
        dossier = chemin_modele(
            opts["modele"],
            lambda p: JOB.emettre("telechargement", {"pct": round(p, 1)},
                                  durable=False))
        if JOB.stop.is_set():
            return

        fils = max(1, min((os.cpu_count() or 4) - 1, 8))
        modele = WhisperModel(dossier, device="cpu", compute_type="int8",
                              cpu_threads=fils, num_workers=1)

        JOB.emettre("etat", {"etat": "transcription",
                             "message": "Transcription en cours"})
        JOB.debut_horloge = time.time()

        # Fichier de sauvegarde incrementale
        dossier_sortie = os.path.join(documents_dir(), "Transcriptions")
        os.makedirs(dossier_sortie, exist_ok=True)
        base = nom_de_fichier_sur(os.path.splitext(JOB.nom)[0])
        chemin_txt = os.path.join(dossier_sortie, base + ".txt")
        n = 2
        while os.path.exists(chemin_txt):
            chemin_txt = os.path.join(dossier_sortie, "%s (%d).txt" % (base, n))
            n += 1
        JOB.sortie = chemin_txt
        fichier_txt = open(chemin_txt, "w", encoding="utf-8", newline="\n")
        if JOB.chapitres:
            # En-tete demande pour un cours : le titre, puis un soulignement.
            fichier_txt.write("%s\n==============\n\n" % (JOB.titre_cours or JOB.nom))
        else:
            fichier_txt.write("# %s\n\n" % JOB.nom)
        fichier_txt.flush()
        JOB.emettre("fichier", {"chemin": chemin_txt})

        prompt = construire_prompt(opts.get("vocabulaire"),
                                   opts.get("hesitations", True))
        amorces = amorces_de(opts.get("vocabulaire"))
        langue = opts.get("langue") or None

        position = 0.0
        precedent = ""      # dernier texte, pour la continuite entre fenetres
        dernier_txt = None
        repetitions = 0
        # Suivi de la diapo courante : un intertitre n'est ecrit qu'au
        # changement, pas a chaque segment.
        etat_diapo = {"n": None}
        chapitre_par_numero = {c["n"]: c for c in JOB.chapitres}

        while position < JOB.duree - 0.2 and not JOB.stop.is_set():
            audio = decoder_fenetre(JOB.chemin, position, WINDOW_SECONDS)
            if audio.size < SAMPLE_RATE // 2:
                break
            derniere_fenetre = (position + WINDOW_SECONDS) >= JOB.duree

            segments, info = modele.transcribe(
                audio,
                language=langue,
                task="transcribe",
                beam_size=3,
                condition_on_previous_text=True,
                initial_prompt=(prompt + " " + precedent).strip()
                                if prompt else (precedent or None),
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                # Pour un cours decoupe en diapos, il faut les horodatages de
                # mots : c'est le seul moyen de recouper un segment que
                # Whisper a fait chevaucher deux diapos (voir
                # uness.assemblage.decouper_aux_frontieres).
                word_timestamps=bool(JOB.chapitres),
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            )
            if langue is None:
                langue = info.language
                JOB.emettre("langue", {"langue": info.language})

            def publier(debut, fin, texte):
                """Ecrit un segment : a l'ecran ET sur le disque, tout de suite."""
                diapo = None
                if JOB.chapitres:
                    diapo = uness_assemblage.diapo_a(debut, JOB.chapitres)
                    if diapo is not None and diapo != etat_diapo["n"]:
                        etat_diapo["n"] = diapo
                        c = chapitre_par_numero.get(diapo) or {}
                        entete = "Diapo %d%s [%s]" % (
                            diapo, " — " + c["titre"] if c.get("titre") else "",
                            hhmmss(c.get("debut", debut)))
                        JOB.emettre("diapo", {"n": diapo,
                                              "titre": c.get("titre", ""),
                                              "debut": c.get("debut", debut),
                                              "ts": hhmmss(c.get("debut", debut))})
                        fichier_txt.write("\n%s\n" % entete)
                JOB.emettre("segment", {"debut": round(debut, 2),
                                        "fin": round(fin, 2),
                                        "ts": hhmmss(debut),
                                        "diapo": diapo,
                                        "texte": texte})
                fichier_txt.write("[%s] %s\n" % (hhmmss(debut), texte))
                fichier_txt.flush()
                os.fsync(fichier_txt.fileno())
                JOB.transcrit = min(max(JOB.transcrit, fin), JOB.duree)
                ecoule = max(0.001, time.time() - JOB.debut_horloge)
                vitesse = JOB.transcrit / ecoule
                JOB.emettre("progres", {
                    "transcrit": round(JOB.transcrit, 1),
                    "duree": round(JOB.duree, 1),
                    "vitesse": round(vitesse, 2),
                    "restant": int((JOB.duree - JOB.transcrit) / vitesse)
                                if vitesse else 0}, durable=False)

            # On publie au fil de l'eau avec UN segment de retard : le dernier
            # segment d'une fenetre peut etre coupe en plein mot, on le jette
            # et la fenetre suivante repart a sa place. Sans ce streaming,
            # l'utilisateur ne verrait rien avant la fin d'une fenetre entiere
            # (plusieurs minutes sur un portable lent).
            en_attente = None
            derniere_fin = None
            for seg in segments:
                if JOB.stop.is_set():
                    break
                texte = couper_boucle(seg.text.strip())
                if est_hallucination(texte, getattr(seg, "no_speech_prob", 0.0),
                                     amorces):
                    continue
                # Boucle inter-segments : 'oui.' 'oui.' 'oui.' ...
                cle = normaliser(texte)
                repetitions = repetitions + 1 if cle == dernier_txt else 0
                dernier_txt = cle
                if repetitions >= 3:
                    continue
                morceaux = [(position + seg.start, position + seg.end, texte)]
                if JOB.chapitres:
                    mots = [(position + m.start, position + m.end, m.word)
                            for m in (getattr(seg, "words", None) or [])]
                    morceaux = uness_assemblage.decouper_aux_frontieres(
                        position + seg.start, position + seg.end, texte,
                        mots, JOB.chapitres)
                for courant in morceaux:
                    if en_attente:
                        publier(*en_attente)
                        precedent = (precedent + " " + en_attente[2])[-200:]
                        derniere_fin = en_attente[1]
                    en_attente = courant

            if JOB.stop.is_set():
                if en_attente:
                    publier(*en_attente)
                break

            if en_attente:
                bord = en_attente[1] > position + WINDOW_SECONDS - 1.0
                if derniere_fenetre or not bord:
                    publier(*en_attente)
                    precedent = (precedent + " " + en_attente[2])[-200:]
                    derniere_fin = en_attente[1]

            position = max(derniere_fin or (position + WINDOW_SECONDS),
                           position + 1.0)

        if JOB.stop.is_set():
            JOB.etat = "arrete"
            JOB.emettre("etat", {"etat": "arrete",
                                 "message": "Transcription arretee. "
                                            "Le texte deja transcrit est garde."})
        else:
            JOB.etat = "fini"
            JOB.transcrit = JOB.duree
            JOB.emettre("etat", {"etat": "fini",
                                 "message": "Transcription terminee."})
    except Exception as e:
        JOB.etat = "erreur"
        JOB.emettre("etat", {"etat": "erreur", "message": str(e)})
    finally:
        if fichier_txt:
            try:
                fichier_txt.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Serveur
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)
TMP = tempfile.mkdtemp(prefix="transcripteur-")
ARRET = threading.Event()


@app.after_request
def _pas_de_cache(r):
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/")
def index():
    return send_file(resource_path(os.path.join("ui", "index.html")))


@app.get("/ui/<path:nom>")
def ui(nom):
    chemin = resource_path(os.path.join("ui", os.path.basename(nom)))
    if not os.path.isfile(chemin):
        return "", 404
    return send_file(chemin)


@app.get("/api/config")
def config():
    return jsonify({
        "modeles": MODELS,
        "modeles_presents": modeles_presents(),
        "coeurs": os.cpu_count() or 1,
        "dossier_sortie": os.path.join(documents_dir(), "Transcriptions"),
        "etat": JOB.etat,
        "nom": JOB.nom,
        "duree": JOB.duree,
        "job": JOB.id,
        "version": VERSION,
        "depot": DEPOT,
        "uness_domaine": uness_cours.DOMAINES[0],
        "titre_cours": JOB.titre_cours,
        "chapitres": JOB.chapitres,
        "sans_audio": JOB.sans_audio,
    })


@app.post("/api/upload")
def upload():
    """Ecrit le fichier sur disque en streaming : jamais tout en RAM."""
    if JOB.etat in ("modele", "transcription", "recuperation"):
        return jsonify({"erreur": "Une transcription est deja en cours."}), 409

    # Le nom arrive encode en pourcent : les en-tetes HTTP sont limites au
    # latin-1 et les noms de fichiers des etudiants sont pleins d'accents.
    nom = unquote(request.headers.get("X-Nom-Fichier", ""), encoding="utf-8")
    nom = os.path.basename(nom.strip()) or "audio"
    ext = os.path.splitext(nom)[1].lower()
    if ext not in EXTS_OK:
        return jsonify({"erreur": "Format non pris en charge (%s). Formats "
                                  "acceptes : %s." %
                                  (ext or "inconnu",
                                   ", ".join(sorted(e[1:] for e in EXTS_OK)))}), 400

    nettoyer()
    JOB.id += 1
    cible = os.path.join(TMP, "audio-%d%s" % (JOB.id, ext))
    with open(cible, "wb") as f:
        while True:
            bloc = request.stream.read(1024 * 1024)
            if not bloc:
                break
            f.write(bloc)

    try:
        duree, codec, lisible = infos_media(cible)
    except Exception as e:
        return jsonify({"erreur": "Fichier audio illisible : %s" % e}), 400
    if duree <= 0:
        return jsonify({"erreur": "Duree du fichier introuvable."}), 400

    JOB.chemin = cible
    JOB.nom = nom
    JOB.duree = duree
    JOB.etat = "pret"
    JOB.journal = []
    JOB.transcrit = 0.0
    JOB.stop = threading.Event()
    # Un fichier audio ordinaire n'a pas de diapos : on efface celles d'un
    # eventuel cours precedent, sinon la sortie serait decoupee au hasard.
    JOB.chapitres = []
    JOB.titre_cours = ""
    JOB.sans_audio = []
    JOB.cle_cours = None

    if lisible:
        JOB.chemin_lecture = cible
        JOB.mime = mimetypes.guess_type(nom)[0] or "audio/mpeg"
    else:
        JOB.chemin_lecture = None
        enc = encodeur_dispo()
        if enc:
            codec_n, fmt, ext2, mime = enc
            sortie = os.path.join(TMP, "lecture-%d%s" % (JOB.id, ext2))
            JOB.mime = mime

            def _travail(jid=JOB.id):
                try:
                    if transcoder(cible, sortie, codec_n, fmt, JOB.stop):
                        if JOB.id == jid:
                            JOB.chemin_lecture = sortie
                            JOB.emettre("lecture_prete", {}, durable=False)
                except Exception:
                    pass
            threading.Thread(target=_travail, daemon=True).start()

    return jsonify({"nom": nom, "duree": duree, "codec": codec,
                    "lisible": lisible, "job": JOB.id})


@app.post("/api/start")
def start():
    if JOB.etat in ("modele", "transcription", "recuperation"):
        return jsonify({"erreur": "Une transcription est deja en cours."}), 409
    if not JOB.chemin:
        return jsonify({"erreur": "Aucun fichier audio charge."}), 400
    o = request.get_json(force=True, silent=True) or {}
    opts = {
        "modele": o.get("modele", "small"),
        "langue": (o.get("langue") or "").strip() or None,
        "hesitations": bool(o.get("hesitations", True)),
        "vocabulaire": o.get("vocabulaire", ""),
    }
    if opts["modele"] not in MODELS:
        return jsonify({"erreur": "Modele inconnu."}), 400
    JOB.options = opts
    JOB.journal = []
    JOB.stop = threading.Event()
    JOB.etat = "modele"
    JOB.thread = threading.Thread(target=boucle_transcription,
                                  args=(JOB.id, opts), daemon=True)
    JOB.thread.start()
    return jsonify({"ok": True})


@app.post("/api/stop")
def stop():
    JOB.stop.set()
    return jsonify({"ok": True})


@app.get("/api/stream")
def stream():
    depuis = request.args.get("depuis", type=int, default=0)
    q = JOB.abonner(depuis)

    @stream_with_context
    def flux():
        try:
            yield ": ok\n\n"
            while not ARRET.is_set():
                try:
                    evt = q.get(timeout=15)
                except Empty:
                    yield ": ping\n\n"  # garde la connexion ouverte
                    continue
                yield "data: %s\n\n" % json.dumps(evt, ensure_ascii=False)
        finally:
            JOB.desabonner(q)

    return Response(flux(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store",
                             "X-Accel-Buffering": "no"})


@app.get("/api/audio")
def audio():
    """Lecture avec requetes Range : indispensable pour le seek sur 3 heures."""
    chemin = JOB.chemin_lecture
    if not chemin or not os.path.isfile(chemin):
        return jsonify({"erreur": "Version lisible pas encore prete."}), 425
    taille = os.path.getsize(chemin)
    plage = request.headers.get("Range", "")
    entetes = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}

    m = re.match(r"bytes=(\d*)-(\d*)", plage)
    if not m or not plage:
        return Response(_lire(chemin, 0, taille - 1), 200,
                        {**entetes, "Content-Length": str(taille),
                         "Content-Type": JOB.mime})
    d, f = m.group(1), m.group(2)
    debut = int(d) if d else max(0, taille - int(f or 0))
    fin = int(f) if f and d else taille - 1
    fin = min(fin, taille - 1)
    if debut > fin or debut >= taille:
        return Response("", 416, {**entetes,
                                  "Content-Range": "bytes */%d" % taille})
    return Response(_lire(chemin, debut, fin), 206,
                    {**entetes,
                     "Content-Type": JOB.mime,
                     "Content-Length": str(fin - debut + 1),
                     "Content-Range": "bytes %d-%d/%d" % (debut, fin, taille)})


def _lire(chemin, debut, fin, bloc=256 * 1024):
    with open(chemin, "rb") as f:
        f.seek(debut)
        reste = fin - debut + 1
        while reste > 0:
            data = f.read(min(bloc, reste))
            if not data:
                break
            reste -= len(data)
            yield data


@app.get("/api/texte")
def texte():
    """Trois variantes : avec horodatages, texte seul, et Markdown.

    Pour un cours UNESS, les titres de diapos deviennent des intertitres (ou
    des '##' en Markdown) ; pour un simple fichier audio, rien ne change.
    """
    horodatages = request.args.get("ts", "1") == "1"
    markdown = request.args.get("md") == "1"
    lignes = []

    with JOB.verrou:
        chapitres = list(JOB.chapitres)
        titre = JOB.titre_cours or os.path.splitext(JOB.nom)[0]
        evenements = list(JOB.journal)

    if markdown:
        lignes.append("# %s\n" % titre)
    elif chapitres:
        lignes.append(titre)
        lignes.append("==============\n")

    par_numero = {c["n"]: c for c in chapitres}
    courante = None
    for e in evenements:
        if e["type"] != "segment":
            continue
        n = e.get("diapo")
        if n is not None and n != courante:
            courante = n
            c = par_numero.get(n) or {}
            nom = "Diapo %d%s" % (n, " — " + c["titre"] if c.get("titre") else "")
            ts = " [%s]" % hhmmss(c.get("debut", e["debut"])) if horodatages else ""
            if lignes:
                lignes.append("")
            lignes.append(("## %s%s" % (nom, ts)) if markdown else (nom + ts))
            lignes.append("")
        lignes.append("[%s] %s" % (e["ts"], e["texte"])
                      if horodatages else e["texte"])

    if not horodatages and not chapitres and not markdown:
        corps = " ".join(lignes)
    else:
        corps = "\n".join(lignes)
    return Response(corps, mimetype="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# Cours UNESS
# ---------------------------------------------------------------------------

COFFRE = uness_session.Coffre(DOSSIER_SESSION)
CONNEXION = uness_session.Connexion(COFFRE)
# La recuperation en cours. Un seul cours a la fois, comme un seul job.
RECUP = {"objet": None, "thread": None}


def _emettre_uness(type_, data, durable=True):
    JOB.emettre(type_, data, durable=durable)


def _url_demandee():
    o = request.get_json(force=True, silent=True) or {}
    return (o.get("url") or "").strip()


def _recuperation(url):
    """Renvoie la Recuperation en cours pour cette URL, ou en cree une."""
    objet = RECUP["objet"]
    if objet is not None and objet.url_index == uness_cours.verifier_url(url)[0]:
        objet.rafraichir_cookies()
        return objet
    objet = uness_recuperation.Recuperation(url, COFFRE, DOSSIER_COURS,
                                            _emettre_uness, JOB.stop)
    RECUP["objet"] = objet
    return objet


@app.get("/api/uness/etat")
def uness_etat():
    """Ce que l'interface a besoin de savoir pour dessiner l'ecran d'accueil."""
    return jsonify({
        "fenetre": uness_session.playwright_disponible(),
        "cookies": bool(COFFRE.lire()),
        "connexion": CONNEXION.etat,
        "message": CONNEXION.message,
        "trace": CONNEXION.trace_vive(),
        "domaine": uness_cours.DOMAINES[0],
        "cache": DOSSIER_COURS,
    })


@app.post("/api/uness/verifier")
def uness_verifier():
    """Valide le lien et teste la session, sans rien telecharger.

    La session n'est declaree valide que si un fichier AUDIO du cours repond
    de l'audio : Moodle renvoie sa page de connexion en 200, un code HTTP ne
    prouve rien.
    """
    try:
        r = _recuperation(_url_demandee())
    except uness_cours.ErreurCours as e:
        return jsonify({"erreur": str(e)}), 400
    if not COFFRE.lire():
        return jsonify({"connecte": False,
                        "message": "Tu n'es pas encore connecte a UNESS."})
    try:
        if not r.diapos:
            r.decouvrir()
    except uness_cours.ErreurCours as e:
        return jsonify({"connecte": False, "message": str(e)})
    except Exception:
        return jsonify({"connecte": False,
                        "message": "Session expiree : reconnecte-toi."})
    valide = r.session_valide()
    return jsonify({
        "connecte": valide,
        "titre": r.titre,
        "diapos": len(r.diapos),
        "message": ("Connecte a UNESS." if valide
                    else "Session expiree : reconnecte-toi."),
    })


@app.post("/api/uness/connexion")
def uness_connexion():
    """Ouvre la fenetre du navigateur. Ne bloque pas : l'interface suit l'etat."""
    try:
        r = _recuperation(_url_demandee())
    except uness_cours.ErreurCours as e:
        return jsonify({"erreur": str(e)}), 400
    try:
        CONNEXION.ouvrir(r.url_index, r.url_de_test(),
                         quand_fini=lambda ok, msg: JOB.emettre(
                             "uness", {"etape": "connexion", "ok": ok,
                                       "message": msg}, durable=False))
    except uness_session.ErreurConnexion as e:
        return jsonify({"erreur": str(e), "manuel": True}), 503
    return jsonify({"ok": True, "message": CONNEXION.message})


@app.post("/api/uness/cookie")
def uness_cookie():
    """Repli manuel : l'utilisateur colle la valeur de son cookie de session."""
    o = request.get_json(force=True, silent=True) or {}
    # Le cookie doit porter le domaine du lien colle, sinon il ne partirait
    # jamais avec les requetes.
    from urllib.parse import urlparse
    domaine = uness_cours.DOMAINES[0]
    if o.get("url"):
        try:
            domaine = urlparse(uness_cours.verifier_url(o["url"])[1]).hostname or domaine
        except uness_cours.ErreurCours as e:
            return jsonify({"erreur": str(e)}), 400
    elif RECUP["objet"]:
        domaine = urlparse(RECUP["objet"].base).hostname or domaine
    try:
        cookies = uness_session.cookies_depuis_texte(o.get("texte"), domaine)
    except uness_session.ErreurConnexion as e:
        return jsonify({"erreur": str(e)}), 400
    COFFRE.ecrire(cookies)
    if RECUP["objet"]:
        RECUP["objet"].rafraichir_cookies()
    return jsonify({"ok": True})


@app.post("/api/uness/deconnexion")
def uness_deconnexion():
    """Efface le profil du navigateur et les cookies. Rien d'autre ne part."""
    CONNEXION.annuler()
    COFFRE.effacer()
    if RECUP["objet"]:
        RECUP["objet"].rafraichir_cookies()
    CONNEXION.etat, CONNEXION.message = "repos", ""
    return jsonify({"ok": True})


@app.post("/api/uness/demarrer")
def uness_demarrer():
    """Recupere l'audio du cours, puis enchaine sur la transcription."""
    if JOB.etat in ("modele", "transcription", "recuperation"):
        return jsonify({"erreur": "Une transcription est deja en cours."}), 409
    o = request.get_json(force=True, silent=True) or {}
    try:
        uness_cours.verifier_url(o.get("url") or "")
    except uness_cours.ErreurCours as e:
        return jsonify({"erreur": str(e)}), 400
    if not COFFRE.lire():
        return jsonify({"erreur": "Connecte-toi d'abord a UNESS."}), 401
    if o.get("modele", "small") not in MODELS:
        return jsonify({"erreur": "Modele inconnu."}), 400

    nettoyer()
    JOB.id += 1
    JOB.journal = []
    JOB.stop = threading.Event()
    JOB.transcrit = 0.0
    JOB.chapitres = []
    JOB.sans_audio = []
    JOB.cle_cours = None
    JOB.etat = "recuperation"
    RECUP["objet"] = None
    RECUP["thread"] = threading.Thread(target=_recuperer_puis_transcrire,
                                       args=(o, JOB.id), daemon=True)
    RECUP["thread"].start()
    return jsonify({"ok": True, "job": JOB.id})


@app.get("/api/uness/chapitres")
def uness_chapitres():
    return jsonify({"titre": JOB.titre_cours, "chapitres": JOB.chapitres,
                    "sans_audio": JOB.sans_audio})


@app.get("/api/uness/historique")
def uness_liste_historique():
    """Les cours deja recuperes, du plus recent au plus ancien.

    Lecture seule et sans surprise : un dossier de cache corrompu donne une
    entree incomplete, jamais une erreur 500 qui viderait l'ecran d'accueil.
    """
    return jsonify({"cours": uness_historique.lister(
        DOSSIER_COURS,
        dossier_txt=os.path.join(documents_dir(), "Transcriptions"),
        nom_fichier=nom_de_fichier_sur)})


@app.post("/api/uness/historique/oublier")
def uness_oublier():
    """Supprime l'audio garde pour ce cours. Le .txt de Documents, lui,
    appartient a l'utilisateur : on n'y touche jamais."""
    cle = ((request.get_json(force=True, silent=True) or {}).get("cle") or "").strip()
    if JOB.cle_cours and cle == JOB.cle_cours:
        return jsonify({"erreur": "Ce cours est celui qui est ouvert. Charge "
                                  "autre chose avant de l'oublier."}), 409
    if not uness_historique.oublier(DOSSIER_COURS, cle):
        return jsonify({"erreur": "Ce cours n'est pas dans le cache."}), 404
    if RECUP["objet"] is not None and RECUP["objet"].cache.cle == cle:
        RECUP["objet"] = None
    return jsonify({"ok": True})


@app.post("/api/uness/historique/rouvrir")
def uness_rouvrir():
    """Recharge un cours deja en cache comme job courant, sans une seule
    requete reseau : de quoi le reecouter et reparcourir son plan."""
    if JOB.etat in ("modele", "transcription", "recuperation"):
        return jsonify({"erreur": "Une transcription est deja en cours."}), 409
    cle = ((request.get_json(force=True, silent=True) or {}).get("cle") or "").strip()
    fiche = uness_historique.rouvrir(DOSSIER_COURS, cle)
    if fiche is None:
        return jsonify({"erreur": "L'audio de ce cours n'est plus la. "
                                  "Relance la recuperation depuis son lien."}), 404

    nettoyer()
    JOB.id += 1
    JOB.journal = []
    JOB.stop = threading.Event()
    JOB.transcrit = 0.0
    JOB.chemin = JOB.chemin_lecture = fiche["chemin"]
    JOB.mime = "audio/mpeg"
    JOB.duree = fiche["duree"]
    JOB.chapitres = fiche["chapitres"]
    JOB.titre_cours = fiche["titre"]
    JOB.sans_audio = fiche["sans_audio"]
    JOB.nom = fiche["titre"]
    JOB.cle_cours = cle
    JOB.etat = "pret"
    return jsonify({"ok": True, "job": JOB.id})


def _recuperer_puis_transcrire(o, job_id):
    try:
        r = uness_recuperation.Recuperation(o["url"], COFFRE, DOSSIER_COURS,
                                            _emettre_uness, JOB.stop)
        RECUP["objet"] = r
        JOB.emettre("etat", {"etat": "recuperation",
                             "message": "Recuperation de l'audio du cours"})
        resultat = r.executer()
        if resultat is None:
            # La session a laches en route. On laisse l'interface proposer la
            # reconnexion : le cache garde tout ce qui est deja telecharge.
            JOB.etat = "erreur"
            JOB.emettre("etat", {
                "etat": "erreur", "reconnexion": True,
                "message": "Session expiree : reconnecte-toi, puis relance. "
                           "Les diapos deja recuperees ne seront pas "
                           "retelechargees."})
            return
        chemin, chapitres, duree = resultat
        if JOB.id != job_id or JOB.stop.is_set():
            return

        JOB.chemin = chemin
        JOB.chemin_lecture = chemin
        JOB.mime = "audio/mpeg"
        JOB.duree = duree
        JOB.chapitres = chapitres
        JOB.titre_cours = r.titre
        JOB.sans_audio = r.sans_audio
        JOB.nom = r.titre
        JOB.cle_cours = r.cache.cle
        JOB.etat = "pret"
        JOB.emettre("lecture_prete", {}, durable=False)

        # Les titres du cours servent de vocabulaire : c'est ce qui fait la
        # difference sur les termes medicaux.
        voc = uness_cours.vocabulaire(r.titre, r.diapos)
        saisi = (o.get("vocabulaire") or "").strip()
        opts = {
            "modele": o.get("modele", "small"),
            "langue": (o.get("langue") or "").strip() or None,
            "hesitations": bool(o.get("hesitations", True)),
            "vocabulaire": (saisi + ", " + voc) if saisi else voc,
        }
        JOB.options = opts
        JOB.etat = "modele"
        boucle_transcription(job_id, opts)
    except uness_recuperation.Interrompu:
        JOB.etat = "arrete"
        JOB.emettre("etat", {"etat": "arrete",
                             "message": "Recuperation arretee."})
    except (uness_cours.ErreurCours, uness_assemblage.ErreurAssemblage) as e:
        JOB.etat = "erreur"
        JOB.emettre("etat", {"etat": "erreur", "message": str(e)})
    except Exception as e:
        JOB.etat = "erreur"
        JOB.emettre("etat", {"etat": "erreur",
                             "message": "La recuperation du cours a echoue : %s" % e})


@app.get("/api/version")
def version_route():
    return jsonify({
        "version": VERSION,
        "depot": DEPOT,
        "maj_auto": maj_activee(),
        "verifie": MAJ["verifie"],
        "disponible": MAJ["disponible"],
        "erreur": MAJ["erreur"],
        "installable": os.name == "nt",
    })


@app.post("/api/maj/verifier")
def maj_verifier():
    return jsonify(chercher_maj())


@app.post("/api/maj/reglage")
def maj_reglage():
    actif = bool((request.get_json(force=True, silent=True) or {}).get("actif"))
    ecrire_reglage("maj_auto", actif)
    if not actif:
        MAJ.update(disponible=None)
    return jsonify({"maj_auto": actif})


@app.post("/api/maj/installer")
def maj_installer():
    if JOB.etat in ("modele", "transcription", "recuperation"):
        return jsonify({"erreur": "Une transcription est en cours. Arrete-la "
                                  "avant de mettre a jour."}), 409
    try:
        threading.Thread(target=_installer_en_fond, daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


def _installer_en_fond():
    try:
        installer_maj()
    except Exception as e:
        JOB.emettre("maj", {"etape": "erreur", "message": str(e)},
                    durable=False)


@app.post("/api/quitter")
def quitter():
    JOB.stop.set()
    threading.Timer(0.6, _arreter).start()
    return jsonify({"ok": True})


def _arreter():
    ARRET.set()
    nettoyer()
    os._exit(0)


def nettoyer():
    """Supprime les fichiers temporaires du job precedent."""
    for attr in ("chemin", "chemin_lecture"):
        p = getattr(JOB, attr, None)
        if p and os.path.isfile(p) and p.startswith(TMP):
            try:
                os.remove(p)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Instance unique + lancement
# ---------------------------------------------------------------------------

def fichier_verrou():
    return os.path.join(tempfile.gettempdir(), "transcripteur.port")


def instance_existante():
    try:
        with open(fichier_verrou(), encoding="utf-8") as f:
            port = int(f.read().strip())
        s = socket.create_connection(("127.0.0.1", port), timeout=0.7)
        s.close()
        return port
    except Exception:
        return None


def main():
    # La fenetre de connexion a UNESS tourne dans un sous-processus : c'est la
    # meme application, relancee avec ce drapeau. Elle n'ouvre ni serveur ni
    # navigateur, elle ecrit son resultat dans le fichier indique.
    if len(sys.argv) > 1 and sys.argv[1] == "--fenetre-connexion":
        sys.exit(uness_session.main_fenetre(sys.argv[2:]))

    deja = instance_existante()
    if deja:
        print("Transcripteur tourne deja. Ouverture du navigateur.")
        if os.environ.get("TRANSCRIPTEUR_SANS_NAVIGATEUR") != "1":
            webbrowser.open("http://127.0.0.1:%d/" % deja)
        return

    port = port_libre()
    with open(fichier_verrou(), "w", encoding="utf-8") as f:
        f.write(str(port))
    url = "http://127.0.0.1:%d/" % port

    print("=" * 58, flush=True)
    print(" Transcripteur %s - transcription locale de cours audio" % VERSION)
    print("=" * 58)
    print(" Interface : %s" % url)
    print(" Modeles   : %s" % MODELS_DIR)
    print(" Textes    : %s" % os.path.join(documents_dir(), "Transcriptions"))
    print(" Pour quitter : ferme cette fenetre (ou Ctrl+C).")
    print("=" * 58)

    if os.environ.get("TRANSCRIPTEUR_SANS_NAVIGATEUR") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Verification de version en tache de fond : une requete GET anonyme vers
    # GitHub, jamais bloquante, silencieuse si pas d'Internet.
    threading.Thread(target=chercher_maj, daemon=True).start()
    from waitress import serve
    try:
        serve(app, host="127.0.0.1", port=port, threads=12,
              channel_timeout=3600, clear_untrusted_proxy_headers=True)
    except KeyboardInterrupt:
        pass
    finally:
        ARRET.set()
        nettoyer()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
