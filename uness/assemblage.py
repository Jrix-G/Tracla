# -*- coding: utf-8 -*-
"""Un seul mp3 propre a partir des mp3 de chaque diapo, et les chapitres.

Pourquoi ne pas simplement concatener les octets des mp3 : chaque fichier
porte ses propres horodatages et, souvent, un bloc Xing/LAME en tete. Colles
bout a bout, les navigateurs annoncent une duree fausse et le seek tombe a
cote. On decode donc tout, on reencode une fois, et la duree annoncee est la
vraie duree.

Les decalages de chaque diapo viennent du nombre d'echantillons reellement
decodes, jamais des durees affichees dans le plan du lecteur.
"""

import os
from fractions import Fraction

import av
import numpy as np

SORTIE_HZ = 24000        # de la parole : 24 kHz suffit largement
DEBIT = 64000            # 64 kbit/s mono : ~19 Mo pour 40 min
SILENCE = 1.0            # separation entre deux diapos, en secondes


class ErreurAssemblage(Exception):
    pass


def _lire_mono(chemin, hz):
    """Decode un fichier entier en float32 mono a 'hz'. Les diapos font
    quelques dizaines de secondes : tout tient en RAM sans probleme."""
    morceaux = []
    with av.open(chemin) as c:
        flux = next((s for s in c.streams if s.type == "audio"), None)
        if flux is None:
            raise ErreurAssemblage("%s ne contient pas d'audio."
                                   % os.path.basename(chemin))
        flux.thread_type = "AUTO"
        r = av.AudioResampler(format="flt", layout="mono", rate=hz)
        for trame in c.decode(flux):
            for out in r.resample(trame):
                morceaux.append(out.to_ndarray().reshape(-1))
        for out in r.resample(None) or []:
            morceaux.append(out.to_ndarray().reshape(-1))
    if not morceaux:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(morceaux).astype(np.float32)


def duree_decodee(chemin, hz=SORTIE_HZ):
    """Duree d'apres les echantillons reellement decodes. Elle est un peu plus
    courte que celle annoncee par le conteneur : ffmpeg retire le silence
    d'amorce que tout encodeur mp3 ajoute (quelques dizaines de ms par
    fichier, ce qui se cumule sur 79 diapos). C'est cette duree-la qui fait
    foi pour les decalages."""
    return _lire_mono(chemin, hz).size / float(hz)


def assembler(diapos, cache, cible, progression=None, stop=None):
    """diapos : [{'n', 'titre', 'fichier'|None}]. Ecrit 'cible' et renvoie
    la liste des chapitres, avec debut/fin exacts en secondes.

    Une diapo sans audio ne produit aucun silence : elle apparait dans les
    chapitres avec debut == fin, a la position ou elle aurait commence.
    """
    dire = progression or (lambda *a, **k: None)
    avec_audio = [d for d in diapos if d.get("fichier")]
    if not avec_audio:
        raise ErreurAssemblage("Aucune diapo n'a d'audio a assembler.")

    temporaire = cible + ".part"
    silence = np.zeros(int(SILENCE * SORTIE_HZ), dtype=np.float32)
    chapitres = []
    echantillons = 0          # position courante, en echantillons ecrits
    premier = True

    with av.open(temporaire, "w", format="mp3") as sortie:
        flux = sortie.add_stream("libmp3lame", rate=SORTIE_HZ)
        flux.bit_rate = DEBIT
        try:
            flux.layout = "mono"
        except Exception:
            pass
        ctx = flux.codec_context
        # L'encodeur impose la taille de ses trames : on l'alimente
        # exactement a cette taille, sinon libmp3lame refuse.
        taille_trame = ctx.frame_size or 1152
        tampon = np.zeros(0, dtype=np.float32)
        ecrits = [0]          # echantillons deja envoyes a l'encodeur (pts)

        def pousser(bloc):
            nonlocal tampon
            tampon = np.concatenate((tampon, bloc)) if tampon.size else bloc
            while tampon.size >= taille_trame:
                _encoder(sortie, flux, tampon[:taille_trame], SORTIE_HZ, ecrits)
                tampon = tampon[taille_trame:]

        for i, d in enumerate(diapos):
            if stop is not None and stop.is_set():
                raise ErreurAssemblage("Assemblage interrompu.")
            debut = echantillons / SORTIE_HZ
            if not d.get("fichier"):
                chapitres.append(_chapitre(d, debut, debut, None))
                continue
            chemin = cache.fichier(d["fichier"])
            audio = _lire_mono(chemin, SORTIE_HZ)
            if audio.size == 0:
                chapitres.append(_chapitre(d, debut, debut, d["fichier"]))
                continue
            if not premier:
                pousser(silence)
                echantillons += silence.size
                debut = echantillons / SORTIE_HZ
            premier = False
            pousser(audio)
            echantillons += audio.size
            chapitres.append(_chapitre(d, debut, echantillons / SORTIE_HZ,
                                       d["fichier"]))
            dire(i + 1, len(diapos))

        if tampon.size:                      # derniere trame, completee
            reste = np.zeros(taille_trame, dtype=np.float32)
            reste[:tampon.size] = tampon
            _encoder(sortie, flux, reste, SORTIE_HZ, ecrits)
        for paquet in flux.encode(None):
            sortie.mux(paquet)

    os.replace(temporaire, cible)
    return chapitres


def _encoder(sortie, flux, bloc, hz, ecrits):
    trame = av.AudioFrame.from_ndarray(
        np.ascontiguousarray(bloc.reshape(1, -1)), format="flt", layout="mono")
    trame.sample_rate = hz
    # Horodatage explicite en echantillons : c'est lui qui donne au fichier
    # final une duree juste, donc un seek juste dans le navigateur.
    trame.time_base = Fraction(1, hz)
    trame.pts = ecrits[0]
    ecrits[0] += bloc.size
    for paquet in flux.encode(trame):
        sortie.mux(paquet)


def _chapitre(d, debut, fin, fichier):
    return {
        "n": d["n"],
        "titre": d.get("titre") or "",
        "debut": round(debut, 3),
        "fin": round(fin, 3),
        "fichier": fichier,
        "sans_audio": fichier is None or fin <= debut + 1e-6,
    }


def verifier(chemin, chapitres, marge=0.5):
    """Controle de coherence : la duree annoncee par le fichier assemble doit
    correspondre a la fin du dernier chapitre. Renvoie (ok, duree, attendue)."""
    from .telechargement import duree_mp3
    attendue = max((c["fin"] for c in chapitres), default=0.0)
    reelle = duree_mp3(chemin)
    if reelle is None:
        return False, 0.0, attendue
    return abs(reelle - attendue) <= marge, reelle, attendue


def rattacher(segments, chapitres):
    """Associe chaque segment transcrit a sa diapo, d'apres son debut.

    La seconde de silence inseree entre deux diapos donne la marge : un
    segment qui commence dans le silence appartient a la diapo suivante,
    puisque c'est elle qu'il introduit.
    """
    bornes = [c for c in chapitres if not c["sans_audio"]]
    sortie = []
    for s in segments:
        t = s["debut"]
        courant = None
        for c in bornes:
            if t >= c["debut"] - 0.001:
                courant = c
            else:
                break
        sortie.append(courant["n"] if courant else (bornes[0]["n"] if bornes else None))
    return sortie


def decouper_aux_frontieres(debut, fin, texte, mots, chapitres):
    """Coupe un segment de Whisper qui chevauche deux diapos.

    La seconde de silence inseree entre deux diapos ne suffit pas a garantir
    la separation : le VAD de faster-whisper *retire* les silences avant de
    donner l'audio au modele, qui ne voit donc plus de pause du tout et
    produit parfois une seule longue phrase couvrant trois diapos. Rattacher
    ce bloc a la diapo de son premier mot mettrait le texte de trois diapos
    sous un seul intertitre.

    On recoupe donc au mot, aux frontieres de diapo. Renvoie une liste de
    (debut, fin, texte) ; sans horodatage de mot, le segment est rendu tel
    quel.
    """
    entier = [(debut, fin, texte)]
    bornes = sorted(c["debut"] for c in chapitres
                    if not c["sans_audio"] and debut < c["debut"] < fin)
    if not bornes or not mots:
        return entier

    morceaux = []
    reste = list(mots)
    d = debut
    for b in bornes:
        avant = [m for m in reste if m[0] < b]
        apres = [m for m in reste if m[0] >= b]
        if not avant or not apres:
            continue
        bout = "".join(m[2] for m in avant).strip()
        if bout:
            morceaux.append((d, avant[-1][1], bout))
        d = apres[0][0]
        reste = apres
    bout = "".join(m[2] for m in reste).strip()
    if bout:
        morceaux.append((d, fin, bout))
    return morceaux or entier


def diapo_a(t, chapitres):
    """Numero de diapo a l'instant t (None avant la premiere)."""
    courant = None
    for c in chapitres:
        if c["sans_audio"]:
            continue
        if t >= c["debut"] - 0.001:
            courant = c["n"]
        else:
            break
    return courant
