# -*- coding: utf-8 -*-
"""Mesure la vitesse en x temps reel des modeles, et compare l'effet du
prompt d'hesitations sur la conservation des 'euh'.

  python tests/bench.py audio.oga base small --secondes 180
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (SAMPLE_RATE, chemin_modele, construire_prompt,  # noqa: E402
                 couper_boucle, decoder_fenetre, est_hallucination)

HESITATIONS = re.compile(r"\b(euh+|heu+|ben|bah|hein|voil[aà]|du coup|alors)\b",
                         re.IGNORECASE)


def transcrire(modele, audio, prompt):
    t0 = time.time()
    segs, info = modele.transcribe(
        audio, language="fr", beam_size=3, condition_on_previous_text=True,
        initial_prompt=prompt, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        word_timestamps=False)
    textes = []
    for s in segs:
        t = couper_boucle(s.text.strip())
        if not est_hallucination(t, getattr(s, "no_speech_prob", 0.0)):
            textes.append(t)
    return " ".join(textes), time.time() - t0


def main():
    chemin = sys.argv[1]
    secondes = 180.0
    noms, args = [], sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--secondes":
            secondes = float(args[i + 1])
            i += 2
        else:
            noms.append(args[i])
            i += 1

    from faster_whisper import WhisperModel
    audio = decoder_fenetre(chemin, 0, secondes)
    duree = len(audio) / SAMPLE_RATE
    fils = max(1, min((os.cpu_count() or 4) - 1, 8))
    print("Audio : %.1f s — %d coeurs, %d fils, int8\n" % (duree, os.cpu_count(), fils))
    print("%-16s %8s %10s %8s %8s" % ("modele", "temps", "x reel", "mots", "hesit."))
    print("-" * 56)

    for nom in noms:
        dossier = chemin_modele(nom, lambda p: None)
        m = WhisperModel(dossier, device="cpu", compute_type="int8",
                         cpu_threads=fils)
        texte, dt = transcrire(m, audio, construire_prompt("", True))
        print("%-16s %7.1fs %9.2fx %8d %8d"
              % (nom, dt, duree / dt, len(texte.split()),
                 len(HESITATIONS.findall(texte))))
        del m

    # A/B du prompt d'hesitations sur le premier modele demande
    if noms:
        print("\nEffet du prompt d'hesitations (modele %s)" % noms[0])
        dossier = chemin_modele(noms[0], lambda p: None)
        m = WhisperModel(dossier, device="cpu", compute_type="int8",
                         cpu_threads=fils)
        for libelle, prompt in (("sans", None),
                                ("avec", construire_prompt("", True))):
            texte, dt = transcrire(m, audio, prompt)
            trouves = HESITATIONS.findall(texte)
            print("  %-5s : %4d mots, %2d marqueurs d'hesitation %s"
                  % (libelle, len(texte.split()), len(trouves),
                     sorted(set(x.lower() for x in trouves))))
            print("          %s" % texte[:220].replace("\n", " "))


if __name__ == "__main__":
    main()
