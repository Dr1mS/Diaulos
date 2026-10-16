# Pont Claude Code ↔ agy

Ce projet fait communiquer des sessions **Claude Code** et des sessions **agy** (la CLI Antigravity de Google, modèles Gemini) qui tournent sur la même machine. Chaque agent peut envoyer un message à l'autre et le réveiller, quel que soit le dossier où il a été lancé, sans que l'utilisateur serve d'intermédiaire.

Le journal détaillé des expériences est dans [docs/JOURNAL.md](docs/JOURNAL.md).

## Installation

```bash
./install.sh      # à relancer après chaque mise à jour du dépôt
./uninstall.sh    # ajoutez --purge pour supprimer aussi les messages
```

`install.sh` met en place :

| Cible | Contenu |
|---|---|
| `~/.agent-bridge/bridge.py` | Le script du pont. Les boîtes aux lettres sont dans le même dossier. |
| `~/.gemini/config/hooks.json` | Hooks agy globaux `SessionStart`, `PreInvocation` et `Stop`, sous la clé `agent-bridge`. |
| `~/.claude/skills/agy-bridge/SKILL.md` | Skill Claude Code qui explique comment envoyer et recevoir des messages. |
| `~/.claude/settings.json` | Règles d'autorisation `Bash(python3 ~/.agent-bridge/bridge.py *)`. |

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

- **Adresses** : `claude` désigne la session Claude Code qui lance `watch claude`. `claude:<nom>` désigne une session Claude nommée. `agy:<conversationId>` désigne une conversation agy. `send agy` marche quand une seule conversation agy est active. Sinon, utilisez `agy:<préfixe>` ou `agy@<partie du chemin du dossier>`. `bridge.py list` affiche les conversations agy actives.
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

La skill `agy-bridge` explique la marche à suivre. Pour être réveillé par agy, lancez l'outil `Monitor` sur `python3 -u ~/.agent-bridge/bridge.py watch claude`. Un `Monitor` expire au bout de 30 min maximum et doit alors être relancé ; les messages arrivés entre-temps restent dans la boîte.

## Sécurité

- **Pas d'authentification** : tout process lancé par votre compte utilisateur peut écrire dans `~/.agent-bridge/inbox/`. Avec le relais, écrire dans la boîte d'agy revient à le faire agir, avec les permissions de sa conversation (`always-proceed`, par exemple).
- **Statut des messages** : un message reçu d'un pair est marqué comme venant d'un agent, et non de l'utilisateur. Il ne vaut jamais approbation.
- **Pas de contournement de permissions** : quand le système de permissions de Claude Code bloque une action, Claude ne demande pas à agy de la faire à sa place. Le blocage remonte à l'utilisateur.
- **Jeton CSRF** : il n'est jamais écrit sur disque ni dans les logs.

## Versions testées

- Claude Code 2.1.301 (Linux)
- agy (Antigravity CLI), language server 1.4.0, modèle Gemini 3.8 Flash (High)
