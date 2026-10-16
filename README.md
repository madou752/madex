# MADEX · version 3.7 : ton Jarvis, toujours là

**MADEX — Madou's Automated Development EXecutive.** Ton majordome numérique : il délègue le code à des sessions
Claude Code en arrière-plan, te demande ton accord avant chaque action, et ne commit jamais sans ton clic.

Déjà en place : aiguilleur, cerveau, sessions de code, garde-fous git, règles vivantes, interface (phase 1),
voix ElevenLabs, réveil « Madex », claquements de mains, réacteur et logo (phase 2),
Spotify, Gmail en lecture seule, applis, sites, projets, volume, fond permanent et onglet CONNEXIONS (phase 3).
Version 3.4 : conversation fluide sans redire « Madex », interruption à la voix, reconnaissance de TA voix,
réponses dites au fil de l'eau, fenêtres façon OS (conso, musique, système, diagnostic), orbe toujours visible,
contexte automatique (projet ouvert dans VS Code), Spotify fiable, mises à jour automatiques.
À venir : mode mentor, revue et suggestions (phase 4), outils anti-tokens (phase 5).

## 1. Prérequis (une seule fois)

1. **Python 3.12** (ou 3.11) : https://www.python.org/downloads/ (coche **« Add python.exe to PATH »**).
2. **Git for Windows** : https://git-scm.com/download/win
3. **Claude Code** : https://code.claude.com/docs/en/setup, puis dans un terminal :
   `claude` (connecte-toi avec ton abonnement), puis `claude update`. Il faut la version **2.1.259 ou plus**.
4. **GitHub CLI** (pour les revues de PR) : `winget install GitHub.cli`, puis `gh auth login`.

## 2. Installation

1. Crée un compte gratuit sur https://elevenlabs.io, puis **Profil → API Keys → Create** et copie la clé.
2. Double-clique sur **`installer.bat`**. Il installe tout, télécharge les modèles vocaux (~600 Mo, une fois),
   te demande la clé ElevenLabs (Entrée pour t'en passer), crée le raccourci **MADEX** sur le Bureau, et propose
   le démarrage automatique avec Windows.
4. Ouvre l'onglet **CONNEXIONS** de Madex : tout s'y configure, guide pas à pas compris (voir plus bas).
3. Lance **`demarrer.bat --test-voix`** : Madex dit une phrase, puis affiche le niveau de ton micro. Si les barres
   bougent quand tu parles, tout est prêt.

Tes projets sont cherchés dans ton dossier `Documents` : chaque sous-dossier est un projet.
Pour changer, modifie `PROJECTS_DIRS` dans `madex/config.py`.

## Mettre à jour

Le plus simple : `git pull` dans le dossier de Madex, puis relance-le.

Tu peux aussi installer un zip `madex-phaseX.Y.zip` : **Diagnostic › METTRE À JOUR DEPUIS UN FICHIER…**, ou glisse
le zip sur la fenêtre Madex. Il attend d'être au repos (aucune session en cours, il ne parle pas), s'installe,
redémarre et te dit « Mise à jour terminée ».

Mises à jour automatiques (**désactivées par défaut**, active-les seulement si tu maîtrises la source),
à ajouter dans le `.env` de Madex :
- `MADEX_AUTO_ZIP=1` : installe tout seul un `madex-phaseX.Y.zip` déposé dans **Téléchargements** ;
- `MADEX_AUTO_GIT=1` : `git pull --ff-only` toutes les 6 heures si le dossier est un dépôt git.

Pour passer de la 3.3 à la 3.4 (une dernière fois à la main) :
1. Décompresse le nouveau zip **dans le même dossier**, en remplaçant les fichiers existants.
2. Relance **`installer.bat`**. Il ferme Madex s'il tourne, ajoute seulement ce qui manque (quelques secondes à
   une minute), et ne te repose aucune question déjà répondue.

Ta clé (`.env`), tes règles, ta mémoire et l'historique des sessions (`%LOCALAPPDATA%\MADEX`) ne sont jamais dans les zips :
ils sont conservés.

## Nouveautés 3.7 : Madex pilote votre Chrome (extension Claude)

**À faire une fois** :
1. Installez l'extension **Claude** depuis le Chrome Web Store
   (https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn) et connectez-vous avec le
   **même compte claude.ai** que Claude Code.
2. Diagnostic › **Claude dans Chrome** › TESTER. La première fois, redémarrez Chrome puis retestez.
3. Dans les réglages de l'extension, autorisez les sites où Madex peut agir (sinon l'extension demande elle-même).

**À la voix** : « va sur GitHub et dis-moi mes notifications », « teste mon projet sur localhost 5173 et dis-moi
les erreurs de la console », « regarde sur le site de la Fnac le prix de la Switch », « dans Chrome, … ».
Pour le reste, le cerveau passe la main tout seul quand il faut votre vrai Chrome (sites où vous êtes connecté).

**Comment ça marche** : une session Claude Code à part (`--chrome`), gardée ouverte, pour que le cerveau reste rapide.
Elle ouvre ses propres onglets (regroupés) et ne dit que le résultat final.

**Sécurité** (Réglages › règles) :
- lire, naviguer, capturer : libre (« lire et naviguer dans Chrome ») ;
- cliquer, taper, remplir, envoyer un fichier, JavaScript : votre accord à chaque fois (« une fois », « pour la
  session » = pour ce site jusqu'au redémarrage, « toujours ») ;
- interdits, même avec votre accord : vos sites pro (`MADEX_BLOCKED_HOSTS` dans le `.env`), Discord, banques et paiement ; pages de paiement en lecture seule ;
- Gmail : lecture seule (aucun clic) ; jamais d'achat, de publication, ni de mot de passe saisi ;
- seule la session navigateur peut toucher Chrome (ni le cerveau, ni les sessions de code).

## Correctif 3.6.2

- Quand la musique démarre (lancée par Madex, par le cerveau ou reprise), Madex se tait net : pas d'annonce,
  ce qu'il restait à dire est jeté, et le titre s'affiche simplement (♪ …) dans le chat.
- Pas d'écoute de suivi juste après (les paroles de la chanson ne sont pas prises pour une demande).
- Le volume de la musique se règle en silence. Vos vraies questions ont toujours une réponse (musique baissée).

## Correctif 3.6.1

- Spotify : la recherche demandait le pays du compte (`market=from_token`), ce qui exige une autorisation que Madex
  ne demande pas : Spotify répondait « 403 Insufficient client scope ». Corrigé, sans avoir à se reconnecter.
- Le test Spotify fait maintenant exactement la même recherche que Madex, et vérifie les autorisations du jeton.
- Plus de trace Python brute dans le journal en cas d'erreur : une ligne claire.

## Nouveautés 3.6 : plus rapide, plus naturel, et on voit ce qui se passe

**JOURNAL** (`Ctrl+J`, ou Diagnostic › JOURNAL, ou « ouvre le journal ») :
- tout ce que fait Madex, en direct : niveau (infos / avertissements / erreurs), filtre (« spotify », « chrono »…) ;
- chaque échange a un numéro (`#12`) et un chronométrage : aiguillage → premier mot de Claude → premier son ;
- **COPIER POUR CLAUDE** copie les dernières lignes utiles, prêtes à coller dans une conversation ;
- fichiers dans `%LOCALAPPDATA%\MADEX\logs` (5 × 2 Mo maximum).

**Conversation** :
- Claude Code reste ouvert en continu : plus de démarrage à chaque question (≈ 1 s pour le premier mot, au lieu de 3) ;
- si la réponse tarde, une petite phrase (« Je regarde, chef. ») pour ne jamais laisser de blanc ;
- détection de la voix humaine (Silero), avec 0,8 s gardées avant : la première syllabe n'est plus coupée ;
- fin de phrase plus rapide (0,7 s), mais il attend si vous dites « euh… », « et… » ;
- **vous pouvez lui couper la parole sans mot magique** : il vérifie que ce n'est pas l'écho de sa propre voix,
  baisse le son le temps de vérifier, puis se tait et vous écoute. « Stop » seul : il se tait, rien d'autre ;
- une nouvelle question pendant qu'il répond remplace l'ancienne réponse (il sait qu'il a été coupé ; « continue »
  pour qu'il reprenne).

**« Lance todo app »** : Madex retrouve le nom même mal entendu, et s'il hésite (ouvrir dans VS Code ou démarrer ?),
il pose UNE question et retient votre réponse pour la prochaine fois. Pour démarrer un projet, il devine la
commande (npm/pnpm/yarn, Flutter, Spring, Django, Laravel, Python) et demande votre accord.

**Spotify** : l'erreur exacte est dite et notée au journal ; Diagnostic › Spotify › TESTER vérifie étape par étape
(Client ID, connexion, compte, appareils, recherche, lecture) ; si l'API refuse, Madex ouvre le titre dans l'appli
Spotify ou utilise les touches multimédia ; bouton **RÉINITIALISER** dans Réglages › Connexions.

## Nouveautés 3.5

**Tes réglages sont à l'abri.** Règles, clés, connexions Spotify/Gmail, vocabulaire… sont maintenant dans
`%LOCALAPPDATA%\MADEX` (récupérés automatiquement depuis l'ancien dossier `data`). Même si tu remplaces tout le
dossier madex, plus rien ne s'efface.

- **Allumer / éteindre** : raccourci MADEX du Bureau, **Ctrl+Alt+M** (lance Madex ou fait apparaître sa fenêtre),
  ou `MADEX.bat`. « Éteins-toi », « arrête-toi », « quitte », « ferme-toi », « bonne nuit » = extinction complète
  (pastille comprise). « Va dormir » / « cache-toi » = cacher la fenêtre. « Redémarre-toi » = relance propre.
  Boutons Redémarrer / Éteindre dans la fenêtre Diagnostic.
- **Pastille** : mini réacteur discret, cachée quand la fenêtre Madex est devant toi, visible sinon, disparaît
  quand Madex s'éteint. Glisse-la où tu veux : elle s'aimante aux bords et reste sur l'écran (multi-écrans).
  Petit point orange = accord en attente, bleu = session en cours. Elle s'estompe au repos.
- **Chat** : un bloc de conversation fixe sous le réacteur (il défile tout seul, tu peux remonter lire).
  La fenêtre **CHAT** (dock ou Ctrl+K) permet d'écrire. Ce que Madex comprend s'affiche en direct pendant que tu parles.
- **Fenêtres** : coins nets et « allumage LED » à l'ouverture ; la fenêtre active brille plus fort. Poignée en bas à
  droite pour redimensionner.
- **Mieux compris** : modèle de transcription plus précis selon ton PC (Diagnostic › Précision : auto / rapide /
  précis / max). Il apprend tes mots : dis « non, j'ai dit Gazo » et il corrige pour toujours.
- **Mode mentor** : « Madex, mode mentor » (ou le bouton MENTOR en haut). Tout passe en ambre. Il relit chaque
  sauvegarde du projet ouvert (20 s après ta dernière modif), t'explique les erreurs, et c'est toi qui corriges.
  Le code à recopier s'affiche dans la fenêtre MENTOR (bouton COPIER), jamais dans ton éditeur. Il retient tes
  erreurs fréquentes. « Fin du mode mentor » pour arrêter.
- **Revue de PR** : « Madex, regarde la PR 42 » (projet ouvert dans l'éditeur, `gh` connecté). Il vérifie chaque
  remarque dans le code, te fait un résumé oral et affiche le détail. Il ne poste jamais rien sur GitHub.
- **Notifications** : « Madex, j'ai des notifs ? » → il lit tes notifications Windows et les résume. Jamais de lui-même.
- **« Madex, quoi de neuf ? »** : le point sur tes mails importants, notifications, sessions et ta conso.
- **Diagnostic** : chaque test affiche son résultat sur sa ligne (✓/✗ + heure), bouton « Tout tester ».
- **Spotify** : autorisations corrigées et vérifiées au démarrage. Si tu avais l'erreur « invalid scope », reconnecte
  Spotify une fois (Réglages › Connexions). Madex ne peut plus modifier son propre code.
- La reconnaissance de ta voix (3.4) est retirée : elle te rejetait parfois.

## Nouveautés 3.4 : un vrai Jarvis

- **Pas de mode à annoncer.** Après chaque réponse, il t'écoute encore ~7 secondes : enchaîne sans redire « Madex »
  (l'état affiche « JE VOUS ÉCOUTE… »). Silence : il se rendort tout seul.
- **Tu peux lui couper la parole** : parle par-dessus lui (« attends, mets plutôt du Jul »), il se tait et t'écoute.
  Une porte qui claque, la musique ou sa propre voix ne l'interrompent pas.
- **Micro** : un clic = il écoute (re-clic = envoyer tout de suite) ; **double-clic = micro coupé / rallumé**
  (l'état est retenu, même après redémarrage). « Coupe le micro » marche aussi à la voix.
- **Réponses plus rapides** : il commence à parler dès la première phrase de Claude.
- **Fenêtres façon OS** : Conso, Musique, Système, Diagnostic, Réglages, Sessions, Historique… déplaçables,
  redimensionnables, leur place est retenue. À la voix : « montre ma conso », « ouvre le diagnostic »,
  « ferme les fenêtres ». **Échap** ferme la fenêtre du dessus.
- **Ta conso Claude** : « Madex, ma conso ? » → % de ta fenêtre de 5 h et de la semaine (avec l'heure de remise à
  zéro), tokens du jour, de la semaine et par modèle.
- **L'orbe** : une petite bulle toujours visible par-dessus tes fenêtres (en bas à droite, déplaçable).
  Clic = parler, double-clic = micro, clic droit = menu. Pour la désactiver : `SHOW_ORB = False` dans `madex/config.py`.
- **Contexte automatique** : il sait quel projet, quel fichier et quelle branche sont ouverts dans VS Code
  (ou Cursor, IntelliJ…). « Ajoute un mode sombre » suffit : plus besoin de dire « sur le portfolio ».
- **Spotify** : « mets-moi du Ninho », « la dernière de SDM », « du rap pour bosser », « attends, plutôt Jul »…
  Il réveille Spotify tout seul et corrige les noms d'artistes d'après ce que tu écoutes vraiment.
  Si Spotify refuse encore : vérifie dans ton app sur developer.spotify.com › **User Management** que ton e-mail
  Spotify est bien ajouté.
- **Diagnostic** : l'état de chaque brique (micro, ta voix, sa voix, Claude, Spotify, Gmail, contexte, version)
  avec un bouton TESTER, et les dernières erreurs.

## 3. Lancement

| Commande | Mode |
|---|---|
| `demarrer.bat` | Complet : réveil vocal + voix + interface |
| `demarrer.bat --texte` | Sans micro : tu écris, il répond à l'oral |
| `demarrer.bat --muet` | Ni micro ni voix, pour tester |
| `demarrer.bat --test-voix` | Vérifie le micro et la voix, puis quitte |

## 4. Toujours là, quand tu l'appelles

Avec le démarrage automatique, Madex tourne **en fond dès que Windows démarre**, sans fenêtre.
- Dis **« Madex »** (ou claque des mains) : sa fenêtre apparaît et il t'écoute.
- **« Madex, va dormir »** (ou « éteins-toi », « cache-toi ») : la fenêtre se ferme, il reste à l'écoute.
- **« Madex, arrête-toi complètement »** : il s'arrête vraiment, jusqu'au prochain démarrage de Windows ou un
  double-clic sur le raccourci MADEX.
- Double-cliquer sur le raccourci alors qu'il tourne déjà affiche simplement sa fenêtre.

## 5. Connexions (onglet CONNEXIONS)

**La voix ElevenLabs (gratuite).** Crée un compte sur elevenlabs.io, puis **Developers → API Keys → Create Key**,
avec les permissions **Text to Speech**, **Voices : Read** et **User : Read**. Colle la clé dans l'onglet, clique sur
**VOIR LES VOIX**, écoute-les (▶), puis **CHOISIR**. Les voix marquées « gratuite » (premade) sont incluses dans
l'offre gratuite (10 000 crédits par mois, environ 20 minutes de parole avec le modèle Flash).
Sans clé, ou en cas de souci, Madex parle avec la voix Edge, ou à défaut la voix intégrée à Windows (hors ligne) :
il n'est plus jamais muet. L'onglet affiche la voix réellement utilisée et la dernière erreur éventuelle.

**Spotify (Premium).** Sur developer.spotify.com/dashboard → **Create app**. Redirect URI :
`http://127.0.0.1:8765/callback/spotify` (exactement), coche **Web API**. Copie le **Client ID** dans l'onglet
(pas le secret, inutile), puis **CONNECTER SPOTIFY** et accepte.

**Gmail (lecture seule).** Sur console.cloud.google.com : crée un projet, active **Gmail API**, configure l'**écran de
consentement** (Externe, ajoute ton adresse Gmail en utilisateur test), puis **Identifiants → ID client OAuth →
Application de bureau** et télécharge le JSON. Dépose-le dans l'onglet, puis **CONNECTER GMAIL** et accepte.
Madex ne peut ni envoyer, ni supprimer, ni modifier un mail : l'autorisation Google est en lecture seule.

## 6. Lui parler

- Dis **« Madex »** ou **« Hey Madex »** au début de ta phrase : « Madex, ouvre Spotify », d'une traite.
- Ou dis juste « Madex », attends le bip, puis parle.
- Ou **claque deux fois des mains**, attends le bip, puis parle.
- Ou clique sur **● PARLER** dans l'interface, ou écris dans la barre du bas.

Ta demande s'arrête quand tu fais une pause d'environ 1,3 s (réglable : `SILENCE_SECONDS`).
Pendant que Madex parle, son micro est coupé pour qu'il ne s'entende pas lui-même.

**La voix.** ElevenLabs avec le modèle `eleven_flash_v2_5` : il parle français, très vite, et coûte deux fois
moins de crédits. Au premier lancement, Madex choisit une voix masculine posée (Daniel, George…). Le compteur
**VOIX** en haut de l'interface montre ton quota du mois. Quand il est épuisé, Madex te prévient une fois et passe
sur sa voix de secours gratuite jusqu'au mois suivant.
Pour choisir une autre voix : copie son identifiant depuis la Voice Library d'ElevenLabs dans `ELEVENLABS_VOICE_ID`
(`madex/config.py`).

**Le réacteur** (au centre) réagit au vrai son : les particules deviennent **blanc glacé** quand tu parles et **bleues** quand Madex répond.
L'octogone devient **orange** quand Madex attend ton accord ; chaque point en orbite est une session en cours (orange = mail important).
Les pages (À valider, Sessions, Règles, Connexions, Historique) s'ouvrent en panneau depuis le **dock** du bas ou à la voix :
« Madex, montre les sessions », « ferme le panneau ». Raccourcis : **Ctrl+K** pour écrire, **Échap** pour fermer,
**O / S / T / R** pour répondre à la demande affichée sous le cercle (une fois / session / toujours / refuser).

**Le bouton micro** : un clic ouvre l'écoute (bip), un second clic envoie tout de suite sans attendre le silence.
Vous avez 6 secondes pour commencer à parler après le bip. Quand Madex entend une phrase sans son nom,
il l'affiche discrètement sous le cercle (« entendu : … ») : pratique pour voir s'il vous comprend mal.
Le détail de ce qu'il entend est aussi dans `%LOCALAPPDATA%\MADEX\madex.log`.

**Confidentialité.** Le micro est analysé en continu, mais uniquement sur ton PC : seuls les mots qui suivent
« Madex » sont envoyés à Claude. La clé ElevenLabs reste dans le fichier `.env`, que Claude ne peut pas lire.

## 7. Ce qu'il fait sans consommer de tokens

| Tu dis | Il fait |
|---|---|
| « ouvre Teams », « lance VS Code », « ouvre WhatsApp » | Ouvre n'importe quelle appli du menu Démarrer (Microsoft Store compris) |
| « ferme Discord » | Ferme proprement (l'appli peut proposer d'enregistrer) |
| « ouvre YouTube », « va sur github » | Ouvre le site |
| « ouvre le projet portfolio » | Ouvre le projet dans VS Code |
| « mets du Daft Punk », « ma playlist chill », « l'album Discovery », « Get Lucky » | Lance sur Spotify |
| « mets mes titres likés », « mets de la musique pour bosser » | Titres likés / playlist de concentration |
| « pause », « reprends », « suivant », « précédent », « c'est quoi ce son ? » | Contrôle Spotify |
| « monte le son », « volume à 30 », « coupe le son » | Volume de la musique, ou de Windows si rien ne joue |
| « résume mes mails » | Résumé vocal des mails non lus importants (Haiku, sans outils) |

Quand Madex parle, la musique baisse toute seule puis remonte. Toutes les 3 minutes, il vérifie s'il y a un mail
**important** non lu, et te l'annonce (expéditeur et objet).

## 8. Comment il travaille

**Tu parles, l'aiguilleur trie :**
- les commandes simples (« ouvre Spotify », « ferme Discord ») sont traitées en local, sans token ;
- une petite question va à Haiku ;
- le code, les projets et la conception vont à Sonnet.

**Les sessions de code.** « Madex, sur le portfolio, ajoute un mode sombre » : une session Claude Code part en
arrière-plan dans le projet. Elle crée une branche dédiée, code, vérifie (tests, lint, build) et **propose** un commit.

**Ton accord, toujours.** Chaque action passe par un garde-fou. Selon les règles, elle passe seule, ou Madex te
demande à voix haute : « La session sur portfolio veut installer des dépendances. J'autorise, chef ? ». Tu réponds :
- « oui » (une fois), « pour la session », « toujours » (la règle change), ou « non » ;
- ou d'un clic sur la carte sous le cercle (ou dans le panneau **À VALIDER**).

Si tu ne réponds pas dans les 9 minutes, l'action est refusée.

**Les commits.** Quand une session a fini, le commit t'attend dans l'interface : fichiers, diff complet, message
modifiable (anglais, Conventional Commits). **Rien n'est commité sans ton clic**, et aucune mention Co-Authored-By
n'est jamais ajoutée.

**Le push.** Seulement quand tu le demandes (« Madex, push le portfolio »), après confirmation.

**Les règles vivantes** (onglet RÈGLES ou à la voix) : « Madex, à partir de maintenant tu peux lancer les tests sans
demander. » Il confirme, tu dis oui. « Annule la dernière règle » revient en arrière.
Le commit, le push et les destructions (`rm -rf`, `reset --hard`…) sont **verrouillés à vie**.

## 9. Exemples

- « Madex, sur le portfolio, ajoute un mode sombre avec un bouton dans la navbar »
- « Où en sont mes sessions ? » / « Qu'est-ce qu'elle a changé ? »
- « Où est géré le login dans mon-api ? » (il lit le code et répond, sans session)
- « Commit mon travail sur mon-blog » (il prépare le commit, tu valides dans l'interface)
- « Relis la PR 42 de mon-app »
- « Ouvre VS Code », « Ferme Spotify », « Nouvelle conversation », « Éteins-toi »

## 10. Où sont les choses

| Dossier | Contenu |
|---|---|
| `prompts/madex.md` | Sa personnalité et ses règles de comportement |
| `madex/config.py` | Réglages (projets, modèles, délais) |
| `%LOCALAPPDATA%\MADEX\regles.json` | Tes règles vivantes |
| `%LOCALAPPDATA%\MADEX\bonnes-pratiques.md` | Ce qu'il a appris de toi |
| `%LOCALAPPDATA%\MADEX\taches/` | Journal complet de chaque session |
| `%LOCALAPPDATA%\MADEX\madex.log` | Journal quand il tourne en arrière-plan |
| `.env` | Tes clés secrètes (ElevenLabs…) |
| `assets/` | Logo et icône de MADEX |

## 11. Dépannage

| Problème | Solution |
|---|---|
| Bandeau jaune « Claude Code trop ancien » | `claude update` |
| « Je ne suis plus connecté à Claude » | Lance `claude` dans un terminal et reconnecte-toi |
| « 0 projet » au démarrage | Vérifie `PROJECTS_DIRS` dans `madex/config.py` |
| Une action est refusée sans que tu aies rien dit | Madex était éteint ou tu n'as pas répondu dans les 9 minutes : le garde-fou refuse toujours par sécurité |
| Le relancer quand il tourne déjà | Il ouvre simplement son interface |
| Tu ne l'entends pas | Onglet CONNEXIONS : regarde « voix utilisée » et l'erreur affichée. Puis `demarrer.bat --test-voix`, et vérifie la sortie audio par défaut de Windows |
| « Clé sans les bonnes permissions » | Recrée la clé ElevenLabs avec Text to Speech + Voices : Read + User : Read |
| Spotify : « aucun appareil » | Ouvre l'application Spotify une fois, puis redemande |
| Gmail : « accès bloqué » sur la page Google | Ajoute ton adresse Gmail dans les utilisateurs test de l'écran de consentement |
| Il ne réagit pas à « Madex » | Lance `demarrer.bat --test-voix` pour vérifier le micro ; vérifie le micro par défaut de Windows |
| Il coupe ta phrase trop tôt | Augmente `SILENCE_SECONDS` dans `madex/config.py` |
| Il se réveille quand quelqu'un dit « Mad's » | Dis plutôt « Hey Madex » ; on ajustera si ça se répète |
| Un claquement de porte le réveille | Rare (il faut deux sons secs espacés de 0,2 à 0,8 s) ; signale-le, on durcira le seuil |
| L'orbe a disparu | Clic droit › « Cacher l'orbe » la masque jusqu'au prochain démarrage de Madex |
| Une mise à jour ne s'installe pas | Fenêtre Diagnostic › ligne Version : l'erreur y est indiquée ; sinon installe le zip à la main |
| Le PC rame quand de la musique passe sur les enceintes | La musique ressemble à de la voix : Madex vérifie chaque passage. Utilise un casque, ou passe `WHISPER_SPOT_MODEL` à `"tiny"` |

**Option avancée : un vrai modèle « Hey Madex ».** Si tu veux encore moins de CPU, tu peux entraîner un modèle
openWakeWord gratuit sur Google Colab (notebook « automatic_model_training » du dépôt openWakeWord, ~1 h).
Place le fichier obtenu dans `models/madex.onnx`, puis `pip install openwakeword` : Madex l'utilisera automatiquement.
