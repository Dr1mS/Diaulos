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
