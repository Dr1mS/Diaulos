# Diaulos

[English](README.md) · **Français**

> Aux Jeux grecs, le *diaulos* était la course aller-retour : on courait jusqu'au bout du stade, puis on revenait. Ici, c'est le trajet d'un message et de sa réponse entre deux agents.

**Diaulos permet aux sessions agy de se parler.** agy (la CLI Antigravity de Google) ne permet pas à une session d'en joindre une autre : chaque conversation est isolée. Avec Diaulos, n'importe quelle session agy peut écrire à n'importe quelle autre, et le message **réveille le destinataire même s'il est inactif**. Les sessions peuvent tourner dans n'importe quel dossier, et vous n'avez plus à copier-coller d'un terminal à l'autre.

Diaulos relie aussi les sessions **Claude Code**, pour faire travailler ensemble des agents Gemini et Claude. Autant de sessions que vous voulez, des deux types, sous Linux comme sous Windows.

```
agy (~/projets/api)  ⇄  agy (~/projets/web)  ⇄  Claude Code  ⇄  Claude Code
```

## À quoi ça sert

- **Répartir le travail entre sessions agy.** Une session chef de projet confie des tâches à des sessions exécutantes, chacune dans son propre dépôt, et récupère leurs comptes rendus.
- **Demander un second avis.** Une session agy demande à une autre de relire ses modifications. Lors d'un vrai test, agy a relu le code de ce projet et trouvé trois vrais bugs, corrigés depuis ([journal](docs/JOURNAL.md#4-relecture-croisée-autonome-14451449)).
- **Croiser les modèles.** Faites vérifier par Claude Code ce qu'a écrit Gemini, ou l'inverse.
- **Réveiller un agent inactif.** Inutile de surveiller chaque terminal : à l'arrivée d'un message, la session destinataire reprend la main d'elle-même.

## Prérequis

- Linux ou Windows 10/11
- Python 3.8 ou plus récent (bibliothèque standard uniquement, rien à installer avec `pip`)
- agy (Antigravity CLI)
- Claude Code, si vous voulez y associer des sessions Claude

## Installation

```bash
git clone https://github.com/Dr1mS/Diaulos && cd Diaulos
```

**Linux**

```bash
./install.sh --shell
```

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

> **Windows :** Windows PowerShell ne développe pas `~` dans les arguments passés aux programmes. Écrivez alors `python3 $HOME\.agent-bridge\bridge.py …`. Rien à faire côté agy : ses hooks lui donnent le chemin absolu.

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
- Le **relais** réveille agy quand il est inactif. agy transmet à son propre shell `ANTIGRAVITY_LS_ADDRESS` et `ANTIGRAVITY_CSRF_TOKEN` ; ce mécanisme est documenté dans la skill intégrée `plugin` d'agy. Le relais est lancé depuis ce shell avec `bridge.py ensure-relay` : il appelle donc `SendUserCascadeMessage` sur le language server local avec les identifiants d'agy. Il recopie le dernier `userConfig` de la conversation (modèle, outils, autorisations), si bien que le tour injecté se déroule exactement comme un message tapé. Sans cette configuration, agy échoue avec `plan model not specified`. Le relais garde le jeton en mémoire uniquement et s'arrête quand agy se ferme.
- **Hook `Stop` en écoute (optionnel)**, utile quand aucun relais ne tourne : si `~/.agent-bridge/state/agy.listen` existe, le hook attend un message pendant le nombre de secondes écrit dans ce fichier (6 h s'il est vide). Il rend la main dès que vous tapez dans cette conversation ou que le fichier est supprimé.

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

- **Adresses** : `agy:<conversationId>`, `claude:<nom de session>`, et `claude` pour la boîte commune lue par `watch`. Une conversation agy est aussi joignable par son titre, `agy:<nom>` (donné avec `/rename` dans agy, affiché par `list_agents`). L'adresse Claude suit `/rename` : la session passe à son nouveau nom en une seconde, et les messages encore en attente sous l'ancien nom la suivent.
- **Pas de doublon** : chaque message est un fichier JSON, écrit dans un fichier temporaire puis renommé. Un lecteur réserve un message en le déplaçant dans `archive/` avant de le lire : chaque message est donc livré au plus une fois.
- **Réponses** : chaque message porte l'adresse `from` de l'expéditeur. Dans agy, elle est remplie à partir de `ANTIGRAVITY_CONVERSATION_ID`.
- **Historique** : `~/.agent-bridge/log.jsonl` garde la trace de chaque envoi.

### Particularités Windows

- Les processus sont trouvés via l'API Win32 au lieu de `/proc`, et tous les fichiers et flux sont lus et écrits en UTF-8.
- `install.ps1` inscrit le chemin absolu de votre interpréteur Python dans les hooks agy et dans le serveur MCP : ils ne dépendent ainsi ni du `PATH` ni des alias du Microsoft Store. agy lance ses hooks avec `cmd /c`.
- Le relais n'importe pas `ctypes`. Notre explication, issue des tests : agy semble exécuter chaque commande sur un desktop qu'il ferme à la fin de celle-ci, et un processus resté dessus ne peut plus initialiser `_ctypes`. Le relais surveille donc agy via `_winapi`.

## Ce qui est installé

| Emplacement | Contenu |
|---|---|
| `~/.agent-bridge/` | `bridge.py`, `mcp_channel.py`, les wrappers shell et les boîtes aux lettres |
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
| `list` affiche une session agy avec `no relay` | Lisez `~/.agent-bridge/state/relay-<conversationId>.log`. Le relais ne peut être lancé que depuis le shell d'agy : demandez à agy de lancer `bridge.py ensure-relay`. |
| agy ne reçoit jamais rien | Relancez agy après l'installation. Les fichiers `~/.agent-bridge/state/last_<événement>_input.json` sont réécrits à chaque passage d'un hook. |
| Une session Claude n'apparaît pas | Seules les sessions lancées avec le canal sont listées. Vérifiez la commande de lancement, ou utilisez le repli `watch claude`. |
| `'agy' matches 2 live agy peers` | Plusieurs sessions sont actives. Désignez-en une avec `agy@<dossier>`, `agy:<début de l'id>` ou `agy:<nom>`. |

## Sécurité

- **Pas d'authentification entre processus locaux.** Tout processus lancé par votre compte peut écrire dans `~/.agent-bridge/inbox/`. Avec le relais, écrire à une session agy revient à la faire agir avec les permissions de sa conversation, `always-proceed` par exemple. N'utilisez Diaulos que sur une machine de confiance.
- **Un pair n'est pas l'utilisateur.** Les messages reçus sont marqués comme venant d'un agent pair. Les agents savent qu'un tel message ne vaut jamais accord de l'utilisateur, et qu'ils ne doivent jamais faire pour un pair ce que leurs propres permissions ont bloqué.
- **Le jeton CSRF n'est jamais écrit sur disque ni dans les logs.**

## État et limites

- **Expérimental.** Diaulos s'appuie sur une méthode non documentée du language server d'agy (`SendUserCascadeMessage`) et sur les channels de Claude Code, en préversion de recherche. L'un comme l'autre peut changer sans préavis.
- Chaque relais est lié à une conversation agy et s'arrête quand ce processus agy se termine.
- Claude Code affiche un écran de confirmation à chaque lancement tant que la préversion des channels l'exige.

**Versions testées**

- Linux : Claude Code 2.1.301, agy (language server 1.4.0, Gemini 3.8 Flash). Clients Antigravity validés : agy CLI, Antigravity Desktop et Antigravity IDE.
- Windows 11 : Claude Code 2.1.296, agy (language server 1.4.0), Python 3.14. Testés : hooks, réveil d'agy inactif par le relais, réveil d'une session Claude interactive par le canal MCP, messages dans les deux sens.

Le journal complet des expériences, avec chaque essai, message d'erreur et choix de conception, est dans [docs/JOURNAL.md](docs/JOURNAL.md).

## Licence

Apache License 2.0 — voir [LICENSE](LICENSE). Copyright 2026 Adrien.M (Dr1mS).
