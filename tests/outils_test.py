# -*- coding: utf-8 -*-
"""Outils communs aux tests : fabrication d'un audio parle de test."""
import os
import subprocess
import sys
import wave

NOM_PIEGE = "Cours de droit – séance n°3 (été).mp3"

TEXTE_FR = (
    "Bonjour a tous et bienvenue dans ce troisieme cours de droit "
    "constitutionnel. Nous allons parler aujourd'hui de la hierarchie des "
    "normes, telle que Hans Kelsen l'a formulee. La Constitution occupe le "
    "sommet de la pyramide, puis viennent les traites internationaux, la loi, "
    "et enfin le reglement. Prenez bien vos notes, cela tombera a l'examen."
)


def parole_sapi(chemin_wav, texte, vitesse=0):
    """Synthese vocale Windows (SAPI). Retourne True si ca a marche."""
    if os.name != "nt":
        return False
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = %d; "
        "$s.SetOutputToWaveFile('%s'); "
        "$s.Speak(@'\n%s\n'@); $s.Dispose()"
        % (vitesse, chemin_wav.replace("'", "''"), texte)
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       check=True, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.getsize(chemin_wav) > 10000
    except Exception:
        return False


def parole_espeak(chemin_wav, texte):
    for binaire in ("espeak-ng", "espeak"):
        try:
            subprocess.run([binaire, "-v", "fr", "-s", "150",
                            "-w", chemin_wav, texte],
                           check=True, timeout=180,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return os.path.getsize(chemin_wav) > 10000
        except Exception:
            continue
    return False


def bip(chemin_wav, secondes=6.0):
    """Repli sans synthese vocale : un signal module. Ne produit pas de mots,
    sert uniquement a valider la plomberie (upload, Range, SSE, arret)."""
    import math
    import struct
    sr = 16000
    with wave.open(chemin_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        trames = bytearray()
        for i in range(int(sr * secondes)):
            t = i / sr
            env = 0.0 if (int(t) % 2) else 1.0  # alterne son / silence
            v = int(12000 * env * math.sin(2 * math.pi * (200 + 80 * math.sin(t)) * t))
            trames += struct.pack("<h", v)
        w.writeframes(bytes(trames))
    return True


# Parole francaise embarquee dans le depot : synthese vocale Windows sur notre
# propre texte (TEXTE_FR), donc aucun contenu tiers a licencier.
#
# Pourquoi elle existe : sur une machine sans voix de synthese, le repli
# etait un simple 'bip'. Whisper y hallucinait "Sous-titres realises par
# Amara.org", le filtre anti-hallucination -- alors casse -- le laissait
# passer, et "au moins un segment recu" passait PAR ACCIDENT. Une fois le
# filtre repare, ce test aurait echoue sur toute machine sans voix, et
# bloque la publication pour une raison qui n'a rien a voir avec le code.
ECHANTILLON_FR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "echantillons", "parole_fr.mp3")


def parole_embarquee(chemin_wav):
    """Decode l'echantillon du depot vers 'chemin_wav'. True si ca a marche."""
    if not os.path.isfile(ECHANTILLON_FR):
        return False
    try:
        import av
        with av.open(ECHANTILLON_FR) as e, \
                av.open(chemin_wav, "w", format="wav") as s:
            fi = next(x for x in e.streams if x.type == "audio")
            fo = s.add_stream("pcm_s16le", rate=16000)
            r = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for t in e.decode(fi):
                for o in r.resample(t):
                    for p in fo.encode(o):
                        s.mux(p)
            for p in fo.encode(None):
                s.mux(p)
        return os.path.getsize(chemin_wav) > 10000
    except Exception:
        return False


def audio_de_test(chemin_wav, texte=TEXTE_FR):
    """Fabrique un wav de test.

    Renvoie 'sapi', 'espeak', 'embarque' ou 'bip'. Seul 'bip' ne contient
    aucun mot : un test qui attend du texte transcrit ne doit pas s'y fier.
    L'echantillon embarque ne vaut que pour TEXTE_FR : un autre texte ne
    peut pas s'en servir.
    """
    if parole_sapi(chemin_wav, texte):
        return "sapi"
    if parole_espeak(chemin_wav, texte):
        return "espeak"
    if texte == TEXTE_FR and parole_embarquee(chemin_wav):
        return "embarque"
    bip(chemin_wav)
    return "bip"


def repeter_audio(source, cible, fois):
    """Concatene un wav N fois (pour fabriquer un fichier de plusieurs heures)."""
    with wave.open(source, "rb") as s:
        params = s.getparams()
        data = s.readframes(s.getnframes())
    with wave.open(cible, "wb") as c:
        c.setparams(params)
        for _ in range(fois):
            c.writeframes(data)
    return cible


if __name__ == "__main__":
    cible = sys.argv[1] if len(sys.argv) > 1 else "test.wav"
    print(audio_de_test(cible), os.path.getsize(cible))


def repeter_en_mp3(source, cible, fois, debit=64000):
    """Fabrique un long fichier (plusieurs heures) en repetant un echantillon,
    encode en mp3 pour rester raisonnable sur le disque."""
    import av
    import numpy as np
    sortie = av.open(cible, "w")
    flux = sortie.add_stream("libmp3lame", rate=16000)
    flux.bit_rate = debit
    resampler = av.AudioResampler(format=flux.codec_context.format,
                                  layout=flux.codec_context.layout, rate=16000)
    for _ in range(fois):
        with av.open(source) as entree:
            fin = next(s for s in entree.streams if s.type == "audio")
            fin.thread_type = "AUTO"
            for trame in entree.decode(fin):
                trame.pts = None
                for out in resampler.resample(trame):
                    for p in flux.encode(out):
                        sortie.mux(p)
    for p in flux.encode(None):
        sortie.mux(p)
    sortie.close()
    return cible
