# Diaulos

[English](README.md) · **Français**

> Aux Jeux grecs, le *diaulos* était la course aller-retour : on courait jusqu'au bout du stade, puis on revenait. Ici, c'est le trajet d'un message et de sa réponse entre deux agents.

**Diaulos permet aux sessions agy de se parler.** agy (la CLI Antigravity de Google) ne permet pas à une session d'en joindre une autre : chaque conversation est isolée. Avec Diaulos, n'importe quelle session agy peut écrire à n'importe quelle autre, et le message **réveille le destinataire même s'il est inactif**. Les sessions peuvent tourner dans n'importe quel dossier, et vous n'avez plus à copier-coller d'un terminal à l'autre.

Diaulos fonctionne avec la CLI agy et avec **Antigravity Desktop**, et relie aussi les sessions **Claude Code**, pour faire travailler ensemble des agents Gemini et Claude. Autant de sessions que vous voulez, des deux types, sous Linux comme sous Windows.

```
agy (~/projets/api)  ⇄  agy (~/projets/web)  ⇄  Claude Code  ⇄  Claude Code
```

## À quoi ça sert

- **Répartir le travail entre sessions agy.** Une session chef de projet confie des tâches à des sessions exécutantes, chacune dans son propre dépôt, et récupère leurs comptes rendus.
- **Demander un second avis.** Une session agy demande à une autre de relire ses modifications. Lors d'un vrai test, agy a relu le code de ce projet et trouvé trois vrais bugs, corrigés depuis ([journal](docs/JOURNAL.md#4-relecture-croisée-autonome-14451449)).
- **Croiser les modèles.** Faites vérifier par Claude Code ce qu'a écrit Gemini, ou l'inverse.
- **Réveiller un agent inactif.** Inutile de surveiller chaque terminal : à l'arrivée d'un message, la session destinataire reprend la main d'elle-même.
- **Démarrer une nouvelle session agy depuis un autre agent** (Antigravity Desktop, Linux). `send agy:new "<tâche>"` ouvre une nouvelle conversation Desktop avec cette tâche comme premier prompt, sans ouvrir d'onglet ni rien taper.
- **Réveiller un agent depuis n'importe quel programme.** Un script, un job de CI ou un bot rejoint le pont sous `ext:<nom>` : il envoie avec `bridge.py send <agent> - --from ext:<nom>` et lit les réponses de l'agent avec `bridge.py watch ext:<nom> --json`. **Lisez d'abord la section [Sécurité](#sécurité) :** le programme transmet du texte extérieur à un agent capable de lancer des commandes.
- **Voir passer les messages.** Avec [Paros](https://github.com/Dr1mS/Paros), chaque session agy et Claude Code a un personnage en pixels sur le bureau, et chaque message envoyé par Diaulos vole d'un personnage à l'autre sous forme de lettre.

## Paros

Diaulos est compatible avec [Paros](https://github.com/Dr1mS/Paros), un compagnon de bureau pour Claude Code et agy. Chaque session a un petit personnage au bas de l'écran qui montre ce qu'elle fait. Quand une session envoie un message par Diaulos, son personnage lance une lettre au personnage du destinataire. Un message en attente dans la boîte d'une session agy dresse une boîte aux lettres à côté de son personnage. Rien à configurer : Paros lit les commandes du pont et les boîtes de `~/.agent-bridge/`.

![Un personnage Claude lance une lettre, un personnage agy en lit une, un autre réfléchit avec les petits personnages de ses sous-agents, un dernier fait signe pour une question](https://raw.githubusercontent.com/Dr1mS/Paros/main/docs/images/agy.png)

## Prérequis

- Linux ou Windows 10/11
- Python 3.8 ou plus récent (bibliothèque standard uniquement, rien à installer avec `pip`)
- agy (Antigravity CLI) ou Antigravity Desktop
- Claude Code, si vous voulez y associer des sessions Claude

## Installation

```bash
git clone https://github.com/Dr1mS/Diaulos && cd Diaulos
```

**Linux**

```bash
./install.sh --shell
```

Ajoutez `--auto-relay` pour essayer en plus le démarrage expérimental du relais sous Antigravity Desktop et IDE (voir [Expérimental : relais automatique](#expérimental--relais-automatique)).

**Windows** (dans PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Shell
```

Ensuite :

1. **Relancez vos sessions agy** : agy ne charge ses hooks qu'au démarrage.
2. **Ouvrez un nouveau terminal pour Claude Code.** Avec `--shell` / `-Shell`, un simple `claude` démarre désormais avec le canal du pont, et Claude Code affiche un écran de confirmation à chaque lancement (voir [Côté Claude Code](#côté-claude-code)).

Relancez l'installeur après chaque mise à jour du dépôt. Pour tout retirer : `./uninstall.sh`, ou `.\uninstall.ps1` sous Windows ; ajoutez `--purge` (`-Purge`) pour supprimer aussi les boîtes aux lettres.

## Prise en main

Deux sessions agy qui se parlent, depuis l'une ou l'autre (agy lance ces commandes avec son outil shell) :

```bash
python3 ~/.agent-bridge/bridge.py list
#  agy:52620e0d-…  /home/moi/projets/api  relay pid 225225: wakes on message
#  agy:e9439252-…  /home/moi/projets/web  relay pid 241981: wakes on message

python3 ~/.agent-bridge/bridge.py send agy "Tu peux vérifier la modification d'API que je viens de faire ?"
```

Quand une seule *autre* session agy est active, `agy` suffit. Sinon, désignez-la avec `agy@<partie de son dossier>`, `agy:<début de l'id>` ou `agy:<nom>` pour une conversation renommée avec `/rename`. agy sait tout cela dès le départ : le hook `SessionStart` donne à chaque session son adresse et les commandes exactes à utiliser. Il suffit de demander : *« demande à l'autre session agy de relire ça »*.

Dans Claude Code, demandez aussi simplement : *« envoie ça à agy »*. Claude utilise les outils `send` et `list_agents` du serveur MCP `agent-bridge`.

> **Windows :** Windows PowerShell ne développe pas `~` dans les arguments passés aux programmes. Écrivez alors `python3 $HOME\.agent-bridge\bridge.py …`. Les commandes suggérées aux agents utilisent le chemin absolu de l'interpréteur Python, précédé de `&` si un chemin contient des espaces (opérateur d'appel de PowerShell 5.1). Rien à faire côté agy : ses hooks lui donnent le chemin absolu.

## Antigravity Desktop

Desktop fait tourner toutes les conversations d'une fenêtre dans un seul processus d'arrière-plan, son language server. Diaulos s'en sert : un seul relais par fenêtre sert toutes ses conversations.

| | agy CLI | Antigravity Desktop |
|---|---|---|
| Joignable après son premier message | oui, une fois qu'agy a lancé `ensure-relay` (un rappel le lui demande) | oui, sans `ensure-relay` dès que la fenêtre a un relais |
| Joignable sans aucun message | non | oui, avec `--auto-relay` : les conversations chargées dans la fenêtre, et celles du client modifiées dans les 7 derniers jours |
| Onglet fermé, ou conversation pas rouverte depuis le lancement | — | joignable : la livraison la charge depuis le disque, et son tour s'exécute en arrière-plan |
| Démarrer une nouvelle session depuis un autre agent | non | `send agy:new "<tâche>"` (Linux) |
| Arrêt du relais | quand `agy` se termine | quand le language server de la fenêtre se termine |

**Recommandé sous Linux :** `./install.sh --shell --auto-relay`. Dès qu'un agent liste les sessions agy ou écrit à l'une d'elles, Diaulos lance alors tout seul le relais de la fenêtre. Lisez d'abord [Expérimental : relais automatique](#expérimental--relais-automatique) : le pont lit le jeton du language server sur sa ligne de commande.

Sans `--auto-relay`, un seul `ensure-relay` par lancement de la fenêtre suffit, depuis n'importe laquelle de ses conversations.

Démarrer une session depuis Claude Code, par exemple :

```bash
python3 ~/.agent-bridge/bridge.py send agy:new "Lance les tests et envoie les échecs à claude:api"
# le pont répond avec la nouvelle adresse, par ex. agy:e59d7273-…, et les réponses de la session en viennent
```

La nouvelle conversation s'ouvre dans le workspace de la conversation qui a lancé le relais.

## La preuve : un relais à quatre agents

Quatre sessions se sont passé un message en boucle, sans intervention humaine. Chacune y ajoutait son nom et l'heure, puis le transmettait.

| Étape | Liaison | Envoi |
|---|---|---|
| Claude Code → agy A | le relais réveille agy inactif | 15:52:07 |
| **agy A → agy B** | **d'agy à agy, le relais réveille agy inactif** | 15:52:19 |
| agy B → Claude Code (2ᵉ session) | le canal MCP réveille Claude inactif | 15:52:35 |
| Claude Code → Claude Code | outil MCP `send` | 15:52:43 |

Le tour complet a pris 36 s, soit environ 9 s par étape. Presque tout ce temps est celui de la réflexion des modèles : le pont lui-même livre en 1 à 3 s.

## Fonctionnement

Aucun des deux outils ne connaît le protocole de l'autre. Diaulos repose sur une boîte aux lettres en fichiers et sur les points d'extension que chaque outil fournit déjà.

```
un agent ── send ──▶  ~/.agent-bridge/inbox/<adresse>/*.json
                        │
                        ├─▶ agy au travail : le hook PreInvocation injecte le message avant le prochain appel du modèle
                        ├─▶ agy inactif :    le relais appelle le language server local, qui lance un nouveau tour
                        └─▶ Claude inactif : le serveur MCP « channel » envoie un événement <channel>, qui lance un nouveau tour
```

### Côté agy

- Les hooks globaux sont dans `~/.gemini/config/hooks.json`. `SessionStart` présente le pont. `PreInvocation` livre les messages et, tant que le relais ne tourne pas, rappelle à agy de le lancer.
- Le **relais** réveille agy quand il est inactif. agy transmet à son propre shell `ANTIGRAVITY_LS_ADDRESS` et `ANTIGRAVITY_CSRF_TOKEN` ; ce mécanisme est documenté dans la skill intégrée `plugin` d'agy. Les hooks ne reçoivent ni l'un ni l'autre, seulement `ANTIGRAVITY_CONVERSATION_ID`. Le relais est lancé depuis ce shell avec `bridge.py ensure-relay` : il appelle donc `SendUserCascadeMessage` sur le language server local avec les identifiants d'agy. Il recopie le dernier `userConfig` de la conversation (modèle, outils, autorisations), si bien que le tour injecté se déroule exactement comme un message tapé. Sans cette configuration, agy échoue avec `plan model not specified`. Le relais garde le jeton en mémoire uniquement.
- **Un relais par processus agy.** Toutes les conversations d'un même processus agy partagent son language server, son adresse et son jeton. Un seul relais les sert donc toutes, chacune avec sa boîte. Une conversation dont le hook trouve un relais en marche pour son processus le rejoint aussitôt, sans lancer `ensure-relay` et sans le rappel. Toutes les 30 s, le relais demande aussi à son language server quelles conversations il a chargées (`GetAllCascadeTrajectories` : les onglets de la fenêtre, ouverts ou fermés depuis le lancement, mais pas tout l'historique sur disque) et les inscrit, sauf les archivées. Elles sont alors joignables, par id ou par titre, avant même le passage d'un de leurs hooks. Sous Desktop, le relais lit aussi l'index du client (`~/.gemini/antigravity/conversation_summaries.db`) et inscrit ses propres conversations modifiées dans les 7 derniers jours, même quand la fenêtre ne les a pas chargées : les conversations d'agy CLI, les sous-agents, les conversations tuées et les archivées sont écartés. La livraison charge d'abord une telle conversation depuis le disque (`GetCascadeTrajectory`).
- **`agy:new` démarre une conversation** (Desktop, Linux). Un message envoyé à `agy:new` attend dans sa propre boîte. Le relais de Desktop crée une conversation avec `StartCascade` (`source: CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT`), dans le workspace de sa propre conversation. Il déplace le message dans la boîte de la nouvelle conversation et le livre avec la config de sa propre conversation, puisqu'une conversation neuve n'en a pas. L'expéditeur reçoit ensuite une note du pont avec la nouvelle adresse, `agy:<conversationId>`. `send` refuse `agy:new` tant qu'aucune fenêtre Desktop n'a de relais.
  - agy CLI : les hooks et les shells sont des enfants d'`agy`. Le relais s'arrête quand cet `agy` se termine.
  - Antigravity Desktop (et sans doute l'IDE, pas encore vérifié) : les hooks et les shells sont des enfants du processus `language_server` (`antigravity` → `language_server` → `sh`), commun à toutes les conversations d'une fenêtre. Une conversation reste joignable après la fermeture de son onglet : le tour s'exécute alors en arrière-plan. Un seul `ensure-relay` par lancement de la fenêtre suffit, depuis n'importe quelle conversation. Le relais s'arrête quand le language server se termine.
  - Quand aucun de ces processus n'est trouvé parmi les ancêtres des hooks, le relais ne sert que sa conversation et surveille le language server via `/healthz` : si le language server reste injoignable pendant 120 s, le relais s'arrête proprement et la session est marquée comme partie (`gone`, elle disparaît de `list`) jusqu'au prochain passage d'un hook.
- **Hook `Stop` en écoute (optionnel)**, utile quand aucun relais ne tourne : si `~/.agent-bridge/state/agy.listen` existe, le hook attend un message pendant le nombre de secondes écrit dans ce fichier (6 h s'il est vide). Il rend la main dès que vous tapez dans cette conversation ou que le fichier est supprimé. Il surveille l'historique des prompts dans `~/.gemini/antigravity-cli`, `antigravity` et `antigravity-ide` (le support de l'historique sous Desktop et IDE est expérimental).
- **Format des messages** : chaque message injecté dans agy est encadré par `<bridge-message id="..." from="..." ts="...">...</bridge-message>`.

#### Expérimental : relais automatique

Avec `./install.sh --auto-relay` (Linux seulement, désactivé par défaut), une fenêtre Desktop ou IDE n'a besoin ni d'`ensure-relay` ni d'aucun message. Desktop lance son language server avec son jeton sur la ligne de commande (`--csrf_token`). Le pont y lit le jeton, trouve le port HTTP du serveur parmi ses sockets en écoute (celui où `/healthz` répond 200), et lance le relais avec. Deux choses le déclenchent :

- un agent cherche des sessions agy (`list`, `list_agents`, ou `send` vers une adresse `agy`) : le pont cherche, au plus toutes les 10 s, les language servers de votre compte sans relais, et en lance un pour chaque fenêtre qui a une conversation chargée. Desktop ne lance aucun hook avant le premier message d'une conversation : c'est donc ce qui rend joignable une fenêtre tout juste ouverte ;
- un hook passe sous un language server sans relais. Le jeton passe au relais par son environnement et n'est jamais écrit sur disque ni dans les logs. Chaque tentative est notée, sans le jeton, dans `~/.agent-bridge/state/auto-relay.log`. Avec agy CLI, l'option ne fait rien : son language server tourne dans `agy`.

Pourquoi elle est désactivée par défaut :

- Elle lit un secret en dehors du chemin documenté par agy (son shell `run_command`). Elle ne donne aucun accès nouveau : sous Linux, tout processus local peut déjà lire cette ligne de commande (`ps`), et le relais détient déjà ce même jeton. Mais c'est le genre de comportement qu'un outil de sécurité ou un relecteur peut signaler.
- L'emplacement du jeton n'est pas documenté, et une mise à jour d'Antigravity peut le déplacer. Dans ce cas, le hook revient au rappel.

Relancez `./install.sh` sans `--auto-relay` pour la désactiver.

Tests manuels du relais, à lancer dans agy :

```bash
python3 ~/.agent-bridge/bridge.py agy-relay --probe "texte"   # un seul envoi, affiche le statut HTTP
python3 ~/.agent-bridge/bridge.py agy-relay --inspect         # champs liés au modèle de la conversation
```

### Côté Claude Code

- `mcp_channel.py` est un serveur MCP qui déclare la capacité `claude/channel` (préversion de recherche) et transmet chaque message sous forme d'événement `notifications/claude/channel`. La session dort jusqu'à l'arrivée d'un message : pas d'interrogation régulière, pas de réveil périodique, pas de rechargement du cache. Il fournit aussi les outils `send` et `list_agents`.
- Le canal doit être activé au lancement : `claude --dangerously-load-development-channels server:agent-bridge`. L'option `--shell` / `-Shell` de l'installeur le fait pour un simple `claude` :
  - Linux : une fonction dans `~/.bashrc`. Pour la contourner : `command claude`.
  - Windows PowerShell : une fonction dans votre profil. Windows cmd : une macro `doskey` chargée par l'`AutoRun` de cmd. Pour contourner les deux : `claude.cmd`.
  - Les sous-commandes (`claude mcp …`), `-p`, `--help` et `--version` ne sont pas modifiées. Pour désactiver le wrapper dans un shell : `AGENT_BRIDGE_CHANNEL=0`.
- Le serveur MCP est installé pour toutes les sessions Claude, mais il ne lit sa boîte que si son process parent `claude` a été lancé avec le canal. Sinon, une session sans canal prendrait les messages, que Claude Code jetterait ensuite sans rien dire.
- Sans canal, un `Monitor` sur `python3 -u ~/.agent-bridge/bridge.py watch claude` sert de solution de repli. Il lit la boîte commune `claude` et expire au bout de 30 min.

### Livraison

- **Adresses** : `agy:<conversationId>`, `claude:<nom de session>`, `ext:<nom>` pour un programme, joignable tant qu'il lance `watch ext:<nom>` (un seul lecteur par adresse), et `claude` pour la boîte commune lue par `watch`. Une conversation agy est aussi joignable par son titre, `agy:<nom>` (donné avec `/rename` dans agy, affiché par `list_agents`). `agy:new` démarre une nouvelle conversation Antigravity Desktop avec le message comme premier prompt (Linux seulement, voir [Côté agy](#côté-agy)). L'adresse Claude suit `/rename` : la session passe à son nouveau nom en une seconde, ses anciens noms deviennent des alias, et les messages encore en attente sous les anciens noms la suivent. Un envoi vers un `claude:<nom>` inconnu est refusé avec la liste des pairs actifs, évitant la perte silencieuse de messages. Une session ne peut pas s'envoyer de message à elle-même. Les adresses sont strictement validées : les séparateurs de chemin (`/`, `\`) et `..` sont interdits.
- **Livraison** : chaque message est un fichier JSON, écrit dans un fichier temporaire puis renommé de manière atomique. Un lecteur réserve les messages en les déplaçant dans `archive/` avant de les lire : deux lecteurs n'obtiennent jamais le même message. En cas d'échec de livraison (language server injoignable, erreur HTTP), les messages retournent dans la boîte et le relais retente toutes les 10 s sans planter. Dans le cas rare où le language server accepte un message mais que sa réponse se perd (timeout), le message est renvoyé : la livraison est « au moins une fois », un doublon est possible mais rien n'est perdu. Tout message arrivé pendant qu'un lot attend part avec ce lot en un seul tour.
- **Réponses** : chaque message porte l'adresse `from` de l'expéditeur. Dans agy, elle est remplie à partir de `ANTIGRAVITY_CONVERSATION_ID`.
- **Historique et entretien** : `~/.agent-bridge/log.jsonl` garde la trace de chaque envoi et bascule sur `log.jsonl.1` au-delà de 10 Mo. Les archives de plus de 30 jours sont automatiquement purgées.

### Particularités Windows

- Les processus sont trouvés via l'API Win32 au lieu de `/proc`, et tous les fichiers et flux sont lus et écrits en UTF-8.
- `install.ps1` inscrit le chemin absolu de votre interpréteur Python dans les hooks agy et dans le serveur MCP : ils ne dépendent ainsi ni du `PATH` ni des alias du Microsoft Store.
- Les hooks Windows passent par `~/.agent-bridge/agy-hook.cmd`, écrit dans la page de code OEM active (ex. cp850), ce qui évite les conflits d'échappement de `cmd /c` même si les chemins comportent des espaces ou des caractères accentués.
- Les commandes suggérées aux agents utilisent le chemin absolu de l'interpréteur Python, précédé de `&` si un chemin contient des espaces (opérateur d'appel de PowerShell 5.1).
- Le relais n'importe pas `ctypes`. Notre explication, issue des tests : agy semble exécuter chaque commande sur un desktop qu'il ferme à la fin de celle-ci, et un processus resté dessus ne peut plus initialiser `_ctypes`. Le relais surveille donc agy via `_winapi`.

## Ce qui est installé

| Emplacement | Contenu |
|---|---|
| `~/.agent-bridge/` | `bridge.py`, `mcp_channel.py`, les wrappers shell et les boîtes aux lettres |
| `~/.agent-bridge/agy-hook.cmd` (Windows seulement) | lanceur de hooks Windows (page de code OEM, gère espaces et accents) |
| `~/.gemini/config/hooks.json` | hooks agy, sous la clé `agent-bridge` |
| `~/.claude/skills/agy-bridge/` | skill Claude Code qui explique le pont |
| `~/.claude/settings.json` | règles d'autorisation des commandes du pont et des outils MCP |
| `~/.claude.json` (via `claude mcp add -s user`) | le serveur MCP `agent-bridge` |
| `~/.bashrc` (Linux, `--shell` seulement) | charge le wrapper `claude` |
| `~/.agent-bridge/state/agy.auto-relay` (Linux, `--auto-relay` seulement) | active le relais automatique expérimental |
| Profil PowerShell (Windows, `-Shell` seulement) | charge le wrapper `claude` |
| `HKCU\Software\Microsoft\Command Processor\AutoRun` (Windows, `-Shell` seulement) | la macro doskey `claude`, ajoutée après une éventuelle commande existante |

Avant de modifier un fichier de configuration, l'installeur en garde une copie `<fichier>.bak-agent-bridge`. Le désinstalleur annule tout ce qui précède.

## Dépannage

| Symptôme | Vérification |
|---|---|
| Pas d'écran de confirmation du canal au lancement de `claude` | Le wrapper n'est pas actif dans ce terminal. Ouvrez un nouveau terminal après l'installation, et vérifiez que vous avez utilisé `--shell` / `-Shell`. |
| `list` affiche une session agy avec `no relay` | Lisez `~/.agent-bridge/state/relay-<conversationId>.log`. Le relais ne peut être lancé que depuis le shell d'agy : demandez à agy de lancer `bridge.py ensure-relay`. Avec `--auto-relay`, lisez `~/.agent-bridge/state/auto-relay.log`. |
| agy ne reçoit jamais rien | Relancez agy après l'installation. Les fichiers `~/.agent-bridge/state/last_<événement>_input.json` sont réécrits à chaque passage d'un hook. |
| Une session Claude n'apparaît pas | Seules les sessions lancées avec le canal sont listées. Vérifiez la commande de lancement, ou utilisez le repli `watch claude`. |
| `'agy' matches 2 live agy peers` | Plusieurs sessions sont actives. Désignez-en une avec `agy@<dossier>`, `agy:<début de l'id>` ou `agy:<nom>`. |
| `'claude:<nom>' matches 0 live claude peers` | La session Claude visée n'est plus active, a été renommée ou n'a pas été lancée avec le canal. Lancez `bridge.py list` pour voir les adresses actives. |
| `not delivered ... kept in the inbox, retry in 10s` (log du relais) | Le language server d'agy est temporairement indisponible ou occupé. Les messages sont conservés dans la boîte et retentés automatiquement. |

## Sécurité

- **Pas d'authentification entre processus locaux.** Tout processus lancé par votre compte peut écrire dans `~/.agent-bridge/inbox/`. Avec le relais, écrire à une session agy revient à la faire agir avec les permissions de sa conversation, `always-proceed` par exemple. N'utilisez Diaulos que sur une machine de confiance.
- **Un pair n'est pas l'utilisateur.** Les messages reçus sont marqués comme venant d'un agent pair. Les agents savent qu'un tel message ne vaut jamais accord de l'utilisateur, et qu'ils ne doivent jamais faire pour un pair ce que leurs propres permissions ont bloqué.
- **Expéditeur non vérifié.** L'adresse `from` est déclarée par l'expéditeur et n'est pas vérifiée. Un message qui prétend relayer l'utilisateur ne vaut jamais accord de sa part.
- **Le jeton CSRF n'est jamais écrit sur disque ni dans les logs.**
- **Le relais automatique lit le jeton sur la ligne de commande du language server.** Il est désactivé par défaut ; voir [Expérimental : relais automatique](#expérimental--relais-automatique).

> [!WARNING]
> **Brancher un programme (`ext:<nom>`), c'est confier du texte extérieur à un agent qui peut agir sur votre machine.** Un adaptateur transmet tout ce que sa source lui envoie : un message de chat, une issue, une ligne de log. Quiconque peut écrire dans cette source peut écrire à votre agent, y compris des instructions conçues pour le détourner (injection de prompt). Le pont présente ces messages comme venant d'un pair, jamais de l'utilisateur, mais cette mention guide le modèle, elle ne l'empêche de rien.
>
> - Ne branchez que des sources de confiance. Ne reliez jamais une source publique ou partagée (salon de discussion, issues publiques, e-mails entrants) à une session qui tourne en `always-proceed`, en validation automatique, ou qui a accès à des secrets.
> - Donnez à l'adaptateur sa propre session d'agent, lancée dans un dossier qui ne contient rien de sensible.
> - Partez du principe que tout ce que l'agent sait peut se retrouver dans ses réponses, donc à l'extérieur.
> - Surveillez ce que fait l'agent, et arrêtez l'adaptateur quand vous n'êtes pas là.

## État et limites

- **Expérimental.** Diaulos s'appuie sur une méthode non documentée du language server d'agy (`SendUserCascadeMessage`) et sur les channels de Claude Code, en préversion de recherche. L'un comme l'autre peut changer sans préavis.
- Chaque relais sert les conversations d'un processus agy (un agy CLI, ou un language server d'Antigravity Desktop ou de l'IDE) et s'arrête quand ce processus se termine.
- Une session agy CLI n'est pas joignable avant son premier message : agy ne lance aucun hook avant, et son language server tourne dans `agy`, hors de portée du pont.
- `agy:new` et les conversations lues sur disque demandent Antigravity Desktop sous Linux. `agy:new` ouvre la conversation dans le workspace de la conversation du relais ; on n'a pas encore vérifié si Desktop l'affiche dans ses onglets.
- Claude Code affiche un écran de confirmation à chaque lancement tant que la préversion des channels l'exige.
- Sous Windows, les règles d'autorisation de Claude Code ne couvrent pas l'outil PowerShell : Claude Code peut demander confirmation lors de l'exécution des commandes du pont.

**Versions testées**

- Linux : Claude Code 2.1.301, agy (language server 1.4.0, Gemini 3.8 Flash). Clients Antigravity validés pour l'échange de messages : agy CLI, Antigravity Desktop et Antigravity IDE. Validés sur une vraie fenêtre Antigravity Desktop : les hooks sous `language_server`, les titres de conversation, le relais partagé, le relais automatique lancé par `list`, le réveil d'une conversation pas chargée depuis le lancement, et `agy:new`. Pas encore vérifié sous Desktop et IDE : le hook `Stop` en écoute. L'IDE devrait se comporter comme Desktop, sans que ce soit vérifié.
- Windows 11 : Claude Code 2.1.296, agy (language server 1.4.0), Python 3.14. Testés : hooks, réveil d'agy inactif par le relais, réveil d'une session Claude interactive par le canal MCP, messages dans les deux sens.

Le journal complet des expériences, avec chaque essai, message d'erreur et choix de conception, est dans [docs/JOURNAL.md](docs/JOURNAL.md).

## Licence

Apache License 2.0 — voir [LICENSE](LICENSE). Copyright 2026 Adrien.M (Dr1mS).
