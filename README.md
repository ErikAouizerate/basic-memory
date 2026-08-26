
## Project

**Basic Memory MCP Server**

A remote MCP server that wraps the `basic-memory` CLI as HTTP-exposed tools. Clients (Claude, Codex, Cursor, or any MCP-capable agent) connect to read, write, and search a knowledge base living in a Basic Memory project — without needing local Python, Markdown files, or a local stdio pipe.

I need a auth bearer token verification

**Core Value:** A client on any machine can query and write to the knowledge base over plain HTTP — write a note, search it semantically, see recent activity (all besic memory feature)— with zero local setup and zero data on the client.
