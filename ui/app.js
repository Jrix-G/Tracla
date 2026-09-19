/* Transcripteur — interface. Aucun framework, aucune ressource externe. */
"use strict";

const $ = (s) => document.querySelector(s);
const audio = $("#audio");

const etat = {
  segments: [],      // {debut, fin, ts, texte, el}
  recu: 0,           // index du dernier événement durable reçu (reprise SSE)
  duree: 0,
  nom: "",
  actif: -1,
  suivre: true,
  phase: "repos",
  source: null,      // EventSource
  modeles: {},
  coeurs: 4,
  entree: "fichier",  // "fichier" | "uness"  (d'où vient l'audio)
  chapitres: [],      // cours UNESS : {n, titre, debut, fin, sans_audio, el}
  diapo: -1,          // diapo surlignée dans le plan
  uness: { connecte: false, fenetre: true, domaine: "formation.uness.fr" },
};

/* Vitesses en « × temps réel » ramenées à 4 cœurs, int8. base et small sont
   mesurés (voir le README) ; turbo et medium sont estimés à partir de leur
   taille relative. Volontairement prudentes : mieux vaut aller plus vite que
   l'estimation affichée. */
const VITESSES = { "base": 4.5, "small": 1.9, "large-v3-turbo": 0.9, "medium": 0.6 };
const ORDRE = ["base", "small", "large-v3-turbo", "medium"];
const DESCRIPTIONS = {
  "base": "Suffisant pour retrouver le fil. Quelques mots approximatifs.",
  "small": "Le meilleur compromis pour un cours de plusieurs heures.",
  "large-v3-turbo": "Presque aussi précis que le plus gros modèle, bien plus rapide.",
  "medium": "Le plus fidèle sur les termes techniques, mais très lent sur un long cours.",
};

/* ------------------------------ Outils ------------------------------ */

function hhmmss(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = String(Math.floor(s / 3600)).padStart(2, "0");
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  return `${h}:${m}:${String(s % 60).padStart(2, "0")}`;
}

function duree_humaine(s) {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

let toast_timer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast_timer);
  toast_timer = setTimeout(() => { t.hidden = true; }, 2600);
}

function erreur_accueil(msg) {
  const e = $("#erreur-accueil");
  e.textContent = msg;
  e.hidden = !msg;
}

/* ---------------------------- Démarrage ---------------------------- */

fetch("/api/config").then((r) => r.json()).then((c) => {
  etat.modeles = c.modeles;
  etat.coeurs = c.coeurs || 4;
  $("#dossier-sortie").textContent = c.dossier_sortie;
  if (c.uness_domaine) $("#uness-domaine").textContent = c.uness_domaine;
  construire_qualites();
  if (["recuperation", "modele", "transcription", "fini", "arrete", "erreur"]
      .includes(c.etat)) {
    // Reprise après un rechargement de page : on retrouve le travail en cours.
    etat.nom = c.nom; etat.duree = c.duree;
    if (c.chapitres && c.chapitres.length) {
      etat.chapitres = c.chapitres;
      etat.nom = c.titre_cours || c.nom;
      construire_plan(c.sans_audio || []);
    }
    passer_en_travail();
  }
});

function construire_qualites() {
  const boite = $("#qualites");
  boite.innerHTML = "";
  for (const nom of ORDRE) {
    const m = etat.modeles[nom];
    if (!m) continue;
    const facteur = (VITESSES[nom] || 1) * Math.min(2, Math.max(0.5, etat.coeurs / 4));
    const pour_1h = 3600 / facteur;
    const l = document.createElement("label");
    l.className = "option-qualite";
    l.innerHTML = `
      <input type="radio" name="modele" value="${nom}" ${nom === "small" ? "checked" : ""}>
      <span>
        <b>${m.label}</b>${nom === "small" ? ' <span class="reco">· recommandé</span>' : ""}
        <span class="aide">${DESCRIPTIONS[nom]}</span>
        <span class="aide">Téléchargement ${m.taille} (une seule fois) ·
          environ ${duree_humaine(pour_1h)} pour 1 h d'audio sur ton PC
          (${etat.coeurs} cœurs)</span>
      </span>`;
    boite.appendChild(l);
  }
}

/* ------------------------------ Dépôt ------------------------------ */

const depot = $("#depot");
const input_fichier = $("#fichier");
let fichier_pret = false;

depot.addEventListener("click", () => input_fichier.click());
depot.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input_fichier.click(); }
});
["dragenter", "dragover"].forEach((t) =>
  depot.addEventListener(t, (e) => { e.preventDefault(); depot.classList.add("survol"); }));
["dragleave", "drop"].forEach((t) =>
  depot.addEventListener(t, (e) => { e.preventDefault(); depot.classList.remove("survol"); }));
depot.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f) envoyer(f);
});
input_fichier.addEventListener("change", () => {
  if (input_fichier.files[0]) envoyer(input_fichier.files[0]);
});
$("#changer").addEventListener("click", () => input_fichier.click());

function envoyer(fichier) {
  erreur_accueil("");
  fichier_pret = false;
  $("#lancer").disabled = true;
  $("#fichier-choisi").hidden = false;
  $("#fichier-nom").textContent = fichier.name;
  $("#fichier-duree").textContent = "envoi en cours…";
  const barre = $("#barre-upload");
  barre.hidden = false;

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  // Les en-têtes HTTP ne supportent que le latin-1 : un nom comme
  // « Cours de droit – séance n°3 (été).mp3 » doit être encodé en pourcent.
  xhr.setRequestHeader("X-Nom-Fichier", encodeURIComponent(fichier.name));
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      barre.firstElementChild.style.width = (100 * e.loaded / e.total) + "%";
    }
  };
  xhr.onload = () => {
    barre.hidden = true;
    let r = {};
    try { r = JSON.parse(xhr.responseText); } catch (_) {}
    if (xhr.status !== 200) {
      erreur_accueil(r.erreur || "L'envoi du fichier a échoué. Réessaie.");
      $("#fichier-choisi").hidden = true;
      return;
    }
    etat.nom = r.nom; etat.duree = r.duree;
    $("#fichier-duree").textContent = duree_humaine(r.duree);
    fichier_pret = true;
    maj_bouton_lancer();
  };
  xhr.onerror = () => {
    barre.hidden = true;
    erreur_accueil("L'envoi du fichier a échoué. Vérifie que le fichier " +
                   "existe toujours, puis réessaie.");
  };
  xhr.send(fichier);
}

/* --------------------------- Cours UNESS ---------------------------
   L'application ne voit jamais le mot de passe : elle ouvre une vraie
   fenêtre de navigateur, l'utilisateur s'y connecte, et elle relit les
   cookies de cette fenêtre. Ici on ne fait que piloter cet aller-retour. */

function choisir_source(quoi) {
  etat.entree = quoi;
  const fichier = quoi === "fichier";
  $("#source-fichier").hidden = !fichier;
  $("#source-uness").hidden = fichier;
  $("#onglet-fichier").setAttribute("aria-selected", String(fichier));
  $("#onglet-uness").setAttribute("aria-selected", String(!fichier));
  $("#note-uness").hidden = fichier;
  $("#lancer").textContent = fichier
    ? "Lancer la transcription" : "Récupérer et transcrire";
  erreur_accueil("");
  maj_bouton_lancer();
  if (!fichier) etat_uness();
}

$("#onglet-fichier").addEventListener("click", () => choisir_source("fichier"));
$("#onglet-uness").addEventListener("click", () => choisir_source("uness"));

function maj_bouton_lancer() {
  $("#lancer").disabled = etat.entree === "fichier"
    ? !fichier_pret
    : !($("#uness-url").value.trim() && etat.uness.connecte);
}

$("#uness-url").addEventListener("input", maj_bouton_lancer);

function afficher_session(connecte, texte) {
  etat.uness.connecte = connecte;
  $("#session-pastille").className = "pastille " + (connecte ? "ok" : "ko");
  $("#session-texte").textContent = texte;
  $("#uness-deconnexion").hidden = !connecte && !etat.uness.cookies;
  $("#uness-connexion").textContent = connecte
    ? "Se reconnecter" : "Se connecter à UNESS";
  maj_bouton_lancer();
}

function etat_uness() {
  fetch("/api/uness/etat").then((r) => r.json()).then((s) => {
    etat.uness.fenetre = s.fenetre;
    etat.uness.cookies = s.cookies;
    if (s.domaine) $("#uness-domaine").textContent = s.domaine;
    // Sans fenêtre possible, le repli manuel n'est plus un repli : on l'ouvre.
    if (!s.fenetre) $("#uness-manuel").open = true;
    if (!s.cookies) { afficher_session(false, "Non connecté à UNESS"); return; }
    verifier_session();
  }).catch(() => afficher_session(false, "Non connecté à UNESS"));
}

function verifier_session() {
  const url = $("#uness-url").value.trim();
  if (!url) {
    afficher_session(false, "Colle d'abord le lien du cours");
    return Promise.resolve(false);
  }
  $("#session-texte").textContent = "Vérification de la session…";
  return fetch("/api/uness/verifier", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  }).then((r) => r.json()).then((s) => {
    if (s.erreur) { afficher_session(false, "Non connecté"); erreur_accueil(s.erreur); return false; }
    erreur_accueil("");
    afficher_session(s.connecte, s.connecte
      ? `Connecté à UNESS${s.diapos ? ` · ${s.diapos} diapos trouvées` : ""}`
      : s.message);
    return s.connecte;
  }).catch(() => { afficher_session(false, "Vérification impossible"); return false; });
}

$("#uness-url").addEventListener("change", () => {
  if (etat.uness.cookies) verifier_session();
});

$("#uness-connexion").addEventListener("click", () => {
  const url = $("#uness-url").value.trim();
  if (!url) { erreur_accueil("Colle d'abord le lien de ton cours UNESS."); return; }
  erreur_accueil("");
  $("#uness-connexion").disabled = true;
  $("#session-texte").textContent = "Ouverture de la fenêtre de connexion…";
  if (!etat.source) brancher_flux();   // pour recevoir la fin de la connexion
  fetch("/api/uness/connexion", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  }).then((r) => r.json().then((j) => ({ ok: r.ok, j }))).then(({ ok, j }) => {
    $("#uness-connexion").disabled = false;
    if (!ok) {
      erreur_accueil(j.erreur || "La fenêtre de connexion n'a pas pu s'ouvrir.");
      $("#uness-manuel").open = true;
      afficher_session(false, "Non connecté à UNESS");
      return;
    }
    $("#session-texte").textContent =
      "Connecte-toi dans la fenêtre qui vient de s'ouvrir…";
  }).catch(() => {
    $("#uness-connexion").disabled = false;
    erreur_accueil("La fenêtre de connexion n'a pas pu s'ouvrir.");
    $("#uness-manuel").open = true;
  });
});

$("#uness-cookie-ok").addEventListener("click", () => {
  const texte = $("#uness-cookie").value;
  fetch("/api/uness/cookie", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ texte, url: $("#uness-url").value.trim() }),
  }).then((r) => r.json()).then((j) => {
    if (j.erreur) { erreur_accueil(j.erreur); return; }
    $("#uness-cookie").value = "";
    erreur_accueil("");
    etat.uness.cookies = true;
    verifier_session().then((ok) => { if (ok) $("#uness-manuel").open = false; });
  });
});

$("#uness-deconnexion").addEventListener("click", () => {
  if (!confirm("Effacer la session UNESS enregistrée sur cet ordinateur ? " +
               "Tu devras te reconnecter la prochaine fois.")) return;
  fetch("/api/uness/deconnexion", { method: "POST" }).then(() => {
    etat.uness.cookies = false;
    afficher_session(false, "Session effacée");
    toast("Session UNESS effacée.");
  });
});

/* ---------------------------- Lancement ---------------------------- */

$("#formulaire").addEventListener("submit", (e) => {
  e.preventDefault();
  const corps = {
    modele: document.querySelector('input[name="modele"]:checked').value,
    langue: $("#langue").value,
    hesitations: $("#hesitations").checked,
    vocabulaire: $("#vocabulaire").value,
  };

  if (etat.entree === "uness") {
    const url = $("#uness-url").value.trim();
    if (!url) { erreur_accueil("Colle d'abord le lien de ton cours UNESS."); return; }
    if (!etat.uness.connecte) { erreur_accueil("Connecte-toi d'abord à UNESS."); return; }
    corps.url = url;
    $("#lancer").disabled = true;
    fetch("/api/uness/demarrer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corps),
    }).then((r) => r.json()).then((r) => {
      if (r.erreur) { erreur_accueil(r.erreur); $("#lancer").disabled = false; return; }
      etat.nom = "Cours UNESS";
      passer_en_travail();
    });
    return;
  }

  if (!fichier_pret) { erreur_accueil("Choisis d'abord un fichier audio."); return; }
  $("#lancer").disabled = true;
  fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corps),
  }).then((r) => r.json()).then((r) => {
    if (r.erreur) { erreur_accueil(r.erreur); $("#lancer").disabled = false; return; }
    passer_en_travail();
  });
});

function passer_en_travail() {
  $("#accueil").hidden = true;
  $("#travail").hidden = false;
  $("#titre-audio").textContent = etat.nom;
  $("#temps").textContent = `00:00:00 / ${hhmmss(etat.duree)}`;
  $("#piste").setAttribute("aria-valuemax", Math.floor(etat.duree));
  // Pour un cours UNESS, l'audio n'existe pas encore : il arrive avec
  // l'évènement « lecture_prete », à la fin de la récupération.
  if (etat.duree > 0) charger_audio();
  brancher_flux();
}

function charger_audio() {
  audio.src = "/api/audio?j=" + Date.now();
}

/* ------------------------------- SSE ------------------------------- */

function brancher_flux() {
  if (etat.source) etat.source.close();
  const s = new EventSource("/api/stream?depuis=" + etat.recu);
  etat.source = s;
  s.onmessage = (e) => traiter(JSON.parse(e.data));
  s.onerror = () => {
    // EventSource se reconnecte seul ; on repart au bon index.
    if (s.readyState === EventSource.CLOSED) setTimeout(brancher_flux, 1500);
  };
}

function traiter(e) {
  if (typeof e.i === "number") etat.recu = e.i + 1;
  switch (e.type) {
    case "segment": ajouter_segment(e); break;
    case "progres": maj_progres(e); break;
    case "telechargement":
      $("#avancement-titre").textContent = "Téléchargement du modèle";
      $("#avancement-detail").textContent =
        `${e.pct.toFixed(0)} % — une seule fois, ensuite l'app marche hors ligne`;
      $("#barre-transcription").style.width = e.pct + "%";
      break;
    case "langue": toast("Langue détectée : " + e.langue); break;
    case "lecture_prete":
      charger_audio();
      $("#etat-lecture").hidden = true;
      break;
    case "fichier": etat.fichier = e.chemin; break;
    case "maj": maj_progression(e); break;
    case "uness": maj_uness(e); break;
    case "diapo": ajouter_intertitre(e); surligner_plan(e.n); break;
    case "etat": maj_etat(e); break;
  }
}

/* Phase 1 : récupération de l'audio du cours. Elle réutilise la même barre
   d'avancement que la transcription — deux phases, un seul endroit à lire. */
function maj_uness(e) {
  const titre = $("#avancement-titre"), detail = $("#avancement-detail");
  const barre = $("#barre-transcription");
  switch (e.etape) {
    case "connexion":
      // Reçu pendant qu'on est encore sur l'accueil.
      if (!$("#accueil").hidden) {
        $("#uness-connexion").disabled = false;
        if (e.ok) { etat.uness.cookies = true; verifier_session(); }
        else { afficher_session(false, e.message); $("#uness-manuel").open = true; }
      }
      break;
    case "analyse":
      titre.textContent = "Lecture de la page du cours";
      detail.textContent = e.message; barre.style.width = "2%";
      break;
    case "plan":
      etat.nom = e.titre || etat.nom;
      $("#titre-audio").textContent = etat.nom;
      titre.textContent = "Récupération de l'audio";
      detail.textContent = e.message;
      break;
    case "audio":
      titre.textContent = "Récupération de l'audio";
      detail.textContent =
        `diapo ${e.fait}/${e.total}${e.cache ? " · déjà en cache" : ""}`;
      barre.style.width = (85 * e.fait / Math.max(1, e.total)).toFixed(1) + "%";
      break;
    case "assemblage":
      titre.textContent = "Assemblage de l'audio";
      detail.textContent = e.total ? `diapo ${e.fait}/${e.total}` : e.message;
      barre.style.width = "92%";
      break;
    case "session":
      titre.textContent = "Session expirée";
      detail.textContent = e.message;
      break;
    case "pret":
      etat.duree = e.duree;
      etat.chapitres = e.chapitres || [];
      etat.nom = e.titre || etat.nom;
      $("#titre-audio").textContent = etat.nom;
      $("#temps").textContent = `00:00:00 / ${hhmmss(etat.duree)}`;
      $("#piste").setAttribute("aria-valuemax", Math.floor(etat.duree));
      construire_plan(e.sans_audio || []);
      charger_audio();
      barre.style.width = "0%";
      titre.textContent = "Audio prêt — tu peux déjà écouter";
      detail.textContent = e.message;
      break;
    case "info":
      detail.textContent = e.message;
      break;
  }
}

/* ------------------------------- Plan ------------------------------- */

function construire_plan(sans_audio) {
  const liste = $("#plan-liste");
  liste.innerHTML = "";
  if (!etat.chapitres.length) { $("#plan").hidden = true; return; }
  for (const c of etat.chapitres) {
    const li = document.createElement("li");
    li.className = "plan-item" + (c.sans_audio ? " muette" : "");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "plan-lien";
    b.innerHTML =
      `<span class="plan-num">${c.n}</span>` +
      `<span class="plan-nom"></span>` +
      `<span class="plan-ts">${c.sans_audio ? "—" : hhmmss(c.debut)}</span>`;
    b.querySelector(".plan-nom").textContent = c.titre || "(sans titre)";
    if (c.sans_audio) {
      b.disabled = true;
      b.title = "Cette diapo n'a pas d'audio.";
    } else {
      b.addEventListener("click", () => aller_a(c.debut + 0.05, true));
    }
    li.appendChild(b);
    liste.appendChild(li);
    c.el = li;
  }
  const m = $("#plan-manquantes");
  if (sans_audio && sans_audio.length) {
    m.textContent = sans_audio.length === 1
      ? `Diapo ${sans_audio[0]} sans audio.`
      : `Diapos sans audio : ${sans_audio.join(", ")}.`;
    m.hidden = false;
  } else { m.hidden = true; }
  $("#plan").hidden = false;
}

function surligner_plan(n) {
  if (n === etat.diapo) return;
  const avant = etat.chapitres.find((c) => c.n === etat.diapo);
  if (avant && avant.el) avant.el.classList.remove("actif");
  etat.diapo = n;
  const c = etat.chapitres.find((x) => x.n === n);
  if (!c || !c.el) return;
  c.el.classList.add("actif");
  if (etat.suivre) c.el.scrollIntoView({ block: "nearest" });
}

/* Diapo courante d'après la position de lecture (le surlignage suit
   l'écoute, pas seulement l'écriture du texte). */
function diapo_a(t) {
  let courant = null;
  for (const c of etat.chapitres) {
    if (c.sans_audio) continue;
    if (t >= c.debut - 0.001) courant = c.n; else break;
  }
  return courant;
}

function maj_etat(e) {
  etat.phase = e.etat;
  const titre = $("#avancement-titre"), detail = $("#avancement-detail");
  if (e.etat === "recuperation") {
    titre.textContent = "Récupération de l'audio du cours";
    detail.textContent = e.message;
  }
  if (e.etat === "modele") { titre.textContent = "Préparation du modèle"; detail.textContent = e.message; }
  if (e.etat === "transcription") { titre.textContent = "Transcription en cours"; }
  if (e.etat === "fini") {
    titre.textContent = "Transcription terminée";
    detail.textContent = etat.fichier ? "Enregistrée dans " + etat.fichier : "";
    $("#barre-transcription").style.width = "100%";
    $("#transcript").setAttribute("aria-busy", "false");
    $("#arreter").disabled = true;
  }
  if (e.etat === "arrete") {
    titre.textContent = "Transcription arrêtée";
    detail.textContent = e.message;
    $("#arreter").disabled = true;
  }
  if (e.etat === "erreur") {
    titre.textContent = e.reconnexion
      ? "Session expirée" : "La transcription s'est arrêtée";
    detail.textContent = e.message;
    $("#arreter").disabled = true;
    // Session expirée : le cache garde tout ce qui est déjà récupéré, il
    // suffit de se reconnecter et de relancer le même lien.
    $("#revenir-accueil").hidden = !e.reconnexion;
  }
}

function maj_progres(e) {
  etat.duree = e.duree || etat.duree;
  const pct = etat.duree ? 100 * e.transcrit / etat.duree : 0;
  $("#barre-transcription").style.width = pct.toFixed(1) + "%";
  $("#piste-transcrite").style.width = pct.toFixed(1) + "%";
  $("#avancement-titre").textContent = "Transcription en cours";
  $("#avancement-detail").textContent =
    `${hhmmss(e.transcrit)} sur ${hhmmss(etat.duree)} · ${e.vitesse.toFixed(1)}× temps réel` +
    (e.restant > 0 ? ` · encore ${duree_humaine(e.restant)}` : "");
}

/* ---------------------------- Transcript ---------------------------- */

const transcript = $("#transcript");

/* Intertitre de diapo dans le transcript. Le serveur envoie l'évènement
   « diapo » juste avant le premier segment de cette diapo. */
function ajouter_intertitre(e) {
  $("#attente")?.remove();
  const h = document.createElement("h2");
  h.className = "intertitre";
  h.dataset.debut = e.debut;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "intertitre-lien";
  b.title = "Écouter à partir d'ici";
  b.textContent = `Diapo ${e.n}${e.titre ? " — " + e.titre : ""}`;
  b.addEventListener("click", () => aller_a(e.debut + 0.05, true));
  const ts = document.createElement("span");
  ts.className = "intertitre-ts";
  ts.textContent = e.ts;
  h.appendChild(b);
  h.appendChild(ts);
  transcript.appendChild(h);
}

function ajouter_segment(e) {
  $("#attente")?.remove();
  const p = document.createElement("p");
  p.className = "segment neuf";
  p.dataset.debut = e.debut;
  const b = document.createElement("button");
  b.className = "horodatage";
  b.type = "button";
  b.textContent = e.ts;
  b.title = "Écouter à partir d'ici";
  b.addEventListener("click", () => aller_a(e.debut, true));
  p.appendChild(b);
  p.appendChild(document.createTextNode(e.texte));
  transcript.appendChild(p);
  setTimeout(() => p.classList.remove("neuf"), 400);
  etat.segments.push({ debut: e.debut, fin: e.fin, el: p });
}

function segment_a(t) {
  const s = etat.segments;
  let lo = 0, hi = s.length - 1, res = -1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (s[m].debut <= t) { res = m; lo = m + 1; } else { hi = m - 1; }
  }
  return res >= 0 && t <= s[res].fin + 1.5 ? res : res;
}

function surligner(t) {
  const i = segment_a(t);
  if (i === etat.actif) return;
  if (etat.actif >= 0) etat.segments[etat.actif]?.el.classList.remove("actif");
  etat.actif = i;
  if (i < 0) return;
  const el = etat.segments[i].el;
  el.classList.add("actif");
  if (etat.suivre) {
    defilement_auto = true;
    el.scrollIntoView({ block: "center",
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    clearTimeout(fin_defilement);
    fin_defilement = setTimeout(() => { defilement_auto = false; }, 900);
  }
}

/* Si l'utilisateur scrolle à la main, on coupe le suivi proprement.
   On écoute l'intention (molette, doigt, touches de défilement) et non
   l'évènement « scroll » : le défilement fluide déclenché par le suivi
   lui-même en émet des dizaines et couperait le suivi tout seul. */
let defilement_auto = false, fin_defilement;

function couper_suivi() {
  if (!etat.suivre || $("#travail").hidden) return;
  etat.suivre = false;
  $("#suivre").checked = false;
  $("#revenir").hidden = false;
}

addEventListener("wheel", couper_suivi, { passive: true });
addEventListener("touchmove", couper_suivi, { passive: true });
addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown"].includes(e.key)) {
    couper_suivi();
  }
});

$("#suivre").addEventListener("change", (e) => {
  etat.suivre = e.target.checked;
  $("#revenir").hidden = etat.suivre;
  if (etat.suivre) recentrer();
});
$("#revenir").addEventListener("click", () => {
  etat.suivre = true;
  $("#suivre").checked = true;
  $("#revenir").hidden = true;
  recentrer();
});
function recentrer() {
  if (etat.actif >= 0) {
    defilement_auto = true;
    etat.segments[etat.actif].el.scrollIntoView({ block: "center", behavior: "smooth" });
    clearTimeout(fin_defilement);
    fin_defilement = setTimeout(() => { defilement_auto = false; }, 900);
  }
}

/* ------------------------------ Lecteur ------------------------------ */

function aller_a(t, jouer) {
  t = Math.max(0, Math.min(t, etat.duree));
  const faire = () => {
    audio.currentTime = t;
    if (jouer) audio.play().catch(() => {});
  };
  if (audio.readyState === 0) {
    audio.addEventListener("loadedmetadata", faire, { once: true });
    audio.load();
  } else faire();
  surligner(t);
}

$("#jouer").addEventListener("click", () => {
  if (audio.paused) audio.play().catch(avertir_lecture); else audio.pause();
});
audio.addEventListener("play", () => {
  $(".ic-play").hidden = true; $(".ic-pause").hidden = false;
});
audio.addEventListener("pause", () => {
  $(".ic-play").hidden = false; $(".ic-pause").hidden = true;
});
audio.addEventListener("timeupdate", () => {
  const t = audio.currentTime;
  const d = etat.duree || audio.duration || 0;
  $("#temps").textContent = `${hhmmss(t)} / ${hhmmss(d)}`;
  const pct = d ? 100 * t / d : 0;
  $("#piste-lue").style.width = pct + "%";
  $("#tete").style.left = pct + "%";
  $("#piste").setAttribute("aria-valuenow", Math.floor(t));
  $("#piste").setAttribute("aria-valuetext", hhmmss(t));
  surligner(t);
  if (etat.chapitres.length) surligner_plan(diapo_a(t));
});
audio.addEventListener("error", avertir_lecture);

function avertir_lecture() {
  const p = $("#etat-lecture");
  p.hidden = false;
  p.textContent = "Ton navigateur ne lit pas ce format directement. " +
    "Une version lisible est en cours de préparation ; la transcription, elle, " +
    "continue normalement.";
}

$("#vitesse").addEventListener("change", (e) => {
  audio.playbackRate = parseFloat(e.target.value);
});
$("#recul").addEventListener("click", () => aller_a(audio.currentTime - 10, false));
$("#avance").addEventListener("click", () => aller_a(audio.currentTime + 10, false));

/* Barre de progression : clic et glisser. */
const piste = $("#piste");
function position_depuis(e) {
  const r = piste.getBoundingClientRect();
  const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  return Math.max(0, Math.min(1, x / r.width)) * (etat.duree || audio.duration || 0);
}
let glisse = false;
piste.addEventListener("pointerdown", (e) => {
  glisse = true; piste.setPointerCapture(e.pointerId); aller_a(position_depuis(e), false);
});
piste.addEventListener("pointermove", (e) => { if (glisse) aller_a(position_depuis(e), false); });
piste.addEventListener("pointerup", () => { glisse = false; });
piste.addEventListener("keydown", (e) => {
  const pas = e.shiftKey ? 60 : 5;
  if (e.key === "ArrowRight") { e.preventDefault(); aller_a(audio.currentTime + pas, false); }
  if (e.key === "ArrowLeft") { e.preventDefault(); aller_a(audio.currentTime - pas, false); }
});

/* Raccourcis clavier globaux : espace = pause, flèches = ±5 s. */
addEventListener("keydown", (e) => {
  if ($("#travail").hidden) return;
  const cible = e.target;
  if (cible.matches("input, textarea, select") ||
      (cible === piste && e.key.startsWith("Arrow"))) return;
  if (e.key === " ") {
    e.preventDefault();
    if (audio.paused) audio.play().catch(() => {}); else audio.pause();
  } else if (e.key === "ArrowRight") {
    e.preventDefault(); aller_a(audio.currentTime + 5, false);
  } else if (e.key === "ArrowLeft") {
    e.preventDefault(); aller_a(audio.currentTime - 5, false);
  }
});

/* ------------------------------ Actions ------------------------------ */

$("#copier").addEventListener("click", async () => {
  const t = await fetch("/api/texte?ts=1").then((r) => r.text());
  try {
    await navigator.clipboard.writeText(t);
    toast("Texte copié.");
  } catch (_) {
    const z = document.createElement("textarea");
    z.value = t; document.body.appendChild(z); z.select();
    document.execCommand("copy"); z.remove();
    toast("Texte copié.");
  }
});

const menu = $("#menu-telecharger");
$("#telecharger").addEventListener("click", () => {
  menu.hidden = !menu.hidden;
  $("#telecharger").setAttribute("aria-expanded", String(!menu.hidden));
});
addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) { menu.hidden = true; $("#telecharger").setAttribute("aria-expanded", "false"); }
});
menu.querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
  menu.hidden = true;
  const ts = b.dataset.ts, md = b.dataset.md === "1";
  const t = await fetch(`/api/texte?ts=${ts}${md ? "&md=1" : ""}`).then((r) => r.text());
  const base = etat.nom.replace(/\.[^.]+$/, "") || "transcription";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([t], {
    type: md ? "text/markdown;charset=utf-8" : "text/plain;charset=utf-8" }));
  a.download = base + (md ? ".md" : ts === "1" ? " (horodaté).txt" : ".txt");
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}));

$("#arreter").addEventListener("click", () => {
  if (!confirm("Arrêter la transcription ? Le texte déjà transcrit est gardé.")) return;
  fetch("/api/stop", { method: "POST" });
});

$("#quitter").addEventListener("click", async () => {
  const en_cours = ["modele", "transcription", "recuperation"].includes(etat.phase);
  if (!confirm(en_cours
      ? "Fermer le Transcripteur ? La transcription en cours s'arrête ; le "
        + "texte déjà écrit reste dans le dossier Transcriptions."
      : "Fermer le Transcripteur ?")) return;
  try { await fetch("/api/quitter", { method: "POST" }); } catch (_) {}
  if (etat.source) etat.source.close();
  document.body.innerHTML =
    '<main id="accueil" class="page"><header class="entete-accueil">' +
    '<h1>Transcripteur fermé</h1><p class="sous-titre">Tu peux fermer cet ' +
    'onglet. Pour relancer, double-clique à nouveau sur Transcripteur.exe.' +
    '</p></header></main>';
});

/* Reprise après expiration de session : on revient à l'accueil, le lien et
   le cache sont toujours là, donc rien n'est retéléchargé. */
$("#revenir-accueil").addEventListener("click", () => {
  $("#travail").hidden = true;
  $("#accueil").hidden = false;
  $("#revenir-accueil").hidden = true;
  choisir_source("uness");
});

$("#nouvelle").addEventListener("click", () => {
  if (["modele", "transcription", "recuperation"].includes(etat.phase) &&
      !confirm("Une transcription est en cours. L'arrêter et en démarrer une autre ?")) return;
  fetch("/api/stop", { method: "POST" }).then(() => location.reload());
});


/* ---------------------------- Mise à jour ----------------------------
   Une seule requête sortante : GET sur la page des releases du dépôt, pour
   comparer les numéros de version. Aucune donnée n'est envoyée, et la case
   « Me prévenir des nouvelles versions » coupe complètement la vérification. */

const bandeau = $("#bandeau-maj");

function afficher_version(v) {
  $("#version").textContent = "Transcripteur " + (v.version === "dev"
    ? "(version de développement)" : v.version);
  $("#maj-auto").checked = v.maj_auto;
  $("#maj-verifier").hidden = !v.maj_auto || v.version === "dev";

  if (!v.disponible) { bandeau.hidden = true; return; }
  etat.maj = v.disponible;
  $("#maj-titre").textContent = "Version " + v.disponible.version + " disponible";
  $("#maj-detail").textContent = v.installable
    ? "Mise à jour en un clic. Tes modèles déjà téléchargés sont conservés."
    : "Télécharge-la depuis la page des versions du projet.";
  $("#maj-installer").hidden = !v.installable;
  bandeau.hidden = false;
}

fetch("/api/version").then((r) => r.json()).then(afficher_version).catch(() => {});

$("#maj-auto").addEventListener("change", (e) => {
  fetch("/api/maj/reglage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actif: e.target.checked }),
  }).then(() => fetch("/api/version").then((r) => r.json()).then(afficher_version));
});

$("#maj-verifier").addEventListener("click", () => {
  $("#maj-verifier").textContent = "Vérification…";
  fetch("/api/maj/verifier", { method: "POST" })
    .then((r) => r.json())
    .then((m) => {
      $("#maj-verifier").textContent = "Vérifier maintenant";
      if (m.disponible) return fetch("/api/version").then((r) => r.json()).then(afficher_version);
      toast(m.erreur || "Tu as déjà la dernière version.");
    })
    .catch(() => {
      $("#maj-verifier").textContent = "Vérifier maintenant";
      toast("Vérification impossible : pas de connexion.");
    });
});

$("#maj-plus-tard").addEventListener("click", () => { bandeau.hidden = true; });

$("#maj-installer").addEventListener("click", () => {
  if (["modele", "transcription", "recuperation"].includes(etat.phase) &&
      !confirm("Une transcription est en cours. La mise à jour va l'arrêter. " +
               "Continuer ?")) return;
  $("#maj-installer").disabled = true;
  $("#maj-plus-tard").hidden = true;
  $("#maj-barre").hidden = false;
  $("#maj-titre").textContent = "Mise à jour en cours";
  $("#maj-detail").textContent = "Ne ferme pas la fenêtre.";
  // Le flux SSE peut ne pas être branché (écran d'accueil) : on l'ouvre.
  if (!etat.source) brancher_flux();
  fetch("/api/maj/installer", { method: "POST" })
    .then((r) => r.json())
    .then((r) => { if (r.erreur) echec_maj(r.erreur); })
    .catch(() => echec_maj("La mise à jour n'a pas pu démarrer."));
});

function maj_progression(e) {
  const barre = $("#maj-barre").firstElementChild;
  if (e.etape === "telechargement") {
    $("#maj-titre").textContent = "Téléchargement de la nouvelle version";
    $("#maj-detail").textContent = e.pct + " %";
    barre.style.width = e.pct + "%";
  } else if (e.etape === "extraction") {
    $("#maj-titre").textContent = "Installation";
    $("#maj-detail").textContent = "Décompression des fichiers…";
    barre.style.width = "100%";
  } else if (e.etape === "redemarrage") {
    $("#maj-titre").textContent = "Redémarrage";
    $("#maj-detail").textContent = "";
    if (etat.source) etat.source.close();
    document.body.innerHTML =
      '<main id="accueil" class="page"><header class="entete-accueil">' +
      '<h1>Mise à jour en cours</h1><p class="sous-titre">Le Transcripteur ' +
      'se ferme, se met à jour et rouvre une nouvelle page tout seul. ' +
      'Ça prend quelques secondes. Tu peux fermer cet onglet.</p>' +
      '</header></main>';
  } else if (e.etape === "erreur") {
    echec_maj(e.message);
  }
}

function echec_maj(message) {
  $("#maj-barre").hidden = true;
  $("#maj-installer").disabled = false;
  $("#maj-plus-tard").hidden = false;
  $("#maj-titre").textContent = "La mise à jour a échoué";
  $("#maj-detail").textContent = message +
    " L\u2019application actuelle continue de fonctionner normalement.";
}
