# Install TaleMondo Studio

## 1. Install on Mac

1. Clone the repository or download and unpack its ZIP archive.
2. Double-click **Install Studio.command** in the repository root. If macOS blocks it, right-click the file and choose **Open**.
3. The installer sets up Xcode Command Line Tools, Homebrew, Python 3.12, ffmpeg, the Studio environment, and a local service at `http://127.0.0.1:18187/`. It then opens the first-run wizard.

Running the installer again after an update refreshes Studio while preserving its data. By default, that data lives in `~/Library/Application Support/H3 Studio`; set `H3_MAC_DATA_DIR` to use another location. The directory name is a legacy internal path.

## 2. First-run wizard

The wizard at `http://127.0.0.1:18187/setup.html` guides you through five steps:

1. **SSH key:** Create a key, copy its public part, and add it to your GPU provider account.
2. **GPU server:** Connect a supported provider or configure your own server. The documented MiniMax H3 path calls for at least 96 GB GPU VRAM and 256 GB system RAM. The wizard can provision a Vast.ai machine. For an existing server, run `bash scripts/h3_new_server.sh --host root@HOST --port PORT`. Installation downloads model weights separately and can take substantial time.
3. **MiniMax API (optional):** Add an API key only if you want the separate API route. The local GPU path does not require it.
4. **Coding agents (optional):** Connect Claude Code or Codex to Studio's MCP tools.
5. **Custom workflows:** Import a ComfyUI graph into Studio. The importer accepts graphs, but operation with models other than the documented H3 path has not been verified here.

Once the wizard reports that the required steps are ready, open **Create** and prepare a job. Check the GPU connection and graph before rendering.

## Troubleshooting

- Service log: `~/Library/Logs/H3 Studio/service-error.log`.
- Restart the service: `launchctl kickstart -k gui/$(id -u)/com.minimax.h3-mac-studio`.
- Port in use: `H3_STUDIO_PORT=18197 bash scripts/install_studio_on_mac.sh`.
- Review server installation progress in the wizard's **GPU & Models** section.
- Verify a server without rendering: `bash scripts/verify_h3_server.sh --host root@HOST --port PORT`.
