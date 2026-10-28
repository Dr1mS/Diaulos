# Diaulos

[English](README.md) · **Français**

[![Tests](https://github.com/Dr1mS/Diaulos/actions/workflows/tests.yml/badge.svg)](https://github.com/Dr1mS/Diaulos/actions/workflows/tests.yml) ![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue) ![Linux | Windows](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey) [![Licence : Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)

> Aux Jeux grecs, le *diaulos* était la course aller-retour : on courait jusqu'au bout du stade, puis on revenait. Ici, c'est le trajet d'un message et de sa réponse entre deux agents.

**Diaulos permet aux sessions agy de se parler.** agy (Google Antigravity) ne permet pas à une session d'en joindre une autre : chaque conversation est isolée. Avec Diaulos, n'importe quelle session agy peut écrire à n'importe quelle autre, et le message **réveille le destinataire même s'il est inactif**, ou même si son terminal est fermé. Les sessions peuvent tourner dans n'importe quel dossier, et vous n'avez plus à copier-coller d'un terminal à l'autre.

Diaulos fonctionne avec la CLI agy et avec **Antigravity Desktop**, et relie aussi les sessions **Claude Code**, pour faire travailler ensemble des agents Gemini et Claude. Autant de sessions que vous voulez, des deux types, sous Linux comme sous Windows.

```
agy (~/projets/api)  ⇄  agy (~/projets/web)  ⇄  Antigravity Desktop  ⇄  Claude Code
```

## Sommaire

- [À quoi ça sert](#à-quoi-ça-sert)
- [Installation](#installation) · [Prise en main](#prise-en-main) · [Référence des commandes](#référence-des-commandes)
- [agy CLI et Antigravity Desktop](#agy-cli-et-antigravity-desktop)
- [Fonctionnement](#fonctionnement)
- [Ce qui est installé](#ce-qui-est-installé) · [Dépannage](#dépannage)
- [Sécurité](#sécurité) · [État et limites](#état-et-limites)
- [Développement](#développement) · [Historique des versions](CHANGELOG.md) · [Journal de labo](docs/JOURNAL.md)

## À quoi ça sert

- **Répartir le travail entre sessions agy.** Une session chef de projet confie des tâches à des sessions exécutantes, chacune dans son propre dépôt, et récupère leurs comptes rendus.
- **Demander un second avis.** Une session agy demande à une autre de relire ses modifications. Lors d'un vrai test, agy a relu le code de ce projet et trouvé trois vrais bugs, corrigés depuis ([journal](docs/JOURNAL.md#4-relecture-croisée-autonome-14451449)).
- **Croiser les modèles.** Faites vérifier par Claude Code ce qu'a écrit Gemini, ou l'inverse.
- **Réveiller un agent inactif.** Inutile de surveiller chaque terminal : à l'arrivée d'un message, la session destinataire reprend la main d'elle-même.
- **Joindre n'importe quelle conversation agy CLI, même si son terminal est fermé.** Le pont lance son tour avec `agy -p --conversation <id>`, dans son dossier, et l'agent répond comme d'habitude.
- **Démarrer une nouvelle session agy depuis un autre agent.** `send agy:new-cli "<tâche>"` démarre une conversation agy CLI avec cette tâche comme premier prompt ; `send agy:new "<tâche>"` en ouvre une dans Antigravity Desktop (Linux), sans ouvrir d'onglet ni rien taper.
- **Réveiller un agent depuis n'importe quel programme.** Un script, un job de CI ou un bot rejoint le pont sous `ext:<nom>` : il envoie avec `bridge.py send <agent> - --from ext:<nom>` et lit les réponses de l'agent avec `bridge.py watch ext:<nom> --json`. **Lisez d'abord la section [Sécurité](#sécurité) :** le programme transmet du texte extérieur à un agent capable de lancer des commandes.
- **Voir passer les messages.** Avec [Paros](https://github.com/Dr1mS/Paros), chaque session a un personnage en pixels sur le bureau, et chaque message envoyé par Diaulos vole d'un personnage à l'autre sous forme de lettre.

## Paros

Diaulos est compatible avec [Paros](https://github.com/Dr1mS/Paros), un compagnon de bureau pour Claude Code et agy. Chaque session a un petit personnage au bas de l'écran qui montre ce qu'elle fait : les sessions Claude Code et agy CLI, et les conversations d'Antigravity Desktop affichées ou actives récemment. Quand une session envoie un message par Diaulos, son personnage lance une lettre au personnage du destinataire. Un message en attente dans la boîte d'une session agy dresse une boîte aux lettres à côté de son personnage. Rien à configurer : Paros lit les commandes du pont, ses boîtes et ses fichiers de pairs dans `~/.agent-bridge/`.

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

Avec Antigravity Desktop, ajoutez `--auto-relay` (expérimental, lisez d'abord [Expérimental : relais automatique](#expérimental--relais-automatique)).

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
#  agy:52620e0d-…  "revue api"  /home/moi/projets/api  relay pid 225225: wakes on message
#  agy:e9439252-…  "web"  /home/moi/projets/web  agy CLI, no process: wakes with agy -p

python3 ~/.agent-bridge/bridge.py send agy@web "Tu peux vérifier la modification d'API que je viens de faire ?"
```

Quand une seule *autre* session agy est active (un agy CLI qui tourne, ou une conversation chargée dans une fenêtre Desktop), `agy` seul suffit. agy sait tout cela dès le départ : le hook `SessionStart` donne à chaque session son adresse et les commandes exactes à utiliser. Il suffit de demander : *« demande à l'autre session agy de relire ça »*.

Dans Claude Code, demandez aussi simplement : *« envoie ça à agy »*. Claude utilise les outils `send` et `list_agents` du serveur MCP `agent-bridge`.

> **Windows :** Windows PowerShell ne développe pas `~` dans les arguments passés aux programmes. Écrivez alors `python3 $HOME\.agent-bridge\bridge.py …`. Les commandes suggérées aux agents utilisent le chemin absolu de l'interpréteur Python, précédé de `&` si un chemin contient des espaces (opérateur d'appel de PowerShell 5.1). Rien à faire côté agy : ses hooks lui donnent le chemin absolu.

## Référence des commandes

`bridge.py` désigne `~/.agent-bridge/bridge.py`. Les sessions Claude Code utilisent les outils MCP `send` et `list_agents`, qui prennent les mêmes adresses et options.

| Commande | Effet |
|---|---|
| `bridge.py list` | Agents joignables : sessions Claude Code, conversations agy des 7 derniers jours, programmes `ext:`, avec la façon dont chacun est réveillé |
| `bridge.py list --days N` / `--all` | Ajoute les conversations agy modifiées dans les N derniers jours / de tout âge (`days` / `all` dans `list_agents`) |
| `bridge.py send <dest> <texte…>` | Envoie un message ; `-` comme texte le lit sur l'entrée standard |
| `… --from <adresse>` | Adresse de l'expéditeur, pour un programme ou un script (agy la remplit lui-même) |
| `… --workspace <dossier>` | Seulement avec `agy:new` et `agy:new-cli` : où travaille la nouvelle session. Pour `agy:new`, aussi le nom d'un workspace Desktop (`workspace` dans l'outil MCP `send`) |
| `bridge.py watch <adresse> [--json]` | Affiche chaque nouveau message pour cette adresse, sans fin. Avec `ext:<nom>`, inscrit le programme comme pair joignable |
| `bridge.py recv <adresse> [--json]` | Affiche et archive une fois les messages en attente |
| `bridge.py ensure-relay` | Depuis un shell agy : lance le relais de ce processus agy (un rappel le demande à agy) |
| `bridge.py agy-relay --probe "<texte>"` / `--inspect` | Depuis un shell agy : un envoi de test / les réglages de modèle de la conversation |

| Adresse | Destinataire |
|---|---|
| `agy:<conversationId>`, `agy:<début de l'id>` | Une conversation agy (CLI ou Desktop) |
| `agy:<titre>` | Une conversation agy par son titre (`/rename`, ou celui que lui donne agy) ; refusé si deux le partagent |
| `agy@<partie de son dossier>` | La conversation agy dont le dossier contient ce texte : la seule active, sinon la seule listée |
| `agy` | La seule autre session agy active : un agy CLI qui tourne, ou une conversation chargée par sa fenêtre Desktop |
| `agy:new`, `agy:new@<titre>` | Une nouvelle conversation Antigravity Desktop, nommée `<titre>` (Linux) |
| `agy:new-cli`, `agy:new-cli@<nom>` | Une nouvelle conversation agy CLI, aussi joignable comme `agy:<nom>` |
| `claude:<nom de session>`, `claude` | Une session Claude Code lancée avec le canal ; `claude` seul : la seule autre, ou, si aucune n'est listée, la boîte commune lue par `watch claude` |
| `ext:<nom>` | Un programme branché avec `watch ext:<nom>` |

## agy CLI et Antigravity Desktop

| | agy CLI | Antigravity Desktop |
|---|---|---|
| Ouverte, après son premier message | joignable une fois qu'agy a lancé `ensure-relay` (un rappel le lui demande) | joignable sans `ensure-relay` dès que la fenêtre a un relais |
| Ouverte, avant tout message | pas joignable : la conversation n'existe pas encore | pas joignable non plus ; les autres conversations de la fenêtre le sont, avec `--auto-relay` |
| Terminal ou onglet fermé | joignable : chaque lot de messages lance un tour avec `agy -p --conversation <id>` | joignable : la livraison la charge depuis le disque, et son tour s'exécute en arrière-plan |
| Listées | conversations des 7 derniers jours ; `--days N` / `--all` pour les plus anciennes ; tout âge par id ou titre exact | pareil |
| Démarrer une nouvelle session depuis un autre agent | `agy:new-cli`, `agy:new-cli@<nom>` | `agy:new`, `agy:new@<titre>` (Linux) |
| Relais | un par processus `agy`, arrêté à sa sortie | un par fenêtre, arrêté avec son language server |

### Conversations agy CLI sans terminal

`agy -p "<message>" --conversation <id>` lance un tour d'une conversation agy CLI, sans terminal et avec vos réglages agy habituels. Diaulos s'en sert pour chaque conversation agy CLI dont le `agy` s'est terminé. `list` affiche une telle conversation avec `agy CLI, no process: wakes with agy -p`, et un message vers elle lance un tour dans son dossier. Si vous rouvrez la conversation avec `agy --conversation <id>`, ses hooks et son relais prennent le relais.

Démarrer une session, par exemple depuis Claude Code :

```bash
python3 ~/.agent-bridge/bridge.py send agy:new-cli@builder "Compile le projet et envoie les erreurs à claude:api" --workspace ~/projets/api
# une fois son premier tour fini, le pont vous envoie son adresse, par ex. agy:9a0af1a2-… ; elle est aussi joignable comme agy:builder
```

Sans `--workspace`, la conversation démarre dans le dossier de l'expéditeur. Le `@<nom>` n'est connu que du pont : agy donne son propre titre à la conversation. Ouvrez la session dans un terminal avec `agy --conversation <id>`.

À savoir :

- Ces tours suivent vos permissions agy. Avec `toolPermission: always-proceed`, ils se déroulent seuls. Avec un réglage qui demande avant certains outils, un tour qui en a besoin attend jusqu'à `--print-timeout` (30 min) puis échoue. Diaulos ne passe jamais `--dangerously-skip-permissions`.
- Le dossier doit faire partie des workspaces de confiance d'agy (`trustedWorkspaces` dans `~/.gemini/antigravity-cli/settings.json`). Les autres dossiers ne sont pas testés.
- Chaque tour lance un nouveau processus `agy`, soit environ 10 s avant que le modèle commence à réfléchir.
- Un message passe à `agy -p` en un seul argument : au-delà de 100 000 caractères, il est refusé et gardé en `.failed`.

### Antigravity Desktop

Desktop fait tourner toutes les conversations d'une fenêtre dans un seul processus d'arrière-plan, son language server. Un seul relais par fenêtre sert donc toutes ses conversations : celles de ses onglets, ouverts ou fermés, et celles des 7 derniers jours que la fenêtre n'a pas chargées.

- **Avec `--auto-relay` (recommandé sous Linux) :** dès qu'un agent liste les sessions agy ou écrit à l'une d'elles, Diaulos lance tout seul le relais de la fenêtre.
- **Sans cette option :** un seul `ensure-relay` par lancement de la fenêtre suffit, depuis n'importe laquelle de ses conversations.

Démarrer une session, par exemple depuis Claude Code :

```bash
python3 ~/.agent-bridge/bridge.py send agy:new@testeur "Lance les tests et envoie les échecs à claude:api" --workspace ~/projets/api
# le pont répond avec la nouvelle adresse, par ex. agy:e59d7273-… ; la session est aussi joignable comme agy:testeur
```

`--workspace` prend un dossier ou le nom d'un workspace Desktop (par ex. `--workspace "mon projet"`). Sans lui, la nouvelle conversation s'ouvre dans le workspace de la conversation qui a lancé le relais. Quand le dossier appartient à un workspace Desktop, Desktop y range la conversation ; tout autre dossier devient seulement son dossier de travail, et la conversation est rangée parmi les simples conversations.

#### Expérimental : relais automatique

Avec `./install.sh --auto-relay` (Linux seulement, désactivé par défaut), une fenêtre Desktop ou IDE n'a besoin ni d'`ensure-relay` ni d'aucun message. Desktop lance son language server avec son jeton sur la ligne de commande (`--csrf_token`). Le pont y lit le jeton, trouve le port HTTP du serveur parmi ses sockets en écoute (celui où `/healthz` répond 200), et lance le relais avec.

Deux choses le déclenchent :

- un agent cherche des sessions agy (`list`, `list_agents`, ou `send` vers une adresse `agy`) : au plus toutes les 10 s, le pont cherche les language servers de votre compte sans relais, et en lance un pour chaque fenêtre qui a une conversation. Desktop ne lance aucun hook avant le premier message d'une conversation : c'est donc ce qui rend joignable une fenêtre tout juste ouverte ;
- un hook passe sous un language server sans relais.

Le jeton passe au relais par son environnement et n'est jamais écrit sur disque ni dans les logs. Chaque tentative est notée, sans le jeton, dans `~/.agent-bridge/state/auto-relay.log`. Avec agy CLI, l'option ne fait rien : son language server tourne dans `agy`.

Pourquoi elle est désactivée par défaut :

- Elle lit un secret en dehors du chemin documenté par agy (son shell `run_command`). Elle ne donne aucun accès nouveau : sous Linux, tout processus local peut déjà lire cette ligne de commande (`ps`), et le relais détient déjà ce même jeton. Mais c'est le genre de comportement qu'un outil de sécurité ou un relecteur peut signaler.
- L'emplacement du jeton n'est pas documenté, et une mise à jour d'Antigravity peut le déplacer. Dans ce cas, le hook revient au rappel.

Relancez `./install.sh` sans `--auto-relay` pour la désactiver.

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

Les outils ne partagent aucun protocole. Diaulos repose sur une boîte aux lettres en fichiers et sur les points d'extension que chaque outil fournit déjà.

```
un agent ── send ──▶  ~/.agent-bridge/inbox/<adresse>/*.json
                        │
                        ├─▶ agy au travail :    le hook PreInvocation injecte le message avant le prochain appel du modèle
                        ├─▶ agy inactif :       le relais appelle le language server local, qui lance un nouveau tour
                        ├─▶ agy CLI fermé :     cli-run lance un tour avec agy -p --conversation <id>
                        └─▶ Claude inactif :    le serveur MCP « channel » envoie un événement <channel>, qui lance un nouveau tour
```

### Côté agy

- **Hooks.** Les hooks globaux sont dans `~/.gemini/config/hooks.json`. `SessionStart` présente le pont. `PreInvocation` livre les messages et, tant qu'aucun relais ne tourne, rappelle à agy de le lancer.
- **Le relais** réveille agy quand il est inactif. agy transmet à son propre shell `ANTIGRAVITY_LS_ADDRESS` et `ANTIGRAVITY_CSRF_TOKEN`, comme le documente sa skill intégrée `plugin` ; les hooks ne reçoivent ni l'un ni l'autre, seulement `ANTIGRAVITY_CONVERSATION_ID`. Le relais est lancé depuis ce shell avec `bridge.py ensure-relay`, et appelle `SendUserCascadeMessage` sur le language server local avec les identifiants d'agy. Il recopie le dernier `userConfig` de la conversation (modèle, outils, autorisations), si bien que le tour injecté se déroule exactement comme un message tapé. Il garde le jeton en mémoire uniquement.
- **Un relais par processus agy.** Toutes les conversations d'un même processus agy partagent son language server, son adresse et son jeton : un seul relais les sert donc toutes, chacune avec sa boîte. Une conversation dont le hook trouve un relais en marche pour son processus le rejoint, sans `ensure-relay` et sans le rappel.
  - agy CLI : les hooks et les shells sont des enfants d'`agy`. Le relais s'arrête quand cet `agy` se termine, et confie les messages arrivés trop tard à `cli-run`.
  - Antigravity Desktop : les hooks et les shells sont des enfants du processus `language_server` (`antigravity` → `language_server` → `sh`), commun à toutes les conversations d'une fenêtre. Le relais s'arrête quand le language server se termine.
  - Quand aucun de ces processus n'est trouvé parmi les ancêtres des hooks, le relais ne sert que sa conversation et interroge le `/healthz` du language server : s'il reste injoignable 120 s, le relais s'arrête et la conversation est marquée partie (`gone`) jusqu'au prochain passage d'un hook.
- **Conversations sans processus.** Pour une conversation agy CLI dont le `agy` s'est terminé, un message lance `bridge.py cli-run`, un seul par conversation. Il attend une seconde pour qu'une rafale de messages parte en un seul tour, lance `agy -p` dans le dossier de la conversation, et s'arrête quand la boîte est vide ; il ne lance jamais de tour pendant qu'un processus `agy` tient la conversation. Ces conversations viennent de l'index d'agy, `~/.gemini/antigravity-cli/conversation_summaries.db`, sans les sous-agents ni les conversations tuées.
- **Hook `Stop` en écoute (optionnel)**, utile quand aucun relais ne tourne : si `~/.agent-bridge/state/agy.listen` existe, le hook attend un message pendant le nombre de secondes écrit dans ce fichier (6 h s'il est vide). Il rend la main dès que vous tapez dans cette conversation ou que le fichier est supprimé.
- **Format des messages** : chaque message injecté dans agy est encadré par `<bridge-message id="..." from="..." ts="...">...</bridge-message>`.

### Coulisses d'Antigravity Desktop

- **Découverte.** Toutes les 30 s, le relais demande à son language server quelles conversations il a chargées (`GetAllCascadeTrajectories`) et les inscrit, sauf les archivées. Il lit aussi l'index de Desktop, `~/.gemini/antigravity/conversation_summaries.db`, pour ses propres conversations des 7 derniers jours (sans les conversations d'agy CLI, les sous-agents, les tuées ni les archivées). `list --days` / `--all` et un message vers un id ou un titre exact inscrivent les plus anciennes. La livraison charge d'abord une conversation depuis le disque (`GetCascadeTrajectory`).
- **`agy:new`.** Un message à `agy:new` attend dans sa propre boîte. Le relais de Desktop crée la conversation avec `StartCascade` (`source: CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT`). Pour un dossier d'un workspace Desktop (un projet de `~/.gemini/config/projects/*.json`), il passe `projectEnvConfig: {projectId, defaultProjectEnvironment: {}}` sans `workspaceUris`, que le serveur refuse ensemble ; tout autre dossier passe dans `workspaceUris`. Avec `@<titre>`, il nomme la conversation avec `UpdateConversationAnnotations`. Le message passe ensuite dans la boîte de la nouvelle conversation. Une conversation encore sans prompt emprunte la config de la dernière conversation de la fenêtre qui en a une. `send` refuse `agy:new` tant qu'aucune fenêtre Desktop n'a de relais.
- **Dossier de données.** Le relais apprend le dossier de données de Desktop par `ANTIGRAVITY_APP_DATA_DIR`. Seul le relais automatique lit la ligne de commande du language server.

### Côté Claude Code

- `mcp_channel.py` est un serveur MCP qui déclare la capacité `claude/channel` (préversion de recherche) et transmet chaque message sous forme d'événement `notifications/claude/channel`. La session dort jusqu'à l'arrivée d'un message : pas d'interrogation régulière, pas de réveil périodique, pas de rechargement du cache. Il fournit aussi les outils `send` et `list_agents`.
- Le canal doit être activé au lancement : `claude --dangerously-load-development-channels server:agent-bridge`. L'option `--shell` / `-Shell` de l'installeur le fait pour un simple `claude` :
  - Linux : une fonction dans `~/.bashrc`. Pour la contourner : `command claude`.
  - Windows PowerShell : une fonction dans votre profil. Windows cmd : une macro `doskey` chargée par l'`AutoRun` de cmd. Pour contourner les deux : `claude.cmd`.
  - Les sous-commandes (`claude mcp …`), `-p`, `--help` et `--version` ne sont pas modifiées. Pour désactiver le wrapper dans un shell : `AGENT_BRIDGE_CHANNEL=0`.
- Le serveur MCP est installé pour toutes les sessions Claude, mais il ne lit sa boîte que si son process parent `claude` a été lancé avec le canal. Sinon, une session sans canal prendrait les messages, que Claude Code jetterait ensuite sans rien dire.
- Sans canal, un `Monitor` sur `python3 -u ~/.agent-bridge/bridge.py watch claude` sert de solution de repli. Il lit la boîte commune `claude` et expire au bout de 30 min.

### Livraison

- **Adresses** : voir la [Référence des commandes](#référence-des-commandes). L'adresse Claude suit `/rename` : la session passe à son nouveau nom en une seconde, ses anciens noms deviennent des alias, et les messages encore en attente sous les anciens noms la suivent. Un envoi vers un `claude:<nom>` inconnu est refusé avec la liste des pairs actifs : rien ne se perd sans le dire. Une session ne peut pas s'envoyer de message à elle-même. Les adresses concrètes sont strictement validées : les séparateurs de chemin (`/`, `\`) et `..` sont interdits.
- **Livraison** : chaque message est un fichier JSON, écrit dans un fichier temporaire puis renommé de manière atomique. Un lecteur réserve les messages en les déplaçant dans `archive/` avant de les lire : deux lecteurs n'obtiennent jamais le même message. En cas d'échec de livraison (language server injoignable, erreur HTTP), les messages retournent dans la boîte et le relais retente toutes les 10 s ; un message refusé pour de bon est gardé en `.failed`, pour qu'un mauvais message ne bloque jamais les autres. Dans le cas rare où le language server accepte un message mais que sa réponse se perd (timeout), le message est renvoyé : la livraison est « au moins une fois », un doublon est possible mais rien n'est perdu. Tout message arrivé pendant qu'un lot attend part avec ce lot en un seul tour.
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
| `~/.agent-bridge/state/agy.auto-relay` (Linux, `--auto-relay` seulement) | active le relais automatique expérimental |
| `~/.gemini/config/hooks.json` | hooks agy, sous la clé `agent-bridge` |
| `~/.claude/skills/agy-bridge/` | skill Claude Code qui explique le pont |
| `~/.claude/settings.json` | règles d'autorisation des commandes du pont et des outils MCP |
| `~/.claude.json` (via `claude mcp add -s user`) | le serveur MCP `agent-bridge` |
| `~/.bashrc` (Linux, `--shell` seulement) | charge le wrapper `claude` |
| Profil PowerShell (Windows, `-Shell` seulement) | charge le wrapper `claude` |
| `HKCU\Software\Microsoft\Command Processor\AutoRun` (Windows, `-Shell` seulement) | la macro doskey `claude`, ajoutée après une éventuelle commande existante |

Avant de modifier un fichier de configuration, l'installeur en garde une copie `<fichier>.bak-agent-bridge`. Le désinstalleur annule tout ce qui précède.

## Dépannage

| Symptôme | Vérification |
|---|---|
| Pas d'écran de confirmation du canal au lancement de `claude` | Le wrapper n'est pas actif dans ce terminal. Ouvrez un nouveau terminal après l'installation, et vérifiez que vous avez utilisé `--shell` / `-Shell`. |
| agy ne reçoit jamais rien | Relancez agy après l'installation. Les fichiers `~/.agent-bridge/state/last_<événement>_input.json` sont réécrits à chaque passage d'un hook. |
| `list` affiche une session agy avec `no relay` | Lisez `~/.agent-bridge/state/relay-<conversationId>.log`. Le relais ne peut être lancé que depuis le shell d'agy : demandez à agy de lancer `bridge.py ensure-relay`. Avec `--auto-relay`, lisez `~/.agent-bridge/state/auto-relay.log`. |
| Un message à une conversation agy CLI marquée `no process` reste sans réponse | Lisez `~/.agent-bridge/state/cli-<conversationId>.log` (`cli-new.log` pour `agy:new-cli`) : il montre chaque lancement d'`agy -p` et son erreur. |
| `agy:new: no Antigravity Desktop window with a running relay` | Ouvrez Desktop, puis listez les sessions agy (avec `--auto-relay`) ou lancez `ensure-relay` dans une de ses conversations. |
| Une session Claude n'apparaît pas | Seules les sessions lancées avec le canal sont listées. Vérifiez la commande de lancement, ou utilisez le repli `watch claude`. |
| `'agy' matches 2 live agy peers` | Plusieurs sessions sont actives, ou plusieurs partagent un titre. Désignez-en une avec `agy@<dossier>`, `agy:<début de l'id>` ou `agy:<nom>`. |
| `'claude:<nom>' matches 0 live claude peers` | La session Claude visée n'est plus active, a été renommée ou n'a pas été lancée avec le canal. Lancez `bridge.py list` pour voir les adresses actives. |
| `not delivered ... kept in the inbox, retry in 10s` (log du relais) | Le language server d'agy est temporairement indisponible ou occupé. Les messages sont conservés dans la boîte et retentés automatiquement. |

## Sécurité

- **Pas d'authentification entre processus locaux.** Tout processus lancé par votre compte peut écrire dans `~/.agent-bridge/inbox/`. Écrire à une session agy revient à la faire agir avec les permissions de sa conversation, `always-proceed` par exemple : par le relais, par `agy -p` pour une conversation agy CLI dont le terminal est fermé, et pour les sessions démarrées par `agy:new` et `agy:new-cli`. N'utilisez Diaulos que sur une machine de confiance.
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

- **Expérimental.** Diaulos s'appuie sur des méthodes non documentées du language server d'agy (`SendUserCascadeMessage`, `StartCascade`…), sur les fichiers qu'écrit agy, et sur les channels de Claude Code, en préversion de recherche. Chacun peut changer sans préavis.
- Une session agy CLI n'est pas joignable avant son premier message : la conversation n'existe pas avant.
- Le relais coûte à agy un appel d'outil au premier tour d'une session agy CLI (`ensure-relay`, après le rappel du hook) : un hook peut injecter des messages, pas lancer une commande.
- `agy:new`, le relais automatique et les conversations Desktop lues sur disque demandent Antigravity Desktop sous Linux.
- Claude Code affiche un écran de confirmation à chaque lancement tant que la préversion des channels l'exige.
- Sous Windows, les règles d'autorisation de Claude Code ne couvrent pas l'outil PowerShell : Claude Code peut demander confirmation lors de l'exécution des commandes du pont.

**Versions testées**

- Linux, Claude Code 2.1.301, agy 1.4.9 (language server 1.4.0, Gemini 3.8 Flash) :
  - échange de messages et réveil de sessions inactives avec la CLI agy, Antigravity Desktop et Antigravity IDE ;
  - sur une vraie fenêtre Antigravity Desktop : le relais partagé, le relais automatique lancé par `list`, le réveil d'une conversation pas chargée depuis le lancement, `agy:new` avec un titre et un workspace ;
  - avec la CLI agy : le réveil d'une conversation dont le terminal est fermé, et `agy:new-cli` avec un nom.
  - Pas encore vérifiés : les nouveautés Desktop sur l'IDE, et le hook `Stop` en écoute sous Desktop et IDE.
- Windows 11, Claude Code 2.1.296, agy (language server 1.4.0), Python 3.14 : hooks, réveil d'agy inactif par le relais, réveil d'une session Claude interactive par le canal MCP, messages dans les deux sens.

Le journal complet des expériences, avec chaque essai, message d'erreur et choix de conception, est dans [docs/JOURNAL.md](docs/JOURNAL.md). Les changements par version sont dans [CHANGELOG.md](CHANGELOG.md).

## Développement

Les tests n'utilisent que la bibliothèque standard. Ils tournent contre des doublures (un faux language server Antigravity, un faux `agy`) dans des dossiers temporaires, et ne touchent donc jamais à vos vraies sessions :

```bash
python3 -m unittest discover -s tests -v
```

Les tests Desktop et agy CLI ne tournent que sous Linux, et sont sautés ailleurs. GitHub Actions lance la suite sous Linux (Python 3.8 et 3.12) et Windows à chaque push et pull request.

## Licence

Apache License 2.0 — voir [LICENSE](LICENSE). Copyright 2026 Adrien.M (Dr1mS).
