# Reverse engineering with REA

[REA (morluto/rea)](https://github.com/morluto/rea) is an MIT-licensed MCP server and CLI
("Reverse Engineer Anything"). It inspects native binaries, JavaScript/Electron apps,
.NET assemblies, Android apps and websites, and returns the evidence behind each finding.

We don't copy REA's source into this repo: it is ~10,000 files / 140 MB of TypeScript plus
Ghidra/Hopper bridges, and it updates often. Instead it runs as a tool next to the repo:

- `npx rea-agents setup` (below) registers it with your agent, or add it by hand to
  your agent's MCP config (pin the version; bump it when you update):

  ```json
  { "mcpServers": { "rea": { "command": "npx", "args": ["-y", "rea-agents@6.10.1", "mcp"] } } }
  ```
- The CLI works on any machine with Node.js: `npx -y rea-agents@6.10.1 --help`.

## Setup (once, on the PC that has the target files)

1. Install Node.js 20+ (nodejs.org).
2. Run from a folder **outside** the project you analyze:

   ```bash
   npx rea-agents setup
   ```

   Choose your agent (Claude Code), approve, then restart the agent.
3. Native binaries (`.exe`, `.dll`, `.ex5` host, Android `.so`) need a decompiler:
   [Ghidra](https://ghidra-sre.org/) (free) is enough. JavaScript, Electron and .NET
   analysis need nothing extra.

## What we use it for

| Use | Example ask to the agent |
|-----|--------------------------|
| Audit our own builds | "Inspect `WildChance_Copier.ex5`'s host DLL imports — does it touch anything besides WebRequest?" |
| Our own web app | "Analyze `static/site/` as a JavaScript application and list every endpoint it calls." |
| Broker / platform interop | "How does this broker's web terminal (that we have an account on) format its order requests, so our bridge can match it?" |
| Learn a feature, build our own | "How does a charting app's session-box tool decide box edges? Show the evidence, then write our version." |
| Security checks | "Check the members page bundle for leaked keys or debug endpoints." |

## Ground rules (from REA's DISCLAIMER and our own)

- Analyse only software we own, are licensed for, or are authorised to test.
- **No** cracking license checks, DRM or protection on paid EAs, indicators or
  platforms; **no** copying someone else's code or assets into WildChance. Learn the
  idea, then write our own implementation.
- Never point it at a broker's live servers beyond normal use of our own account.
- Respect each product's licence/terms; reverse engineering is restricted in some
  countries.
- Keep findings and samples out of the repo if they contain third-party code.
