# Diaulos — pont Claude Code ↔ agy

[English](README.md) · **Français**

> Aux Jeux grecs, le *diaulos* était la course aller-retour : on courait jusqu'au bout du stade, puis on revenait. Ici, c'est le trajet d'un message et de sa réponse entre deux agents.

Ce projet fait communiquer des sessions **Claude Code** et des sessions **agy** (la CLI Antigravity de Google, modèles Gemini) qui tournent sur la même machine. Chaque agent peut envoyer un message à l'autre et le réveiller, quel que soit le dossier où il a été lancé, sans que l'utilisateur serve d'intermédiaire.

Le journal détaillé des expériences est dans [docs/JOURNAL.md](docs/JOURNAL.md).

## Installation

```bash
./install.sh --shell   # --shell : un simple `claude` démarre avec le canal (voir plus bas)
./install.sh      # à relancer après chaque mise à jour du dépôt
./uninstall.sh    # ajoutez --purge pour supprimer aussi les messages
```

`install.sh` met en place :

| Cible | Contenu |
|---|---|
| `~/.agent-bridge/bridge.py` | Le script du pont. Les boîtes aux lettres sont dans le même dossier. |
| `~/.agent-bridge/mcp_channel.py` | Serveur MCP « channel » de Claude Code, déclaré pour l'utilisateur sous le nom `agent-bridge`. |
| `~/.gemini/config/hooks.json` | Hooks agy globaux `SessionStart`, `PreInvocation` et `Stop`, sous la clé `agent-bridge`. |
| `~/.claude/skills/agy-bridge/SKILL.md` | Skill Claude Code qui explique comment envoyer et recevoir des messages. |
| `~/.claude/settings.json` | Règles d'autorisation `Bash(python3 ~/.agent-bridge/bridge.py *)` et des outils `mcp__agent-bridge__*`. |

Avant de modifier un fichier de configuration, le script en garde une copie `<fichier>.bak-agent-bridge`. Relancez ensuite vos sessions agy : elles ne chargent leurs hooks qu'au démarrage.

## Principe

Aucun des deux outils ne connaît le protocole de l'autre. Le pont repose sur une boîte aux lettres en fichiers et sur les points d'extension que chaque outil fournit déjà.

```
Claude Code                      ~/.agent-bridge/                       agy (n'importe quel dossier)
───────────                      ────────────────                       ────────────────────────────
send agy "…"      ──────────▶  inbox/agy-<conv>/*.json  ──▶  hook PreInvocation   (agy travaille)
                                                        ──▶  relais LS           (agy inactif, sans limite)
                                                        ──▶  hook Stop en écoute (agy vient de finir, optionnel)
Monitor "watch claude" ◀─────  inbox/claude/*.json      ◀──  send claude "…"
```

- **Adresses** : `agy:<conversationId>` désigne une conversation agy, `claude:<nom de session>` une session Claude dotée du canal MCP, et `claude` la boîte commune lue par `watch claude`. Voir « Plusieurs sessions » plus bas pour le choix automatique du destinataire.
- **Réponses** : chaque message porte l'adresse `from` de l'expéditeur, et on répond à cette adresse. Dans agy, l'expéditeur est rempli tout seul à partir de `ANTIGRAVITY_CONVERSATION_ID`.
- **Livraison** : chaque message est un fichier JSON, écrit dans un fichier temporaire puis renommé. Un lecteur le réserve en le déplaçant dans `archive/` avant de le lire. Un message est donc livré au plus une fois, sans doublon. `log.jsonl` garde la trace de chaque envoi.

## Côté agy

- **`SessionStart`** : présente le pont à agy et lui donne son adresse.
- **`PreInvocation`** : injecte les messages en attente avant chaque appel du modèle. Tant que le relais de la conversation ne tourne pas, il ajoute un rappel éphémère qui demande à agy de lancer `python3 ~/.agent-bridge/bridge.py ensure-relay`.
- **Relais** : il ne peut être lancé que par une commande shell d'agy, la seule à recevoir `ANTIGRAVITY_LS_ADDRESS` et `ANTIGRAVITY_CSRF_TOKEN`. Ce mécanisme est documenté dans la skill intégrée `plugin` d'agy. Le relais appelle `SendUserCascadeMessage` sur le language server local, ce qui réveille agy même inactif. Il recopie la configuration du dernier message tapé (`userConfig`, qui inclut le modèle) ; sans elle, agy échoue avec `plan model not specified`. Il garde le jeton en mémoire uniquement et s'arrête quand agy se ferme.
- **`Stop` en écoute (optionnel)** : utile quand aucun relais ne tourne. Si `~/.agent-bridge/state/agy.listen` existe, le hook attend un message pendant le nombre de secondes écrit dans ce fichier, ou 6 h s'il est vide. Il s'arrête dès que l'utilisateur tape dans cette conversation (détecté via `~/.gemini/antigravity-cli/history.jsonl`) ou dès que le fichier est supprimé.

Tests manuels du relais, à lancer dans agy :

```bash
python3 ~/.agent-bridge/bridge.py agy-relay --probe "texte"   # un seul envoi, affiche le statut HTTP
python3 ~/.agent-bridge/bridge.py agy-relay --inspect         # champs liés au modèle de la conversation
```

## Côté Claude Code

**Canal MCP (recommandé).** Lancez Claude Code avec :

```bash
claude --dangerously-load-development-channels server:agent-bridge
```

Avec `install.sh --shell`, la commande `claude` le fait toute seule : une fonction ajoutée à `~/.bashrc` ajoute le canal aux sessions interactives, mais pas aux sous-commandes (`claude mcp …`), au mode `-p`, à `--help` ni à `--version`. Pour lancer `claude` sans le canal : `command claude`, ou `AGENT_BRIDGE_CHANNEL=0`. Comme les channels sont en préversion, Claude Code affiche un écran de confirmation à chaque lancement.

Le serveur `mcp_channel.py` surveille la boîte de la session et transmet chaque message sous forme d'événement `notifications/claude/channel`. Claude reste endormi jusqu'à l'arrivée d'un message : pas de `Monitor` à relancer, pas de réveil toutes les 30 min, pas de rechargement du cache. Il fournit aussi les outils `send` et `list_agents`. L'adresse de la session est `claude:<nom de session>`, lue dans `~/.claude/sessions/<pid>.json`.

Le serveur est installé pour toutes les sessions Claude, mais il ne touche à la boîte que si la ligne de commande de son process parent `claude` contient le canal `server:agent-bridge`. Sans cette précaution, une session lancée sans canal prendrait les messages, et Claude Code jetterait les événements sans rien dire. Les « channels » sont en préversion de recherche : https://code.claude.com/docs/en/channels-reference.md.

**Solution de repli.** Sans canal, lancez l'outil `Monitor` sur `python3 -u ~/.agent-bridge/bridge.py watch claude`, qui lit la boîte commune `claude`. Il expire au bout de 30 min et doit être relancé.

## Plusieurs sessions, et agy ↔ agy

Le pont relie n'importe quel nombre de sessions. `bridge.py list` (ou l'outil `list_agents`) affiche toutes les sessions actives et la façon dont chacune est réveillée. `send agy` ou `send claude` choisit la seule *autre* session de ce type, puisque l'expéditeur est exclu. S'il y en a plusieurs, la commande refuse et affiche la liste ; utilisez alors `<type>:<préfixe>` ou `<type>@<partie du chemin du dossier>`. Deux sessions agy peuvent donc se parler directement, et deux sessions Claude aussi.

## Sécurité

- **Pas d'authentification** : tout process lancé par votre compte utilisateur peut écrire dans `~/.agent-bridge/inbox/`. Avec le relais, écrire dans la boîte d'agy revient à le faire agir, avec les permissions de sa conversation (`always-proceed`, par exemple).
- **Statut des messages** : un message reçu d'un pair est marqué comme venant d'un agent, et non de l'utilisateur. Il ne vaut jamais approbation.
- **Pas de contournement de permissions** : quand le système de permissions de Claude Code bloque une action, Claude ne demande pas à agy de la faire à sa place. Le blocage remonte à l'utilisateur.
- **Jeton CSRF** : il n'est jamais écrit sur disque ni dans les logs.

## Versions testées

- Claude Code 2.1.301 (Linux)
- agy (Antigravity CLI), language server 1.4.0, modèle Gemini 3.8 Flash (High)

## Licence

Apache License 2.0 — voir [LICENSE](LICENSE). Copyright 2026 Adrien.M (Dr1mS).
