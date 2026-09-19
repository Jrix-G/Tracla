# Transcripteur

Transcrit en texte, en direct, de longs enregistrements de cours audio —
sur ton ordinateur, sans Internet, sans compte, sans clé.
L'audio ne quitte jamais la machine.

---

## Pour l'utilisateur

1. Dézippe le dossier `Transcripteur` où tu veux (Bureau, Documents…).
2. Double-clique sur **`Transcripteur.exe`**. Une petite fenêtre noire s'ouvre
   (laisse-la ouverte, elle fait tourner l'application) et ton navigateur
   s'ouvre tout seul.
3. Glisse ton fichier audio, choisis la qualité, clique **Lancer la
   transcription**. Le texte s'écrit pendant que tu écoutes.
4. Le texte est enregistré au fur et à mesure dans
   `Documents\Transcriptions\` : même si tout se ferme brutalement, rien n'est
   perdu.
5. Pour quitter : ferme la fenêtre noire.

**Au premier lancement seulement**, l'application télécharge le modèle de
transcription (une barre de progression s'affiche). Ensuite elle fonctionne
totalement hors ligne.

**Si Windows affiche « Windows a protégé votre ordinateur »** : c'est normal,
le programme n'est pas signé numériquement (une signature coûte plusieurs
centaines d'euros par an). Clique sur **Informations complémentaires**, puis
sur **Exécuter quand même**.

---

## Pour le développeur

### Architecture

```
app.py                  backend : serveur local, transcription, routes HTTP
ui/index.html           interface (2 écrans : accueil, transcription)
ui/style.css            thème clair/sombre, colonne de lecture
ui/app.js               SSE, lecteur audio, surlignage, actions
tests/parcours.py       parcours complet contre un serveur démarré
tests/smoke_exe.py      test de fumée sur l'exe construit (utilisé par la CI)
tests/bench.py          vitesse des modèles, A/B du prompt d'hésitations
tests/outils_test.py    fabrication d'un audio de test (SAPI / espeak / bip)
build.bat               construit dist\Transcripteur\Transcripteur.exe
.github/workflows/      même build sur windows-latest, zip en artefact
```

**Flux général.** Le navigateur envoie le fichier en streaming vers un dossier
temporaire (`/api/upload`, jamais de lecture complète en mémoire), puis
`/api/start` démarre un thread de transcription unique et annulable. Ce thread
publie des évènements dans un journal en mémoire ; `/api/stream` les diffuse en
Server-Sent Events. Chaque évènement durable porte un index `i` : au
rechargement de la page, le navigateur se réabonne avec `?depuis=<index>` et ne
perd rien.

**RAM bornée.** `decoder_fenetre()` décode l'audio par fenêtres de 10 minutes
(≈ 38 Mo en float32 16 kHz mono) via PyAV, avec un *seek sur le flux* et non
sur le conteneur — plusieurs conteneurs (ogg, mkv) ignorent silencieusement le
seek conteneur et rejouent le fichier depuis le début, ce qui rendrait la 3ᵉ
heure d'un cours très lente à atteindre. Un cours de 4 h consomme donc autant
de mémoire qu'un cours de 10 min.

**Découpe sans mot coupé.** À la fin de chaque fenêtre, le dernier segment est
jeté s'il touche le bord, et la fenêtre suivante repart exactement à la fin du
dernier segment conservé : le mot éventuellement coupé est retranscrit
entièrement. Les deux derniers segments servent d'`initial_prompt` à la fenêtre
suivante, ce qui conserve le style sur tout le cours.

**Lecture audio.** `/api/audio` implémente les requêtes HTTP Range (206,
`Content-Range`, 416 hors limites) : sans ça, le seek d'un fichier de 3 h ne
fonctionne pas. Si le navigateur ne sait pas lire le format d'origine (wma,
certains mkv), une version mp3/ogg est transcodée **en tâche de fond** avec
PyAV, sans bloquer la transcription.

**Paramètres Whisper.** `device="cpu"`, `compute_type="int8"`,
`cpu_threads = min(cœurs - 1, 8)` (on laisse de la marge pour que la lecture
audio reste fluide), `vad_filter=True` avec `min_silence_duration_ms=500`,
`beam_size=3`, `condition_on_previous_text=True`, `word_timestamps=False`.

**Filtre anti-hallucination.** Deux mécanismes volontairement prudents, pour ne
jamais supprimer de vrai contenu :
- un segment n'est jeté que s'il correspond à un résidu connu
  (« Sous-titres réalisés par… », « Merci d'avoir regardé », « ♪ ») **et** que
  Whisper doutait déjà de la présence de parole (`no_speech_prob > 0.5`) ;
- les boucles de répétition sont plafonnées à 3 occurrences (motifs de 1 à 5
  mots), à l'intérieur d'un segment comme entre segments consécutifs.

### Construire l'exe

**En local (Windows) :** double-clique sur `build.bat`. Il vérifie que Python
est installé, crée `.venv`, installe `requirements.txt` + PyInstaller, et
produit `dist\Transcripteur\`. Le livrable est ce dossier entier, zippé.

**Sans rien installer :** pousse sur `main`/`master`, ou lance le workflow
« Construire Transcripteur.exe » à la main dans l'onglet Actions. La CI
construit sur `windows-latest`, exécute le test de fumée sur l'exe produit, et
publie le dossier en artefact.

Le build utilise `--onedir` et non `--onefile` : `--onefile` réextrait plusieurs
centaines de mégaoctets à *chaque* lancement.

### Vitesses mesurées

Mesures réelles sur la machine de développement (12 cœurs, 8 fils alloués,
`int8`, CPU seul), sur de la parole française authentique.

| Qualité | Modèle | × temps réel | 1 h d'audio en… | Mesuré ? |
|---|---|---|---|---|
| Rapide | `base` | **12,3×** (lecture) / **10,4×** (soutenu sur 2 h 07) | ~6 min | oui |
| Équilibré | `small` | **5,3×** (lecture) / **4,6×** (parole spontanée) | ~13 min | oui |
| Précis | `large-v3-turbo` | ~2× estimé | ~30 min | non (disque plein) |
| Très précis | `medium` | ~1,2× estimé | ~50 min | non (disque plein) |

`large-v3-turbo` et `medium` n'ont pas pu être mesurés faute de place disque
sur la machine de développement (1,5 à 1,6 Go chacun). Les chiffres affichés
dans l'interface pour ces deux modèles sont des estimations, pas des mesures.

**Ce qu'il faut recommander.** Sur un PC ordinaire (4 cœurs, 2 à 4 fois plus
lent que la machine de mesure), « Équilibré » tombe autour de **1,2 à 1,5×
temps réel**. Ça tient — le texte reste devant la lecture — mais la marge est
mince : sur un cours de 3 h, la transcription finit environ 30 à 50 minutes
avant la fin de l'écoute, et il n'y a presque pas d'avance dans les premières
minutes. Si le portable est plus ancien ou si d'autres applications tournent,
« Rapide » reste le choix sûr : il garde une avance confortable sur la
lecture, au prix de quelques mots approximatifs. Le texte apparaît dès les
premières secondes dans les deux cas.

### Ce que fait vraiment le prompt d'hésitations

Vérifié sur 6 min 20 de parole spontanée (interview, locuteur qui hésite),
comptage automatique des « euh » et des marqueurs de discours
(`ben`, `hein`, `voilà`, `du coup`, `alors`) :

| Modèle | Prompt | mots | « euh » | marqueurs |
|---|---|---|---|---|
| `base` | sans | 1083 | 0 | 7 |
| `base` | **avec** | 1119 | **17** | **29** |
| `small` | sans | 1078 | 0 | 7 |
| `small` | avec | 1089 | 0 | 10 |

Deux conclusions, contre-intuitives :

1. **Sans le prompt, les « euh » disparaissent complètement**, quel que soit le
   modèle : Whisper a été entraîné sur des transcriptions nettoyées et lisse
   spontanément le discours.
2. **Le prompt ne fonctionne bien que sur `base`.** Sur `base`, il fait passer
   les « euh » de 0 à 17 et quadruple les marqueurs de discours. Sur `small`,
   il augmente un peu les marqueurs (7 → 10) mais ne ramène aucun « euh » :
   le modèle plus gros « corrige » davantage et résiste au style imposé par le
   prompt.

Autrement dit, il y a un vrai arbitrage : **`base` pour un verbatim fidèle aux
hésitations, `small` pour des mots plus justes mais un discours lissé.** C'est
pour ça que la case « Garder les hésitations » est cochée par défaut et que le
texte de l'option ne promet pas plus que ce qui est observé.

### Publier une version

Le zip ne contient **aucun modèle** : seulement le programme et ses
bibliothèques (PyAV/ffmpeg, CTranslate2, ONNX Runtime, NumPy, Python).
Estimation à partir des roues Windows officielles : **90 à 130 Mo à
télécharger**, ~300 Mo une fois décompressé. Le modèle se télécharge au
premier lancement, une seule fois : 145 Mo pour « Rapide », 480 Mo pour
« Équilibré ». L'étape « Mesurer le poids du livrable » du workflow affiche
la taille réelle et les 25 plus gros fichiers, pour savoir quoi alléger.

Pour publier :

```bash
git init && git add . && git commit -m "Transcripteur"
git branch -M main
git remote add origin https://github.com/<toi>/transcripteur.git
git push -u origin main          # la CI construit et teste l'exe
git tag v1.0.0 && git push --tags # la CI publie la Release
```

Le lien à donner est alors
`https://github.com/<toi>/transcripteur/releases/latest` : une page, un
bouton, un zip. Les Releases GitHub sont gratuites et sans limite de
téléchargement sur un dépôt public (2 Go par fichier).

**Installer sur un PC sans Internet.** Le cache des modèles est le dossier
`modeles` posé à côté de l'exe. Il suffit de copier ce dossier depuis un PC
où l'application a déjà tourné : aucune connexion n'est alors nécessaire.

**Réduire encore la taille.** Les trois gros postes sont PyAV (~26 Mo,
ffmpeg), CTranslate2 (~19 Mo, le moteur) et ONNX Runtime (~14 Mo, uniquement
pour la détection de silence Silero). Supprimer ONNX Runtime impose de passer
`vad_filter=False`, ce qui dégrade nettement la qualité sur les longs silences
d'un cours : le jeu n'en vaut pas la chandelle. UPX compresse les DLL mais
casse régulièrement CTranslate2 et ONNX Runtime — à éviter.

### Tests

```bash
python app.py                              # démarre le serveur (note le port)
python tests/parcours.py http://127.0.0.1:PORT --modele base
python tests/bench.py audio.ogg base small large-v3-turbo --secondes 180
python tests/smoke_exe.py                  # après un build, teste l'exe
```

### Ce qui a été vérifié

Tout ceci a été exécuté réellement, pas seulement relu :

- parcours complet (`tests/parcours.py`) : 19 vérifications, dont l'upload
  d'un fichier nommé `Cours de droit – séance n°3 (été).ogg`, les requêtes
  Range (206 / `Content-Range` / 416), le flux SSE de bout en bout, la reprise
  du flux à un index donné, les deux exports et la sauvegarde incrémentale ;
- fichier long : 2 h 07 d'audio, mémoire du serveur **stable à ~525 Mo** du
  début à la fin (pas de croissance), 10,4× temps réel soutenu, et un seek
  dans la 2ᵉ heure **pendant** que la transcription tourne ;
- format que le navigateur refuse (`.wma`) : détecté, transcodé en mp3 en
  tâche de fond, lecture ensuite possible, transcription jamais bloquée ;
- `kill -9` en pleine transcription : le fichier de `Documents\Transcriptions`
  contient bien tout ce qui avait été transcrit jusque-là ;
- interface (navigateur réel) : dépôt de fichier, streaming du texte,
  clic sur un horodatage, surlignage, raccourcis clavier, coupure et reprise
  du suivi, rechargement de page en cours de transcription (55 segments
  restaurés), thèmes clair et sombre, bouton Quitter ;
- instance unique : un second lancement rouvre le navigateur sur la première
  et se termine.

**Ce qui n'a pas pu être vérifié ici :** l'exe Windows lui-même. Le
développement s'est fait sous Linux ; `build.bat` et le workflow GitHub
Actions produisent l'exe et le workflow exécute `tests/smoke_exe.py` dessus
(démarrage, interface embarquée, parcours complet, arrêt par `/api/quitter`).
Tant que ce workflow n'a pas tourné au vert, considère le packaging comme
non validé.

### Dépannage

| Symptôme | Cause et solution |
|---|---|
| « Le modèle n'a pas pu être téléchargé » | Pas d'Internet au premier lancement. Une seule connexion est nécessaire, ensuite tout est local. |
| L'application dit qu'elle tourne déjà | Une instance est active : elle rouvre simplement le navigateur. Ferme l'ancienne fenêtre noire pour repartir de zéro. |
| Le lecteur ne lit pas le son | Format que le navigateur refuse (wma, certains mkv). Une version lisible se prépare en tâche de fond ; la transcription, elle, continue. |
| L'exe plante au démarrage sans message | Vérifier que le build a bien inclus `ctranslate2` et `av` (`--collect-all`). Lancer `Transcripteur.exe` depuis une invite de commandes pour voir l'erreur. |
| Transcription beaucoup trop lente | Descendre d'un cran de qualité, et fermer les autres applications lourdes. |

### Activer le GPU Nvidia (utilisateur avancé)

Volontairement **non** embarqué : les bibliothèques CUDA pèsent plus de 2 Go et
échouent sur les machines sans carte Nvidia. Pour l'activer depuis les sources :

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*
```

puis dans `app.py`, remplacer dans `boucle_transcription()` :

```python
modele = WhisperModel(dossier, device="cuda", compute_type="float16")
```

Il faut un pilote Nvidia récent et une carte avec au moins 5 Go de VRAM pour
`large-v3-turbo`. Le gain est d'un ordre de grandeur (10 à 30× temps réel).

### Licence des échantillons de test

Les fichiers téléchargés par les tests proviennent de Wikimedia Commons
(« Fr-Spoken Wikipedia-Marie Curie », CC BY-SA 3.0 ; « Magali Balent de l'IRIS
au micro de Jacques Aristide », domaine public). Ils ne sont pas redistribués
dans ce dépôt.
