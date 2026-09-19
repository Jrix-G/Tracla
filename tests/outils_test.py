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


def audio_de_test(chemin_wav, texte=TEXTE_FR):
    """Fabrique un wav de test. Renvoie 'sapi', 'espeak' ou 'bip'."""
    if parole_sapi(chemin_wav, texte):
        return "sapi"
    if parole_espeak(chemin_wav, texte):
        return "espeak"
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
