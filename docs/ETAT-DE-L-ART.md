# État de l'art : Diaulos face aux outils de coordination d'agents de code

*Novembre 2026. Les liens, les étoiles et les dates de dernier commit ont été relevés le 6 novembre 2026 avec l'API GitHub. Ces chiffres changent vite.*

## En bref

Il existe déjà plusieurs façons de faire travailler ensemble des agents de code (Claude Code, Gemini/agy, Codex, OpenCode…). Elles tombent dans quatre familles :

1. **Les boîtes aux lettres MCP.** Un serveur MCP stocke les messages et chaque agent vient les relever avec un outil. L'agent doit être en train de travailler pour lire son courrier : un agent inactif ne voit rien.
2. **Les orchestrateurs tmux.** Un script tape le message dans le terminal de l'agent (`tmux send-keys`) et lit l'écran (`capture-pane`). Ça réveille l'agent, mais en simulant un clavier : c'est fragile, et réservé à Linux et macOS.
3. **Les équipes intégrées d'un éditeur**, comme les *agent teams* de Claude Code. Elles fonctionnent bien, mais seulement entre agents de ce même éditeur.
4. **Les protocoles d'interopérabilité** (A2A, ANP). Ils visent des services en ligne et supposent des serveurs HTTP, des certificats, des annuaires. C'est disproportionné pour des sessions qui tournent dans un terminal.

Diaulos occupe une place que ces familles laissent vide :
- Il réveille une session inactive par les points d'extension prévus par chaque outil, sans simuler de clavier ni interroger en boucle.
- Il relie des agents d'éditeurs différents (Claude Code et agy, CLI ou Desktop).
- Il relie maintenant des machines différentes, par un relais chiffré de bout en bout, appairé par un humain.

## Méthode

- Une session agy (Gemini) a fait une première recherche sur le web, puis vérifié chaque projet cité.
- Les dépôts, les étoiles et les dates ont été recoupés avec l'API GitHub.
- Les descriptions viennent de la page de chaque projet. Nous n'avons pas installé ni testé ces outils, sauf mention contraire.
- Ce que nous disons des *agent teams* de Claude Code repose sur l'usage de l'auteur de Diaulos, pas sur une mesure publiée (voir plus bas).

## Les outils proches

| Projet | Famille | Ce qu'il fait | Réveille une session inactive ? | Plusieurs éditeurs ? | Plusieurs machines ? | Étoiles, dernier commit |
|---|---|---|---|---|---|---|
| [mcp_agent_mail_rust](https://github.com/Dicklesworthstone/mcp_agent_mail_rust) (suite de [mcp_agent_mail](https://github.com/Dicklesworthstone/mcp_agent_mail), 2 232 ★) | Boîte aux lettres MCP | Identités, boîtes, fils de discussion, verrous consultatifs sur les fichiers, archive Git, index SQLite, interface en terminal et web | Non : les agents relèvent leur courrier | Oui (tout client MCP) | Possible via un transport HTTP MCP, sans chiffrement de bout en bout ni appairage | 234 ★, 2026-11-06 |
| [postal-mcp](https://github.com/tkellogg/postal-mcp) | Boîte aux lettres MCP | File de messages SQLite derrière un serveur MCP HTTP (`send_to_agent`, `check_mail`) | Non | Oui | Non | 4 ★, 2025-06-29 |
| [agent-deck](https://github.com/asheshgoplani/agent-deck) | Gestionnaire de sessions | Une interface en terminal pour piloter Claude, Gemini, OpenCode, Codex : worktrees, bacs à sable Docker, mode « chef d'orchestre » | Il relance des prompts à travers les processus qu'il gère, pas par un canal natif | Oui | Via SSH | 1 217 ★, 2026-11-06 |
| [swarmux](https://github.com/6missedcalls/swarmux) | Orchestrateur tmux | Lancer, écrire à, surveiller des agents Claude Code depuis un shell, avec `tmux` et `jq` | Oui, en simulant des frappes (`send-keys`) | Claude Code seulement | Non | 0 ★, 2026-02-27 |
| [Tmux-Orchestrator](https://github.com/absmartly/Tmux-Orchestrator) | Orchestrateur tmux | Hiérarchie orchestrateur, chefs de projet, ingénieurs, tous Claude, dans tmux | Oui, par tmux | Claude seulement | Non | 4 ★, 2025-08-01 |
| [AgentMux](https://github.com/markuswondrak/AgentMux) | Orchestrateur tmux | Pipeline déterministe (PM, architecte, codeur, relecteur) qui avance sur l'état de fichiers partagés | Par tmux | Oui (Claude, Gemini, Codex) | Non | 38 ★, 2026-05-17 |
| [orchestrator](https://github.com/MatchaOnMuffins/orchestrator) | Application de bureau | Plusieurs Claude Code en parallèle, chacun dans son worktree Git et sa branche | Sans objet : c'est l'humain qui pilote | Claude seulement | Non | 30 ★, 2026-06-30 |
| [claude-code-orchestrator](https://github.com/mohsen1/claude-code-orchestrator) | Orchestration par SDK | Équipes hiérarchiques de Claude Code lancées avec le Claude Agent SDK | Sans objet : le programme lance les agents | Claude seulement | Non | 20 ★, 2026-01-24 |
| Agent teams de Claude Code (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) | Intégré à l'éditeur | Un chef d'équipe et des coéquipiers Claude, une liste de tâches partagée, des messages entre eux | Oui, en interne | Claude seulement | Non | Fonction expérimentale de Claude Code |
| **Diaulos** | Pont de messagerie | Messages entre sessions Claude Code et agy (CLI, Desktop), et entre machines | **Oui, par les points d'extension natifs** : canal MCP pour Claude, language server et `agy -p` pour agy | **Oui (Claude et Gemini)** | **Oui, par relais chiffré de bout en bout** | — |

## Les protocoles

| Protocole | Rôle | Pourquoi ce n'est pas la même chose que Diaulos |
|---|---|---|
| [A2A](https://github.com/a2aproject/A2A) (Agent2Agent, Linux Foundation, 26 434 ★) | Interopérabilité entre applications d'agents : `AgentCard`, cycle de vie des tâches, gRPC, HTTP, JSON-RPC | Pensé pour des services exposés sur le réseau, avec authentification d'entreprise. Il faudrait un serveur HTTP par session de terminal. Diaulos pourrait un jour exposer ses sessions en A2A, mais A2A ne réveille pas un Claude Code inactif à lui seul. |
| MCP | Outils et contexte pour un agent | Le client appelle le serveur, pas l'inverse : un serveur MCP ordinaire ne peut pas déclencher un tour. Les *channels* de Claude Code (aperçu de recherche) ajoutent cette poussée ; c'est ce qu'utilise Diaulos. |
| [ACP](https://github.com/agentclientprotocol/agent-client-protocol) (Agent Client Protocol, 4 587 ★) | Relier un éditeur à un agent de code | Relation éditeur vers agent, pas agent vers agent. |
| [ANP](https://github.com/agent-network-protocol/AgentNetworkProtocol) (Agent Network Protocol, 1 468 ★) | Découverte et communication décentralisées entre agents (identités décentralisées) | Une vision à l'échelle d'Internet, loin du poste de développement. |

## Entre machines

Pour relier des agents sur des machines différentes, on rencontre trois approches :

- **Un VPN ou un réseau superposé** (Tailscale, WireGuard). Il donne une connectivité IP, mais demande une installation et des comptes, et il ouvre toute la machine plutôt que les seuls agents.
- **Un service en ligne.** Il demande un compte, et le fournisseur peut lire le code et les prompts qui passent.
- **Le modèle de [magic-wormhole](https://github.com/magic-wormhole/magic-wormhole)** (23 082 ★) : un code court, un échange de clés authentifié (SPAKE2) par un serveur de rendez-vous, puis un canal chiffré.

Diaulos suit l'esprit de magic-wormhole :
- **Appairage :** un code à usage unique contenant un secret de 256 bits, qui authentifie un échange X25519. Les deux personnes comparent ensuite un code à 6 chiffres.
- **Chiffrement :** une clé par paire de machines, une clé et un sujet de relais par sens, et des trames scellées en ChaCha20-Poly1305.
- **Transport :** les deux machines ne font que des requêtes HTTPS sortantes, vers ntfy.sh ou un relais auto-hébergé. Le relais ne voit que du chiffré.

Diaulos utilise un secret long plutôt qu'un PAKE. Le code est donc plus long à transmettre que celui de magic-wormhole, mais il n'y a pas de dépendance PAKE.

## Sécurité : l'authentification ne suffit pas

Les travaux sur la sécurité des systèmes multi-agents (OWASP pour les applications agentiques, les écrits de Simon Willison sur l'injection de prompt) répètent une idée. Prouver qu'un message vient bien d'un agent ne dit rien de ce qu'il contient. Un agent authentique a pu lire une page, une issue ou un fichier piégé, puis transmettre l'injection à ses pairs.

Diaulos traite les deux côtés :
- **Côté identité.** Sur une machine, l'expéditeur est déclaratif. Entre machines, le nom de la machine est prouvé par sa clé.
- **Côté contenu.** Un message d'un pair n'est jamais un accord de l'utilisateur, et un agent ne fait jamais pour un pair ce que ses propres permissions bloquent.
- **Les décisions qui ouvrent l'accès restent humaines.** Seule une personne au terminal peut appairer une machine ou élargir sa liste d'autorisations.
- **Les mots de la passerelle sont écrits localement.** Un refus renvoie un avis fixe, sans détail sur les sessions locales.

Diaulos ne protège pas une machine contre ses propres processus. Les expéditeurs locaux ne sont pas authentifiés, et la barrière du terminal se contourne par un programme qui tourne déjà sous le compte de l'utilisateur. Le README le dit explicitement.

## Agent teams de Claude Code et Diaulos : le coût des réveils

Les *agent teams* de Claude Code sont agréables à utiliser : un chef d'équipe répartit les tâches, les coéquipiers se parlent, et tout reste dans Claude Code.

En usage réel, l'auteur de Diaulos a constaté que les coéquipiers d'une *agent team* se réveillent entre eux **2 à 3 fois plus souvent que nécessaire**. Pour un travail comparable, la consommation de tokens est **environ deux fois celle de Diaulos**. C'est une observation d'usage, pas encore une mesure publiée.

Cette différence s'explique par la façon dont Diaulos réveille un agent. Chaque réveil fait relire à l'agent son contexte : c'est ce qui coûte des tokens.
- **Claude Code.** Une session dort jusqu'à l'arrivée d'un message sur son canal : pas d'interrogation périodique, pas de réveil programmé, pas de rechargement de cache.
- **agy.** Le relais ne lance un tour que lorsqu'un message attend. Les messages qui arrivent pendant ce temps sont livrés ensemble, en un seul tour.
- **Côté agent.** Un agent qui n'a rien à dire n'écrit pas, et rien ne le réveille par principe.

Pour transformer cette observation en chiffre, nous proposons un protocole de mesure reproductible :
1. **Tâche identique.** Une même tâche découpée en trois rôles (par exemple : planificateur, développeur, relecteur), sur le même dépôt et le même commit.
2. **Deux configurations.** Une *agent team* de Claude Code d'un côté, trois sessions Claude Code reliées par Diaulos de l'autre. Même modèle et mêmes consignes.
3. **Mesures.** Nombre de tours par agent, tours sans action utile (réveil suivi d'aucun outil ni message), tokens d'entrée et de sortie (`/cost` ou les journaux de session), durée totale, résultat (tests qui passent).
4. **Répétitions.** Au moins cinq essais par configuration, en notant la médiane et l'écart.

Les deux approches ne s'excluent pas. Les *agent teams* restent le plus simple pour une équipe 100 % Claude sur une machine. Diaulos devient utile dès qu'il faut mêler Claude et Gemini, réveiller des sessions qui existent déjà, garder la main sur qui réveille qui, ou passer d'une machine à l'autre.

## Ce qui distingue Diaulos

- **Réveil natif entre éditeurs concurrents.** Le canal MCP de Claude Code d'un côté, le language server d'agy et `agy -p` de l'autre : ni simulation de clavier, ni interrogation en boucle.
- **Des sessions qui existent déjà.** Diaulos ne lance pas une équipe : il relie les sessions que l'utilisateur a déjà ouvertes, y compris une conversation agy CLI dont le terminal est fermé.
- **Sobriété.** La bibliothèque standard de Python suffit en local. Le paquet `cryptography` n'est demandé que pour le mode entre machines.
- **Messagerie entre machines appairée par un humain**, chiffrée de bout en bout, sans port ouvert.
- **Visibilité.** Avec [Paros](https://github.com/Dr1mS/Paros), chaque message vole d'un animal de bureau à l'autre.

Ses limites, en face :
- Il repose sur des méthodes non documentées du language server d'agy et sur les *channels* de Claude Code, encore en aperçu de recherche.
- Deux familles d'agents seulement (Claude Code et agy).
- Pas de verrous de fichiers ni de tableau de tâches partagé.
- Pas de mesure publiée de sa consommation de tokens.

## Idées à reprendre

1. **Verrous consultatifs sur les fichiers** (mcp_agent_mail). Quand Claude et agy travaillent dans le même dépôt, un agent réserve un fichier ou un motif avant de l'éditer, et les autres le voient.
2. **Un worktree par nouvelle session** (orchestrator, agent-deck). Une option `--worktree` pour `agy:new-cli` créerait un worktree Git et une branche, pour isoler chaque travailleur.
3. **Un tableau de tâches partagé** (agent teams) : tâches en attente, prises, terminées, pour éviter le travail en double.
4. **Une fiche de compétences par session** (`AgentCard` d'A2A). `list` montrerait ce que sait faire chaque session avant qu'on lui confie une tâche.
5. **Mesurer.** Le protocole ci-dessus, publié avec ses résultats, rendrait l'argument du coût vérifiable.

## Sources

Dépôts consultés le 6 novembre 2026 :
- https://github.com/Dicklesworthstone/mcp_agent_mail
- https://github.com/Dicklesworthstone/mcp_agent_mail_rust
- https://github.com/tkellogg/postal-mcp
- https://github.com/asheshgoplani/agent-deck
- https://github.com/6missedcalls/swarmux
- https://github.com/absmartly/Tmux-Orchestrator
- https://github.com/markuswondrak/AgentMux
- https://github.com/MatchaOnMuffins/orchestrator
- https://github.com/mohsen1/claude-code-orchestrator
- https://github.com/a2aproject/A2A
- https://github.com/agentclientprotocol/agent-client-protocol
- https://github.com/agent-network-protocol/AgentNetworkProtocol
- https://github.com/magic-wormhole/magic-wormhole
