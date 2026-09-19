# -*- coding: utf-8 -*-
"""Import d'un cours UNESS (lecteur Adobe Presenter sur Moodle).

Quatre etapes, quatre modules :
  session.py        la fenetre de connexion et les cookies qui en sortent
  cours.py          lire la page du cours : titres des diapos, noms des mp3
  telechargement.py recuperer les mp3, avec cache et reprise
  assemblage.py     un seul mp3 propre + les chapitres

Aucun de ces modules ne connait Flask : app.py les appelle et diffuse
la progression sur le flux SSE existant.
"""
