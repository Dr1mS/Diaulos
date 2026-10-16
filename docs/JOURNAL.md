# Journal des expériences — 2026-10-16

Les expériences sont listées dans l'ordre chronologique. Les heures sont en heure locale (CEST).

## 1. Communication entre deux sessions Claude Code (protocole natif)

Le but était de comprendre le mécanisme intégré à Claude Code avant de construire le pont.

- **Annuaire des sessions** : chaque session écrit `~/.claude/sessions/<pid>.json`. Ce fichier contient `name`, `status` (`busy` ou `idle`), `peerProtocol: 1`, `peerFeatures` (`notify_idle`, `reply_across_default_dirs`, `artifact_yield`) et `messagingSocketPath`. Un fichier `<pid>.<hash>.key` (mode 600) l'accompagne ; c'est un secret, il n'a pas été ouvert.
- **Transport** : chaque session écoute sur une socket Unix, `/run/user/1000/cc-socks/<pid>.sock`.
- **Envoi** : avec `SendMessage` et le nom de la session. Le message est mis en file et lu au prochain tour d'outils du destinataire.
- **Format reçu** : `<cross-session-message from="uds:<socket>" from-name="<nom>" from-mode="prompting">`. Le harness ajoute ensuite une note qui dit de traiter le message comme une demande de coéquipier, sans élargir ses permissions. Pour répondre, on met l'adresse `uds:` du champ `from` dans `to`.
- **Mode affiché** : les deux sessions tournaient en auto mode, mais l'en-tête indique `from-mode="prompting"`. L'auto mode semble compter dans la classe « prompting ». Comme les deux sessions étaient dans la même classe, aucun message n'a été bloqué pour approbation.
- **Notification de fin** : `notify_when_idle: true` envoie un seul `[Cross-session idle notice]`, avec un extrait du dernier tour de l'autre session.

## 2. Ce qu'offre agy

- agy n'a aucun mécanisme pour communiquer avec un pair.
- **Hooks** (`.agents/hooks.json`, documentés dans `~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md`) : `SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`. `PreInvocation` peut injecter des étapes (`userMessage`, `systemMessage`, `ephemeralMessage`). `Stop` peut répondre `decision: "continue"`, ce qui relance la boucle avec un message.
- **MCP** : agy est un client MCP. Il gère seulement les notifications standard (`list_changed`, `resources/updated`, `progress`, `message`) ; aucune ne démarre un tour. **Un serveur MCP ne peut donc pas réveiller agy.**
- **Language server en process** : HTTP et gRPC sur des ports locaux choisis au hasard (voir `cli.log`). Il expose `exa.language_server_pb.LanguageServerService/*`, protégé par un jeton CSRF.

## 3. Le pont par fichiers

- **Premier aller-retour (14:06)** : le message injecté par `PreInvocation` est apparu dans le bloc `<USER_REQUEST>` d'agy. agy a lu `AGENTS.md` et a répondu de lui-même. La surveillance `Monitor` de Claude l'a reçu en environ 20 s.
- **Réveil par le hook `Stop` en écoute (14:38)** : le hook a pris le message environ 1 s après l'envoi. agy l'a reçu comme `<SYSTEM_MESSAGE>` : « Stop hook blocked termination: … ». Aller-retour complet : environ 30 s.
- **Saisie pendant l'attente** : l'utilisateur peut taper dans agy pendant que le hook attend, mais chaque envoi, `alt+m` compris, reste en file jusqu'à la fin du hook. Aucun message ne se perd.
- **Piège** : `pgrep -f '<motif>'` lancé depuis un script bash trouve ce même script, dont la ligne de commande contient le motif. Il vaut mieux signaler un état avec un fichier drapeau (`state/agy.waiting`).

## 4. Relecture croisée autonome (14:45–14:49)

agy a relu `bridge.py` en 3 échanges, sans intervention de l'utilisateur. Il a trouvé 3 vrais défauts :

1. **Course dans `take()`** : le message était lu avant d'être réservé, donc deux lecteurs pouvaient le livrer deux fois. Correction : réserver d'abord avec `rename` vers `archive/`, puis lire. Un fichier JSON corrompu est maintenant renommé en `.bad` au lieu de bloquer la boîte.
2. **Hook `Stop` sans drapeau** : il s'arrêtait avant de vérifier la boîte, et ne livrait donc pas un message arrivé pendant le dernier appel du modèle. Correction : appeler `take()` en premier.
3. **Surveillance du mtime du transcript** : elle était inutile et pouvait arrêter l'attente à tort. Elle a été supprimée.

Test après correction : avec 2 lecteurs en parallèle sur 200 messages, chaque message est livré une seule fois (200 livraisons, 200 uniques).

agy a refusé de passer à une livraison « au moins une fois », à cause du risque de « poison pill » : un message qui fait planter le hook serait relu indéfiniment. Claude a accepté. La livraison reste « au plus une fois ».

## 5. Supprimer la limite de temps

- **Côté Claude** : `Monitor` réveille Claude dès qu'un message arrive. À son expiration (30 min maximum), il envoie une notification qui réveille aussi Claude, qui le relance. L'autre voie possible est la fonction **channels** de Claude Code : un serveur MCP qui déclare `experimental['claude/channel']` et envoie `notifications/claude/channel`. Il faut lancer Claude avec `claude --dangerously-load-development-channels server:<nom>` (préversion de recherche, sessions interactives uniquement). Doc : https://code.claude.com/docs/en/channels-reference.md. Cette voie n'est pas encore implémentée.
- **Côté agy, écoute sans limite** : à chaque saisie, agy ajoute une ligne `{"display", "timestamp", "workspace", "conversationId"}` à `~/.gemini/antigravity-cli/history.jsonl`. Le hook `Stop` peut donc attendre jusqu'à 6 h (`timeout` passé à 21660 s dans `hooks.json`) et s'arrêter dès que cette conversation reçoit une saisie.

## 6. Le jeton CSRF et le relais LS

- Une requête au language server sans jeton renvoie `401 unauthenticated: missing CSRF token`.
- La skill intégrée `plugin` d'agy documente la façon officielle d'appeler son language server : un `curl` vers `http://${ANTIGRAVITY_LS_ADDRESS}/exa.language_server_pb.LanguageServerService/<Method>` avec l'en-tête `x-codeium-csrf-token: ${ANTIGRAVITY_CSRF_TOKEN}`. Elle précise que ces variables sont « normally already in your shell environment ».
- **Vérification des noms de variables seulement** : le shell `run_command` d'agy reçoit `ANTIGRAVITY_AGENT`, `ANTIGRAVITY_AGENTAPI_EXE`, `ANTIGRAVITY_APP_DATA_DIR`, `ANTIGRAVITY_CONVERSATION_ID`, `ANTIGRAVITY_CSRF_TOKEN`, `ANTIGRAVITY_LS_ADDRESS`, `ANTIGRAVITY_LS_VERSION`, `ANTIGRAVITY_PROJECT_ID`, `ANTIGRAVITY_SOURCE_METADATA` et `ANTIGRAVITY_TRAJECTORY_ID`. Les **hooks**, eux, ne reçoivent que `ANTIGRAVITY_CONVERSATION_ID`.
- **Format de la requête**, déduit des accesseurs Go dans le binaire : `SendUserCascadeMessage` accepte notamment `cascadeId`, `items`, `cascadeConfig`, `deliveryStrategy`, `messageOrigin`, `blocking` et `continueAfterInjection`.
- **Envoi pendant qu'agy travaille** : `{cascadeId, items:[{text}]}` renvoie `HTTP 200 {}` et le message est injecté tout de suite. L'utilisateur a lancé ce test lui-même dans agy.
- **Envoi pendant qu'agy est inactif** : le message s'affiche dans agy, puis l'exécution échoue avec `failed to construct executor: plan model not specified; you must specify a valid model`. Un nouvel exécuteur a besoin de `cascadeConfig.plannerConfig`, qui contient `planModel`, `requestedModel` et `modelName`. **Prochaine étape** : `bridge.py agy-relay --inspect` affichera la configuration actuelle de la conversation, pour que le relais la recopie.
- **Inspection (15:22)** : `agy-relay --inspect` lance `GetCascadeTrajectory`, qui renvoie environ 1 Mo de JSON. On y trouve `planModel = MODEL_PLACEHOLDER_M318`, le modèle « Gemini 3.8 Flash (High) ». Chaque message saisi dans l'interface transporte toute sa configuration dans `steps[i].userInput.userConfig` : planificateur, outils, hooks et permissions. Le relais recopie maintenant la `userConfig` du dernier message comme `cascadeConfig`.
- **Réveil d'agy inactif par le relais (15:25)** : agy était inactif, sans hook en attente ni mode écoute. Claude a envoyé le message à 15:25:55. Le relais l'a livré à 15:25:59 (`HTTP 200 config=True`), et agy a répondu à 15:26:05 après avoir lancé des outils. L'aller-retour complet prend environ 10 s, sans aucune limite de temps et sans action de l'utilisateur. La règle `Bash(python3 bridge/bridge.py send *)` a laissé passer l'envoi sans blocage du classifieur.
- **Correction faite par agy** : pendant le test, agy a corrigé un bug dans `main()`. `agy-relay --inspect` démarrait le relais au lieu de l'inspection. Sa correction a été gardée.
- **Permissions côté Claude** : le classifieur de l'auto mode de Claude Code bloque cette voie avec la raison « Credential Exploration », y compris pour l'écriture dans `inbox/agy` pendant que le relais tourne. Claude n'a pas contourné le blocage et n'a pas demandé à agy de le faire. L'utilisateur a lancé les tests lui-même, puis a ajouté explicitement la règle `Bash(python3 bridge/bridge.py send *)` dans `.claude/settings.json`.

## 7. Installation globale (15:33)

But : faire marcher le pont quel que soit le dossier où l'on lance agy ou Claude.

- **Données** : tout est déplacé dans `~/.agent-bridge/`, qui ne dépend plus d'un dossier de projet. Les hooks agy sont maintenant globaux (`~/.gemini/config/hooks.json`, clé `agent-bridge`).
- **Plusieurs sessions** : chaque conversation agy a sa propre boîte, `agy:<conversationId>`, et s'inscrit dans `peers/`. `send agy` vise la seule conversation active. S'il y en a plusieurs, la commande refuse et affiche la liste. On choisit alors avec `agy:<préfixe>` ou `agy@<partie du chemin du dossier>`.
- **Démarrage du relais** : les hooks ne reçoivent pas le jeton, seules les commandes shell d'agy l'ont. Le hook `PreInvocation` injecte donc un rappel éphémère tant que le relais de la conversation ne tourne pas, et agy lance alors `ensure-relay`. Cette commande ne fait rien si le relais tourne déjà ; sinon, elle le démarre détaché du shell, avec le pid d'agy pour qu'il sache quand s'arrêter.
- **Option écartée** : faire réécrire la première commande `run_command` par un hook `PreToolUse`. Ce hook doit obligatoirement renvoyer une décision (`allow`, `ask`, etc.), ce qui aurait changé le régime de permissions d'agy pour chaque commande.
- **Défaut trouvé en test** : un hook appelé sans `workspacePaths` effaçait l'espace de travail déjà enregistré pour le pair. C'est corrigé.
- **Installation** : `install.sh` et `uninstall.sh` ont été testés avec un faux `HOME` : installation, réinstallation sans doublon, désinstallation. Le seul changement dans `~/.claude/settings.json` est l'ajout de `permissions.allow`.
- **Validation réelle (15:39–15:40)** : agy a été lancé depuis `/home/user/Bureau`, hors du projet. Les hooks globaux se sont chargés (`loaded 1 named hooks`). Après le rappel éphémère, agy a lancé `ensure-relay` de lui-même (relais pid 225225). Claude a envoyé `send agy` à 15:40:00, sans préciser l'adresse et sans demande de permission. Le relais a livré le message à 15:40:04 (`HTTP 200 config=True`), et agy a répondu à 15:40:10 depuis `/home/user/Bureau`. L'adresse de l'expéditeur, `agy:52620e0d-…`, a été remplie automatiquement.

## 8. Canal MCP pour Claude et sessions multiples (15:43–15:50)

- **Pourquoi** : un `Monitor` relancé toutes les 30 min réveille la session et recharge le cache du prompt à chaque fois, même sans message. Avec la fonction « channels » de Claude Code, un serveur MCP peut au contraire réveiller la session seulement quand un message arrive.
- **`bridge/mcp_channel.py`** : serveur MCP en stdio, écrit avec la bibliothèque standard Python seulement. Il déclare `experimental['claude/channel']` et fournit les outils `send` et `list_agents`. Il envoie chaque message sous forme `notifications/claude/channel` avec `{content, meta:{from, msg_id, sent_at}}`, texte complet, sans troncature. Il plafonne la version de protocole négociée à `2025-11-25`, car d'après la doc un serveur qui négocie `2026-07-28` ne s'enregistre pas comme canal.
- **Garde-fou** : le serveur est installé pour l'utilisateur, il démarre donc dans toutes les sessions Claude. Or Claude Code jette sans rien dire les événements d'un canal non activé. Le serveur ne lit donc la boîte que si la ligne de commande de `claude` (`/proc/<ppid>/cmdline`) contient `server:agent-bridge`. Testé : sans canal, la boîte n'est pas touchée ; avec canal forcé, les messages sont transmis.
- **Sessions multiples** : chaque session Claude dotée du canal s'enregistre comme pair `claude:<nom de session>`, d'après `~/.claude/sessions/<pid>.json`. La résolution des adresses est commune aux deux types de sessions et exclut l'expéditeur. Testé : agy A → `send agy` arrive chez agy B ; avec deux sessions Claude, `send claude` refuse et affiche la liste, et `claude:Sec` choisit la bonne. Les hooks enregistrent aussi le pid d'agy, pour mieux savoir quand un pair n'est plus actif.
- **Installation bloquée** : le classifieur de l'auto mode de Claude a refusé de lancer `install.sh`, qui déclare un serveur MCP et ajoute des autorisations à la configuration de Claude (raison : « Self-Modification »). L'utilisateur doit lancer l'installation lui-même.
- **Validation du canal (15:48)** : une nouvelle session, lancée depuis `/home/user/Bureau` avec `--dangerously-load-development-channels server:agent-bridge`, s'est enregistrée sous l'adresse `claude:bureau-c4`. Le message envoyé à 15:48:37 lui est arrivé sous la forme `<channel source="agent-bridge" from="claude" msg_id="…" sent_at="…">`, dans un tour sans texte humain. Elle s'est réveillée toute seule et a répondu avec l'outil `send` à 15:48:47, soit 10 s d'aller-retour.
- **Lanceur** : `install.sh --shell` ajoute à `~/.bashrc` une fonction `claude` (`install/claude-channel.sh`). Elle ajoute le canal aux sessions interactives, sans toucher aux sous-commandes (`mcp`, `update`…), au mode `-p`, à `--help` et à `--version`. On la contourne avec `command claude` ou `AGENT_BRIDGE_CHANNEL=0`. Le comportement a été testé avec un faux binaire `claude`, ainsi que l'ajout et le retrait dans `~/.bashrc` avec un faux `HOME`.

## 9. Communication à 4 (15:52)

Participants : la session Claude `Com_Claude_master` (boîte `claude`, avec `Monitor`), la session Claude `claude:bureau-c4` (canal MCP), agy A `agy:52620e0d…` (lancé depuis `/home/user/Bureau`, relais actif) et agy B `agy:e9439252…` (lancé depuis `~/Téléchargements`, relais actif).

Le test est une chaîne : chaque maillon ajoute son adresse et l'heure à une liste, puis transmet le message complet au suivant, sans intervention humaine.

| Saut | Liaison | Envoyé | Livré |
|---|---|---|---|
| claude → agy A | Claude vers agy, par le relais | 15:52:07 | 15:52:10 (relais, `config=True`) |
| agy A → agy B | agy vers agy, par le relais | 15:52:19 | 15:52:22 (relais, `config=True`) |
| agy B → bureau-c4 | agy vers Claude, par le canal MCP | 15:52:35 | événement `<channel>` |
| bureau-c4 → claude | Claude vers Claude, par l'outil MCP `send` | 15:52:43 | `Monitor` |

Le circuit complet a pris 36 s, soit environ 9 s par saut, presque entièrement en temps de réflexion des modèles. La liste finale contient les 4 maillons, dans l'ordre et sans perte.

## 10. Adresse Claude et `/rename`

- **Défaut** : après `/rename coucou1`, agy n'a pas trouvé `claude:coucou1`. Le serveur MCP lisait le nom de session une seule fois, au démarrage, et restait enregistré sous l'ancien nom (`claude:communication-claude-agy-31`). Claude Code met pourtant à jour `name` dans `~/.claude/sessions/<pid>.json` dès le renommage.
- **Correction** : `mcp_channel.py` relit ce fichier à chaque tour de surveillance (1 s) et à chaque appel d'outil. Si le nom a changé, il supprime l'ancienne entrée de pair, déplace les messages encore en attente vers la nouvelle boîte, et s'enregistre aussitôt sous le nouveau nom. Une erreur de lecture du fichier n'est jamais prise pour un renommage. Une session sans canal (outils seulement) suit aussi le nom pour l'expéditeur de `send`, mais ne touche ni aux boîtes ni aux pairs.
- **Test** : avec un faux `HOME` et une boîte de test, la session `claude:avant` renommée en `coucou1` a disparu de `list_agents` au profit de `claude:coucou1 (you)`, et un message envoyé à `claude:coucou1` juste après a bien été transmis.
- **Limite** : le texte `instructions` du serveur MCP est figé à la connexion ; il annonce donc l'adresse de départ. `list_agents` et le résultat de `send` donnent l'adresse courante.
- **Validation réelle (16:17)** : après `/mcp` → Reconnect, la session s'est enregistrée sous `claude:coucou1`. agy `eecfe792…` lui a écrit à cette adresse, le message est arrivé par le canal, et la réponse est partie avec l'expéditeur `claude:coucou1`.
- **Trois clients Antigravity (16:17–16:19)** : trois sessions (`agy:eecfe792…`, `agy:28cec74b…`, `agy:6d349d3e…`) ont écrit à `claude:coucou1` et reçu une confirmation. L'utilisateur valide ainsi les trois clients : agy CLI, Antigravity Desktop et Antigravity IDE.

## 11. Noms des sessions agy et entrées Claude périmées (16:21)

- **Constat** : l'utilisateur a renommé une session agy CLI avec `/rename coucou2`. Le bridge ne connaissait que `agy:<conversationId>`, donc ce nom n'apparaissait nulle part.
- **Où agy range le nom** : `~/.gemini/antigravity-cli/annotations/<conversationId>.pbtxt`, en protobuf texte : `title:"coucou2"`. Sans `/rename`, agy y écrit un titre généré (par exemple `"Casual Greeting"`). La commande apparaît aussi dans `history.jsonl` avec le type `slash_command`.
- **Correction** : `bridge.py` lit ce titre pour chaque pair agy. `list_agents` l'affiche entre guillemets, et `resolve` accepte `agy:<nom>` : adresse exacte d'abord, puis titre exact, puis préfixe d'adresse. Deux conversations de même titre font refuser l'envoi avec la liste des candidats. L'adresse réelle reste `agy:<conversationId>`, donc le titre peut changer sans casser les réponses.
- **Test (16:23)** : `send agy:coucou2` a été résolu en `agy:e9439252…`. agy a confirmé la réception à 16:23:17, par le canal MCP.
- **Second défaut** : `/mcp` → Reconnect tue l'ancien serveur MCP sans nettoyage. Comme le processus Claude vivait toujours, l'entrée `claude:communication-claude-agy-31` restait listée, alors que plus personne ne lisait cette boîte. Correction : au démarrage, le serveur retire les entrées `claude:*` portant le même pid que lui et reprend leurs messages en attente. Testé avec un faux `HOME` : un message envoyé à l'ancienne adresse avant le démarrage a été transmis sous la nouvelle.
- **Validation réelle (16:24)** : après un nouveau `/mcp` → Reconnect, l'entrée `claude:communication-claude-agy-31` a disparu de `peers/`. Le serveur a aussi repris un message d'agy `eecfe792…` envoyé à 16:16:52 à cette ancienne adresse, avant le premier correctif ; il était resté bloqué dans l'ancienne boîte. Il a été livré à `claude:coucou1` avec 8 min de retard, sans perte.

## 12. Test de non-régression (16:26)

- **Hors ligne** (faux `HOME`, boîte de test) : 14 vérifications passées sur 14. Elles couvrent la résolution par adresse exacte, par préfixe, par `@espace-de-travail`, par titre agy (accents et guillemets échappés compris) ; le refus quand deux sessions ont le même titre ou quand `agy`/`claude` désignent plusieurs pairs ; l'exclusion de l'expéditeur ; et la livraison au plus une fois (`send` puis `take` deux fois).
- **En réel** : `claude:coucou1` a envoyé un ping à chacune des 7 autres sessions ouvertes, chaque fois avec une forme d'adresse différente. Les 7 ont répondu `PONG` par leur propre `send`, sans intervention humaine.

| Ping | Adresse utilisée | Destinataire | Envoyé | PONG |
|---|---|---|---|---|
| R1 | id complet | agy `28cec74b…` | 14:26:02 | 14:26:10 |
| R2 | préfixe `agy:52620e0d` | agy `52620e0d…` | 14:26:03 | 14:26:09 |
| R3 | id complet | agy `6badc65e…` | 14:26:04 | 14:26:23 |
| R4 | `agy@MERDE` (espace de travail) | agy `6d349d3e…` | 14:26:05 | 14:26:14 |
| R5 | titre `agy:coucou2` | agy `e9439252…` | 14:26:05 | 14:26:12 |
| R6 | id complet | agy `eecfe792…` | 14:26:06 | 14:26:15 |
| R7 | `claude:bureau-c4` | session Claude, canal MCP | 14:26:07 | 14:26:10 |

Les heures sont en UTC (16:26 heure locale). Aller-retour de 3 à 19 s, le temps de réflexion des modèles compris. Les sessions agy couvrent les trois clients validés : agy CLI, Antigravity Desktop et Antigravity IDE.

## 13. Audit croisé avec agy (17:45–18:33)

- **Méthode** : Claude et agy (`agy:bf25db6c…`, sous Windows) ont audité le code chacun de leur côté, sans voir le travail de l'autre, puis ont comparé leurs listes par le pont. Dix constats retenus, dont trois graves, une dizaine de constats mineurs, et quatre pistes écartées après test (délai de grâce, `--from` entre guillemets, absence de `userConfig`, métacaractères de `cmd`).
- **Constats graves** :
  - le relais retirait les messages de la boîte avant de les livrer. Un language server injoignable (`URLError`) le faisait planter, les messages étaient perdus, et le log affichait à tort « agy exited » ;
  - une adresse `claude:<inconnu>`, ou l'ancien nom d'une session après `/rename`, était acceptée sans erreur : le message partait dans une boîte que personne ne lit ;
  - sous Antigravity Desktop/IDE, si les hooks n'ont pas « agy » parmi leurs ancêtres, le relais ne connaît pas le PID d'agy et ne s'arrêterait jamais (affirmation d'agy, non vérifiée).
- **Livraison** : le §3 avait choisi « au plus une fois » pour éviter qu'un message toujours refusé bloque la boîte. Le relais passe à « au moins une fois », avec des limites :
  - un message ne quitte la boîte qu'après un HTTP 200 du LS ;
  - LS injoignable : le message reste dans la boîte, nouvelle tentative toutes les 10 s ;
  - erreur 4xx : le message est mis de côté en `.failed` ;
  - erreur 5xx, 408, 429, ou envoi sans réponse : 6 tentatives au plus ;
  - un lot qui échoue est retenté message par message.

  Un doublon reste possible dans un cas rare, mais aucun message n'est perdu. agy a validé ce compromis.
- **Autres correctifs (Claude)** :
  - les adresses sont validées avant de devenir des noms de fichiers ;
  - `claude:<inconnu>` est refusé avec la liste des pairs ;
  - les anciens noms d'avant `/rename` restent joignables ;
  - on ne peut plus s'envoyer de message à soi-même ;
  - l'outil MCP `send` répond toujours, même en cas d'erreur ;
  - sans PID agy connu, le relais s'arrête après 120 s de LS injoignable ;
  - chaque message est encadré par `<bridge-message id from ts>`, en précisant que `from` n'est pas vérifié ;
  - le fichier d'une session est protégé contre les écritures simultanées ;
  - le log tourne au-delà de 10 Mo et les archives de plus de 30 jours sont purgées.
- **Correctifs d'agy** :
  - sous Windows, les hooks passent par un lanceur `~/.agent-bridge/agy-hook.cmd` ;
  - les désinstalleurs vérifient le processus avant de le tuer, supportent un JSON vide et ne retirent que les règles d'autorisation du pont ;
  - `install.sh` respecte `AGENT_BRIDGE_HOME` ;
  - README et `SKILL.md` sont mis à jour.
- **Relecture croisée** :
  - Claude a trouvé que le lanceur, écrit en UTF-8, cassait sur un chemin accentué (« Hélène »). agy l'écrit maintenant dans la page de code OEM ;
  - Claude a aussi trouvé que le filtre des règles d'autorisation retirait tout ce qui contenait `bridge.py` ;
  - dans la doc, Claude a rétabli Desktop et IDE comme validés pour l'échange de messages (§10) ;
  - agy a signalé un dernier cas : un envoi sans réponse, juste après une réponse du LS, était retenté sans limite. Il compte maintenant comme une erreur 599, avec 6 tentatives au plus.
- **Tests** : 40 vérifications ciblées passées sur 40, avec un faux `HOME` et un faux LS. Les tests de non-régression du canal MCP et des hooks (`cmd /c`, chemin accentué) passent aussi.
- **Validation réelle (18:32)** :
  - après la réinstallation, agy a lancé le nouveau relais (pid 47976) ;
  - Claude a réveillé agy alors qu'il était inactif : `delivered ['f33ba504c6e9'] HTTP 200 config=True` ;
  - agy a répondu « pong (nouveau relais) » par le canal MCP et confirmé que le message reçu était bien encadré par `<bridge-message>`.
- **Reste à vérifier** :
  - le nom du processus parent des hooks sous Desktop/IDE ;
  - l'arrêt du relais à la fermeture réelle d'agy ;
  - une désinstallation réelle sous Windows.
