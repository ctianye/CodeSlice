# CodeSlice v0.1.0

The first public Windows release of CodeSlice.

## Download

Download **CodeSlice-0.1.0-Setup.exe** below. The x64 NSIS installer includes
the Electron desktop application and its local Python backend. Python and
Node.js do not need to be installed separately.

## Get started

1. Run the installer on Windows 10 (version 1809 or later) or Windows 11.
2. Open CodeSlice and add a local source-code repository.
3. Configure a compatible chat model and API key in **Settings → Model**.
4. Open a source file, select a code block, and ask a question.
5. Follow the file-and-line citations in answers; review and approve any
   requested file changes or shell commands.

## Highlights

- Local Tree-sitter code indexing with combined FTS5 and vector search.
- Navigable citations linking answers back to source files and lines.
- Human approval before an agent writes a file or runs a command.
- Searchable reading notes that can be cited across projects.
- Model credentials saved in Windows Credential Manager.
- Refreshed CodeSlice desktop, installer, and application icons.

Repository files, the search index, saved notes, and conversations are stored on
the user's computer. Code included as context in a question is sent to the
model provider that the user configures. The provider's retention and privacy
policies apply.

## Windows note

This first release is not code-signed. Windows may show a SmartScreen or
unknown-publisher notice when the installer starts. Verify the download using
the SHA-256 checksum displayed with the release asset.

SHA-256: `2D3CB28BFC2FC68B50F530E6E4D106BB878A05B58B01878B3343AA4B33DDBE68`
