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
3. Choisis d'où vient le cours :
   - **Fichier audio** — glisse ton enregistrement, choisis la qualité, clique
     **Lancer la transcription**. Le texte s'écrit pendant que tu écoutes.
   - **Cours UNESS (lien)** — colle l'adresse du cours, clique **Se connecter à
     UNESS**, connecte-toi dans la fenêtre qui s'ouvre, puis **Récupérer et
     transcrire** (voir plus bas).
4. Le texte est enregistré au fur et à mesure dans
   `Documents\Transcriptions\` : même si tout se ferme brutalement, rien n'est
   perdu.
5. Pour quitter : ferme la fenêtre noire.

**Au premier lancement seulement**, l'application télécharge le modèle de
transcription (une barre de progression s'affiche). Ensuite elle fonctionne
totalement hors ligne. Si tu changes de qualité plus tard, le nouveau modèle
se télécharge à ce moment-là, une seule fois lui aussi.

**Mises à jour.** Quand une nouvelle version existe, un bandeau apparaît en
haut de la page. Un clic sur « Mettre à jour » télécharge, installe et relance
l'application toute seule — les modèles déjà téléchargés sont conservés, il n'y
a rien à retélécharger. Tu peux couper ces vérifications avec la case
« Me prévenir des nouvelles versions », en bas de l'écran d'accueil.

### Importer un cours UNESS

Un cours de `formation.uness.fr` est un lecteur Adobe Presenter : il n'y a pas
un fichier audio, mais **un mp3 par diapo**. Le Transcripteur les récupère
tous, les assemble en un seul fichier, et transcrit le tout d'une traite en
gardant la structure du cours.

1. Ouvre ton cours dans ton navigateur habituel et copie l'adresse de la page
   du lecteur (elle finit en général par `index.htm`).
2. Colle-la dans le champ **Lien du cours**.
3. Clique **Se connecter à UNESS**. Une fenêtre de navigateur s'ouvre sur ton
   cours : connecte-toi comme d'habitude, SSO et double authentification
   comprises. Dès que la connexion est valide, la fenêtre se referme toute
   seule et la pastille passe au vert.
4. Clique **Récupérer et transcrire**. Tu vois d'abord « Récupération de
   l'audio : diapo 23/79 », puis la transcription en direct. **Tu peux écouter
   dès que l'audio est prêt**, sans attendre la fin du texte.

Le texte obtenu est structuré par diapo :

```
Sémiologie des troubles respiratoires
==============

Diapo 6 — Poumons et bronches [00:04:12]

[00:04:13] Le poumon droit comporte trois lobes…
```

Trois formats sont téléchargeables : avec horodatages, texte seul, et
Markdown (`##` par diapo). Le fichier enregistré au fil de l'eau dans
`Documents\Transcriptions\` suit la même structure.

**Ce que le Transcripteur ne fait jamais.** Il ne demande ni n'enregistre ton
mot de passe : tu te connectes toi-même, dans une vraie fenêtre de navigateur.
Il n'accepte que les liens en `https` vers `formation.uness.fr` — une adresse
sur un autre domaine est refusée, pour que ta session ne parte jamais ailleurs.
Il n'écrit jamais de cookie ni d'en-tête d'authentification dans ses messages.

**Usage personnel, avec ton propre compte.** Le Transcripteur utilise
uniquement la session de l'utilisateur connecté et ne contourne aucun contrôle
d'accès : il ne télécharge que ce que tu peux déjà lire dans ton navigateur.
Il traite **un cours à la fois**, séquentiellement, avec une pause entre chaque
requête — ce n'est pas un outil de moissonnage du site.

**Ce qui est gardé sur ton ordinateur.** La session (profil du navigateur et
cookies) vit dans `session-uness\`, à côté de l'exe ; l'audio téléchargé vit
dans `cours-uness\`. Le bouton **Se déconnecter et effacer la session** vide le
premier ; tu peux supprimer le second à la main quand tu n'en as plus besoin.

**Si la session expire pendant le téléchargement**, l'application le dit et
s'arrête proprement : reconnecte-toi, relance le même lien, et seules les
diapos manquantes sont téléchargées. Rien n'est jamais retéléchargé deux fois.

**Si une diapo n'a pas d'audio** (ça arrive), elle est signalée à la fin
(« Diapos sans audio : 12, 45 ») et apparaît en grisé dans le plan.

**Si la fenêtre de connexion ne s'ouvre pas** (pas de Microsoft Edge, politique
d'entreprise), déplie *« La fenêtre ne s'ouvre pas ? Coller le cookie à la
main »* : un mini-guide explique où trouver la valeur de `MoodleSession` avec
<kbd>F12</kbd>.

**Si Windows affiche « Windows a protégé votre ordinateur »** : c'est normal,
le programme n'est pas signé numériquement (une signature coûte plusieurs
centaines d'euros par an). Clique sur **Informations complémentaires**, puis
sur **Exécuter quand même**.

---

## Pour le développeur

### Architecture

```
app.py                    backend : serveur local, transcription, routes HTTP
uness/session.py          fenêtre de connexion (Playwright + Edge), cookies
uness/cours.py            liste blanche d'URL, lecture du plan, titres, mp3
uness/telechargement.py   client HTTP prudent, cache par cours, reprise
uness/assemblage.py       un seul mp3 propre + chapitres + rattachement
uness/recuperation.py     enchaînement des quatre étapes ci-dessus
ui/index.html             interface (2 écrans : accueil, transcription)
ui/style.css              thème clair/sombre, colonne de lecture, plan
ui/app.js                 SSE, lecteur audio, surlignage, plan, actions
tests/parcours.py         parcours complet contre un serveur démarré
tests/faux_uness.py       faux Moodle + faux lecteur Presenter (voir plus bas)
tests/uness_test.py       les modules UNESS contre ce faux serveur
tests/uness_parcours.py   l'import UNESS bout en bout, par les routes HTTP
tests/uness_fenetre_exe.py la fenêtre de connexion depuis l'EXE construit
tests/smoke_exe.py        test de fumée sur l'exe construit (utilisé par la CI)
tests/bench.py            vitesse des modèles, A/B du prompt d'hésitations
tests/outils_test.py      fabrication d'un audio de test (SAPI / espeak / bip)
build.bat                 construit dist\Transcripteur\Transcripteur.exe
.github/workflows/        même build sur windows-latest, zip en artefact
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
  mots), à l'intérieur d'un segment comme entre segments consécutifs ;
- pour un cours UNESS, où les titres de diapos servent d'`initial_prompt`, un
  segment qui restitue un titre **mot pour mot dans un silence**
  (`no_speech_prob > 0.6`) est jeté : c'est le prompt recraché, pas du contenu.
  Le même titre réellement prononcé est conservé, puisque le doute sur la
  présence de parole est alors faible.

### Import d'un cours UNESS

**Rien n'est codé en dur.** Le préfixe des mp3 (`a24x…`) est propre à chaque
cours. `uness/cours.py` part de `index.htm`, essaie les manifestes connus des
sorties Adobe Presenter (`data/presentation.xml`, `data/presentationData.js`…)
**plus** les scripts réellement chargés par la page, et parse chaque candidat
en XML puis en JS. Si aucun ne donne de diapos, il balaie la page à la
recherche de `*.mp3`. En tout dernier recours seulement, il déduit le motif
(`^(.*?)(\d+)\.mp3$`) et sonde les numéros — sans s'arrêter au premier trou
quand le nombre total de diapos est connu, et sur deux absences consécutives
sinon.

**Les titres viennent des données, pas de l'affichage.** Le plan du lecteur
tronque les titres à l'écran (« Segmentation pulmona… ») ; les titres complets
sont dans le manifeste. Le faux serveur de test reproduit exactement ce piège :
un plan HTML tronqué à 22 caractères, et les vrais titres uniquement dans le
XML.

**Détection de session.** Moodle renvoie sa page de connexion en **200**, pas en
401. Une réponse n'est donc acceptée que si son `Content-Type` est `audio/*`
(ou un 206) ; du HTML qui parle de connexion vaut « session absente ». La
fenêtre de connexion applique le même test et ne se ferme que quand il passe.

**Le piège CORS du SSO.** Ce test doit passer par
`contexte.request` (l'`APIRequestContext` de Playwright), **jamais** par un
`fetch()` exécuté dans la page. Pendant une connexion fédérée, l'onglet se
trouve sur le domaine du fournisseur d'identité ; un `fetch` vers
`formation.uness.fr` depuis cet onglet est alors une requête *cross-origin*,
que le navigateur bloque puisque Moodle n'envoie aucun en-tête CORS. La
fenêtre ne pouvait donc jamais constater que la connexion avait abouti, et
restait ouverte indéfiniment. Le contexte, lui, partage les cookies de la
fenêtre sans être soumis à ces règles.

Ce défaut était invisible tant que le faux serveur tenait sur une seule
origine — c'est pour ça que `tests/faux_uness.py` lance désormais, avec
`sso=True`, un **fournisseur d'identité sur un second port** et y laisse
l'onglet. Le scénario B de `tests/uness_fenetre_exe.py` échoue sur l'ancien
code (« toujours ouverte après 90 s ») et passe sur le nouveau : c'est un
garde-fou, pas une décoration.

**Assemblage.** Les octets des mp3 ne sont **jamais** concaténés : chaque
fichier porte ses propres horodatages et un bloc Xing/LAME, et un tel collage
donne une durée fausse et un seek qui tombe à côté. Chaque diapo est donc
décodée avec PyAV, réencodée une fois en mp3 mono 24 kHz / 64 kbit/s, avec
**1 s de silence** entre deux diapos — de quoi laisser le VAD de Whisper
séparer naturellement les diapos. Les décalages viennent du **nombre
d'échantillons réellement décodés**, pas des durées affichées dans le plan :
ffmpeg retire le silence d'amorce que tout encodeur mp3 ajoute, et sur 79
diapos cet écart se cumulerait.

Mesuré sur le faux serveur à la taille réelle (79 diapos, 21 min 34 s) : **80 ms
d'écart total** entre la durée du fichier assemblé et la somme des diapos
décodées, et **aucune dérive** — chaque chapitre dure exactement son mp3
d'origine à 20 ms près, y compris le 79ᵉ.

**Transcription en un seul passage, texte découpé par diapo.** Le fichier
assemblé est transcrit d'une traite, pour garder le contexte d'une diapo à
l'autre — des clips de 5 à 30 s transcrits séparément dégradent nettement
Whisper. Le rattachement se fait ensuite sur l'horodatage.

Mais la seconde de silence ne suffit pas : **le VAD de faster-whisper *retire*
les silences** avant de donner l'audio au modèle, qui ne voit donc plus aucune
pause et produit parfois une seule longue phrase couvrant trois diapos.
Rattacher ce bloc à la diapo de son premier mot mettrait le texte de trois
diapos sous un seul intertitre — c'est exactement ce qui se passait avant
correction. Pour un cours, `word_timestamps` est donc activé et
`uness.assemblage.decouper_aux_frontieres` recoupe **au mot** tout segment à
cheval. Sans horodatage de mot, le segment est rendu tel quel plutôt que coupé
au hasard.

Les titres du cours servent d'`initial_prompt` (titre du cours + titres de
diapos dédupliqués, tronqués à 850 caractères pour rester sous la limite de
Whisper). C'est ce qui fait la différence sur les termes médicaux — sibilants,
ronchi, crépitants, pneumothorax, fibroscopie bronchique. Contrepartie
mesurée : Whisper recrache parfois son prompt dans un silence, d'où le
garde-fou décrit plus haut.

**Reprise.** Le cache est un dossier par cours (`cours-uness\<clé>\`). Un mp3
déjà présent n'est repris que s'il se **décode** vraiment (une coupure laisse
des fichiers de taille correcte mais illisibles). Les diapos dont on sait
qu'elles n'ont pas d'audio sont mémorisées dans `sans-audio.json` : on ne
redemande pas au serveur un fichier dont on sait déjà qu'il n'existe pas.

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

### Mise à jour automatique

L'exe embarque son numéro de version (`version.txt`, écrit par la CI à partir
du tag). Au démarrage, un thread interroge
`api.github.com/repos/<dépôt>/releases/latest` — **une requête GET anonyme,
rien n'est envoyé**, et l'échec est silencieux si la machine est hors ligne.
Si le tag distant est supérieur, un bandeau s'affiche.

Le bouton « Mettre à jour » télécharge le zip de la release (progression
diffusée dans le flux SSE existant), le décompresse dans `%TEMP%`, écrit un
script `.bat` et le lance détaché. Ce script :

1. attend la fermeture complète de l'application (sinon Windows garde les
   fichiers verrouillés et la copie échoue) ;
2. copie les nouveaux fichiers **par-dessus** les anciens avec
   `robocopy /E` — sans `/PURGE`, donc **`modeles` et `reglages.json` ne sont
   jamais supprimés** : pas de re-téléchargement de 480 Mo après chaque mise
   à jour ;
3. relance `Transcripteur.exe`, qui rouvre le navigateur sur un nouvel onglet
   (le port est retiré au hasard à chaque démarrage, l'ancien onglet ne peut
   donc pas se reconnecter — l'écran le dit).

Si la copie échoue, le script laisse une fenêtre ouverte avec la marche à
suivre, et **l'ancienne version reste fonctionnelle** : rien n'est supprimé
avant que la nouvelle ne soit en place.

Un build local (`build.bat`) inscrit `dev` comme version : la vérification est
alors désactivée, pour ne pas proposer de « mettre à jour » une version de
travail vers une release plus ancienne.

Remarques de conception :

- la vérification est la **seule** connexion sortante de l'application en
  dehors du téléchargement des modèles, et elle est coupable en un clic ;
- pas de signature ni de somme de contrôle : l'archive vient de GitHub en
  HTTPS et la chaîne de confiance s'arrête là. Signer l'exe coûte plusieurs
  centaines d'euros par an, ce qui n'a pas de sens ici ;
- la mise à jour est refusée si une transcription est en cours.

### Publier une version

Le zip ne contient **aucun modèle** : seulement le programme et ses
bibliothèques (PyAV/ffmpeg, CTranslate2, ONNX Runtime, NumPy, Python,
Playwright). Mesuré sur un build local : **131 Mo à télécharger**, 349 Mo une
fois décompressé. Le modèle arrive au premier lancement, une seule fois :
145 Mo pour « Rapide », 480 Mo pour « Équilibré ».

**+35 Mo au zip pour la fenêtre de connexion.** Le plus gros fichier du
livrable est désormais `playwright\driver\node.exe` (81,6 Mo sur disque,
~35 Mo compressés) : c'est le pilote de Playwright. En revanche **aucun
navigateur n'est embarqué ni téléchargé** — `channel="msedge"` pilote
l'Edge déjà installé sur toute machine Windows 10/11, ce qui aurait ajouté
150 Mo de plus.

Attention en modifiant le workflow : le test de fumée télécharge un modèle
**dans le dossier de l'exe**, puisque le cache est volontairement placé à côté
du binaire. Sans l'étape « Nettoyer les traces du test », l'archive publiée
embarque 140 Mo de modèle de test. L'étape « Mesurer le
poids du livrable » affiche la taille réelle et les 25 plus gros fichiers :
c'est là qu'on voit ce genre de dérive.

Pour publier :

```bash
git init && git add . && git commit -m "Transcripteur"
git branch -M main
git remote add origin https://github.com/<toi>/transcripteur.git
git push -u origin main          # la CI construit et teste l'exe
git tag v1.0.0 && git push --tags # la CI publie la Release
```

Pour les versions suivantes, il suffit de pousser un tag plus grand
(`v1.1.0`) : les utilisateurs de `v1.0.0` verront le bandeau au lancement
suivant. **Le numéro doit rester au format `vX.Y.Z`** — c'est ce que compare
l'application.

Le lien à donner est alors
`https://github.com/<toi>/transcripteur/releases/latest` : une page, un
bouton, un zip. Les Releases GitHub sont gratuites et sans limite de
téléchargement sur un dépôt public (2 Go par fichier).

**Installer sur un PC sans Internet.** Le cache des modèles est le dossier
`modeles` posé à côté de l'exe. Il suffit de copier ce dossier depuis un PC
où l'application a déjà tourné : aucune connexion n'est alors nécessaire.

**Réduire encore la taille.** Les gros postes mesurés, décompressés :

| Fichier | Taille | Remarque |
|---|---|---|
| `ctranslate2.dll` | 56,5 Mo | le moteur d'inférence, incompressible |
| `onnxruntime` (2 fichiers) | 35,8 Mo | uniquement la détection de silence Silero |
| `av.libs` codecs vidéo | ~35 Mo | libx265, libaom, libvpx, libx264, dav1d |
| `libscipy_openblas64_.dll` | 19,5 Mo | algèbre linéaire de NumPy |
| `avcodec-61.dll` | 14,6 Mo | le décodage audio, lui, est indispensable |

Le seul vrai gisement restant, ce sont les **codecs vidéo embarqués par
PyAV** : l'application ne fait que de l'audio et traîne un encodeur H.265.
Les supprimer revient à retirer des DLL à l'intérieur du paquet PyAV, qui les
lie au chargement — à tenter seulement en s'appuyant sur le test de fumée
pour valider. Supprimer ONNX Runtime imposerait `vad_filter=False`, ce qui
dégrade nettement la qualité sur les longs silences d'un cours : le jeu n'en
vaut pas la chandelle. UPX compresse les DLL mais casse régulièrement
CTranslate2 et ONNX Runtime — à éviter.

### Tests

```bash
python app.py                              # démarre le serveur (note le port)
python tests/parcours.py http://127.0.0.1:PORT --modele base
python tests/uness_test.py                 # modules UNESS (--rapide : sans les 79 diapos)
python tests/uness_parcours.py             # import UNESS bout en bout, par HTTP
python tests/uness_fenetre_exe.py          # après un build : la fenêtre (2 scénarios)
python tests/uness_fenetre_exe.py --source # la même, sans attendre un build
python tests/bench.py audio.ogg base small large-v3-turbo --secondes 180
python tests/smoke_exe.py                  # après un build, teste l'exe
```

**Le faux serveur UNESS.** Impossible de tester l'import sur le vrai site sans
compte ; `tests/faux_uness.py` imite donc ce qui compte : `index.htm` avec un
plan **tronqué**, `data/presentation.xml` avec les titres complets accentués,
des mp3 numérotés générés par PyAV, des réponses 206 avec `Range` et
`Accept-Ranges`, des diapos sans audio, des 503 passagers — et surtout **une
page de connexion renvoyée en 200** quand le cookie manque. Il se lance aussi
seul, pour tester l'interface à la main :

```bash
python tests/faux_uness.py --port 8777 --diapos 20 --sans-audio 5,9
# puis, dans l'app, coller l'URL affichée après avoir mis
# TRANSCRIPTEUR_TEST_UNESS=1 (cette variable, et elle seule, autorise
# http://127.0.0.1 en plus de la liste blanche)
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
  et se termine ;
- import UNESS, modules (`tests/uness_test.py`) : **84 vérifications**, dont
  le refus des URL hors liste blanche (`http`, autre domaine,
  `formation.uness.fr.evil.com`), une page de connexion renvoyée en 200 non
  prise pour une session, le sondage qui franchit un trou, la reprise après
  expiration de session sans retélécharger les diapos déjà là, les 503
  passagers, les titres complets extraits du manifeste et non du plan tronqué,
  et un cours de **79 diapos** assemblé sans dérive ;
- import UNESS, bout en bout (`tests/uness_parcours.py`) : **34 vérifications**
  à travers les vraies routes HTTP, jusqu'à une transcription complète avec
  le modèle `base` sur de la **vraie parole** (synthèse vocale) — chapitres
  disponibles *avant* la fin de la transcription, `/api/audio` en 206 sur
  l'audio assemblé, les trois formats de sortie, et surtout : chaque diapo du
  faux cours **dit son propre numéro**, et le test vérifie que ce numéro tombe
  bien sous le bon intertitre pour les 9 diapos sonores ;
- fenêtre de connexion **depuis l'exe construit**
  (`tests/uness_fenetre_exe.py`) : 16 vérifications sur deux scénarios —
  Playwright démarre bien depuis le bundle PyInstaller, Edge s'ouvre, une page
  de connexion en 200 n'est pas prise pour une session, la connexion est
  détectée dès que le cookie arrive, les cookies remontent, le profil reste sur
  le disque, et aucune valeur de cookie n'apparaît dans la sortie du processus.
  Le **scénario B** rejoue un SSO : l'onglet reste sur un fournisseur
  d'identité servi sur un autre port, et la fenêtre doit quand même conclure ;
- interface réelle (navigateur, contre le faux serveur) : les deux entrées de
  l'accueil, le repli « coller le cookie », la récupération des 8 diapos avec
  la diapo 3 grisée dans le plan, la lecture possible **avant** la fin de la
  transcription, les intertitres, le surlignage du plan pendant la lecture, et
  un **seek à 0 ms d'écart sur les 7 chapitres sonores** (durée annoncée par
  le navigateur : 104,02 s, conforme).

**L'exe Windows, lui, a été construit et testé** (Windows 11, Python 3.12,
PyInstaller 6.22.3) : `tests/smoke_exe.py` passe ses 19 vérifications sur
`dist\Transcripteur\Transcripteur.exe`, et `tests/uness_fenetre_exe.py`
confirme que la fenêtre de connexion Playwright fonctionne **depuis l'exe
packagé** et pas seulement depuis le script.

**Ce qui n'a pas pu être vérifié ici :** le vrai site UNESS. Aucun compte
n'était disponible, donc tout l'import a été validé contre
`tests/faux_uness.py`. La structure réelle du lecteur Adobe Presenter du cours
de l'étudiant reste à confirmer — c'est l'objet de la check-list ci-dessous.

### Check-list du test réel (à faire avec un vrai compte)

Dans l'ordre, avec le cours de 79 diapos et sa capture du plan sous les yeux :

1. **Le lien.** Coller l'URL du lecteur. Vérifier qu'elle est acceptée, et
   qu'une URL bidon (`https://exemple.com/x/`) est refusée avec un message
   clair.
2. **La connexion.** Cliquer « Se connecter à UNESS ». Une fenêtre Edge doit
   s'ouvrir sur le cours. Se connecter (SSO + 2FA). Elle doit se refermer seule
   et la pastille passer au vert avec « 79 diapos trouvées ». **Si le nombre
   n'est pas 79, c'est la découverte du plan qui a échoué** : dis-le-moi, avec
   le nombre affiché.
3. **La mémoire de session.** Fermer l'app, la relancer, recoller le lien :
   la pastille doit passer au vert **sans reconnexion**.
4. **Le plan.** Comparer les titres du plan affiché à ceux de la capture.
   Regarder surtout : les titres sont-ils **complets** ou tronqués
   (« Segmentation pulmona… ») ? Sont-ils dans le **bon ordre** ? Les accents
   sont-ils corrects ?
5. **Les diapos sans audio.** Noter ce que l'app annonce en fin de
   récupération et vérifier que ces diapos-là sont bien muettes dans le
   lecteur UNESS.
6. **La durée.** L'audio assemblé doit faire ~42 min. Comparer à la somme des
   durées de la capture : quelques secondes d'écart sont normales (silences
   d'amorce mp3), plusieurs minutes ne le sont pas.
7. **Le seek.** Cliquer 5 ou 6 entrées du plan au hasard, dont la **1ʳᵉ**, une
   du **milieu** et la **79ᵉ**. À chaque fois, l'audio doit démarrer sur le
   début de cette diapo. C'est le point le plus important à vérifier.
8. **Le texte.** Une fois la transcription finie, ouvrir le `.txt` de
   `Documents\Transcriptions\`. Vérifier que le texte sous « Diapo 7 » parle
   bien du sujet de la diapo 7, et pas de la 6 ou de la 8. Vérifier aussi les
   termes médicaux (sibilants, ronchi, crépitants, pneumothorax…).
9. **La reprise.** Relancer le même lien : la récupération doit être quasi
   instantanée (« déjà en cache »), sans un seul retéléchargement.
10. **La déconnexion.** Cliquer « Se déconnecter et effacer la session », puis
    vérifier que le dossier `session-uness\` a bien disparu et que la pastille
    est repassée au gris.

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
